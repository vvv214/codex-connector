from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(slots=True)
class CodexThreadSnapshot:
    thread_id: str
    rollout_path: Path
    cwd: str
    title: str
    updated_at: float
    first_user_message: str = ""


@dataclass(slots=True)
class SessionNotification:
    thread_id: str
    workspace: str
    title: str
    event_type: str
    body: str = ""
    repo_path: str = ""
    updated_at: float = 0.0


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."


def _workspace_name(cwd: str) -> str:
    path = Path(cwd or "")
    name = path.name.strip()
    if name:
        return name
    parent = path.parent.name.strip()
    if parent:
        return parent
    return path.as_posix().strip() or "session"


def _compact(text: str) -> str:
    return " ".join((text or "").split())


def _short_title(text: str, fallback: str, *, limit: int = 28) -> str:
    compact = _compact(text)
    if not compact:
        compact = fallback
    return _truncate(compact, limit)


def _notification_icon(event_type: str) -> str:
    icons = {
        "task_started": "🔵",
        "task_complete": "🟢",
        "agent_message": "🔹",
        "user_message": "⚪",
    }
    return icons.get(event_type, "⚪")


def _preferred_db_title(title: str, first_user_message: str) -> str:
    compact_title = _compact(title)
    if not compact_title:
        return ""
    compact_first = _compact(first_user_message)
    if compact_first and compact_title == compact_first:
        return ""
    return compact_title


def _topic_from_text(text: str) -> str:
    compact = _compact(text)
    if not compact:
        return ""
    return _truncate(compact, 120)


def _recent_rollout_topic(path: Path, logger: logging.Logger | None = None) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(0, size - 16_384)
            handle.seek(start)
            if start:
                handle.readline()
            chunk = handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except Exception:
        if logger is not None:
            logger.exception("failed to read rollout topic from %s", path)
        return ""

    for raw_line in reversed(chunk.splitlines()):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event = payload.get("payload")
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "task_complete":
            topic = _topic_from_text(str(event.get("last_agent_message") or ""))
        elif event_type == "agent_message":
            topic = _topic_from_text(str(event.get("message") or ""))
        else:
            continue
        if topic:
            return topic
    return ""


def display_thread_title(
    thread: CodexThreadSnapshot,
    *,
    topic_source: str | None = None,
    allow_rollout_scan: bool = True,
    prefer_topic: bool = False,
    logger: logging.Logger | None = None,
) -> str:
    topic = _topic_from_text(topic_source or "")
    preferred_title = _preferred_db_title(thread.title, thread.first_user_message)
    if prefer_topic and topic:
        return topic
    if preferred_title:
        return preferred_title

    if topic:
        return topic

    if allow_rollout_scan:
        topic = _recent_rollout_topic(thread.rollout_path, logger)
        if topic:
            return topic

    fallback = _compact(thread.title) or _compact(thread.first_user_message)
    return fallback or thread.thread_id[:8]


def format_notification(notification: SessionNotification) -> str:
    icon = _notification_icon(notification.event_type)
    header_prefix = f"{icon} [{notification.workspace}] "
    title = _short_title(
        notification.title,
        notification.thread_id[:8],
        limit=max(16, 80 - len(header_prefix)),
    )
    header = f"{header_prefix}{title}"
    if notification.event_type in {"agent_message", "task_started"}:
        body = ""
    else:
        body = notification.body.strip()
    if body and _compact(body) == _compact(notification.title):
        body = ""
    if body:
        return f"{header}\n{body}"
    return header


def _open_read_only_sqlite(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def load_thread_snapshots(db_path: Path, logger: logging.Logger) -> list[CodexThreadSnapshot]:
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_read_only_sqlite(db_path)
        conn.row_factory = sqlite3.Row
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
        first_user_message_expr = "first_user_message" if "first_user_message" in columns else "'' AS first_user_message"
        rows = conn.execute(
            f"""
            SELECT id, rollout_path, cwd, title, updated_at, {first_user_message_expr}
            FROM threads
            ORDER BY updated_at ASC
            """
        ).fetchall()
    except Exception:
        logger.exception("failed to load Codex sessions from %s", db_path)
        return []
    finally:
        if conn is not None:
            conn.close()

    snapshots: list[CodexThreadSnapshot] = []
    for row in rows:
        rollout_path = Path(str(row["rollout_path"])).expanduser()
        if not rollout_path.is_absolute():
            rollout_path = db_path.parent / rollout_path
        snapshots.append(
            CodexThreadSnapshot(
                thread_id=str(row["id"]),
                rollout_path=rollout_path.resolve(),
                cwd=str(row["cwd"] or ""),
                title=str(row["title"] or ""),
                updated_at=float(row["updated_at"] or 0.0),
                first_user_message=str(row["first_user_message"] or ""),
            )
        )
    return snapshots


def parse_rollout_line(
    line: str,
    thread: CodexThreadSnapshot,
    include_user_messages: bool,
    last_agent_body: str | None = None,
) -> SessionNotification | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    event = payload.get("payload")
    if not isinstance(event, dict):
        return None

    event_type = str(event.get("type") or "").strip()
    topic_source = ""
    if event_type == "user_message":
        if not include_user_messages:
            return None
        body = str(event.get("message") or "").strip()
        topic_source = body
    elif event_type == "agent_message":
        body = str(event.get("message") or "").strip()
        topic_source = body
    elif event_type == "task_started":
        body = "Codex session started."
    elif event_type == "task_complete":
        topic_source = str(event.get("last_agent_message") or "").strip()
        body = topic_source
        if last_agent_body and body and body == last_agent_body.strip():
            body = ""
            topic_source = ""
    else:
        return None

    return SessionNotification(
        thread_id=thread.thread_id,
        workspace=_workspace_name(thread.cwd),
        title=display_thread_title(
            thread,
            topic_source=topic_source,
            allow_rollout_scan=False,
            prefer_topic=event_type in {"agent_message", "task_complete"},
        ),
        event_type=event_type,
        body=body,
        repo_path=thread.cwd,
        updated_at=thread.updated_at,
    )


class CodexSessionMonitor:
    def __init__(
        self,
        *,
        state_db_path: str | Path,
        poll_interval_seconds: float,
        include_user_messages: bool,
        target_chat_ids: Callable[[], Sequence[int]],
        send_message: Callable[[int, str], None],
        on_notification: Callable[[int, SessionNotification], None] | None = None,
        logger: logging.Logger,
        agent_update_interval_seconds: float = 60.0,
    ):
        self.state_db_path = Path(state_db_path).expanduser()
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.include_user_messages = include_user_messages
        self._target_chat_ids = target_chat_ids
        self._send_message = send_message
        self._on_notification = on_notification
        self._logger = logger
        self._agent_update_interval_seconds = max(0.0, float(agent_update_interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._primed = False
        self._offsets: dict[str, int | None] = {}
        self._pending_agent_updates: dict[str, SessionNotification] = {}
        self._last_agent_notification_at: dict[str, float] = {}
        self._recent_delivery_signatures: dict[tuple[int, str], list[tuple[str, str, str]]] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self.prime()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="codex-session-monitor",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def prime(self) -> None:
        with self._lock:
            if self._primed:
                return
            for thread in load_thread_snapshots(self.state_db_path, self._logger):
                self._offsets[thread.thread_id] = self._file_size(thread.rollout_path)
            self._primed = True

    def poll_once(self) -> int:
        if not self._primed:
            self.prime()

        notifications = 0
        threads = load_thread_snapshots(self.state_db_path, self._logger)
        for thread in threads:
            notifications += self._poll_thread(thread)
        notifications += self._flush_due_agent_updates()
        return notifications

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval_seconds):
            try:
                self.poll_once()
            except Exception:
                self._logger.exception("Codex session monitor loop failed")

    def _poll_thread(self, thread: CodexThreadSnapshot) -> int:
        with self._lock:
            if thread.thread_id not in self._offsets:
                self._offsets[thread.thread_id] = 0

            offset = self._offsets[thread.thread_id]
            if offset is None:
                current_size = self._file_size(thread.rollout_path)
                if current_size is None:
                    return 0
                self._offsets[thread.thread_id] = current_size
                return 0

            current_size = self._file_size(thread.rollout_path)
            if current_size is None:
                return 0
            if current_size < offset:
                offset = 0

            notifications = 0
            last_agent_body: str | None = None
            buffered_agent_notification: SessionNotification | None = None
            try:
                with thread.rollout_path.open("rb") as handle:
                    handle.seek(offset)
                    while True:
                        line = handle.readline()
                        if not line:
                            self._offsets[thread.thread_id] = handle.tell()
                            break
                        try:
                            decoded = line.decode("utf-8")
                        except UnicodeDecodeError:
                            decoded = line.decode("utf-8", errors="replace")
                        notification = parse_rollout_line(
                            decoded,
                            thread,
                            self.include_user_messages,
                            last_agent_body=last_agent_body,
                        )
                        if notification is None:
                            continue
                        if notification.event_type == "agent_message":
                            last_agent_body = notification.body
                            buffered_agent_notification = notification
                            continue

                        if buffered_agent_notification is not None:
                            if notification.event_type == "task_complete":
                                if not notification.body.strip():
                                    latest = _truncate(
                                        _compact(
                                            buffered_agent_notification.body
                                            or buffered_agent_notification.title
                                        ),
                                        160,
                                    )
                                    if latest:
                                        notification.body = latest
                                        notification.title = buffered_agent_notification.title
                                elif not notification.title.strip():
                                    notification.title = buffered_agent_notification.title
                            else:
                                notifications += self._emit(buffered_agent_notification)
                            buffered_agent_notification = None

                        notifications += self._emit(notification)
                    if buffered_agent_notification is not None:
                        notifications += self._emit(buffered_agent_notification)
            except Exception:
                self._logger.exception(
                    "failed to read rollout file for thread_id=%s path=%s",
                    thread.thread_id,
                    thread.rollout_path,
                )
            return notifications

    def _emit(self, notification: SessionNotification) -> int:
        if notification.event_type == "agent_message":
            self._pending_agent_updates[notification.thread_id] = notification
            return self._flush_due_agent_updates(notification.thread_id)
        if notification.event_type == "task_started":
            self._last_agent_notification_at[notification.thread_id] = time.time()
            self._pending_agent_updates.pop(notification.thread_id, None)
        elif notification.event_type == "task_complete":
            pending = self._pending_agent_updates.pop(notification.thread_id, None)
            self._last_agent_notification_at.pop(notification.thread_id, None)
            if pending is not None and not notification.body.strip():
                latest = _truncate(_compact(pending.body or pending.title), 160)
                if latest:
                    notification.body = latest
        self._deliver(notification)
        return 1

    def _flush_due_agent_updates(self, thread_id: str | None = None) -> int:
        now = time.time()
        notifications = 0
        thread_ids = [thread_id] if thread_id is not None else list(self._pending_agent_updates)
        for candidate in thread_ids:
            notification = self._pending_agent_updates.get(candidate)
            if notification is None:
                continue
            last_sent_at = self._last_agent_notification_at.get(candidate)
            if (
                last_sent_at is not None
                and self._agent_update_interval_seconds > 0
                and now - last_sent_at < self._agent_update_interval_seconds
            ):
                continue
            self._pending_agent_updates.pop(candidate, None)
            self._last_agent_notification_at[candidate] = now
            self._deliver(notification)
            notifications += 1
        return notifications

    def _deliver(self, notification: SessionNotification) -> None:
        text = format_notification(notification)
        chat_ids = list(dict.fromkeys(self._target_chat_ids()))
        for chat_id in chat_ids:
            if self._was_recently_delivered(chat_id, notification):
                continue
            try:
                if self._on_notification is not None:
                    self._on_notification(chat_id, notification)
                self._send_message(chat_id, text)
                self._remember_delivery(chat_id, notification)
            except Exception:
                self._logger.exception(
                    "failed to send session notification chat_id=%s thread_id=%s",
                    chat_id,
                    notification.thread_id,
                )

    def _notification_signature(self, notification: SessionNotification) -> tuple[str, str, str]:
        return (
            notification.event_type,
            _compact(notification.title),
            _compact(notification.body),
        )

    def _was_recently_delivered(self, chat_id: int, notification: SessionNotification) -> bool:
        key = (chat_id, notification.thread_id)
        signature = self._notification_signature(notification)
        return signature in self._recent_delivery_signatures.get(key, [])

    def _remember_delivery(self, chat_id: int, notification: SessionNotification) -> None:
        key = (chat_id, notification.thread_id)
        signature = self._notification_signature(notification)
        history = self._recent_delivery_signatures.setdefault(key, [])
        if signature in history:
            return
        history.append(signature)
        if len(history) > 32:
            del history[0]

    def _file_size(self, path: Path) -> int | None:
        try:
            return path.stat().st_size
        except FileNotFoundError:
            self._logger.warning("rollout file missing: %s", path)
            return None
        except Exception:
            self._logger.exception("failed to stat rollout file: %s", path)
            return None
