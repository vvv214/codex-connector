from __future__ import annotations

import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.commands import parse_message
from codex_connector.rendering import render_help_text


class CommandParsingTests(unittest.TestCase):
    def test_parse_project(self) -> None:
        parsed = parse_message("/project app")
        self.assertEqual(parsed.kind, "project")
        self.assertEqual(parsed.argument, "app")

    def test_parse_plain_text_as_continue(self) -> None:
        parsed = parse_message("please tighten the tests")
        self.assertEqual(parsed.kind, "continue")
        self.assertEqual(parsed.argument, "please tighten the tests")

    def test_help_text_includes_core_commands(self) -> None:
        text = render_help_text()
        self.assertIn("/new <prompt>", text)
        self.assertIn("/status", text)
        self.assertIn("/project [name]", text)


if __name__ == "__main__":
    unittest.main()
