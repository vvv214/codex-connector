# AGENTS.md

## Scope

`codex-connector` is a local-first Telegram bridge for driving local Codex
sessions. Key areas:

- `src/codex_connector/`: CLI, routing, bridge logic, and session mirroring
- `tests/`: behavior and regression tests
- `docs/` and `examples/`: operator-facing docs and usage examples
- `config.example.json`: public config template

## Preferred validation

- Install with `python -m pip install -e .`
- Run `pytest` for logic changes
- Keep the `codex-connector` CLI contract in sync with README examples

## Review focus

- Treat the local-first trust boundary as a core invariant: changes should not
  quietly move execution, secrets, or repo access off the local machine.
- Flag anything that could leak bot tokens, chat IDs, local repo paths, state
  databases, or mirrored thread content.
- Review command-routing changes carefully: `/project`, `/new`, `/continue`,
  `/status`, and session-pinning behavior should remain predictable.
- Check that mirroring and routing changes still handle multiple projects and
  latest-session fallback correctly.
- Flag docs or examples that encourage committing `config.json`,
  `state.sqlite3`, runtime logs, or other local artifacts.
- Prefer comments on correctness, privacy, and operator safety over minor style
  issues.
