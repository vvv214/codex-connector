from __future__ import annotations

import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.codex_adapter import CodexAdapter, CodexResult
from codex_connector.rendering import render_task_result
from codex_connector.models import TaskRun


class CodexAdapterTests(unittest.TestCase):
    def test_builds_expected_new_command(self) -> None:
        adapter = CodexAdapter(binary="codex")
        self.assertEqual(
            adapter.build_command("fix the bug", "new"),
            ["codex", "exec", "--skip-git-repo-check", "fix the bug"],
        )

    def test_builds_expected_resume_command(self) -> None:
        adapter = CodexAdapter(binary="codex")
        self.assertEqual(
            adapter.build_command("fix the bug", "continue"),
            ["codex", "exec", "--skip-git-repo-check", "resume", "--last", "fix the bug"],
        )

    def test_renders_result_summary(self) -> None:
        task = TaskRun(
            task_id="task-1",
            chat_id=1,
            project_name="alpha",
            prompt="hello",
            mode="new",
            status="done",
            started_at=1.0,
            ended_at=3.0,
            return_code=0,
            summary="Completed successfully.",
            stdout_tail="stdout tail",
            stderr_tail="",
        )
        rendered = render_task_result(task, max_chars=500)
        self.assertIn("🟢 alpha", rendered)
        self.assertIn("Project: alpha", rendered)
        self.assertIn("Summary:", rendered)
        self.assertIn("Completed successfully.", rendered)
        self.assertNotIn("STDOUT", rendered)


if __name__ == "__main__":
    unittest.main()
