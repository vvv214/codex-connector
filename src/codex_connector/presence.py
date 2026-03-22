from __future__ import annotations

import logging
import re
import subprocess
import sys
import time


_HID_IDLE_PATTERN = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
_LOCKED_PATTERN = re.compile(r'"CGSSessionScreenIsLocked"\s*=\s*Yes')
_ON_CONSOLE_PATTERN = re.compile(r'"kCGSSessionOnConsoleKey"\s*=\s*(Yes|No)')


class DesktopPresence:
    def is_user_active(self) -> bool:
        raise NotImplementedError


class MacOSDesktopPresence(DesktopPresence):
    def __init__(
        self,
        *,
        idle_threshold_seconds: float,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: float = 2.0,
    ) -> None:
        self.idle_threshold_seconds = max(0.0, float(idle_threshold_seconds))
        self.logger = logger or logging.getLogger("codex_connector")
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self._last_checked_at = 0.0
        self._last_active = False
        self._warned_unsupported = False
        self._warned_failure = False
        self._warned_screensaver_failure = False
        self._warned_lock_state_failure = False
        self._warned_threshold_fallback = False

    def is_user_active(self) -> bool:
        if sys.platform != "darwin":
            if not self._warned_unsupported:
                self.logger.info("desktop activity detection is only supported on macOS; session notifications stay normal")
                self._warned_unsupported = True
            return False

        now = time.time()
        if now - self._last_checked_at < self.cache_ttl_seconds:
            return self._last_active

        self._last_checked_at = now
        if self._is_screen_obscured():
            self._last_active = False
            return self._last_active

        self._last_active = self._query_idle_seconds() < self._effective_idle_threshold_seconds()
        return self._last_active

    def _effective_idle_threshold_seconds(self) -> float:
        if self.idle_threshold_seconds > 0.0:
            return self.idle_threshold_seconds

        threshold = self._query_system_screensaver_idle_seconds()
        if threshold is not None and threshold > 0.0:
            return threshold

        if not self._warned_threshold_fallback:
            self.logger.info("failed to read system screensaver idle time; falling back to 120s for presence detection")
            self._warned_threshold_fallback = True
        return 120.0

    def _query_idle_seconds(self) -> float:
        try:
            output = subprocess.check_output(
                ["ioreg", "-c", "IOHIDSystem"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except Exception:
            if not self._warned_failure:
                self.logger.warning("failed to read macOS idle time; session notifications stay normal")
                self._warned_failure = True
            return self.idle_threshold_seconds + 1.0

        match = _HID_IDLE_PATTERN.search(output)
        if match is None:
            if not self._warned_failure:
                self.logger.warning("macOS idle time is unavailable; session notifications stay normal")
                self._warned_failure = True
            return self.idle_threshold_seconds + 1.0

        idle_nanoseconds = int(match.group(1))
        return idle_nanoseconds / 1_000_000_000.0

    def _is_screen_obscured(self) -> bool:
        return self._is_screensaver_running() or self._is_session_locked()

    def _is_screensaver_running(self) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-x", "ScreenSaverEngine"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
        except Exception:
            if not self._warned_screensaver_failure:
                self.logger.warning("failed to read macOS screensaver status; falling back to idle-time presence checks")
                self._warned_screensaver_failure = True
            return False
        return result.returncode == 0

    def _is_session_locked(self) -> bool:
        try:
            output = subprocess.check_output(
                ["ioreg", "-n", "Root", "-d1"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except Exception:
            if not self._warned_lock_state_failure:
                self.logger.warning("failed to read macOS lock state; falling back to idle-time presence checks")
                self._warned_lock_state_failure = True
            return False

        if _LOCKED_PATTERN.search(output):
            return True

        on_console_match = _ON_CONSOLE_PATTERN.search(output)
        if on_console_match is not None:
            return on_console_match.group(1) == "No"
        return False

    def _query_system_screensaver_idle_seconds(self) -> float | None:
        try:
            output = subprocess.check_output(
                ["defaults", "-currentHost", "read", "com.apple.screensaver", "idleTime"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).strip()
        except Exception:
            return None

        try:
            value = float(output)
        except ValueError:
            return None
        return value if value > 0 else None


def create_desktop_presence(*, idle_threshold_seconds: float, logger: logging.Logger | None = None) -> DesktopPresence | None:
    if sys.platform != "darwin":
        return None
    return MacOSDesktopPresence(idle_threshold_seconds=idle_threshold_seconds, logger=logger)
