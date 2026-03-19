from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.models import TaskRun
from codex_connector.rendering import render_help_text, render_project_sessions, render_task_result


class RenderingTests(unittest.TestCase):
    def test_render_help_text(self) -> None:
        help_text = render_help_text()
        self.assertIn("/project [name]  list active project and recent sessions, or switch active project", help_text)
        self.assertIn("/new <prompt>", help_text)
        self.assertIn("/continue <prompt>", help_text)
        self.assertIn("/last", help_text)
        self.assertIn("/status", help_text)
        self.assertIn("/help", help_text)

    def test_render_project_sessions_no_sessions(self) -> None:
        text = render_project_sessions(active_project_name="my-proj", sessions=[])
        self.assertIn("Active project: my-proj", text)
        self.assertIn("Recent sessions: none", text)

    def test_render_project_sessions_with_sessions(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        sessions = [
            ("proj-a", "First session title", now - 3600),
            ("proj-b", "Second session title", now - 1800),
            ("proj-a", "Third session title, this one is longer than most to test truncation", now),
        ]
        sessions.sort(key=lambda x: x[2], reverse=True) # Sort by timestamp (index 2) descending
        text = render_project_sessions(active_project_name="proj-a", sessions=sessions)
        self.assertIn("Active project: proj-a", text)
        self.assertIn("Recent sessions:", text)
        self.assertIn("1. proj-a", text)
        self.assertIn("2. proj-b", text)
        self.assertIn("3. proj-a", text)
        self.assertIn("Third session title", text)
        self.assertLess(text.find("Second session title"), text.find("First session title"))
        self.assertLess(text.find("Third session title"), text.find("Second session title"))

    def test_render_project_sessions_with_prefix(self) -> None:
        text = render_project_sessions(active_project_name="my-proj", sessions=[], prefix="Hello there!")
        self.assertIn("Hello there!", text)
        self.assertIn("Active project: my-proj", text)

    def test_render_project_sessions_truncation(self) -> None:
        # Create a long session list to test truncation
        sessions = []
        now = datetime.now(timezone.utc).timestamp()
        for i in range(20):
            sessions.append((f"proj-{i}", f"Session title {i}", now - i * 100))
        
        # Max chars is 4000 by default, line length is ~72 + 20 for timestamp + 4 for index = ~100
        # 4000 / 100 = 40 lines. Plus header lines. Should truncate.
        text = render_project_sessions(active_project_name="my-proj", sessions=sessions, max_chars=500)
        self.assertIn("Active project: my-proj", text)
        self.assertIn("Recent sessions:", text)
        self.assertIn("... 1", text) # Check for omitted message
        self.assertLess(len(text), 550) # Should be less than max_chars + some buffer

    def test_render_task_result_hides_success_stdout(self) -> None:
        task = TaskRun(
            task_id="task-1",
            chat_id=1,
            project_name="proj-a",
            prompt="hello",
            mode="continue",
            status="done",
            started_at=1.0,
            ended_at=2.0,
            return_code=0,
            summary="Short success summary",
            stdout_tail="very long stdout tail",
        )
        text = render_task_result(task, max_chars=500)
        self.assertIn("Short success summary", text)
        self.assertNotIn("very long stdout tail", text)

if __name__ == "__main__":
    unittest.main()
