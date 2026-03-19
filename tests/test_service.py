from __future__ import annotations

import sqlite3
import tempfile
import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.codex_adapter import CodexResult
from codex_connector.config import load_config
from codex_connector.models import ChatState, TaskRun
from codex_connector.service import BridgeService, configure_logging
from codex_connector.state import StateStore
from codex_connector.telegram import TelegramUpdate


class FakeAdapter:
    def __init__(self, binary: str = "codex", timeout_seconds: int = 0):
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    def run(self, repo_path: str, prompt: str, mode: str) -> CodexResult:
        return CodexResult(
            ok=True,
            return_code=0,
            stdout=f"repo={repo_path} prompt={prompt} mode={mode}",
            stderr="",
            started_at=10.0,
            ended_at=11.0,
        )


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.answered_callbacks: list[str] = []

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> None:
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "inline_keyboard": inline_keyboard,
            }
        )

    def answer_callback_query(self, callback_query_id: str) -> None:
        self.answered_callbacks.append(callback_query_id)


class ServiceTests(unittest.TestCase):
    def _write_thread(
        self,
        conn: sqlite3.Connection,
        *,
        thread_id: str,
        rollout_path: Path,
        cwd: str,
        title: str,
        updated_at: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO threads (id, rollout_path, created_at, updated_at, cwd, title, git_branch)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (thread_id, str(rollout_path), updated_at, updated_at, cwd, title, "main"),
        )
        conn.commit()

    def test_rejects_overlapping_task_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repo").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [{"name": "alpha", "repo_path": "./repo"}]
                }
                """.strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            store.set_chat(ChatState(chat_id=7, project_name="alpha", repo_path=str((root / "repo").resolve()), last_active_at=1.0, current_task_id="task-1"))
            store.add_task(
                TaskRun(
                    task_id="task-1",
                    chat_id=7,
                    project_name="alpha",
                    prompt="old",
                    mode="continue",
                    status="running",
                    started_at=1.0,
                )
            )
            service = BridgeService(config=config, store=store, adapter=FakeAdapter())

            message = service.submit_task(7, "new request", "continue")

            self.assertIn("already running", message)
            self.assertIsNotNone(store.running_task_for_chat(7))

    def test_run_task_sync_persists_chat_binding_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repo").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [{"name": "alpha", "repo_path": "./repo"}]
                }
                """.strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter())

            task = service.run_task_sync(42, "do the thing", "new", project_name="alpha")

            self.assertEqual(task.status, "done")
            self.assertEqual(task.chat_id, 42)
            self.assertIsNone(store.get_chat(42).current_task_id)
            self.assertEqual(store.get_chat(42).project_name, "alpha")
            self.assertEqual(store.last_task_for_project("alpha").task_id, task.task_id)

    def test_session_notification_updates_active_project_for_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repo-a").mkdir()
            (root / "repo-b").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [
                    {"name": "alpha", "repo_path": "./repo-a"},
                    {"name": "beta", "repo_path": "./repo-b"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter())

            from codex_connector.codex_sessions import SessionNotification

            service._record_session_notification(
                42,
                SessionNotification(
                    thread_id="thread-1",
                    workspace="beta",
                    title="Recent beta work",
                    event_type="agent_message",
                    repo_path=str((root / "repo-b").resolve()),
                ),
            )

            task = service.run_task_sync(42, "follow up", "continue")

            self.assertEqual(task.project_name, "beta")
            self.assertEqual(store.get_chat(42).project_name, "beta")
            self.assertEqual(store.get_chat(42).active_project_name, "beta")

    def test_project_command_lists_recent_sessions_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()
            db_path = root / "state_5.sqlite"
            config_path = root / "config.json"
            config_path.write_text(
                f"""
                {{
                  "projects": [
                    {{"name": "alpha", "repo_path": "./repo-a"}},
                    {{"name": "beta", "repo_path": "./repo-b"}}
                  ],
                  "codex_sessions": {{
                    "state_db_path": "{db_path}"
                  }}
                }}
                """.strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            store.upsert_chat(
                7,
                project_name="alpha",
                repo_path=str(repo_a.resolve()),
                last_active_at=1.0,
                active_project_name="alpha",
            )
            service = BridgeService(config=config, store=store, adapter=FakeAdapter())

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
            self._write_thread(
                conn,
                thread_id="alpha-thread",
                rollout_path=root / "alpha.jsonl",
                cwd=str(repo_a.resolve()),
                title="Older alpha session",
                updated_at=1,
            )
            self._write_thread(
                conn,
                thread_id="beta-thread",
                rollout_path=root / "beta.jsonl",
                cwd=str(repo_b.resolve()),
                title="Newest beta session",
                updated_at=2,
            )
            conn.close()

            text = service.handle_message(7, "/project")

            self.assertIsNotNone(text)
            assert text is not None
            self.assertIn("Active project: alpha", text)
            self.assertIn("Recent sessions:", text)
            self.assertLess(text.index("Newest beta session"), text.index("Older alpha session"))

    def test_project_switch_returns_recent_session_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()
            db_path = root / "state_5.sqlite"
            config_path = root / "config.json"
            config_path.write_text(
                f"""
                {{
                  "projects": [
                    {{"name": "alpha", "repo_path": "./repo-a"}},
                    {{"name": "beta", "repo_path": "./repo-b"}}
                  ],
                  "codex_sessions": {{
                    "state_db_path": "{db_path}"
                  }}
                }}
                """.strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter())

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
            self._write_thread(
                conn,
                thread_id="beta-thread",
                rollout_path=root / "beta.jsonl",
                cwd=str(repo_b.resolve()),
                title="Newest beta session",
                updated_at=2,
            )
            conn.close()

            text = service.handle_message(7, "/project beta")

            self.assertIsNotNone(text)
            assert text is not None
            self.assertIn("Active project set to beta", text)
            self.assertIn("Newest beta session", text)
            self.assertEqual(store.get_chat(7).project_name, "beta")
            self.assertEqual(store.get_chat(7).active_project_name, "beta")

    def test_project_command_sends_inline_keyboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [
                    {"name": "alpha", "repo_path": "./repo-a"},
                    {"name": "beta", "repo_path": "./repo-b"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            (root / "repo-a").mkdir()
            (root / "repo-b").mkdir()
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            telegram = FakeTelegram()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter(), telegram=telegram)

            service.handle_telegram_update(TelegramUpdate(update_id=1, chat_id=7, text="/project", message_id=11))

            self.assertEqual(len(telegram.sent), 1)
            self.assertIn("Active project:", str(telegram.sent[0]["text"]))
            keyboard = telegram.sent[0]["inline_keyboard"]
            self.assertIsNotNone(keyboard)
            assert keyboard is not None
            flat = [button["callback_data"] for row in keyboard for button in row]
            self.assertIn("project:alpha", flat)
            self.assertIn("project:beta", flat)

    def test_new_without_prompt_sends_project_picker_and_arms_pending_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [
                    {"name": "alpha", "repo_path": "./repo-a"},
                    {"name": "beta", "repo_path": "./repo-b"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            (root / "repo-a").mkdir()
            (root / "repo-b").mkdir()
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            telegram = FakeTelegram()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter(), telegram=telegram)

            service.handle_telegram_update(TelegramUpdate(update_id=1, chat_id=7, text="/new", message_id=11))

            self.assertEqual(len(telegram.sent), 1)
            self.assertIn("New task mode armed", str(telegram.sent[0]["text"]))
            keyboard = telegram.sent[0]["inline_keyboard"]
            self.assertIsNotNone(keyboard)
            assert keyboard is not None
            flat = [button["callback_data"] for row in keyboard for button in row]
            self.assertIn("new:alpha", flat)
            self.assertIn("new:beta", flat)
            self.assertEqual(store.get_chat(7).pending_mode, "new")

    def test_plain_text_after_new_picker_runs_new_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [{"name": "alpha", "repo_path": "./repo-a"}]
                }
                """.strip(),
                encoding="utf-8",
            )
            (root / "repo-a").mkdir()
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter())

            picker = service.handle_message(7, "/new")
            queued = service.handle_message(7, "start fresh")

            self.assertIsNotNone(picker)
            self.assertIn("Queued new task", str(queued))
            task = store.last_task_for_project("alpha")
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task.mode, "new")
            self.assertIsNone(store.get_chat(7).pending_mode)

    def test_project_callback_switches_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [
                    {"name": "alpha", "repo_path": "./repo-a"},
                    {"name": "beta", "repo_path": "./repo-b"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            (root / "repo-a").mkdir()
            (root / "repo-b").mkdir()
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            telegram = FakeTelegram()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter(), telegram=telegram)

            service.handle_telegram_update(
                TelegramUpdate(
                    update_id=2,
                    chat_id=7,
                    text="project:beta",
                    message_id=12,
                    kind="callback",
                    callback_query_id="cb-1",
                )
            )

            self.assertEqual(store.get_chat(7).active_project_name, "beta")
            self.assertEqual(telegram.answered_callbacks, ["cb-1"])
            self.assertEqual(len(telegram.sent), 1)
            self.assertIn("Active project set to beta", str(telegram.sent[0]["text"]))

    def test_new_callback_sets_project_and_keeps_new_picker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                """
                {
                  "projects": [
                    {"name": "alpha", "repo_path": "./repo-a"},
                    {"name": "beta", "repo_path": "./repo-b"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            (root / "repo-a").mkdir()
            (root / "repo-b").mkdir()
            config = load_config(config_path)
            store = StateStore(root / "state.json")
            store.load()
            telegram = FakeTelegram()
            service = BridgeService(config=config, store=store, adapter=FakeAdapter(), telegram=telegram)

            service.handle_telegram_update(
                TelegramUpdate(
                    update_id=2,
                    chat_id=7,
                    text="new:beta",
                    message_id=12,
                    kind="callback",
                    callback_query_id="cb-2",
                )
            )

            self.assertEqual(store.get_chat(7).active_project_name, "beta")
            self.assertEqual(store.get_chat(7).pending_mode, "new")
            self.assertEqual(telegram.answered_callbacks, ["cb-2"])
            self.assertEqual(len(telegram.sent), 1)
            self.assertIn("New task target set to beta", str(telegram.sent[0]["text"]))
            keyboard = telegram.sent[0]["inline_keyboard"]
            self.assertIsNotNone(keyboard)
            assert keyboard is not None
            flat = [button["callback_data"] for row in keyboard for button in row]
            self.assertIn("new:alpha", flat)
            self.assertIn("new:beta", flat)

    def test_configure_logging_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_path = root / "logs" / "bridge.log"

            logger = configure_logging(log_path)
            logger.info("hello")
            for handler in logger.handlers:
                flush = getattr(handler, "flush", None)
                if callable(flush):
                    flush()

            self.assertTrue(log_path.parent.exists())
            self.assertTrue(log_path.exists())


if __name__ == "__main__":
    unittest.main()
