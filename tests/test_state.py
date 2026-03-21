from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.models import ChatState, TaskRun
from codex_connector.state import StateStore


class StateStoreTests(unittest.TestCase):
    def test_persists_and_restores_chat_and_task_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = StateStore(path)
            store.load()
            store.set_chat(ChatState(chat_id=1, project_name="alpha", repo_path="/repo", last_active_at=1.5))
            store.add_task(
                TaskRun(
                    task_id="task-1",
                    chat_id=1,
                    project_name="alpha",
                    prompt="hello",
                    mode="continue",
                    status="done",
                    started_at=2.0,
                    ended_at=3.0,
                    return_code=0,
                    summary="ok",
                )
            )

            restored = StateStore(path)
            restored.load()

            chat = restored.get_chat(1)
            self.assertIsNotNone(chat)
            self.assertEqual(chat.project_name, "alpha")
            task = restored.last_task_for_project("alpha")
            self.assertIsNotNone(task)
            self.assertEqual(task.task_id, "task-1")
            self.assertEqual(task.summary, "ok")

    def test_upsert_chat_preserves_current_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = StateStore(path)
            store.load()
            store.set_chat(
                ChatState(
                    chat_id=7,
                    project_name="alpha",
                    repo_path="/repo-a",
                    last_active_at=1.0,
                    current_task_id="task-7",
                )
            )

            store.upsert_chat(
                7,
                project_name="beta",
                repo_path="/repo-b",
                last_active_at=2.0,
                active_project_name="beta",
                pinned_project_name="beta",
            )

            chat = store.get_chat(7)
            self.assertIsNotNone(chat)
            self.assertEqual(chat.project_name, "beta")
            self.assertEqual(chat.repo_path, "/repo-b")
            self.assertEqual(chat.current_task_id, "task-7")
            self.assertEqual(chat.active_project_name, "beta")
            self.assertEqual(chat.pinned_project_name, "beta")

    def test_set_chat_pending_mode_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = StateStore(path)
            store.load()
            store.set_chat(ChatState(chat_id=8, project_name="alpha", repo_path="/repo-a", last_active_at=1.0))

            store.set_chat_pending_mode(8, "new")

            restored = StateStore(path)
            restored.load()
            chat = restored.get_chat(8)
            self.assertIsNotNone(chat)
            self.assertEqual(chat.pending_mode, "new")

    def test_get_recent_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = StateStore(path)
            store.load()

            # Add multiple tasks for different chats and projects
            store.add_task(
                TaskRun(
                    task_id="task-1",
                    chat_id=1,
                    project_name="proj-a",
                    prompt="p1",
                    mode="new",
                    status="done",
                    started_at=1.0,
                )
            )
            store.add_task(
                TaskRun(
                    task_id="task-2",
                    chat_id=1,
                    project_name="proj-b",
                    prompt="p2",
                    mode="new",
                    status="done",
                    started_at=2.0,
                )
            )
            store.add_task(
                TaskRun(
                    task_id="task-3",
                    chat_id=1,
                    project_name="proj-a",
                    prompt="p3",
                    mode="continue",
                    status="done",
                    started_at=3.0,
                )
            )
            store.add_task(
                TaskRun(
                    task_id="task-4",
                    chat_id=2,
                    project_name="proj-c",
                    prompt="p4",
                    mode="new",
                    status="done",
                    started_at=4.0,
                )
            )

            # Test without project filter, limit 2
            sessions_all = store.get_recent_sessions(chat_id=1, limit=2)
            self.assertEqual(len(sessions_all), 2)
            self.assertEqual(sessions_all[0].task_id, "task-3") # Newest
            self.assertEqual(sessions_all[1].task_id, "task-2")

            # Test with project filter "proj-a"
            sessions_proja = store.get_recent_sessions(chat_id=1, project_name="proj-a", limit=5)
            self.assertEqual(len(sessions_proja), 2)
            self.assertEqual(sessions_proja[0].task_id, "task-3")
            self.assertEqual(sessions_proja[1].task_id, "task-1")

            # Test with project filter "proj-b"
            sessions_projb = store.get_recent_sessions(chat_id=1, project_name="proj-b", limit=5)
            self.assertEqual(len(sessions_projb), 1)
            self.assertEqual(sessions_projb[0].task_id, "task-2")

            # Test for a different chat_id
            sessions_chat2 = store.get_recent_sessions(chat_id=2, limit=5)
            self.assertEqual(len(sessions_chat2), 1)
            self.assertEqual(sessions_chat2[0].task_id, "task-4")

            # Test for no sessions
            sessions_none = store.get_recent_sessions(chat_id=99, limit=5)
            self.assertEqual(len(sessions_none), 0)




if __name__ == "__main__":
    unittest.main()
