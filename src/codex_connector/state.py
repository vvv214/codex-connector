from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import ChatState, TaskRun

_UNSET = object()
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db_path = self._derive_db_path(self.path)
        self._lock = threading.RLock()
        self._loaded = False

    @staticmethod
    def _derive_db_path(path: Path) -> Path:
        if path.suffix.lower() in _SQLITE_SUFFIXES:
            return path
        return path.with_suffix(".sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    last_active_at REAL NOT NULL,
                    current_task_id TEXT,
                    active_project_name TEXT,
                    pinned_project_name TEXT,
                    pending_mode TEXT
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    project_name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    return_code INTEGER,
                    summary TEXT NOT NULL DEFAULT '',
                    stdout_tail TEXT NOT NULL DEFAULT '',
                    stderr_tail TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    request_key TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_project_started
                ON tasks(project_name, started_at DESC, task_id DESC);

                CREATE INDEX IF NOT EXISTS idx_tasks_chat_started
                ON tasks(chat_id, started_at DESC, task_id DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_request_key
                ON tasks(request_key)
                WHERE request_key IS NOT NULL;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _legacy_json_exists(self) -> bool:
        return self.path != self.db_path and self.path.exists()

    def _has_state(self) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    EXISTS(SELECT 1 FROM chats LIMIT 1) AS has_chats,
                    EXISTS(SELECT 1 FROM tasks LIMIT 1) AS has_tasks
                """
            ).fetchone()
            if row is None:
                return False
            return bool(row["has_chats"]) or bool(row["has_tasks"])
        finally:
            conn.close()

    def _migrate_legacy_json_if_needed(self) -> None:
        if not self._legacy_json_exists() or self._has_state():
            return

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        chats = payload.get("chats", {})
        tasks = payload.get("tasks", [])
        conn = self._connect()
        try:
            for raw in chats.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chats(
                        chat_id,
                        project_name,
                        repo_path,
                        last_active_at,
                        current_task_id,
                        active_project_name,
                        pinned_project_name,
                        pending_mode
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(raw["chat_id"]),
                        str(raw["project_name"]),
                        str(raw["repo_path"]),
                        float(raw["last_active_at"]),
                        raw.get("current_task_id"),
                        raw.get("active_project_name"),
                        raw.get("pinned_project_name"),
                        raw.get("pending_mode"),
                    ),
                )
            for raw in tasks:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tasks(
                        task_id,
                        chat_id,
                        project_name,
                        prompt,
                        mode,
                        status,
                        started_at,
                        ended_at,
                        return_code,
                        summary,
                        stdout_tail,
                        stderr_tail,
                        error,
                        request_key
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(raw["task_id"]),
                        int(raw["chat_id"]),
                        str(raw["project_name"]),
                        str(raw["prompt"]),
                        str(raw["mode"]),
                        str(raw["status"]),
                        float(raw["started_at"]),
                        (float(raw["ended_at"]) if raw.get("ended_at") is not None else None),
                        (int(raw["return_code"]) if raw.get("return_code") is not None else None),
                        str(raw.get("summary", "")),
                        str(raw.get("stdout_tail", "")),
                        str(raw.get("stderr_tail", "")),
                        raw.get("error"),
                        raw.get("request_key"),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.load()

    @staticmethod
    def _row_to_chat(row: sqlite3.Row) -> ChatState:
        return ChatState(
            chat_id=int(row["chat_id"]),
            project_name=str(row["project_name"]),
            repo_path=str(row["repo_path"]),
            last_active_at=float(row["last_active_at"]),
            current_task_id=row["current_task_id"],
            active_project_name=row["active_project_name"],
            pinned_project_name=row["pinned_project_name"],
            pending_mode=row["pending_mode"],
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRun:
        return TaskRun(
            task_id=str(row["task_id"]),
            chat_id=int(row["chat_id"]),
            project_name=str(row["project_name"]),
            prompt=str(row["prompt"]),
            mode=str(row["mode"]),
            status=str(row["status"]),
            started_at=float(row["started_at"]),
            ended_at=(float(row["ended_at"]) if row["ended_at"] is not None else None),
            return_code=(int(row["return_code"]) if row["return_code"] is not None else None),
            summary=str(row["summary"] or ""),
            stdout_tail=str(row["stdout_tail"] or ""),
            stderr_tail=str(row["stderr_tail"] or ""),
            error=row["error"],
            request_key=row["request_key"],
        )

    def _write_chat(self, chat: ChatState) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO chats(
                    chat_id,
                    project_name,
                    repo_path,
                    last_active_at,
                    current_task_id,
                    active_project_name,
                    pinned_project_name,
                    pending_mode
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat.chat_id,
                    chat.project_name,
                    chat.repo_path,
                    chat.last_active_at,
                    chat.current_task_id,
                    chat.active_project_name,
                    chat.pinned_project_name,
                    chat.pending_mode,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_task(self, task: TaskRun) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks(
                    task_id,
                    chat_id,
                    project_name,
                    prompt,
                    mode,
                    status,
                    started_at,
                    ended_at,
                    return_code,
                    summary,
                    stdout_tail,
                    stderr_tail,
                    error,
                    request_key
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.chat_id,
                    task.project_name,
                    task.prompt,
                    task.mode,
                    task.status,
                    task.started_at,
                    task.ended_at,
                    task.return_code,
                    task.summary,
                    task.stdout_tail,
                    task.stderr_tail,
                    task.error,
                    task.request_key,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def load(self) -> None:
        with self._lock:
            self._initialize()
            self._migrate_legacy_json_if_needed()
            self._loaded = True

    def save(self) -> None:
        with self._lock:
            self._ensure_loaded()
            if self.path == self.db_path:
                return
            payload = {
                "chats": {
                    str(chat.chat_id): {
                        "chat_id": chat.chat_id,
                        "project_name": chat.project_name,
                        "repo_path": chat.repo_path,
                        "last_active_at": chat.last_active_at,
                        "current_task_id": chat.current_task_id,
                        "active_project_name": chat.active_project_name,
                        "pinned_project_name": chat.pinned_project_name,
                        "pending_mode": chat.pending_mode,
                    }
                    for chat in self._all_chats()
                },
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "chat_id": task.chat_id,
                        "project_name": task.project_name,
                        "prompt": task.prompt,
                        "mode": task.mode,
                        "status": task.status,
                        "started_at": task.started_at,
                        "ended_at": task.ended_at,
                        "return_code": task.return_code,
                        "summary": task.summary,
                        "stdout_tail": task.stdout_tail,
                        "stderr_tail": task.stderr_tail,
                        "error": task.error,
                        "request_key": task.request_key,
                    }
                    for task in self._all_tasks()
                ],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _all_chats(self) -> list[ChatState]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM chats ORDER BY chat_id ASC").fetchall()
            return [self._row_to_chat(row) for row in rows]
        finally:
            conn.close()

    def _all_tasks(self) -> list[TaskRun]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM tasks ORDER BY started_at ASC, task_id ASC").fetchall()
            return [self._row_to_task(row) for row in rows]
        finally:
            conn.close()

    def get_chat(self, chat_id: int) -> ChatState | None:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (int(chat_id),)).fetchone()
                return None if row is None else self._row_to_chat(row)
            finally:
                conn.close()

    def chat_ids(self) -> list[int]:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                rows = conn.execute("SELECT chat_id FROM chats ORDER BY chat_id ASC").fetchall()
                return [int(row["chat_id"]) for row in rows]
            finally:
                conn.close()

    def set_chat(self, chat: ChatState) -> None:
        with self._lock:
            self._ensure_loaded()
            self._write_chat(chat)

    def upsert_chat(
        self,
        chat_id: int,
        project_name: str | None = None,
        repo_path: str | None = None,
        last_active_at: float | None = None,
        current_task_id: str | None = None,
        active_project_name: str | None = None,
        pinned_project_name: str | None | object = _UNSET,
    ) -> ChatState:
        with self._lock:
            self._ensure_loaded()
            chat = self.get_chat(chat_id)

            if chat is None:
                if project_name is None or repo_path is None or last_active_at is None:
                    raise ValueError(
                        "project_name, repo_path, and last_active_at are required for new chat state"
                    )
                chat = ChatState(
                    chat_id=chat_id,
                    project_name=project_name,
                    repo_path=repo_path,
                    last_active_at=last_active_at,
                    current_task_id=current_task_id,
                    active_project_name=active_project_name,
                    pinned_project_name=(None if pinned_project_name is _UNSET else pinned_project_name),
                )
            else:
                if project_name is not None:
                    chat.project_name = project_name
                if repo_path is not None:
                    chat.repo_path = repo_path
                if last_active_at is not None:
                    chat.last_active_at = last_active_at
                if current_task_id is not None:
                    chat.current_task_id = current_task_id
                if active_project_name is not None:
                    chat.active_project_name = active_project_name
                if pinned_project_name is not _UNSET:
                    chat.pinned_project_name = pinned_project_name

            self._write_chat(chat)
            return chat

    def set_chat_task(self, chat_id: int, task_id: str | None, last_active_at: float | None = None) -> None:
        with self._lock:
            self._ensure_loaded()
            chat = self.get_chat(chat_id)
            if chat is None:
                raise KeyError(f"unknown chat {chat_id}")
            chat.current_task_id = task_id
            if last_active_at is not None:
                chat.last_active_at = last_active_at
            self._write_chat(chat)

    def add_task(self, task: TaskRun) -> None:
        with self._lock:
            self._ensure_loaded()
            self._write_task(task)

    def set_chat_pending_mode(self, chat_id: int, pending_mode: str | None) -> None:
        with self._lock:
            self._ensure_loaded()
            chat = self.get_chat(chat_id)
            if chat is None:
                raise KeyError(f"unknown chat {chat_id}")
            chat.pending_mode = pending_mode
            self._write_chat(chat)

    def update_task(self, task: TaskRun) -> None:
        with self._lock:
            self._ensure_loaded()
            self._write_task(task)

    def get_task(self, task_id: str) -> TaskRun | None:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (str(task_id),)).fetchone()
                return None if row is None else self._row_to_task(row)
            finally:
                conn.close()

    def find_task_by_request_key(self, request_key: str) -> TaskRun | None:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE request_key = ? LIMIT 1",
                    (str(request_key),),
                ).fetchone()
                return None if row is None else self._row_to_task(row)
            finally:
                conn.close()

    def tasks_for_project(self, project_name: str) -> list[TaskRun]:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE project_name = ?
                    ORDER BY started_at ASC, task_id ASC
                    """,
                    (str(project_name),),
                ).fetchall()
                return [self._row_to_task(row) for row in rows]
            finally:
                conn.close()

    def last_task_for_project(self, project_name: str) -> TaskRun | None:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE project_name = ?
                    ORDER BY started_at DESC, task_id DESC
                    LIMIT 1
                    """,
                    (str(project_name),),
                ).fetchone()
                return None if row is None else self._row_to_task(row)
            finally:
                conn.close()

    def running_task_for_chat(self, chat_id: int) -> TaskRun | None:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT t.*
                    FROM chats AS c
                    JOIN tasks AS t ON t.task_id = c.current_task_id
                    WHERE c.chat_id = ? AND t.status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (int(chat_id),),
                ).fetchone()
                return None if row is None else self._row_to_task(row)
            finally:
                conn.close()

    def get_recent_sessions(
        self, chat_id: int, project_name: str | None = None, limit: int = 5
    ) -> list[TaskRun]:
        with self._lock:
            self._ensure_loaded()
            conn = self._connect()
            try:
                if project_name is None:
                    rows = conn.execute(
                        """
                        SELECT * FROM tasks
                        WHERE chat_id = ?
                        ORDER BY started_at DESC, task_id DESC
                        LIMIT ?
                        """,
                        (int(chat_id), int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM tasks
                        WHERE chat_id = ? AND project_name = ?
                        ORDER BY started_at DESC, task_id DESC
                        LIMIT ?
                        """,
                        (int(chat_id), str(project_name), int(limit)),
                    ).fetchall()
                return [self._row_to_task(row) for row in rows]
            finally:
                conn.close()
