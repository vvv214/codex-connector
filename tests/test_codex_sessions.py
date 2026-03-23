from __future__ import annotations

import json
import logging
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.codex_sessions import (
    CodexSessionMonitor,
    CodexThreadSnapshot,
    SessionNotification,
    format_notification,
    parse_rollout_line,
)


class Collector:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    def send(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FailingCollector:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, chat_id: int, text: str) -> None:
        self.calls += 1
        raise RuntimeError("transient send failure")


def _write_thread(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    rollout_path: Path,
    cwd: str,
    title: str,
    updated_at: int,
    first_user_message: str | None = None,
) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
    names = ["id", "rollout_path", "created_at", "updated_at", "cwd", "title", "git_branch"]
    values: list[object] = [thread_id, str(rollout_path), updated_at, updated_at, cwd, title, "main"]
    if "first_user_message" in columns:
        names.append("first_user_message")
        values.append(title if first_user_message is None else first_user_message)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO threads ({', '.join(names)}) VALUES ({placeholders})", values)
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
        self.assertIn("🔹 [example-project]", rendered)
        self.assertEqual(len(rendered.splitlines()), 1)
        self.assertLessEqual(len(rendered.splitlines()[0]), 80)

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
        self.assertEqual(notification.body, "")
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
        self.assertIn("🟢 [example-project]", rendered)
        self.assertIn(message.strip(), rendered)

    def test_parse_rollout_line_uses_recent_topic_when_title_is_stale(self) -> None:
        notification = parse_rollout_line(
            json.dumps(
                {
                    "payload": {
                        "type": "agent_message",
                        "message": "Refactor Telegram session titles to use the latest topic summary",
                    }
                }
            ),
            CodexThreadSnapshot(
                thread_id="thread-2",
                rollout_path=Path("/tmp/rollout.jsonl"),
                cwd="/Users/tianhao/Documents/GitHub/example-project",
                title="Long original prompt",
                updated_at=1.0,
                first_user_message="Long original prompt",
            ),
            include_user_messages=False,
        )

        self.assertIsNotNone(notification)
        assert notification is not None
        self.assertEqual(notification.title, "Refactor Telegram session titles to use the latest topic summary")

    def test_parse_rollout_line_prefers_latest_topic_for_agent_messages(self) -> None:
        notification = parse_rollout_line(
            json.dumps({"payload": {"type": "agent_message", "message": "Current work is different now"}}),
            CodexThreadSnapshot(
                thread_id="thread-3",
                rollout_path=Path("/tmp/rollout.jsonl"),
                cwd="/Users/tianhao/Documents/GitHub/example-project",
                title="Renamed session title",
                updated_at=1.0,
                first_user_message="Original first prompt",
            ),
            include_user_messages=False,
        )

        self.assertIsNotNone(notification)
        assert notification is not None
        self.assertEqual(notification.title, "Current work is different now")

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
                agent_update_interval_seconds=0.0,
            )

            monitor.prime()
            _append_jsonl(old_rollout, {"payload": {"type": "agent_message", "message": "Live update"}})

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

            self.assertEqual(len(collector.messages), 3)
            self.assertTrue(any("Live update" in text for _, text in collector.messages))
            self.assertTrue(any("🔵 [fresh-project] Fresh session" in text for _, text in collector.messages))
            completion_text = next(
                text
                for _, text in collector.messages
                if "🟢 [fresh-project] Fresh body" in text
            )
            self.assertEqual(completion_text.strip(), "🟢 [fresh-project] Fresh body")

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
                agent_update_interval_seconds=0.0,
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

            _append_jsonl(rollout, {"payload": {"type": "agent_message", "message": "中文更新 with emoji-like text"}})

            monitor.poll_once()

            self.assertEqual(len(collector.messages), 1)
            self.assertIn("中文更新", collector.messages[0][1])

    def test_monitor_prefers_dynamic_title_from_agent_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state_dynamic_title.sqlite"
            rollout_path = root / "dynamic_title.jsonl"
            rollout_path.write_text("", encoding="utf-8")

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
                    first_user_message TEXT NOT NULL DEFAULT '',
                    git_branch TEXT
                )
                """
            )
            _write_thread(
                conn,
                thread_id="thread-dynamic",
                rollout_path=rollout_path,
                cwd="/Users/tianhao/Documents/GitHub/dynamic-project",
                title="Stale initial title that used to be the first prompt",
                updated_at=1,
                first_user_message="Stale initial title that used to be the first prompt",
            )
            conn.close()

            collector = Collector()
            monitor = CodexSessionMonitor(
                state_db_path=db_path,
                poll_interval_seconds=0.1,
                include_user_messages=False,
                target_chat_ids=lambda: [123],
                send_message=collector.send,
                logger=logging.getLogger("test.codex_sessions.dynamic"),
                agent_update_interval_seconds=0.0,
            )

            monitor.prime()
            _append_jsonl(rollout_path, {"payload": {"type": "agent_message", "message": "First update message"}})
            _append_jsonl(rollout_path, {"payload": {"type": "agent_message", "message": "Second update message"}})
            _append_jsonl(rollout_path, {"payload": {"type": "agent_message", "message": "Final topic summary"}})

            monitor.poll_once()

            self.assertEqual(len(collector.messages), 1)
            self.assertIn("🔹 [dynamic-project] Final topic summary", collector.messages[0][1])

    def test_monitor_throttles_agent_updates_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state_throttle.sqlite"
            rollout_path = root / "throttle.jsonl"
            rollout_path.write_text("", encoding="utf-8")

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
                thread_id="thread-throttle",
                rollout_path=rollout_path,
                cwd="/Users/tianhao/Documents/GitHub/throttle-project",
                title="Throttle session",
                updated_at=1,
            )
            conn.close()

            collector = Collector()
            monitor = CodexSessionMonitor(
                state_db_path=db_path,
                poll_interval_seconds=0.1,
                include_user_messages=False,
                target_chat_ids=lambda: [123],
                send_message=collector.send,
                logger=logging.getLogger("test.codex_sessions.throttle"),
                agent_update_interval_seconds=60.0,
            )

            monitor.prime()
            _append_jsonl(rollout_path, {"payload": {"type": "task_started"}})
            _append_jsonl(rollout_path, {"payload": {"type": "agent_message", "message": "First live update"}})
            _append_jsonl(rollout_path, {"payload": {"type": "agent_message", "message": "Second live update"}})
            _append_jsonl(rollout_path, {"payload": {"type": "task_complete", "last_agent_message": "Final summary"}})

            monitor.poll_once()

            self.assertEqual(len(collector.messages), 2)
            self.assertIn("🔵 [throttle-project] Throttle session", collector.messages[0][1])
            self.assertIn("🟢 [throttle-project] Final summary", collector.messages[1][1])

    def test_monitor_deduplicates_replayed_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state_replay.sqlite"
            rollout_path = root / "replay.jsonl"
            rollout_path.write_text("", encoding="utf-8")

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
                thread_id="thread-replay",
                rollout_path=rollout_path,
                cwd="/Users/tianhao/Documents/GitHub/replay-project",
                title="Replay session",
                updated_at=1,
            )
            conn.close()

            collector = Collector()
            monitor = CodexSessionMonitor(
                state_db_path=db_path,
                poll_interval_seconds=0.1,
                include_user_messages=False,
                target_chat_ids=lambda: [123],
                send_message=collector.send,
                logger=logging.getLogger("test.codex_sessions.replay"),
                agent_update_interval_seconds=0.0,
            )

            monitor.prime()
            _append_jsonl(rollout_path, {"payload": {"type": "task_started"}})
            _append_jsonl(rollout_path, {"payload": {"type": "task_complete", "last_agent_message": "Done once"}})
            _append_jsonl(rollout_path, {"payload": {"type": "task_started"}})
            _append_jsonl(rollout_path, {"payload": {"type": "task_complete", "last_agent_message": "Done once"}})

            monitor.poll_once()

            self.assertEqual(len(collector.messages), 2)
            self.assertIn("🔵 [replay-project] Replay session", collector.messages[0][1])
            self.assertIn("🟢 [replay-project] Done once", collector.messages[1][1])

    def test_monitor_does_not_retry_identical_replay_after_send_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "state_send_error.sqlite"
            rollout_path = root / "send_error.jsonl"
            rollout_path.write_text("", encoding="utf-8")

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
                thread_id="thread-send-error",
                rollout_path=rollout_path,
                cwd="/Users/tianhao/Documents/GitHub/replay-project",
                title="Replay session",
                updated_at=1,
            )
            conn.close()

            collector = FailingCollector()
            monitor = CodexSessionMonitor(
                state_db_path=db_path,
                poll_interval_seconds=0.1,
                include_user_messages=False,
                target_chat_ids=lambda: [123],
                send_message=collector.send,
                logger=logging.getLogger("test.codex_sessions.send_error"),
                agent_update_interval_seconds=0.0,
            )

            monitor.prime()
            _append_jsonl(rollout_path, {"payload": {"type": "task_complete", "last_agent_message": "Done once"}})
            _append_jsonl(rollout_path, {"payload": {"type": "task_complete", "last_agent_message": "Done once"}})

            monitor.poll_once()

            self.assertEqual(collector.calls, 1)

    def test_on_notification_can_suppress_agent_messages_only(self) -> None:
        collector = Collector()
        monitor = CodexSessionMonitor(
            state_db_path=Path("/tmp/unused.sqlite"),
            poll_interval_seconds=0.1,
            include_user_messages=False,
            target_chat_ids=lambda: [123],
            send_message=collector.send,
            on_notification=lambda _chat_id, notification: notification.event_type != "agent_message",
            logger=logging.getLogger("test.codex_sessions.filter"),
            agent_update_interval_seconds=0.0,
        )

        monitor._deliver(
            SessionNotification(
                thread_id="thread-1",
                workspace="example-project",
                title="Live update",
                event_type="agent_message",
            )
        )
        monitor._deliver(
            SessionNotification(
                thread_id="thread-1",
                workspace="example-project",
                title="Done",
                event_type="task_complete",
                body="Final answer",
            )
        )

        self.assertEqual(len(collector.messages), 1)
        self.assertIn("🟢 [example-project] Done", collector.messages[0][1])


if __name__ == "__main__":
    unittest.main()
