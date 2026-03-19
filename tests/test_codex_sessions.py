from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.codex_sessions import (
    CodexSessionMonitor,
    CodexThreadSnapshot,
    format_notification,
    parse_rollout_line,
)


class Collector:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    def send(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


def _write_thread(conn: sqlite3.Connection, *, thread_id: str, rollout_path: Path, cwd: str, title: str, updated_at: int) -> None:
    conn.execute(
        """
        INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, git_branch)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (thread_id, str(rollout_path), updated_at, updated_at, cwd, title, "main"),
    )
    conn.commit()


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload))
        handle.write("\n")


class CodexSessionTests(unittest.TestCase):
    def test_format_notification_shortens_title_and_update_body(self) -> None:
        notification = parse_rollout_line(
            json.dumps(
                {
                    "payload": {
                        "type": "agent_message",
                        "message": "word " * 80,
                    }
                }
            ),
            CodexThreadSnapshot(
                thread_id="thread-12345678",
                rollout_path=Path("/tmp/rollout.jsonl"),
                cwd="/Users/tianhao/Documents/GitHub/example-project",
                title="This is a very long first prompt that should not become a giant Telegram title",
                updated_at=1.0,
            ),
            include_user_messages=False,
        )

        self.assertIsNotNone(notification)
        assert notification is not None
        rendered = format_notification(notification)
        self.assertIn("[example-project]", rendered)
        self.assertIn("update", rendered)
        self.assertLessEqual(len(rendered.splitlines()[0]), 80)
        self.assertLess(len(rendered.splitlines()[-1]), 200)

    def test_parse_rollout_line_dedupes_completed_body(self) -> None:
        thread = CodexThreadSnapshot(
            thread_id="thread-1",
            rollout_path=Path("/tmp/rollout.jsonl"),
            cwd="/Users/tianhao/Documents/GitHub/example-project",
            title="Fix the bug",
            updated_at=1.0,
        )

        notification = parse_rollout_line(
            json.dumps({"payload": {"type": "task_complete", "last_agent_message": "Final body"}}),
            thread,
            include_user_messages=False,
            last_agent_body="Final body",
        )

        self.assertIsNotNone(notification)
        assert notification is not None
        self.assertEqual(notification.event_type, "task_complete")
        self.assertEqual(notification.workspace, "example-project")
        self.assertIn("Completed.", notification.body)
        self.assertNotIn("Final body", format_notification(notification))

    def test_completed_notification_keeps_full_body(self) -> None:
        thread = CodexThreadSnapshot(
            thread_id="thread-1",
            rollout_path=Path("/tmp/rollout.jsonl"),
            cwd="/Users/tianhao/Documents/GitHub/example-project",
            title="Wrap up",
            updated_at=1.0,
        )
        message = "Final answer. " * 120

        notification = parse_rollout_line(
            json.dumps({"payload": {"type": "task_complete", "last_agent_message": message}}),
            thread,
            include_user_messages=False,
        )

        self.assertIsNotNone(notification)
        assert notification is not None
        rendered = format_notification(notification)
        self.assertIn("Completed.", rendered)
        self.assertIn(message.strip(), rendered)

    def test_monitor_skips_existing_history_and_reads_new_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state_5.sqlite"
            old_rollout = root / "old.jsonl"
            new_rollout = root / "new.jsonl"
            old_rollout.write_text(
                "\n".join(
                    [
                        json.dumps({"payload": {"type": "task_started"}}),
                        json.dumps({"payload": {"type": "agent_message", "message": "Historical body"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            new_rollout.write_text(
                "\n".join(
                    [
                        json.dumps({"payload": {"type": "task_started"}}),
                        json.dumps({"payload": {"type": "agent_message", "message": "Fresh body"}}),
                        json.dumps({"payload": {"type": "task_complete", "last_agent_message": "Fresh body"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    cwd TEXT NOT NULL,
                    title TEXT NOT NULL,
                    git_branch TEXT
                )
                """
            )
            _write_thread(
                conn,
                thread_id="old-thread",
                rollout_path=old_rollout,
                cwd="/Users/tianhao/Documents/GitHub/example-project",
                title="Existing session",
                updated_at=1,
            )
            conn.close()

            collector = Collector()
            monitor = CodexSessionMonitor(
                state_db_path=db_path,
                poll_interval_seconds=0.1,
                include_user_messages=False,
                target_chat_ids=lambda: [390429375],
                send_message=collector.send,
                logger=logging.getLogger("test.codex_sessions"),
            )

            monitor.prime()
            _append_jsonl(
                old_rollout,
                {"payload": {"type": "agent_message", "message": "Live update"}},
            )

            conn = sqlite3.connect(db_path)
            _write_thread(
                conn,
                thread_id="new-thread",
                rollout_path=new_rollout,
                cwd="/Users/tianhao/Documents/GitHub/fresh-project",
                title="Fresh session",
                updated_at=2,
            )
            conn.close()

            monitor.poll_once()

            self.assertEqual(len(collector.messages), 4)
            self.assertTrue(any("Live update" in text for _, text in collector.messages))
            self.assertTrue(any("[fresh-project] Fresh session" in text for _, text in collector.messages))
            self.assertTrue(any("started" in text.lower() for _, text in collector.messages))
            completion_text = next(
                text
                for _, text in collector.messages
                if "Fresh session" in text and "completed" in text.lower()
            )
            self.assertIn("Completed.", completion_text)
            self.assertNotIn("Fresh body", completion_text)

    def test_monitor_handles_non_ascii_rollout_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state_5.sqlite"
            rollout = root / "unicode.jsonl"
            rollout.write_text("", encoding="utf-8")

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    cwd TEXT NOT NULL,
                    title TEXT NOT NULL,
                    git_branch TEXT
                )
                """
            )
            conn.commit()
            conn.close()

            collector = Collector()
            monitor = CodexSessionMonitor(
                state_db_path=db_path,
                poll_interval_seconds=0.1,
                include_user_messages=False,
                target_chat_ids=lambda: [390429375],
                send_message=collector.send,
                logger=logging.getLogger("test.codex_sessions"),
            )
            monitor.prime()

            conn = sqlite3.connect(db_path)
            _write_thread(
                conn,
                thread_id="unicode-thread",
                rollout_path=rollout,
                cwd="/Users/tianhao/Documents/GitHub/unicode-project",
                title="Unicode session",
                updated_at=2,
            )
            conn.close()

            _append_jsonl(
                rollout,
                {"payload": {"type": "agent_message", "message": "中文更新 with emoji-like text"}},
            )

            monitor.poll_once()

            self.assertEqual(len(collector.messages), 1)
            self.assertIn("中文更新", collector.messages[0][1])


if __name__ == "__main__":
    unittest.main()
