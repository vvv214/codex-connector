from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.codex_adapter import CodexResult
from codex_connector.cli import main
from codex_connector.models import ChatState, TaskRun
from codex_connector.state import StateStore


class FakeAdapter:
    def __init__(self, binary: str = "codex", timeout_seconds: int = 0):
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    def run(self, repo_path: str, prompt: str, mode: str) -> CodexResult:
        return CodexResult(
            ok=True,
            return_code=0,
            stdout="finished",
            stderr="",
            started_at=1.0,
            ended_at=2.0,
        )


class CliTests(unittest.TestCase):
    def test_serve_accepts_config_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repo").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "telegram": {"bot_token": "token", "allowed_chat_ids": [123]},
                        "projects": [{"name": "alpha", "repo_path": "./repo"}],
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_connector.cli.SingleInstanceLock.acquire", autospec=True) as acquire_mock:
                with patch("codex_connector.cli.SingleInstanceLock.release", autospec=True) as release_mock:
                    with patch("codex_connector.cli.BridgeService.serve", autospec=True) as serve_mock:
                        exit_code = main(["serve", "--config", str(config_path)])

            acquire_mock.assert_called_once()
            release_mock.assert_called_once()

            self.assertEqual(exit_code, 0)
            serve_mock.assert_called_once()

    def test_run_uses_explicit_state_path_and_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repo").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"projects": [{"name": "alpha", "repo_path": "./repo"}]}),
                encoding="utf-8",
            )
            explicit_state = root / "override-state.json"
            buffer = io.StringIO()

            with patch("codex_connector.cli.create_runner", return_value=FakeAdapter()):
                with contextlib.redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "--config",
                            str(config_path),
                            "--state",
                            str(explicit_state),
                            "run",
                            "--chat-id",
                            "99",
                            "--project",
                            "alpha",
                            "hello",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            store = StateStore(explicit_state)
            store.load()
            self.assertTrue(store.db_path.exists())
            chat = store.get_chat(99)
            self.assertIsNotNone(chat)
            assert chat is not None
            self.assertEqual(chat.project_name, "alpha")
            task = store.get_recent_sessions(chat_id=99, limit=1)[0]
            self.assertEqual(task.chat_id, 99)
            self.assertIn("finished", buffer.getvalue())

    def test_status_reads_explicit_state_after_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repo").mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"projects": [{"name": "alpha", "repo_path": "./repo"}]}),
                encoding="utf-8",
            )
            state_path = root / "state.json"
            store = StateStore(state_path)
            store.load()
            store.set_chat(ChatState(chat_id=77, project_name="alpha", repo_path=str((root / "repo").resolve()), last_active_at=1.0))
            store.add_task(
                TaskRun(
                    task_id="task-77",
                    chat_id=77,
                    project_name="alpha",
                    prompt="hello",
                    mode="continue",
                    status="done",
                    started_at=1.0,
                    ended_at=2.0,
                    return_code=0,
                    summary="done",
                )
            )

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = main(
                    [
                        "status",
                        "--config",
                        str(config_path),
                        "--state",
                        str(state_path),
                        "--chat-id",
                        "77",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = buffer.getvalue()
            self.assertIn("Project: alpha", output)
            self.assertIn("task-77", output)


if __name__ == "__main__":
    unittest.main()
