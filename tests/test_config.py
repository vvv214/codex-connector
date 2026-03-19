from __future__ import annotations

import json
import tempfile
import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.config import apply_overrides, load_config


class ConfigTests(unittest.TestCase):
    def test_loads_nested_config_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "telegram": {
                            "bot_token": "nested-token",
                            "allowed_chat_ids": [111, 222],
                            "poll_interval_seconds": 5,
                            "request_timeout_seconds": 11,
                        },
                        "codex": {"binary": "codex-nested", "timeout_seconds": 600},
                        "codex_sessions": {
                            "enabled": True,
                            "state_db_path": "./runtime/codex/state_5.sqlite",
                            "poll_interval_seconds": 3.5,
                            "include_user_messages": True,
                        },
                        "runtime": {
                            "state_path": "./runtime/state.json",
                            "log_path": "./runtime/logs/bridge.log",
                        },
                        "projects": [
                            {
                                "name": "alpha",
                                "repo_path": "./repos/alpha",
                                "branch": "main",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.telegram_bot_token, "nested-token")
            self.assertEqual(config.allowed_chat_ids, frozenset({111, 222}))
            self.assertEqual(config.codex_binary, "codex-nested")
            self.assertEqual(config.codex_timeout_seconds, 600)
            self.assertTrue(config.codex_sessions.enabled)
            self.assertEqual(config.codex_sessions.state_db_path, (root / "runtime/codex/state_5.sqlite").resolve())
            self.assertEqual(config.codex_sessions.poll_interval_seconds, 3.5)
            self.assertTrue(config.codex_sessions.include_user_messages)
            self.assertEqual(config.poll_sleep_seconds, 5.0)
            self.assertEqual(config.request_timeout_seconds, 11)
            self.assertEqual(config.state_file, (root / "runtime/state.json").resolve())
            self.assertEqual(config.log_file, (root / "runtime/logs/bridge.log").resolve())
            self.assertEqual(config.projects[0].repo_path, str((root / "repos/alpha").resolve()))
            self.assertEqual(config.max_output_chars, 1200)

    def test_apply_overrides_replaces_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"projects": [{"name": "alpha", "repo_path": "./repo"}]}),
                encoding="utf-8",
            )

            config = load_config(config_path)
            updated = apply_overrides(
                config,
                state_path=root / "override-state.json",
                log_path=root / "override.log",
            )

            self.assertEqual(updated.state_file, (root / "override-state.json").resolve())
            self.assertEqual(updated.log_file, (root / "override.log").resolve())

    def test_project_by_repo_path(self) -> None:
        from codex_connector.models import Project, AppConfig

        # Create dummy projects
        project1 = Project(name="proj1", repo_path="/a/b/c")
        project2 = Project(name="proj2", repo_path="/x/y")
        project3 = Project(name="proj3", repo_path="/a/b/c/d/e") # Subdirectory of proj1

        # Create a dummy AppConfig
        config = AppConfig(projects=[project1, project2, project3])

        # Test exact match
        found_project = config.project_by_repo_path("/a/b/c")
        self.assertIsNotNone(found_project)
        self.assertEqual(found_project.name, "proj1")

        # Test sub-directory match
        found_project = config.project_by_repo_path("/a/b/c/foo/bar")
        self.assertIsNotNone(found_project)
        self.assertEqual(found_project.name, "proj1") # Should match the parent project

        # Test another exact match
        found_project = config.project_by_repo_path("/x/y")
        self.assertIsNotNone(found_project)
        self.assertEqual(found_project.name, "proj2")

        # Test no match
        found_project = config.project_by_repo_path("/non/existent/path")
        self.assertIsNone(found_project)

        # Test with a subdirectory that is itself a project
        found_project = config.project_by_repo_path("/a/b/c/d/e/f")
        self.assertIsNotNone(found_project)
        self.assertEqual(found_project.name, "proj3") # Should match the most specific project

        found_project = config.project_by_repo_path("/a/b/c/d/e")
        self.assertIsNotNone(found_project)
        self.assertEqual(found_project.name, "proj3")

        # Test with relative paths (should resolve internally)
        # Create a temporary directory structure for testing relative paths
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "repo_alpha").mkdir()
            (tmp_path / "repo_beta").mkdir()
            (tmp_path / "repo_alpha" / "sub").mkdir()

            rel_proj_alpha = Project(name="rel_alpha", repo_path=str(tmp_path / "repo_alpha"))
            rel_config = AppConfig(projects=[rel_proj_alpha])

            found_project = rel_config.project_by_repo_path(str(tmp_path / "repo_alpha"))
            self.assertIsNotNone(found_project)
            self.assertEqual(found_project.name, "rel_alpha")

            found_project = rel_config.project_by_repo_path(str(tmp_path / "repo_alpha" / "sub"))
            self.assertIsNotNone(found_project)
            self.assertEqual(found_project.name, "rel_alpha")

            found_project = rel_config.project_by_repo_path(str(tmp_path / "repo_beta"))
            self.assertIsNone(found_project)


if __name__ == "__main__":
    unittest.main()
