# codex-connector

<p align="center">
  <strong>Bridge your local coding agent to your phone.</strong><br />
  mobile control, project switching, session mirroring, phone friendly.
</p>

<p align="center">
  <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/vvv214/codex-connector/ci.yml?branch=main&label=CI" />
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB" />
  <img alt="Local-first" src="https://img.shields.io/badge/runtime-local--first-0F766E" />
</p>

`codex-connector` is a small bridge for the common setup many of us already have: strong local agent tools on the machine (e.g., codex), but no clean way to reach them from a phone. 

`Telegram` is used as a thin remote control for the local agent setup you already trust on your machine.




## Why

Your laptop already knows how to run the real work. The missing piece is a simple mobile control plane. This project adds a thin Telegram layer on top of the local workflow you already have:

- Continue the latest Codex session by sending plain text from your phone.
- Start a new task in a specific repo without opening a terminal.
- See compact progress updates while Codex is running locally.
- Mirror desktop Codex sessions back into Telegram.
- Keep project context aligned with the latest active session.

## Design Goals

- Keep execution local. Repos, secrets, and toolchains stay on your machine.
- Optimize for phone use. Updates should be short, readable, and actionable.
- Stay small. This is a thin control plane over a local agent CLI, not a new agent platform.
- Prefer opinionated defaults over transport abstraction and plugin sprawl.

## Core Idea

There are only two moving parts in the core:

- `Telegram transport`: receive commands, send short updates, render buttons.
- `Runner`: turn `new` or `continue` into a local CLI invocation inside a repo.

Today the built-in runner is `codex`. The runner boundary is intentionally small so another local CLI can be added without rewriting the Telegram flow.


## Why A Thin Bridge

If you already use `codex` locally, a lot of the hard part is already solved. Codex already has session state, memory, repo context, and the local toolchain. For this use case, the problem is not "build another assistant stack." The problem is just "reach the setup you already trust from your phone."

```text
What already exists on your machine:

  codex/claude code/gemini cli...
  - memory / session state
  - repo context
  - local tools and secrets

Ways to reach it from a phone:

  build a website
    cost: frontend, auth, deployment, maintenance

  add OpenClaw
    cost: solves more than this repo needs

  add codex-connector
    value: thin bridge, local execution stays local
```

This repo takes the last path. In other words:

- If Codex already gives you the memory and execution model you want, and you only need phone access, this repo is the right shape.
- If you want vendor-managed cloud execution, use those first-party products directly.
- If you want a full remote product surface, build a website.
- If you want an always-on multi-channel assistant that does much more than bridge into Codex, OpenClaw is closer.
- If you want to train or serve your own model stack, nanochat is solving a different problem entirely.

## How It Works

| Capability | What you get |
| --- | --- |
| Project switching | `/project` shows recent sessions, lets you pin a repo, and offers a `Follow latest` escape hatch |
| New-task picker | `/new` without a prompt opens a project picker and arms the next plain-text message as a fresh session |
| Session continuity | Plain text follows the latest active project by default, or stays pinned to the project you selected |
| Desktop mirroring | Local Codex sessions can push `started`, `update`, and `completed` events into Telegram |
| Mobile-friendly output | Intermediate updates are short; long completions are split into multiple Telegram messages |
| Local-first runtime | Repos, tools, and Codex execution stay on your machine |

## Quick Start

1. Install the package from source.

   ```bash
   python3 -m pip install -e .
   ```

2. Create a Telegram bot with `@BotFather`, then send it `hi` once from your phone.

3. Ask your local Codex to create the config for you.

   ```text
   I made a new Telegram bot. Here is the token. I already sent it "hi" from my phone.
   Please create codex-connector/config.json on this Mac, fetch my Telegram chat id, populate my local projects, enable session mirroring, and keep the config local.
   ```

   If you prefer manual editing, [config.example.json](config.example.json) is the reference file, but the intended flow is to let Codex write your local config.

4. Start the bridge.

   ```bash
   codex-connector serve --config ./config.json
   ```

   Run this on the Mac that has your repos, local CLI, and config. If this process stops, Telegram control and notifications stop too.
   The bridge does not send an automatic "I'm alive" message on startup, so test it with a command from Telegram after it starts.

5. Open Telegram and try:

   ```text
   /status
   /project
   /project latest
   /new
   summarize the latest changes
   /continue tighten the tests
   ```

   Start with `/status` or `/project`. Plain text is treated as a real prompt, so avoid sending casual messages like `hi` after the bridge is running unless you want Codex to act on them.

## Telegram Flow

- `/project` shows recent sessions, inline project buttons, and a `Follow latest` button.
- Selecting a project pins routing to that repo until you switch again.
- `/project latest` clears the pin and returns to automatic latest-session routing.
- `/new` opens a project picker and treats the next plain-text message as a fresh session.
- Plain text without a command continues the pinned project if you selected one; otherwise it follows the latest active project context.
- `/status`, `/last`, and `/help` stay available as lightweight control commands.
- `🔵` means the task was received and started.
- `🔹` means a live progress update.
- `🟢` means the final completion message.
- Mirrored sessions use simple emoji markers, send a short acknowledgement first, throttle live progress updates to at most once per minute per session, and fold the last pending live hint into the final completion message when a run finishes quickly.

## Extending The Runner

If you want to use another local CLI, the intended customization point is the runner layer:

1. Add a runner implementation next to [codex_adapter.py](src/codex_connector/codex_adapter.py).
2. Register it in [runner.py](src/codex_connector/runner.py).
3. Point `config.json` at a different `runner.provider` and `runner.binary`.

The core repo only ships `codex` by default. First-party support for every agent CLI is intentionally out of scope.

Minimal template: [docs/custom-runner.md](docs/custom-runner.md)

## Realtime Session Mirroring

When `codex_sessions.enabled` is `true`, the bridge reads the local Codex `threads` table in read-only mode and mirrors activity from every local session:

- `task_started`
- `agent_message`
- `task_complete`

Behavior details:

- Existing history is skipped on startup.
- New sessions are followed from the beginning.
- Intermediate `update` messages are shortened for mobile reading.
- Completion messages are sent in full, split across multiple Telegram messages when needed.
- Mirrored sessions can automatically update routing when the chat is in `Follow latest` mode.
- If you pinned a project with `/project <name>`, mirrored sessions in other repos do not override that choice.
- On macOS, mirrored session notifications can switch to `silent` or `suppress` while the desktop is active.

## Development

Run tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Build sdist and wheel:

```bash
uv build
```

Local CLI examples:

```bash
codex-connector run --config ./config.json --project codex-connector --mode new "summarize this repo"
codex-connector status --config ./config.json --chat-id 390429375
codex-connector last --config ./config.json --chat-id 390429375
```

## Security Notes

- Restrict `allowed_chat_ids` to chats you control.
- `serve` now fails closed unless `allowed_chat_ids` is configured or `security.allow_unlisted_chats` is explicitly enabled.
- Keep `config.json` local and out of git.
- Leave `security.require_existing_repos` enabled unless you have a strong reason not to.
- Only expose repositories you trust.
- Treat this as a personal local tool, not a public multi-user service.

## Limitations

- Telegram is a compact control layer, not a rich IDE.
- Outputs are optimized for phones, not diffs or large logs.
- The bridge assumes your local `codex` CLI and project environment are already configured correctly.

## Contributing

- Keep changes small, local-first, and Telegram-first; avoid turning this into a hosted service or generic chat framework.
- Add or update `unittest` coverage for command parsing, state persistence, and Telegram callbacks when behavior changes.
- Prefer mobile-oriented UX: short intermediate updates, explicit project context, and deterministic callback flows.
- When adding config surface area, update both [config.example.json](config.example.json) and this README in the same change.
- If you add another chat transport or runner, keep the default Telegram + Codex path simple.
