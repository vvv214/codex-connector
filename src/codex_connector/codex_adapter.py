from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CodexResult:
    ok: bool
    return_code: int
    stdout: str
    stderr: str
    started_at: float
    ended_at: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)


class CodexAdapter:
    def __init__(self, binary: str = "codex", timeout_seconds: int = 0):
        self.binary = binary
        self.timeout_seconds = timeout_seconds or None

    def build_command(self, prompt: str, mode: str) -> list[str]:
        prompt = prompt.strip()
        if mode == "new":
            return [self.binary, "exec", "--skip-git-repo-check", prompt]
        if mode == "continue":
            return [self.binary, "exec", "--skip-git-repo-check", "resume", "--last", prompt]
        raise ValueError(f"unsupported mode: {mode}")

    def run(self, repo_path: str | Path, prompt: str, mode: str) -> CodexResult:
        started_at = time.time()
        command = self.build_command(prompt, mode)
        proc = subprocess.run(
            command,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        ended_at = time.time()
        return CodexResult(
            ok=proc.returncode == 0,
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            started_at=started_at,
            ended_at=ended_at,
        )
