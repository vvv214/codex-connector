from __future__ import annotations

from .runner import BaseCliRunner, RunnerResult


CodexResult = RunnerResult


class CodexAdapter(BaseCliRunner):
    def build_command(self, prompt: str, mode: str) -> list[str]:
        prompt = prompt.strip()
        if mode == "new":
            return [self.binary, "exec", "--skip-git-repo-check", prompt]
        if mode == "continue":
            return [self.binary, "exec", "--skip-git-repo-check", "resume", "--last", prompt]
        raise ValueError(f"unsupported mode: {mode}")
