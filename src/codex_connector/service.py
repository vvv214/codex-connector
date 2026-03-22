from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError

from .commands import ParsedMessage, parse_message
from .config import AppConfig
from .codex_sessions import CodexSessionMonitor, SessionNotification, display_thread_title, load_thread_snapshots
from .models import ChatState, Project, TaskRun
from .presence import DesktopPresence, create_desktop_presence
from .rendering import (
    render_help_text,
    render_last_task,
    render_new_task_picker,
    render_project_sessions,
    render_status,
    render_task_notification,
    render_task_result,
)
from .runner import Runner, RunnerResult
from .state import StateStore
from .telegram import TelegramBotClient, TelegramUpdate
from .telegram_runtime import OutboxMessage, TelegramRuntimeStore

FOLLOW_LATEST_CALLBACK = "__latest__"


class BridgeService:
    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        adapter: Runner,
        telegram: TelegramBotClient | None = None,
        logger: logging.Logger | None = None,
        desktop_presence: DesktopPresence | None = None,
        runtime_store: TelegramRuntimeStore | None = None,
    ):
        self.config = config
        self.store = store
        self.adapter = adapter
        self.telegram = telegram
        self.logger = logger or logging.getLogger("codex_connector")
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="codex-connector")
        self._session_monitor: CodexSessionMonitor | None = None
        self._desktop_presence = desktop_presence
        self._runtime_store = runtime_store or TelegramRuntimeStore(self._runtime_store_path(config.state_file))
        self._sender_stop_event = threading.Event()
        self._sender_thread: threading.Thread | None = None
        self._last_poll_error_message: str | None = None
        self._last_poll_error_at: float = 0.0
        self._poll_error_repeat_count = 0
        if self._desktop_presence is None and self.config.codex_sessions.desktop_active_mode in {"silent", "suppress"}:
            self._desktop_presence = create_desktop_presence(
                idle_threshold_seconds=self.config.codex_sessions.desktop_idle_threshold_seconds,
                logger=self.logger,
            )

    def close(self) -> None:
        if self._session_monitor is not None:
            self._session_monitor.close()
        self._stop_sender_loop()
        self._executor.shutdown(wait=False, cancel_futures=False)

    @staticmethod
    def _runtime_store_path(state_file: str | Path) -> Path:
        state_path = Path(state_file).expanduser().resolve()
        return state_path.parent / f"{state_path.stem}.runtime.sqlite3"

    def serve(self) -> None:
        if self.telegram is None:
            raise RuntimeError("telegram client is required for serve mode")
        if not self.config.telegram_bot_token:
            raise RuntimeError("telegram_bot_token is missing from config")
        try:
            self.telegram.set_default_commands()
        except Exception:
            self.logger.exception("failed to register Telegram commands")
        monitor = self._ensure_session_monitor()
        if monitor is not None:
            monitor.start()
        self._start_sender_loop()
        offset = self._runtime_store.get_next_poll_offset()
        self.logger.info("starting Telegram polling")
        try:
            while True:
                try:
                    updates = self.telegram.get_updates(offset=offset, timeout=self.config.poll_timeout_seconds)
                except Exception as exc:
                    self._log_poll_error(exc)
                    time.sleep(self.config.poll_sleep_seconds)
                    continue
                self._clear_poll_error_state()
                for update in updates:
                    if self._runtime_store.is_update_processed(update.update_id):
                        offset = max(offset or 0, update.update_id + 1)
                        self._runtime_store.set_next_poll_offset(offset)
                        continue
                    try:
                        self.handle_telegram_update(update)
                        self._runtime_store.mark_update_processed(update.update_id)
                        offset = max(offset or 0, update.update_id + 1)
                        self._runtime_store.set_next_poll_offset(offset)
                    except Exception as exc:
                        self.logger.exception("failed to process Telegram update update_id=%s", update.update_id)
                        self._notify(
                            update.chat_id,
                            f"Error: {exc}",
                            update.message_id,
                            dedupe_key=f"telegram:error:{update.update_id}",
                        )
        finally:
            if monitor is not None:
                monitor.close()
            self._stop_sender_loop()

    def _start_sender_loop(self) -> None:
        if self.telegram is None:
            return
        thread = self._sender_thread
        if thread is not None and thread.is_alive():
            return
        self._sender_stop_event.clear()
        self._sender_thread = threading.Thread(
            target=self._sender_loop,
            name="codex-connector-sender",
            daemon=True,
        )
        self._sender_thread.start()

    def _stop_sender_loop(self) -> None:
        self._sender_stop_event.set()
        thread = self._sender_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._sender_thread = None

    def _sender_loop(self) -> None:
        while not self._sender_stop_event.wait(0.5):
            try:
                messages = self._runtime_store.get_due_messages(limit=20)
                if not messages:
                    continue
                for message in messages:
                    if self._sender_stop_event.is_set():
                        return
                    self._deliver_outbox_message(message)
            except Exception:
                self.logger.exception("telegram sender loop failed")
                time.sleep(1.0)

    def _deliver_outbox_message(self, message: OutboxMessage) -> None:
        if self.telegram is None:
            return
        try:
            self.telegram.send_message(
                message.chat_id,
                message.text,
                reply_to_message_id=message.reply_to_message_id,
                inline_keyboard=message.inline_keyboard,
                disable_notification=message.disable_notification,
            )
        except Exception as exc:
            if self._is_transient_send_error(exc):
                delay = self._retry_delay_seconds(message.attempts)
                self._runtime_store.mark_message_retry(
                    message.id,
                    error=self._format_poll_error(exc),
                    delay_seconds=delay,
                )
                self.logger.warning(
                    "telegram send retry id=%s in %.1fs: %s",
                    message.id,
                    delay,
                    self._format_poll_error(exc),
                )
                return
            self._runtime_store.mark_message_failed(
                message.id,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.logger.exception("telegram send failed permanently outbox_id=%s", message.id)
            return
        self._runtime_store.mark_message_sent(message.id)

    def _is_transient_send_error(self, exc: Exception) -> bool:
        if self._is_transient_poll_error(exc):
            return True
        return False

    def _retry_delay_seconds(self, attempts: int) -> float:
        exponent = max(0, attempts)
        return min(60.0, 2.0 * (2**exponent))

    def _log_poll_error(self, exc: Exception) -> None:
        if not self._is_transient_poll_error(exc):
            self.logger.exception("failed to fetch Telegram updates")
            return

        message = self._format_poll_error(exc)
        now = time.time()
        if message == self._last_poll_error_message and now - self._last_poll_error_at < 60:
            self._poll_error_repeat_count += 1
            return

        if message == self._last_poll_error_message and self._poll_error_repeat_count > 1:
            self.logger.warning(
                "Telegram polling still failing (%s repeats suppressed): %s",
                self._poll_error_repeat_count - 1,
                message,
            )
        else:
            self.logger.warning("Telegram polling error: %s", message)
        self._last_poll_error_message = message
        self._last_poll_error_at = now
        self._poll_error_repeat_count = 1

    def _clear_poll_error_state(self) -> None:
        if self._last_poll_error_message is None:
            return
        if self._poll_error_repeat_count > 1:
            self.logger.info(
                "Telegram polling recovered after %s suppressed repeats",
                self._poll_error_repeat_count - 1,
            )
        self._last_poll_error_message = None
        self._last_poll_error_at = 0.0
        self._poll_error_repeat_count = 0

    def _is_transient_poll_error(self, exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        if isinstance(exc, HTTPError):
            return exc.code in {408, 409, 425, 429} or exc.code >= 500
        return isinstance(exc, URLError)

    def _format_poll_error(self, exc: Exception) -> str:
        if isinstance(exc, HTTPError):
            return f"HTTP {exc.code}: {exc.reason}"
        if isinstance(exc, URLError):
            reason = exc.reason
            if isinstance(reason, BaseException):
                return f"{type(reason).__name__}: {reason}"
            if reason:
                return str(reason)
        return f"{type(exc).__name__}: {exc}"

    def handle_telegram_update(self, update: TelegramUpdate) -> None:
        if self.config.allowed_chat_ids and update.chat_id not in self.config.allowed_chat_ids:
            self.logger.warning("rejected unauthorized chat_id=%s", update.chat_id)
            return
        if update.kind == "callback":
            self._handle_callback_update(update)
            return

        message = parse_message(update.text)
        response = self._handle_parsed_message(
            update.chat_id,
            message,
            update.message_id,
            request_key=f"telegram:update:{update.update_id}",
        )
        if response and self.telegram is not None:
            inline_keyboard = self._keyboard_for_message(update.chat_id, message)
            self._send_message(
                update.chat_id,
                response,
                reply_to_message_id=update.message_id,
                inline_keyboard=inline_keyboard,
                dedupe_key=f"telegram:reply:{update.update_id}",
            )

    def handle_message(
        self,
        chat_id: int,
        text: str,
        message_id: int | None = None,
        request_key: str | None = None,
    ) -> str | None:
        message = parse_message(text)
        return self._handle_parsed_message(chat_id, message, message_id, request_key=request_key)

    def _handle_parsed_message(
        self,
        chat_id: int,
        message: ParsedMessage,
        message_id: int | None = None,
        request_key: str | None = None,
    ) -> str | None:
        kind = message.kind
        argument = message.argument
        if kind == "empty":
            return None
        if kind == "help":
            return render_help_text()
        if kind == "status":
            return self.render_status(chat_id)
        if kind == "last":
            return self.render_last(chat_id)
        if kind == "project":
            return self.switch_project(chat_id, argument)
        if kind == "new" and not argument:
            return self.prepare_new_task(chat_id)
        if kind in {"new", "continue"}:
            mode = self._mode_for_message(chat_id, message)
            response = self.submit_task(
                chat_id,
                argument,
                mode,
                message_id=message_id,
                request_key=request_key,
            )
            if response is None:
                self._set_pending_mode(chat_id, None)
            return response
        if kind == "unknown":
            return "Unknown command. Send /help for usage."
        response = self.submit_task(
            chat_id,
            argument,
            "continue",
            message_id=message_id,
            request_key=request_key,
        )
        if response is None:
            self._set_pending_mode(chat_id, None)
        return response

    def _mode_for_message(self, chat_id: int, message: ParsedMessage) -> str:
        if message.kind == "new":
            return "new"
        if not message.from_plain_text:
            return message.kind
        chat = self.store.get_chat(chat_id)
        if chat is not None and chat.pending_mode in {"new", "continue"}:
            return chat.pending_mode
        return message.kind

    def _resolve_project(self, chat_id: int, project_name: str | None = None) -> Project:
        if project_name:
            project = self.config.project_by_name(project_name)
            if project is None:
                raise ValueError(f"Unknown project: {project_name}")
            self._remember_project(chat_id, project, pin=None)
            return project

        chat = self.store.get_chat(chat_id)
        if chat is not None:
            routed_name = self._routed_project_name(chat)
            project = self.config.project_by_name(routed_name)
            if project is not None:
                return project
        project = self.config.default_project()
        if project is None:
            raise RuntimeError("No project is configured")
        self._remember_project(chat_id, project, pin=None)
        return project

    def _remember_project(self, chat_id: int, project: Project, pin: bool | None) -> None:
        pinned_project_name: str | None | object
        if pin is None:
            pinned_project_name = self.store.get_chat(chat_id).pinned_project_name if self.store.get_chat(chat_id) is not None else None
        elif pin:
            pinned_project_name = project.name
        else:
            pinned_project_name = None
        self.store.upsert_chat(
            chat_id,
            project_name=project.name,
            repo_path=project.repo_path,
            last_active_at=time.time(),
            active_project_name=project.name,
            pinned_project_name=pinned_project_name,
        )

    def _routed_project_name(self, chat: ChatState) -> str:
        pinned_name = (chat.pinned_project_name or "").strip()
        if pinned_name:
            return pinned_name
        latest_name = (chat.project_name or "").strip()
        if latest_name:
            return latest_name
        fallback_name = (chat.active_project_name or "").strip()
        if fallback_name:
            return fallback_name
        return ""

    def _routing_label(self, chat: ChatState | None) -> str:
        if chat is None:
            project = self.config.default_project()
            return f"following latest ({project.name if project is not None else 'n/a'})"
        if chat.pinned_project_name:
            latest_name = chat.project_name or chat.pinned_project_name
            if latest_name == chat.pinned_project_name:
                return f"pinned to {chat.pinned_project_name}"
            return f"pinned to {chat.pinned_project_name} (latest is {latest_name})"
        current = self._routed_project_name(chat) or "n/a"
        return f"following latest ({current})"

    def _set_pending_mode(self, chat_id: int, pending_mode: str | None) -> None:
        if self.store.get_chat(chat_id) is None:
            self._resolve_project(chat_id)
        self.store.set_chat_pending_mode(chat_id, pending_mode)

    def _ensure_chat_state(self, chat_id: int) -> ChatState:
        chat = self.store.get_chat(chat_id)
        if chat is not None:
            return chat
        self._resolve_project(chat_id)
        chat = self.store.get_chat(chat_id)
        if chat is None:
            raise RuntimeError("No project is configured")
        return chat

    def switch_project(self, chat_id: int, project_name: str) -> str:
        if not project_name:
            return self.render_project_sessions(chat_id)
        if project_name == "latest":
            chat = self.store.get_chat(chat_id)
            if chat is None:
                self._resolve_project(chat_id)
                chat = self.store.get_chat(chat_id)
            latest_name = (chat.project_name or "") if chat is not None else ""
            self.store.upsert_chat(
                chat_id,
                last_active_at=time.time(),
                pinned_project_name=None,
            )
            suffix = f" Current latest project: {latest_name}." if latest_name else ""
            return self.render_project_sessions(chat_id, prefix=f"Now following the latest session again.{suffix}")
        project = self.config.project_by_name(project_name)
        if project is None:
            return self.render_project_sessions(chat_id, prefix=f"Unknown project: {project_name}")
        self._remember_project(chat_id, project, pin=True)
        return self.render_project_sessions(chat_id, prefix=f"Pinned project to {project.name}.")

    def prepare_new_task(self, chat_id: int, project_name: str | None = None, prefix: str | None = None) -> str:
        if project_name:
            project = self.config.project_by_name(project_name)
            if project is None:
                raise ValueError(f"Unknown project: {project_name}")
            self._remember_project(chat_id, project, pin=True)
        else:
            project = self._resolve_project(chat_id, project_name)
        self._set_pending_mode(chat_id, "new")
        intro = prefix or f"New task mode armed for {project.name}. Send the prompt after choosing a project."
        return render_new_task_picker(
            project.name,
            self._recent_session_rows(),
            max_chars=self.config.max_output_chars,
            prefix=intro,
        )

    def render_status(self, chat_id: int) -> str:
        chat = self._ensure_chat_state(chat_id)
        project = self.config.project_by_name(self._routed_project_name(chat))
        running_task = self.store.running_task_for_chat(chat_id)
        text = render_status(chat, project, running_task)
        if running_task is None:
            project_name = self._routed_project_name(chat)
            last_task = self.store.last_task_for_project(project_name) if project_name else None
            if last_task is not None:
                text += f"\nLast task: {last_task.status} ({last_task.mode}) {last_task.task_id}"
        return text

    def render_last(self, chat_id: int) -> str:
        chat = self._ensure_chat_state(chat_id)
        return render_last_task(self.store.last_task_for_project(self._routed_project_name(chat)))

    def render_project_sessions(self, chat_id: int, prefix: str | None = None) -> str:
        chat = self.store.get_chat(chat_id)
        return render_project_sessions(
            self._routing_label(chat),
            self._recent_session_rows(),
            max_chars=self.config.max_output_chars,
            prefix=prefix,
        )

    def _ensure_session_monitor(self) -> CodexSessionMonitor | None:
        if not self.config.codex_sessions.enabled or self.telegram is None:
            return None
        if self._session_monitor is None:
            self._session_monitor = CodexSessionMonitor(
                state_db_path=self.config.codex_sessions.state_db_path,
                poll_interval_seconds=self.config.codex_sessions.poll_interval_seconds,
                include_user_messages=self.config.codex_sessions.include_user_messages,
                target_chat_ids=self._session_target_chat_ids,
                send_message=self._send_session_message,
                on_notification=self._record_session_notification,
                logger=self.logger,
            )
        return self._session_monitor

    def _session_target_chat_ids(self) -> list[int]:
        if self.config.allowed_chat_ids:
            return sorted(self.config.allowed_chat_ids)
        return self.store.chat_ids()

    def _send_session_message(self, chat_id: int, text: str) -> None:
        delivery_mode = self._session_delivery_mode()
        if delivery_mode == "suppress":
            return
        self._send_message(
            chat_id,
            text,
            disable_notification=(delivery_mode == "silent"),
            dedupe_key=f"session:{chat_id}:{self._text_fingerprint(text)}",
        )

    def _session_delivery_mode(self) -> str:
        mode = self.config.codex_sessions.desktop_active_mode
        if mode not in {"silent", "suppress"}:
            return "always"
        if self._desktop_presence is None:
            return "always"
        if not self._desktop_presence.is_user_active():
            return "always"
        return mode

    def _handle_callback_update(self, update: TelegramUpdate) -> None:
        if self.telegram is not None and update.callback_query_id:
            self.telegram.answer_callback_query(update.callback_query_id)
        action, _, project_name = update.text.partition(":")
        project_name = project_name.strip()
        if action not in {"project", "new"} or not project_name:
            return
        if action == "project":
            target = "latest" if project_name == FOLLOW_LATEST_CALLBACK else project_name
            response = self.switch_project(update.chat_id, target)
            inline_keyboard = self._project_keyboard(update.chat_id, action="project")
        else:
            response = self.prepare_new_task(
                update.chat_id,
                project_name=project_name,
                prefix=f"New task target set to {project_name}. Send the prompt for a fresh session.",
            )
            inline_keyboard = self._project_keyboard(update.chat_id, action="new")
        if response and self.telegram is not None:
            callback_kind = "project" if action == "project" else "new"
            self._send_message(
                update.chat_id,
                response,
                reply_to_message_id=update.message_id,
                inline_keyboard=inline_keyboard,
                dedupe_key=f"telegram:callback:{callback_kind}:{update.callback_query_id or update.message_id}",
            )

    def _record_session_notification(self, chat_id: int, notification: SessionNotification) -> None:
        project = self.config.project_by_repo_path(notification.repo_path)
        if project is None:
            project = self.config.project_by_name(notification.workspace)
        if project is None:
            for candidate in self.config.projects:
                if Path(candidate.repo_path).name == notification.workspace:
                    project = candidate
                    break
        if project is None:
            return
        self._remember_project(chat_id, project, pin=None)

    def submit_task(
        self,
        chat_id: int,
        prompt: str,
        mode: str,
        message_id: int | None = None,
        request_key: str | None = None,
    ) -> str | None:
        prompt = prompt.strip()
        if not prompt:
            return f"Usage: /{mode} <prompt>"
        if request_key:
            existing = self.store.find_task_by_request_key(request_key)
            if existing is not None:
                self.logger.info(
                    "skipping duplicate Telegram task request request_key=%s task_id=%s",
                    request_key,
                    existing.task_id,
                )
                return None
        if self.store.running_task_for_chat(chat_id) is not None:
            return "A task is already running for this chat. Wait for it to finish before starting another."

        project = self._resolve_project(chat_id)
        self._validate_project_runtime(project)
        task = self._enqueue_task(chat_id, project, prompt, mode, request_key=request_key)
        self._executor.submit(self._execute_task, task.task_id, project, prompt, mode, chat_id, message_id, True)
        return None

    def run_task_sync(self, chat_id: int, prompt: str, mode: str, project_name: str | None = None) -> TaskRun:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        if self.store.running_task_for_chat(chat_id) is not None:
            raise RuntimeError("A task is already running for this chat.")
        project = self._resolve_project(chat_id, project_name)
        self._validate_project_runtime(project)
        task = self._enqueue_task(chat_id, project, prompt, mode, request_key=None)
        return self._execute_task(task.task_id, project, prompt, mode, chat_id, message_id=None, notify=False)

    def _enqueue_task(
        self,
        chat_id: int,
        project: Project,
        prompt: str,
        mode: str,
        *,
        request_key: str | None = None,
    ) -> TaskRun:
        task_id = uuid.uuid4().hex
        started_at = time.time()
        task = TaskRun(
            task_id=task_id,
            chat_id=chat_id,
            project_name=project.name,
            prompt=prompt,
            mode=mode,
            status="queued",
            started_at=started_at,
            request_key=request_key,
        )
        self.store.add_task(task)
        self.store.set_chat_task(chat_id, task_id, last_active_at=started_at)
        return task

    def _validate_project_runtime(self, project: Project) -> None:
        repo_path = Path(project.repo_path).expanduser().resolve()
        if self.config.security.require_existing_repos and not repo_path.is_dir():
            raise RuntimeError(f"Project repo_path does not exist: {repo_path}")
        if self.config.security.require_git_repos and not (repo_path / ".git").exists():
            raise RuntimeError(f"Project is not a git repository: {repo_path}")

    def _execute_task(
        self,
        task_id: str,
        project: Project,
        prompt: str,
        mode: str,
        chat_id: int,
        message_id: int | None,
        notify: bool,
    ) -> TaskRun:
        task = self.store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"unknown task {task_id}")
        task.status = "running"
        self.store.update_task(task)
        try:
            result = self.adapter.run(project.repo_path, prompt, mode)
            task = self._task_from_result(task, result)
        except Exception as exc:
            task.status = "failed"
            task.ended_at = time.time()
            task.summary = f"Execution failed: {exc}"
            task.error = str(exc)
            self.store.update_task(task)
            self._clear_chat_task(chat_id, task_id)
            if notify:
                self._notify(
                    chat_id,
                    render_task_notification(task, task.summary),
                    message_id,
                    dedupe_key=f"task:{task_id}:final",
                )
            self.logger.exception("task failed task_id=%s", task_id)
            return task

        self.store.update_task(task)
        self._clear_chat_task(chat_id, task_id)
        if notify:
            final_output = result.stdout if result.ok else (result.stderr or result.stdout)
            self._notify(
                chat_id,
                render_task_notification(task, final_output),
                message_id,
                dedupe_key=f"task:{task_id}:final",
            )
        return task

    def _task_from_result(self, task: TaskRun, result: RunnerResult) -> TaskRun:
        task.status = "done" if result.ok else "failed"
        task.return_code = result.return_code
        task.ended_at = result.ended_at
        task.summary = self._summarize_result(result)
        task.stdout_tail = result.stdout.strip()[-500:]
        task.stderr_tail = result.stderr.strip()[-500:]
        return task

    def _summarize_result(self, result: RunnerResult) -> str:
        stdout_tail = result.stdout.strip()[-400:]
        stderr_tail = result.stderr.strip()[-400:]
        if result.ok:
            return stdout_tail or "Completed successfully."
        return stderr_tail or stdout_tail or "Codex exited with an error."

    def _clear_chat_task(self, chat_id: int, task_id: str) -> None:
        chat = self.store.get_chat(chat_id)
        if chat is None or chat.current_task_id != task_id:
            return
        self.store.set_chat_task(chat_id, None, last_active_at=time.time())

    def _notify(
        self,
        chat_id: int,
        text: str,
        message_id: int | None,
        *,
        dedupe_key: str | None = None,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        disable_notification: bool = False,
    ) -> None:
        self._send_message(
            chat_id,
            text,
            reply_to_message_id=message_id,
            inline_keyboard=inline_keyboard,
            disable_notification=disable_notification,
            dedupe_key=dedupe_key,
        )

    def _send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        disable_notification: bool = False,
        dedupe_key: str | None = None,
    ) -> None:
        if self.telegram is None:
            return
        sender_thread = self._sender_thread
        if sender_thread is None or not sender_thread.is_alive():
            self._send_message_now(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                inline_keyboard=inline_keyboard,
                disable_notification=disable_notification,
            )
            return
        try:
            self._runtime_store.enqueue_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                inline_keyboard=inline_keyboard,
                disable_notification=disable_notification,
                dedupe_key=dedupe_key,
            )
        except Exception:
            self.logger.exception("failed to enqueue Telegram response chat_id=%s", chat_id)

    def _send_message_now(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        disable_notification: bool = False,
    ) -> None:
        if self.telegram is None:
            return
        try:
            self.telegram.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                inline_keyboard=inline_keyboard,
                disable_notification=disable_notification,
            )
        except Exception:
            self.logger.exception("failed to send Telegram response chat_id=%s", chat_id)

    def _text_fingerprint(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    def _recent_session_rows(self) -> list[tuple[str, str, float]]:
        db_path = self.config.codex_sessions.state_db_path.expanduser()
        if not db_path.exists():
            return []

        rows: list[tuple[str, str, float]] = []
        snapshots = sorted(load_thread_snapshots(db_path, self.logger), key=lambda item: item.updated_at, reverse=True)
        for snapshot in snapshots:
            project = self.config.project_by_repo_path(snapshot.cwd)
            if project is None:
                project = self.config.project_by_name(Path(snapshot.cwd).name)
            label = project.name if project is not None else (Path(snapshot.cwd).name.strip() or "session")
            title = display_thread_title(snapshot, logger=self.logger)
            rows.append((label, title, snapshot.updated_at))
        return rows

    def _keyboard_for_message(
        self, chat_id: int, message: ParsedMessage
    ) -> list[list[dict[str, str]]] | None:
        if message.kind == "project":
            return self._project_keyboard(chat_id, action="project")
        if message.kind == "new" and not message.argument:
            return self._project_keyboard(chat_id, action="new")
        return None

    def _project_keyboard(self, chat_id: int, action: str = "project") -> list[list[dict[str, str]]]:
        chat = self.store.get_chat(chat_id)
        pinned_name = None if chat is None else chat.pinned_project_name
        keyboard: list[list[dict[str, str]]] = []
        if action == "project":
            latest_label = "• Follow latest" if pinned_name is None else "Follow latest"
            keyboard.append(
                [{"text": self._truncate_button_label(latest_label), "callback_data": f"project:{FOLLOW_LATEST_CALLBACK}"}]
            )
        row: list[dict[str, str]] = []
        for project in self.config.projects:
            label = project.name if project.name != pinned_name else f"• {project.name}"
            row.append({"text": self._truncate_button_label(label), "callback_data": f"{action}:{project.name}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return keyboard

    def _truncate_button_label(self, label: str) -> str:
        if len(label) <= 28:
            return label
        return f"{label[:25]}..."


def configure_logging(log_file: str | Path) -> logging.Logger:
    logger = logging.getLogger("codex_connector")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger
