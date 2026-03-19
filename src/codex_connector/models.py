from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Project:
    name: str
    repo_path: str
    branch: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class CodexSessionsConfig:
    enabled: bool = False
    state_db_path: Path = field(default_factory=lambda: Path.home() / ".codex" / "state_5.sqlite")
    poll_interval_seconds: float = 2.0
    include_user_messages: bool = False


@dataclass(slots=True)
class SecurityConfig:
    allow_unlisted_chats: bool = False
    require_existing_repos: bool = True
    require_git_repos: bool = False


@dataclass(slots=True)
class ChatState:
    chat_id: int
    project_name: str
    repo_path: str
    last_active_at: float
    current_task_id: str | None = None
    active_project_name: str | None = None
    pending_mode: str | None = None


@dataclass(slots=True)
class TaskRun:
    task_id: str
    chat_id: int
    project_name: str
    prompt: str
    mode: str
    status: str
    started_at: float
    ended_at: float | None = None
    return_code: int | None = None
    summary: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


@dataclass(slots=True)
class AppConfig:
    projects: list[Project]
    telegram_bot_token: str = ""
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    codex_binary: str = "codex"
    codex_timeout_seconds: int = 0
    codex_sessions: CodexSessionsConfig = field(default_factory=CodexSessionsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    poll_timeout_seconds: int = 20
    poll_sleep_seconds: float = 2.0
    request_timeout_seconds: int = 30
    log_file: Path = Path("codex_connector.log")
    state_file: Path = Path("state.json")
    default_project_name: str | None = None
    max_output_chars: int = 1200

    def project_by_name(self, name: str) -> Project | None:
        for project in self.projects:
            if project.name == name:
                return project
        return None

    def default_project(self) -> Project | None:
        if self.default_project_name:
            project = self.project_by_name(self.default_project_name)
            if project is not None:
                return project
        return self.projects[0] if self.projects else None

    def project_by_repo_path(self, repo_path: str) -> Project | None:
        path = Path(repo_path).resolve()
        best_match: Project | None = None
        best_match_len = -1

        for project in self.projects:
            project_path = Path(project.repo_path).resolve()
            if path == project_path or path.is_relative_to(project_path):
                # Check for direct match first, or if the project_path is a parent
                # We want the longest project_path (most specific)
                if len(str(project_path)) > best_match_len:
                    best_match_len = len(str(project_path))
                    best_match = project
        return best_match
