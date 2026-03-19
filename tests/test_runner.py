from __future__ import annotations

import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.codex_adapter import CodexAdapter
from codex_connector.models import AppConfig, Project, RunnerConfig
from codex_connector.runner import create_runner


class RunnerTests(unittest.TestCase):
    def test_create_runner_builds_codex_adapter(self) -> None:
        config = AppConfig(
            projects=[Project(name="alpha", repo_path="/tmp/alpha")],
            runner=RunnerConfig(provider="codex", binary="codex-custom", timeout_seconds=15),
        )

        runner = create_runner(config)

        self.assertIsInstance(runner, CodexAdapter)
        self.assertEqual(runner.build_command("fix bug", "new"), ["codex-custom", "exec", "--skip-git-repo-check", "fix bug"])

    def test_create_runner_rejects_unknown_provider(self) -> None:
        config = AppConfig(
            projects=[Project(name="alpha", repo_path="/tmp/alpha")],
            runner=RunnerConfig(provider="gemini", binary="gemini", timeout_seconds=0),
        )

        with self.assertRaises(ValueError):
            create_runner(config)


if __name__ == "__main__":
    unittest.main()
