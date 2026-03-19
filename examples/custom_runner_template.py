from __future__ import annotations

from codex_connector.runner import BaseCliRunner


class CustomRunner(BaseCliRunner):
    """
    Minimal example for a fork or focused PR.

    Steps:
    1. Copy this file into your fork.
    2. Rename the class and tweak `build_command`.
    3. Register it in `src/codex_connector/runner.py`.
    4. Set `runner.provider` and `runner.binary` in config.json.
    """

    def build_command(self, prompt: str, mode: str) -> list[str]:
        prompt = prompt.strip()
        if mode == "new":
            return [self.binary, "--some-flag", prompt]
        if mode == "continue":
            return [self.binary, "resume", prompt]
        raise ValueError(f"unsupported mode: {mode}")
