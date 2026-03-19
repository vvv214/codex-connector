# Codex Remote Companion

`codex-connector` is a lightweight local bridge for driving a Codex CLI session from Telegram. It keeps the execution on your machine, routes messages to a chosen local repository, and returns compact run summaries to your phone.

## What It Does

- Continue the latest Codex session for a repository.
- Start a new Codex session in a selected repository.
- Keep a small local state file for chat-to-project mapping and recent task history.
- Run as a local Telegram bot poller, with all execution staying on your machine.

## Requirements

- Python 3.11 or newer.
- A working `codex` CLI on your machine.
- A Telegram bot token from `@BotFather`.
- A local machine that has direct access to the repositories you want to use.

## Install

Install the package however your build workflow produces it, then run the `codex-connector` console script.

If you are working from a source checkout, the expected entry points are:

```bash
codex-connector serve --config ./config.json
codex-connector run --config ./config.json --project privsyn --mode continue "summarize the latest changes"
```

## Configuration

Copy `config.example.json` to `config.json` and edit the values for your machine.

The config defines:

- Telegram bot credentials and allowed chat ids.
- A small allowlist of local repositories.
- Optional Codex binary path and `codex.timeout_seconds` for long-running tasks. Use `0` to disable the subprocess timeout.
- Optional realtime session mirroring from the local Codex session database.
- Optional polling, state, logging, and `runtime.max_output_chars` settings.

The CLI also supports explicit file paths for state and logs:

```bash
codex-connector serve --config ./config.json --state ./state.json --log ./codex-connector.log
```

## Realtime Codex Sessions

If you want Telegram to mirror activity from every local Codex session, add a `codex_sessions` section to `config.json` and set `enabled` to `true`.

Example:

```json
{
  "codex_sessions": {
    "enabled": true,
    "state_db_path": "/Users/you/.codex/state_5.sqlite",
    "poll_interval_seconds": 2.0,
    "include_user_messages": false
  }
}
```

When enabled, the bridge reads the local Codex `threads` table in read-only mode and sends compact notifications for new `task_started`, `agent_message`, and `task_complete` events. Intermediate updates are shortened for mobile, while long completion messages are split into multiple Telegram messages instead of being truncated. It skips old history at startup and only starts from the beginning of new threads created after the bridge starts.

Each notification also updates your Telegram chat's active project to the configured project whose `repo_path` matches the session's working directory. After a notification arrives, plain text or `/continue` will target that project automatically.

## Telegram Commands

The bot understands these commands in the allowed chat:

- `/project` shows the active project and a recent Codex session list (newest first), and includes inline buttons for quick switching.
- `/project <name>` switches the active project and shows the same session list.
- `/new <prompt>` starts a new Codex session in the active repository.
- `/continue <prompt>` continues the latest Codex session in the active repository.
- `/last` shows the most recent known task for that project.
- `/status` shows the active project and whether a task is running.
- `/help` shows the command list.

Plain text without a command is treated as `/continue`.

## CLI Commands

- `serve --config <path> [--state <path>] [--log <path>]` starts the Telegram bridge.
- `run --config <path> --project <name> --mode <new|continue> [--chat-id <id>] <prompt>` runs one task directly.
- `status --config <path> --chat-id <id> [--state <path>]` shows the active project and running state.
- `last --config <path> --chat-id <id> [--state <path>]` shows the latest recorded task.

## Security Notes

- Restrict `allowed_chat_ids` to your own Telegram chat ids.
- Keep the bot token local and out of source control.
- Only list repositories you trust in the config file.
- Do not accept repo paths from chat input.
- Treat the bridge as a personal local tool, not a multi-user service.

## Suggested Workflow

1. Create a Telegram bot and set the token in `config.json`.
2. Add your chat id to `allowed_chat_ids`.
3. Add one or more local repositories under `projects`.
4. Start the bridge with `codex-connector serve --config ./config.json`.
5. Message the bot with `/project`, `/new`, or plain text follow-ups.
