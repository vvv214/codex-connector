from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


class SingleInstanceError(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise SingleInstanceError(
                    f"Another codex-connector serve process is already running ({self.path})."
                ) from exc

        handle.seek(0)
        handle.truncate(0)
        handle.write(f"pid={os.getpid()}\nstarted_at={int(time.time())}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        self._handle.close()
        self._handle = None

