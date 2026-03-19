# codex-connector

<p align="center">
  <strong>Drive local Codex sessions from Telegram.</strong><br />
  Local-first mobile control, project switching, session mirroring, and phone-friendly results.
</p>

<p align="center">
  <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/vvv214/codex-connector/ci.yml?branch=main&label=CI" />
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB" />
  <img alt="Local-first" src="https://img.shields.io/badge/runtime-local--first-0F766E" />
</p>

`codex-connector` is a small bridge that lets you keep using your local `codex` CLI from your phone. It does not move your repos or secrets into the cloud. Telegram is the control surface; execution still happens on your machine.

> Use Telegram as a thin remote control for the Codex setup you already trust on your laptop.
>
> Scope is intentionally narrow: this repo is Telegram-first, local-first, and single-user oriented. If you want Slack, Discord, Matrix, Signal, or a more generic transport layer, fork it or send a focused PR rather than expanding the core into a bot framework.

## Why

Codex is excellent at the desk, but awkward the moment you walk away from your laptop. This project adds a thin Telegram layer on top of the local workflow you already have:

- Continue the latest Codex session by sending plain text from your phone.
- Start a new task in a specific repo without opening a terminal.
- See compact progress updates while Codex is running locally.
- Mirror desktop Codex sessions back into Telegram.
- Keep project context aligned with the latest active session.

## Design Goals

- Keep execution local. Repos, secrets, and toolchains stay on your machine.
- Optimize for phone use. Updates should be short, readable, and actionable.
- Stay small. This is a thin control plane over local Codex, not a new agent platform.
- Prefer opinionated defaults over transport abstraction and plugin sprawl.

## Demo

Full walkthrough: [docs/demo.md](docs/demo.md)

```text
You      /project
Bot      Active project: codex-connector
         Recent sessions:
         1. codex-connector | 2026-03-19 10:18:21 | tighten Telegram callback handling
         2. CoPaw          | 2026-03-19 09:55:03 | review memory routing
         [• codex-connector] [CoPaw]
         [meta-autoresearch] [dpsgd-pe]

You      /new
Bot      New task mode armed for codex-connector. Send the prompt after choosing a project.
         [• codex-connector] [CoPaw]

You      add a smoke test for project callback buttons
Bot      Queued new task 71d8b7... for codex-connector

Bot      [codex-connector] callback tests · update
         added Telegram callback parsing and inline button coverage

Bot      [codex-connector] callback tests · completed
         Added callback-query support, split long Telegram replies, and
         kept /project switch buttons wired to the active chat context.
```

## Best For

- People already using local Codex who want quick mobile follow-up while away from the keyboard
- Personal single-user setups where Telegram is only the transport, not the execution environment
- Workflows that benefit from lightweight session mirroring without exposing repos to a hosted agent

## Not Trying To Be

- a multi-user chatbot service
- a hosted relay or remote execution layer
- a cross-platform messaging abstraction for every chat app
- a replacement for the Codex desktop app or CLI

## What It Does

| Capability | What you get |
| --- | --- |
| Remote control | `/new`, `/continue`, `/status`, `/last`, and plain-text follow-ups from Telegram |
| Project switching | `/project` shows recent sessions and inline buttons for quick switching |
| New-task picker | `/new` without a prompt opens a project picker and arms the next plain-text message as a fresh session |
| Session continuity | Plain text defaults to continuing the latest active project context |
| Desktop mirroring | Local Codex sessions can push `started`, `update`, and `completed` events into Telegram |
| Mobile-friendly output | Intermediate updates are short; long completions are split into multiple Telegram messages |
| Local-first runtime | Repos, tools, and Codex execution stay on your machine |

## Architecture

```mermaid
flowchart LR
    phone["Telegram on Phone"] --> bot["Telegram Bot API"]
    bot --> bridge["codex-connector Bridge"]
    bridge --> state["JSON State Store"]
    bridge --> codex["Local Codex CLI"]
    codex --> repo["Local Repositories"]
    codexdb["~/.codex/state_5.sqlite + rollout files"] --> bridge
    bridge --> bot
```

## Quick Start

1. Install the package from source.

   ```bash
   python3 -m pip install -e .
   ```

2. Copy the example config.

   ```bash
   cp config.example.json config.json
   ```

3. Fill in your Telegram token, chat id, and local project paths.

4. Start the bridge.

   ```bash
   codex-connector serve --config ./config.json
   ```

5. Open Telegram and try:

   ```text
   /project
   /new
   summarize the latest changes
   /continue tighten the tests
   /status
   ```

## Configuration

Copy `config.example.json` to `config.json` and edit the values for your machine.

The config supports:

- Telegram bot credentials and an allowlist of chat ids
- A fixed list of local repositories
- Optional `codex.timeout_seconds` for long-running Codex tasks
- Optional realtime session mirroring from local Codex state
- Optional `security` rules for chat allowlisting and repo validation
- Optional runtime settings such as `state_path`, `log_path`, and `max_output_chars`

Example:

```json
{
  "telegram": {
    "bot_token": "123456789:REPLACE_WITH_YOUR_BOT_TOKEN",
    "allowed_chat_ids": [123456789],
    "poll_interval_seconds": 2,
    "request_timeout_seconds": 30
  },
  "codex": {
    "binary": "codex",
    "timeout_seconds": 0
  },
  "codex_sessions": {
    "enabled": true,
    "state_db_path": "~/.codex/state_5.sqlite",
    "poll_interval_seconds": 2.0,
    "include_user_messages": false
  },
  "security": {
    "allow_unlisted_chats": false,
    "require_existing_repos": true,
    "require_git_repos": false
  },
  "runtime": {
    "state_path": "./state.json",
    "log_path": "./codex-connector.log",
    "max_output_chars": 1200
  },
  "projects": [
    {
      "name": "codex-connector",
      "repo_path": "/Users/you/Documents/GitHub/codex-connector"
    }
  ]
}
```

## Telegram Commands

| Command | Behavior |
| --- | --- |
| `/project` | Show the active project, recent sessions, and inline project buttons |
| `/project <name>` | Switch the active project and show the same session list |
| `/new` | Open a project picker and arm the next plain-text message as a fresh session |
| `/new <prompt>` | Start a new Codex task in the active project immediately |
| `/continue <prompt>` | Continue the latest Codex session in the active project |
| `/last` | Show the most recent recorded task for the active project |
| `/status` | Show the active project and whether a task is running |
| `/help` | Show the command list |

Plain text without a command is treated as `/continue`.

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
- The latest mirrored session can automatically update the active project for that chat.

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
- If you want another chat transport, the preferred path is a focused fork or a narrowly scoped PR that does not complicate the Telegram path.

## Out Of Scope

- multi-user bot hosting or tenant isolation
- remote code execution on machines you do not control
- first-party support for every chat application
- full IDE-style chat history, diff browsing, or rich artifact rendering inside Telegram
- replacing the local Codex CLI; this project is a thin control plane, not a new agent runtime

## Repository

- Demo walkthrough: [docs/demo.md](docs/demo.md)
- Example config: [config.example.json](config.example.json)
- Package metadata: [pyproject.toml](pyproject.toml)
