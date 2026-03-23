# codex-connector

<div align="center">
  <h1>codex-connector</h1>
  <p><strong>Fill Codex's mobile gap from your phone.</strong></p>
  <p>Telegram control, multi-session routing, compact session mirroring, local-first execution.</p>
  <p>
    <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/vvv214/codex-connector/ci.yml?branch=main&label=CI" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB" />
    <img alt="Local-first" src="https://img.shields.io/badge/runtime-local--first-0F766E" />
    <img alt="Telegram" src="https://img.shields.io/badge/transport-Telegram-26A5E4" />
  </p>
</div>

`codex-connector` is a thin remote-control layer for your local coding agent workflow.

It exists for one narrow job: let you drive the local Codex setup you already trust from your phone, without moving repos, secrets, or execution off your own machine. Telegram is the current transport, not the core identity.

## Table of Contents

- [Why This Exists](#why-this-exists)
- [Features](#features)
- [Install](#install)
- [Quick Start](#quick-start)
- [Telegram Commands](#telegram-commands)
- [Session Mirroring](#session-mirroring)
- [How It Works](#how-it-works)
- [Extending The Runner](#extending-the-runner)
- [Security Notes](#security-notes)
- [Development](#development)
- [Contributing](#contributing)

## Why This Exists

- Codex is useful on the desktop, but you may still want a phone control plane.
- Native remote flows often focus on one active session; many real workflows span multiple repos and threads.
- The useful part is not "build a Telegram bot". The useful part is "reach the local agent workflow you already have".

## Features

| Capability | What you actually get |
| --- | --- |
| Mobile control | Start a new task, continue the latest one, inspect status, and switch projects from Telegram |
| Multi-session routing | One chat can follow multiple local sessions and keep routing aligned with the latest active project |
| Compact mirroring | Local sessions can mirror `started`, `update`, and `completed` events back to your phone |
| Local-first runtime | Repos, secrets, tools, and execution stay on your Mac |
| Project picker | `/project` and `/new` expose inline buttons instead of forcing you to type repo names |
| Update toggle | `/updates on|off` lets you keep final results while muting intermediate progress messages |

## Install

**From source**

```bash
git clone https://github.com/vvv214/codex-connector.git
cd codex-connector
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

**User install on macOS Homebrew Python**

```bash
python3 -m pip install --user --break-system-packages -e .
```

## Quick Start

1. Create a Telegram bot with `@BotFather`.
2. Send the bot one message from your phone.
3. Ask your local Codex to create `config.json` for you.

```text
I made a new Telegram bot. Here is the token. I already sent it "hi" from my phone.
Please create codex-connector/config.json on this Mac, fetch my Telegram chat id,
populate my local projects, enable session mirroring, and keep the config local.
```

4. Start the bridge on the Mac that owns your repos and local Codex setup.

```bash
codex-connector serve --config ./config.json
```

5. Test the flow from Telegram.

```text
/status
/project
/new
summarize the latest changes
/continue tighten the tests
```

If you want the bridge to keep running beyond a terminal session, run it under your OS service manager. On macOS, `launchd` is the right default.

## Telegram Commands

| Command | Behavior |
| --- | --- |
| `/project` | Show recent sessions, list projects, and pin one |
| `/project latest` | Clear a manual pin and go back to latest-session routing |
| `/new [prompt]` | Start a fresh session, or open the project picker if no prompt is given |
| `/continue <prompt>` | Continue the active or pinned project |
| `/status` | Show current project, routing state, and any running task |
| `/last` | Show the latest task for the active project |
| `/updates [on|off]` | Toggle intermediate mirrored updates while keeping final results |
| `/help` | Show the command summary |

Plain text is treated as `/continue`.

## Session Mirroring

When `codex_sessions.enabled` is `true`, the bridge watches the local Codex `threads` database in read-only mode and mirrors:

- `task_started`
- `agent_message`
- `task_complete`

The mirroring path is optimized for phone reading:

- started and completed messages stay explicit
- intermediate updates are compact and throttled
- long final outputs are split across Telegram messages when needed
- `/updates off` mutes only intermediate `agent_message` updates
- desktop-aware delivery can stay `always`, `silent`, or `suppress`

## How It Works

The core stays intentionally small:

- **Telegram transport** receives commands, sends replies, and renders inline buttons.
- **Runner** turns `new` or `continue` into a local CLI invocation inside a repo.
- **Session monitor** watches local Codex sessions and mirrors compact state back to Telegram.
- **State store** keeps chat routing, task history, and per-chat preferences in SQLite.

This is not trying to become a general chat framework or hosted agent platform.

## Extending The Runner

The built-in runner is `codex`, but the boundary is intentionally narrow.

To add another local CLI:

1. Add a runner next to [codex_adapter.py](src/codex_connector/codex_adapter.py).
2. Register it in [runner.py](src/codex_connector/runner.py).
3. Point `config.json` at a different `runner.provider` and `runner.binary`.

Template and notes:

- [docs/custom-runner.md](docs/custom-runner.md)
- [examples/custom_runner_template.py](examples/custom_runner_template.py)

## Security Notes

- Restrict `allowed_chat_ids` to chats you control.
- Keep `config.json` local and out of git.
- Leave `security.require_existing_repos` enabled unless you have a strong reason not to.
- Treat this as a personal local tool, not a public multi-user service.
- Only expose repositories you trust the local agent to operate on.

## Development

Run tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Build distributions:

```bash
uv build
```

Useful local CLI examples:

```bash
codex-connector run --config ./config.json --project codex-connector --mode new "summarize this repo"
codex-connector status --config ./config.json --chat-id 390429375
codex-connector last --config ./config.json --chat-id 390429375
```

## Contributing

- Keep the core thin, local-first, and focused on phone control for local coding agents.
- Prefer small changes with `unittest` coverage for command parsing, state persistence, Telegram behavior, and routing.
- Optimize for mobile readability: short intermediate updates, clear project context, deterministic buttons, and explicit final results.
- If you add config surface area, update both `config.example.json` and this README in the same change.
- Other transports or runners are welcome as focused extensions, but the default Codex path should stay simple.

## Out Of Scope

- Turning this repo into a general-purpose assistant platform
- First-party support for every chat app
- Moving execution away from the local machine
- Replacing the local Codex workflow you already use
