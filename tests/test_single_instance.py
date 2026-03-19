from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.single_instance import SingleInstanceLock


class SingleInstanceLockTests(unittest.TestCase):
    def test_second_process_cannot_acquire_same_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "serve.lock"
            lock = SingleInstanceLock(lock_path)
            lock.acquire()
            try:
                env = dict(os.environ)
                extra_path = str(SRC)
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = extra_path if not existing else f"{extra_path}:{existing}"
                code = """
from pathlib import Path
from codex_connector.single_instance import SingleInstanceError, SingleInstanceLock
import sys

lock = SingleInstanceLock(Path(sys.argv[1]))
try:
    lock.acquire()
except SingleInstanceError:
    raise SystemExit(23)
else:
    lock.release()
    raise SystemExit(0)
"""
                blocked = subprocess.run(
                    [sys.executable, "-c", code, str(lock_path)],
                    env=env,
                    check=False,
                )
                self.assertEqual(blocked.returncode, 23)
            finally:
                lock.release()

            env = dict(os.environ)
            extra_path = str(SRC)
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = extra_path if not existing else f"{extra_path}:{existing}"
            code = """
from pathlib import Path
from codex_connector.single_instance import SingleInstanceLock
import sys

lock = SingleInstanceLock(Path(sys.argv[1]))
lock.acquire()
lock.release()
"""
            reopened = subprocess.run(
                [sys.executable, "-c", code, str(lock_path)],
                env=env,
                check=False,
            )
            self.assertEqual(reopened.returncode, 0)


if __name__ == "__main__":
    unittest.main()
