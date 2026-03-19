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


def _session_label(event_type: str) -> str:
    labels = {
        "task_started": "started",
        "task_complete": "completed",
        "agent_message": "update",
        "user_message": "user",
    }
    return labels.get(event_type, event_type.replace("_", " "))


def _compact(text: str) -> str:
    return " ".join((text or "").split())


def _short_title(text: str, fallback: str) -> str:
    compact = _compact(text)
    if not compact:
        compact = fallback
    return _truncate(compact, 28)


def format_notification(notification: SessionNotification) -> str:
    title = _short_title(notification.title, notification.thread_id[:8])
    header = f"[{notification.workspace}] {title} · {_session_label(notification.event_type)}"
    if notification.event_type == "agent_message":
        body = _truncate(_compact(notification.body), 160)
    elif notification.event_type == "task_started":
        body = ""
    else:
        body = notification.body.strip()
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
        rows = conn.execute(
            """
            SELECT id, rollout_path, cwd, title, updated_at
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
    if event_type == "user_message":
        if not include_user_messages:
            return None
        body = str(event.get("message") or "").strip()
    elif event_type == "agent_message":
        body = str(event.get("message") or "").strip()
    elif event_type == "task_started":
        body = "Codex session started."
    elif event_type == "task_complete":
        body = str(event.get("last_agent_message") or "").strip()
        if last_agent_body and body and body == last_agent_body.strip():
            body = ""
        if body:
            body = f"Completed.\n{body}"
        else:
            body = "Completed."
    else:
        return None

    return SessionNotification(
        thread_id=thread.thread_id,
        workspace=_workspace_name(thread.cwd),
        title=thread.title.strip() or thread.thread_id[:8],
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
    ):
        self.state_db_path = Path(state_db_path).expanduser()
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.include_user_messages = include_user_messages
        self._target_chat_ids = target_chat_ids
        self._send_message = send_message
        self._on_notification = on_notification
        self._logger = logger
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._primed = False
        self._offsets: dict[str, int | None] = {}

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
                        self._emit(notification)
                        notifications += 1
            except Exception:
                self._logger.exception(
                    "failed to read rollout file for thread_id=%s path=%s",
                    thread.thread_id,
                    thread.rollout_path,
                )
            return notifications

    def _emit(self, notification: SessionNotification) -> None:
        text = format_notification(notification)
        chat_ids = list(dict.fromkeys(self._target_chat_ids()))
        for chat_id in chat_ids:
            try:
                if self._on_notification is not None:
                    self._on_notification(chat_id, notification)
                self._send_message(chat_id, text)
            except Exception:
                self._logger.exception(
                    "failed to send session notification chat_id=%s thread_id=%s",
                    chat_id,
                    notification.thread_id,
                )

    def _file_size(self, path: Path) -> int | None:
        try:
            return path.stat().st_size
        except FileNotFoundError:
            self._logger.warning("rollout file missing: %s", path)
            return None
        except Exception:
            self._logger.exception("failed to stat rollout file: %s", path)
            return None
