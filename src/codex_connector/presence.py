from __future__ import annotations

import logging
import re
import subprocess
import sys
import time


_HID_IDLE_PATTERN = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


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
        self._last_active = self._query_idle_seconds() < self.idle_threshold_seconds
        return self._last_active

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


def create_desktop_presence(*, idle_threshold_seconds: float, logger: logging.Logger | None = None) -> DesktopPresence | None:
    if sys.platform != "darwin":
        return None
    return MacOSDesktopPresence(idle_threshold_seconds=idle_threshold_seconds, logger=logger)
