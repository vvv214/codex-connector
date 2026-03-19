from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import AppConfig, CodexSessionsConfig, Project, RunnerConfig, SecurityConfig


class ConfigError(ValueError):
    pass


def _resolve_path(base_dir: Path, raw_path: str | None, default_name: str) -> Path:
    candidate = Path(raw_path if raw_path is not None else default_name).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def _lookup(payload: dict[str, Any], *paths: tuple[str, ...], default: Any = None) -> Any:
    for path in paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict) or key not in current:
                break
            current = current[key]
        else:
            if current is not None:
                return current
    return default


def _as_int_set(values: Any) -> frozenset[int]:
    if values is None:
        return frozenset()
    if isinstance(values, (str, bytes)):
        values = [values]
    if not isinstance(values, list):
        raise ConfigError("allowed chat ids must be a list")
    return frozenset(int(value) for value in values if str(value).strip())


def _parse_projects(raw_projects: Any, base_dir: Path) -> list[Project]:
    if not isinstance(raw_projects, list) or not raw_projects:
        raise ConfigError("config.projects must be a non-empty list")
    projects: list[Project] = []
    for item in raw_projects:
        if not isinstance(item, dict):
            raise ConfigError("each project must be an object")
        name = str(item.get("name", "")).strip()
        repo_path = str(item.get("repo_path", "")).strip()
        if not name:
            raise ConfigError("each project needs a name")
        if not repo_path:
            raise ConfigError(f"project {name!r} needs a repo_path")
        repo = Path(repo_path).expanduser()
        if not repo.is_absolute():
            repo = base_dir / repo
        projects.append(
            Project(
                name=name,
                repo_path=str(repo.resolve()),
                branch=(str(item["branch"]).strip() if item.get("branch") else None),
                notes=(str(item["notes"]).strip() if item.get("notes") else None),
            )
        )
    return projects


def _parse_codex_sessions(payload: dict[str, Any], base_dir: Path) -> CodexSessionsConfig:
    raw = _lookup(payload, ("codex_sessions",), default={})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("config.codex_sessions must be an object")

    enabled = bool(raw.get("enabled", False))
    state_db_path = _resolve_path(
        base_dir,
        str(raw.get("state_db_path")) if raw.get("state_db_path") is not None else None,
        "~/.codex/state_5.sqlite",
    )
    poll_interval_seconds = float(raw.get("poll_interval_seconds", 2.0))
    include_user_messages = bool(raw.get("include_user_messages", False))
    return CodexSessionsConfig(
        enabled=enabled,
        state_db_path=state_db_path,
        poll_interval_seconds=poll_interval_seconds,
        include_user_messages=include_user_messages,
    )


def _parse_security(payload: dict[str, Any]) -> SecurityConfig:
    raw = _lookup(payload, ("security",), default={})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("config.security must be an object")
    return SecurityConfig(
        allow_unlisted_chats=bool(raw.get("allow_unlisted_chats", False)),
        require_existing_repos=bool(raw.get("require_existing_repos", True)),
        require_git_repos=bool(raw.get("require_git_repos", False)),
    )


def _parse_runner(payload: dict[str, Any]) -> RunnerConfig:
    provider = str(_lookup(payload, ("runner", "provider"), default="codex")).strip().lower() or "codex"
    binary = _lookup(
        payload,
        ("runner", "binary"),
        ("codex_binary",),
        ("codex", "binary"),
        default=provider,
    )
    timeout_seconds = _lookup(
        payload,
        ("runner", "timeout_seconds"),
        ("codex_timeout_seconds",),
        ("codex", "timeout_seconds"),
        ("codex", "request_timeout_seconds"),
        default=0,
    )
    return RunnerConfig(
        provider=provider,
        binary=str(binary).strip() or provider,
        timeout_seconds=int(timeout_seconds),
    )


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError("config file must contain a JSON object")

    projects = _parse_projects(_lookup(payload, ("projects",), default=[]), path.parent)
    allowed_chat_ids = _as_int_set(
        _lookup(payload, ("allowed_chat_ids",), ("telegram", "allowed_chat_ids"), default=[])
    )
    default_project_name = _lookup(
        payload,
        ("default_project_name",),
        ("runtime", "default_project_name"),
        default=None,
    )
    if default_project_name is not None:
        default_project_name = str(default_project_name).strip() or None

    bot_token = _lookup(
        payload,
        ("bot_token",),
        ("telegram_bot_token",),
        ("telegram", "bot_token"),
        ("telegram", "token"),
        default="",
    )
    runner = _parse_runner(payload)
    poll_timeout_seconds = _lookup(
        payload,
        ("poll_timeout_seconds",),
        ("telegram", "poll_timeout_seconds"),
        default=20,
    )
    poll_sleep_seconds = _lookup(
        payload,
        ("poll_sleep_seconds",),
        ("poll_interval_seconds",),
        ("telegram", "poll_sleep_seconds"),
        ("telegram", "poll_interval_seconds"),
        default=2.0,
    )
    request_timeout_seconds = _lookup(
        payload,
        ("request_timeout_seconds",),
        ("telegram", "request_timeout_seconds"),
        default=30,
    )
    codex_sessions = _parse_codex_sessions(payload, path.parent)
    security = _parse_security(payload)
    log_path = _lookup(payload, ("log_file",), ("log_path",), ("runtime", "log_path"), default=None)
    state_path = _lookup(payload, ("state_file",), ("state_path",), ("runtime", "state_path"), default=None)
    max_output_chars = _lookup(payload, ("max_output_chars",), ("runtime", "max_output_chars"), default=1200)

    return AppConfig(
        projects=projects,
        telegram_bot_token=str(bot_token).strip(),
        allowed_chat_ids=allowed_chat_ids,
        runner=runner,
        codex_sessions=codex_sessions,
        security=security,
        poll_timeout_seconds=int(poll_timeout_seconds),
        poll_sleep_seconds=float(poll_sleep_seconds),
        request_timeout_seconds=int(request_timeout_seconds),
        log_file=_resolve_path(path.parent, str(log_path) if log_path is not None else None, "codex_connector.log"),
        state_file=_resolve_path(path.parent, str(state_path) if state_path is not None else None, "state.json"),
        default_project_name=default_project_name,
        max_output_chars=int(max_output_chars),
    )


def apply_overrides(config: AppConfig, *, state_path: str | Path | None = None, log_path: str | Path | None = None) -> AppConfig:
    updates: dict[str, Any] = {}
    if state_path is not None:
        updates["state_file"] = Path(state_path).expanduser().resolve()
    if log_path is not None:
        updates["log_file"] = Path(log_path).expanduser().resolve()
    if not updates:
        return config
    return replace(config, **updates)


def validate_config(config: AppConfig, *, for_serve: bool = False) -> None:
    if for_serve and not config.allowed_chat_ids and not config.security.allow_unlisted_chats:
        raise ConfigError(
            "allowed_chat_ids must be configured for serve mode unless security.allow_unlisted_chats is true"
        )

    for project in config.projects:
        repo_path = Path(project.repo_path).expanduser().resolve()
        if config.security.require_existing_repos and not repo_path.is_dir():
            raise ConfigError(f"project {project.name!r} repo_path does not exist: {repo_path}")
        if config.security.require_git_repos and not (repo_path / ".git").exists():
            raise ConfigError(f"project {project.name!r} is not a git repository: {repo_path}")
