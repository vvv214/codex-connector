from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .models import AppConfig


@dataclass(slots=True)
class RunnerResult:
    ok: bool
    return_code: int
    stdout: str
    stderr: str
    started_at: float
    ended_at: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)


class Runner(Protocol):
    def run(self, repo_path: str | Path, prompt: str, mode: str) -> RunnerResult: ...


class BaseCliRunner(ABC):
    def __init__(self, binary: str, timeout_seconds: int = 0):
        self.binary = binary
        self.timeout_seconds = timeout_seconds or None

    def run(self, repo_path: str | Path, prompt: str, mode: str) -> RunnerResult:
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
        return RunnerResult(
            ok=proc.returncode == 0,
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            started_at=started_at,
            ended_at=ended_at,
        )

    @abstractmethod
    def build_command(self, prompt: str, mode: str) -> list[str]:
        raise NotImplementedError


RunnerFactory = Callable[[AppConfig], Runner]


def _build_codex_runner(config: AppConfig) -> Runner:
    from .codex_adapter import CodexAdapter

    return CodexAdapter(
        binary=config.runner.binary,
        timeout_seconds=config.runner.timeout_seconds,
    )


RUNNER_FACTORIES: dict[str, RunnerFactory] = {
    "codex": _build_codex_runner,
}


def create_runner(config: AppConfig) -> Runner:
    provider = config.runner.provider
    factory = RUNNER_FACTORIES.get(provider)
    if factory is None:
        available = ", ".join(sorted(RUNNER_FACTORIES))
        raise ValueError(f"unsupported runner provider: {provider}. Available: {available}")
    return factory(config)
