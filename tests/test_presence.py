from __future__ import annotations

import logging
import unittest
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.presence import MacOSDesktopPresence


class PresenceTests(unittest.TestCase):
    def test_screensaver_running_marks_user_inactive(self) -> None:
        presence = MacOSDesktopPresence(idle_threshold_seconds=300, logger=logging.getLogger("presence-test"))

        with patch("codex_connector.presence.sys.platform", "darwin"):
            with patch("codex_connector.presence.subprocess.run", return_value=CompletedProcess(args=[], returncode=0)):
                self.assertFalse(presence.is_user_active())

    def test_auto_threshold_uses_system_screensaver_idle_time(self) -> None:
        presence = MacOSDesktopPresence(idle_threshold_seconds=0, logger=logging.getLogger("presence-test"))

        def fake_check_output(cmd: list[str], **_: object) -> str:
            if cmd[:3] == ["defaults", "-currentHost", "read"]:
                return "300\n"
            if cmd[:3] == ["ioreg", "-n", "Root"]:
                return '"IOConsoleUsers" = ({"kCGSSessionOnConsoleKey"=Yes})\n'
            if cmd[:2] == ["ioreg", "-c"]:
                return '"HIDIdleTime" = 120000000000\n'
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("codex_connector.presence.sys.platform", "darwin"):
            with patch(
                "codex_connector.presence.subprocess.run",
                return_value=CompletedProcess(args=[], returncode=1),
            ):
                with patch("codex_connector.presence.subprocess.check_output", side_effect=fake_check_output):
                    self.assertTrue(presence.is_user_active())

    def test_locked_session_marks_user_inactive(self) -> None:
        presence = MacOSDesktopPresence(idle_threshold_seconds=0, logger=logging.getLogger("presence-test"))

        def fake_check_output(cmd: list[str], **_: object) -> str:
            if cmd[:3] == ["ioreg", "-n", "Root"]:
                return '"CGSSessionScreenIsLocked" = Yes\n'
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("codex_connector.presence.sys.platform", "darwin"):
            with patch(
                "codex_connector.presence.subprocess.run",
                return_value=CompletedProcess(args=[], returncode=1),
            ):
                with patch("codex_connector.presence.subprocess.check_output", side_effect=fake_check_output):
                    self.assertFalse(presence.is_user_active())


if __name__ == "__main__":
    unittest.main()
