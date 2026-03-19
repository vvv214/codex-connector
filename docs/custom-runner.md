# Custom Runner

`codex-connector` keeps the runner boundary intentionally small.

The default runner is `codex`, but a fork can replace it with another local CLI by changing only a few pieces:

1. Copy [examples/custom_runner_template.py](../examples/custom_runner_template.py).
2. Implement `build_command(prompt, mode)`.
3. Register the class in [src/codex_connector/runner.py](../src/codex_connector/runner.py).
4. Set `runner.provider` and `runner.binary` in `config.json`.

The runner contract is minimal:

- input: `repo_path`, `prompt`, `mode`
- output: `RunnerResult`
- base class: `BaseCliRunner`

That is deliberate. The transport and mobile UX should stay stable even if the underlying local CLI changes.

## Why this repo does not ship every runner

This project is trying to stay small:

- default path: Telegram + local Codex
- customization path: fork or focused PR
- non-goal: first-party support for every agent CLI

If you want to wire up `gemini`, `claude`, `aider`, or something private inside your own environment, the intended move is to add a runner implementation in your fork.
