from __future__ import annotations

import argparse
import sys

from .codex_adapter import CodexAdapter
from .config import apply_overrides, load_config, validate_config
from .service import BridgeService, configure_logging
from .state import StateStore
from .telegram import TelegramBotClient

_GLOBAL_OPTIONS = {"--config", "--state", "--log"}


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None

    hoisted: list[str] = []
    remainder: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _GLOBAL_OPTIONS:
            if i + 1 >= len(argv):
                remainder.append(token)
                break
            hoisted.extend([token, argv[i + 1]])
            i += 2
            continue
        if token.startswith("--config="):
            hoisted.extend(["--config", token.split("=", 1)[1]])
        elif token.startswith("--state="):
            hoisted.extend(["--state", token.split("=", 1)[1]])
        elif token.startswith("--log="):
            hoisted.extend(["--log", token.split("=", 1)[1]])
        else:
            remainder.append(token)
        i += 1
    return hoisted + remainder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-connector")
    parser.add_argument("--config", default="config.json", help="Path to the JSON config file")
    parser.add_argument("--state", help="Override the state file path")
    parser.add_argument("--log", help="Override the log file path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start Telegram polling")

    run = subparsers.add_parser("run", help="Run Codex once without Telegram")
    run.add_argument("prompt", help="Prompt to send to Codex")
    run.add_argument("--project", help="Project name")
    run.add_argument("--chat-id", type=int, help="Chat id to bind and store the run under")
    run.add_argument("--mode", choices=["new", "continue"], default="continue")

    status = subparsers.add_parser("status", help="Show status for a chat or project")
    status.add_argument("--chat-id", type=int, help="Chat id from state")
    status.add_argument("--project", help="Project name")

    last = subparsers.add_parser("last", help="Show the latest task for a chat or project")
    last.add_argument("--chat-id", type=int, help="Chat id from state")
    last.add_argument("--project", help="Project name")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(_normalize_argv(list(argv)))
    config = apply_overrides(load_config(args.config), state_path=args.state, log_path=args.log)
    validate_config(config, for_serve=args.command == "serve")
    logger = configure_logging(config.log_file)
    store = StateStore(config.state_file)
    store.load()
    adapter = CodexAdapter(binary=config.codex_binary, timeout_seconds=config.codex_timeout_seconds)
    telegram = TelegramBotClient(config.telegram_bot_token, timeout_seconds=config.request_timeout_seconds) if config.telegram_bot_token else None
    service = BridgeService(config=config, store=store, adapter=adapter, telegram=telegram, logger=logger)

    try:
        if args.command == "serve":
            if telegram is None:
                raise SystemExit("telegram_bot_token is required for serve mode")
            service.serve()
            return 0

        if args.command == "run":
            chat_id = args.chat_id if args.chat_id is not None else 0
            try:
                task = service.run_task_sync(chat_id, args.prompt, args.mode, project_name=args.project)
            except (RuntimeError, ValueError) as exc:
                raise SystemExit(str(exc)) from exc
            from .rendering import render_task_result
            print(render_task_result(task, config.max_output_chars))
            return 0

        if args.command == "status":
            if args.chat_id is not None:
                print(service.render_status(args.chat_id))
            else:
                project = config.project_by_name(args.project) if args.project else config.default_project()
                if args.project and project is None:
                    raise SystemExit(f"Unknown project: {args.project}")
                if project is None:
                    raise SystemExit("No project is configured")
                print(f"Project: {project.name}\nRepo: {project.repo_path}")
            return 0

        if args.command == "last":
            if args.chat_id is not None:
                print(service.render_last(args.chat_id))
            else:
                project = config.project_by_name(args.project) if args.project else config.default_project()
                if args.project and project is None:
                    raise SystemExit(f"Unknown project: {args.project}")
                if project is None:
                    raise SystemExit("No project is configured")
                task = store.last_task_for_project(project.name)
                from .rendering import render_last_task

                print(render_last_task(task))
            return 0

        parser.error("unknown command")
        return 2
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
