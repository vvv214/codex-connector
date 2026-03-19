from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .codex_adapter import CodexAdapter, CodexResult
from .commands import ParsedMessage, parse_message
from .config import AppConfig
from .codex_sessions import CodexSessionMonitor, SessionNotification, load_thread_snapshots
from .models import ChatState, Project, TaskRun
from .rendering import (
    render_help_text,
    render_last_task,
    render_new_task_picker,
    render_project_sessions,
    render_status,
    render_task_result,
)
from .state import StateStore
from .telegram import TelegramBotClient, TelegramUpdate


class BridgeService:
    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        adapter: CodexAdapter,
        telegram: TelegramBotClient | None = None,
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self.store = store
        self.adapter = adapter
        self.telegram = telegram
        self.logger = logger or logging.getLogger("codex_connector")
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="codex-connector")
        self._session_monitor: CodexSessionMonitor | None = None

    def close(self) -> None:
        if self._session_monitor is not None:
            self._session_monitor.close()
        self._executor.shutdown(wait=False, cancel_futures=False)

    def serve(self) -> None:
        if self.telegram is None:
            raise RuntimeError("telegram client is required for serve mode")
        if not self.config.telegram_bot_token:
            raise RuntimeError("telegram_bot_token is missing from config")
        monitor = self._ensure_session_monitor()
        if monitor is not None:
            monitor.start()
        offset: int | None = None
        self.logger.info("starting Telegram polling")
        try:
            while True:
                try:
                    updates = self.telegram.get_updates(offset=offset, timeout=self.config.poll_timeout_seconds)
                except Exception:
                    self.logger.exception("failed to fetch Telegram updates")
                    time.sleep(self.config.poll_sleep_seconds)
                    continue
                for update in updates:
                    offset = max(offset or 0, update.update_id + 1)
                    try:
                        self.handle_telegram_update(update)
                    except Exception as exc:
                        self.logger.exception("failed to process Telegram update update_id=%s", update.update_id)
                        self._notify(update.chat_id, f"Error: {exc}", update.message_id)
        finally:
            if monitor is not None:
                monitor.close()

    def handle_telegram_update(self, update: TelegramUpdate) -> None:
        if self.config.allowed_chat_ids and update.chat_id not in self.config.allowed_chat_ids:
            self.logger.warning("rejected unauthorized chat_id=%s", update.chat_id)
            return
        if update.kind == "callback":
            self._handle_callback_update(update)
            return

        message = parse_message(update.text)
        response = self._handle_parsed_message(update.chat_id, message, update.message_id)
        if response and self.telegram is not None:
            inline_keyboard = self._keyboard_for_message(update.chat_id, message)
            self.telegram.send_message(
                update.chat_id,
                response,
                reply_to_message_id=update.message_id,
                inline_keyboard=inline_keyboard,
            )

    def handle_message(self, chat_id: int, text: str, message_id: int | None = None) -> str | None:
        message = parse_message(text)
        return self._handle_parsed_message(chat_id, message, message_id)

    def _handle_parsed_message(
        self,
        chat_id: int,
        message: ParsedMessage,
        message_id: int | None = None,
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
            response = self.submit_task(chat_id, argument, mode, message_id=message_id)
            if response.startswith("Queued "):
                self._set_pending_mode(chat_id, None)
            return response
        if kind == "unknown":
            return "Unknown command. Send /help for usage."
        response = self.submit_task(chat_id, argument, "continue", message_id=message_id)
        if response.startswith("Queued "):
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
            self._remember_project(chat_id, project)
            return project

        chat = self.store.get_chat(chat_id)
        if chat is not None:
            active_name = chat.active_project_name or chat.project_name
            project = self.config.project_by_name(active_name)
            if project is not None:
                return project
        project = self.config.default_project()
        if project is None:
            raise RuntimeError("No project is configured")
        self._remember_project(chat_id, project)
        return project

    def _remember_project(self, chat_id: int, project: Project) -> None:
        self.store.upsert_chat(
            chat_id,
            project_name=project.name,
            repo_path=project.repo_path,
            last_active_at=time.time(),
            active_project_name=project.name,
        )

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
        project = self.config.project_by_name(project_name)
        if project is None:
            return self.render_project_sessions(chat_id, prefix=f"Unknown project: {project_name}")
        self._remember_project(chat_id, project)
        return self.render_project_sessions(chat_id, prefix=f"Active project set to {project.name}")

    def prepare_new_task(self, chat_id: int, project_name: str | None = None, prefix: str | None = None) -> str:
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
        project = self.config.project_by_name(chat.project_name)
        running_task = self.store.running_task_for_chat(chat_id)
        text = render_status(chat, project, running_task)
        if running_task is None:
            last_task = self.store.last_task_for_project(chat.project_name)
            if last_task is not None:
                text += f"\nLast task: {last_task.status} ({last_task.mode}) {last_task.task_id}"
        return text

    def render_last(self, chat_id: int) -> str:
        chat = self._ensure_chat_state(chat_id)
        return render_last_task(self.store.last_task_for_project(chat.project_name))

    def render_project_sessions(self, chat_id: int, prefix: str | None = None) -> str:
        chat = self.store.get_chat(chat_id)
        active_project = None
        if chat is not None:
            active_name = chat.active_project_name or chat.project_name
            active_project = self.config.project_by_name(active_name)
        if active_project is None:
            active_project = self.config.default_project()
        return render_project_sessions(
            active_project.name if active_project is not None else None,
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
        if self.telegram is None:
            return
        self.telegram.send_message(chat_id, text)

    def _handle_callback_update(self, update: TelegramUpdate) -> None:
        if self.telegram is not None and update.callback_query_id:
            self.telegram.answer_callback_query(update.callback_query_id)
        action, _, project_name = update.text.partition(":")
        project_name = project_name.strip()
        if action not in {"project", "new"} or not project_name:
            return
        if action == "project":
            response = self.switch_project(update.chat_id, project_name)
            inline_keyboard = self._project_keyboard(update.chat_id, action="project")
        else:
            response = self.prepare_new_task(
                update.chat_id,
                project_name=project_name,
                prefix=f"New task target set to {project_name}. Send the prompt for a fresh session.",
            )
            inline_keyboard = self._project_keyboard(update.chat_id, action="new")
        if response and self.telegram is not None:
            self.telegram.send_message(
                update.chat_id,
                response,
                reply_to_message_id=update.message_id,
                inline_keyboard=inline_keyboard,
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
        self._remember_project(chat_id, project)

    def submit_task(self, chat_id: int, prompt: str, mode: str, message_id: int | None = None) -> str:
        prompt = prompt.strip()
        if not prompt:
            return f"Usage: /{mode} <prompt>"
        if self.store.running_task_for_chat(chat_id) is not None:
            return "A task is already running for this chat. Wait for it to finish before starting another."

        project = self._resolve_project(chat_id)
        self._validate_project_runtime(project)
        task = self._enqueue_task(chat_id, project, prompt, mode)
        self._executor.submit(self._execute_task, task.task_id, project, prompt, mode, chat_id, message_id, True)
        return f"Queued {mode} task {task.task_id} for {project.name}"

    def run_task_sync(self, chat_id: int, prompt: str, mode: str, project_name: str | None = None) -> TaskRun:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        if self.store.running_task_for_chat(chat_id) is not None:
            raise RuntimeError("A task is already running for this chat.")
        project = self._resolve_project(chat_id, project_name)
        self._validate_project_runtime(project)
        task = self._enqueue_task(chat_id, project, prompt, mode)
        return self._execute_task(task.task_id, project, prompt, mode, chat_id, message_id=None, notify=False)

    def _enqueue_task(self, chat_id: int, project: Project, prompt: str, mode: str) -> TaskRun:
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
                self._notify(chat_id, render_task_result(task, self.config.max_output_chars), message_id)
            self.logger.exception("task failed task_id=%s", task_id)
            return task

        self.store.update_task(task)
        self._clear_chat_task(chat_id, task_id)
        if notify:
            self._notify(chat_id, render_task_result(task, self.config.max_output_chars), message_id)
        return task

    def _task_from_result(self, task: TaskRun, result: CodexResult) -> TaskRun:
        task.status = "done" if result.ok else "failed"
        task.return_code = result.return_code
        task.ended_at = result.ended_at
        task.summary = self._summarize_result(result)
        task.stdout_tail = result.stdout.strip()[-500:]
        task.stderr_tail = result.stderr.strip()[-500:]
        return task

    def _summarize_result(self, result: CodexResult) -> str:
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

    def _notify(self, chat_id: int, text: str, message_id: int | None) -> None:
        if self.telegram is None:
            return
        try:
            self.telegram.send_message(chat_id, text, reply_to_message_id=message_id)
        except Exception:
            self.logger.exception("failed to send Telegram response chat_id=%s", chat_id)

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
            title = snapshot.title.strip() or snapshot.thread_id[:8]
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
        active_name = None if chat is None else (chat.active_project_name or chat.project_name)
        keyboard: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for project in self.config.projects:
            label = project.name if project.name != active_name else f"• {project.name}"
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
