from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class OutboxMessage:
    id: int
    chat_id: int
    text: str
    reply_to_message_id: int | None = None
    inline_keyboard: list[list[dict[str, str]]] | None = None
    disable_notification: bool = False
    attempts: int = 0
    dedupe_key: str | None = None


class TelegramRuntimeStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _initialize(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS processed_updates (
                        update_id INTEGER PRIMARY KEY,
                        processed_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS telegram_outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dedupe_key TEXT,
                        chat_id INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        reply_to_message_id INTEGER,
                        inline_keyboard_json TEXT,
                        disable_notification INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        available_at REAL NOT NULL,
                        sent_at REAL,
                        last_error TEXT
                    );

                    CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_dedupe
                    ON telegram_outbox(dedupe_key)
                    WHERE dedupe_key IS NOT NULL;

                    CREATE INDEX IF NOT EXISTS idx_outbox_pending
                    ON telegram_outbox(status, available_at, id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def get_next_poll_offset(self) -> int | None:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM runtime_meta WHERE key = 'next_poll_offset'"
                ).fetchone()
                if row is None:
                    return None
                return int(row["value"])
            finally:
                conn.close()

    def set_next_poll_offset(self, offset: int) -> None:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_meta(key, value)
                    VALUES('next_poll_offset', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(int(offset)),),
                )
                conn.commit()
            finally:
                conn.close()

    def is_update_processed(self, update_id: int) -> bool:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM processed_updates WHERE update_id = ?",
                    (int(update_id),),
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def mark_update_processed(self, update_id: int, processed_at: float | None = None) -> None:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO processed_updates(update_id, processed_at)
                    VALUES(?, ?)
                    """,
                    (int(update_id), float(processed_at or time.time())),
                )
                conn.commit()
            finally:
                conn.close()

    def enqueue_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        disable_notification: bool = False,
        dedupe_key: str | None = None,
        available_at: float | None = None,
    ) -> int:
        with self._lock:
            self._initialize()
            now = time.time()
            conn = self._connect()
            try:
                inline_keyboard_json = None
                if inline_keyboard is not None:
                    inline_keyboard_json = json.dumps(
                        inline_keyboard,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO telegram_outbox(
                        dedupe_key,
                        chat_id,
                        text,
                        reply_to_message_id,
                        inline_keyboard_json,
                        disable_notification,
                        status,
                        attempts,
                        created_at,
                        available_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                    """,
                    (
                        dedupe_key,
                        int(chat_id),
                        str(text),
                        reply_to_message_id,
                        inline_keyboard_json,
                        1 if disable_notification else 0,
                        now,
                        float(available_at if available_at is not None else now),
                    ),
                )
                if conn.total_changes == 0 and dedupe_key is not None:
                    row = conn.execute(
                        "SELECT id FROM telegram_outbox WHERE dedupe_key = ?",
                        (dedupe_key,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("failed to retrieve deduplicated outbox row")
                    conn.commit()
                    return int(row["id"])
                row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
                conn.commit()
                return int(row["id"])
            finally:
                conn.close()

    def get_due_messages(self, *, now: float | None = None, limit: int = 20) -> list[OutboxMessage]:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id,
                           chat_id,
                           text,
                           reply_to_message_id,
                           inline_keyboard_json,
                           disable_notification,
                           attempts,
                           dedupe_key
                    FROM telegram_outbox
                    WHERE status = 'pending' AND available_at <= ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (float(now if now is not None else time.time()), int(limit)),
                ).fetchall()
            finally:
                conn.close()

        messages: list[OutboxMessage] = []
        for row in rows:
            inline_keyboard = None
            if row["inline_keyboard_json"]:
                inline_keyboard = json.loads(str(row["inline_keyboard_json"]))
            messages.append(
                OutboxMessage(
                    id=int(row["id"]),
                    chat_id=int(row["chat_id"]),
                    text=str(row["text"]),
                    reply_to_message_id=(
                        int(row["reply_to_message_id"])
                        if row["reply_to_message_id"] is not None
                        else None
                    ),
                    inline_keyboard=inline_keyboard,
                    disable_notification=bool(row["disable_notification"]),
                    attempts=int(row["attempts"]),
                    dedupe_key=str(row["dedupe_key"]) if row["dedupe_key"] is not None else None,
                )
            )
        return messages

    def mark_message_sent(self, message_id: int, *, sent_at: float | None = None) -> None:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE telegram_outbox
                    SET status = 'sent', sent_at = ?, last_error = NULL
                    WHERE id = ?
                    """,
                    (float(sent_at or time.time()), int(message_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_message_retry(
        self,
        message_id: int,
        *,
        error: str,
        delay_seconds: float,
        retry_at: float | None = None,
    ) -> None:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE telegram_outbox
                    SET attempts = attempts + 1,
                        available_at = ?,
                        last_error = ?
                    WHERE id = ?
                    """,
                    (
                        float(retry_at if retry_at is not None else time.time() + max(0.0, delay_seconds)),
                        str(error),
                        int(message_id),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_message_failed(self, message_id: int, *, error: str) -> None:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE telegram_outbox
                    SET status = 'failed',
                        attempts = attempts + 1,
                        last_error = ?
                    WHERE id = ?
                    """,
                    (str(error), int(message_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def pending_message_count(self) -> int:
        with self._lock:
            self._initialize()
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM telegram_outbox WHERE status = 'pending'"
                ).fetchone()
                return int(row["count"] or 0)
            finally:
                conn.close()
