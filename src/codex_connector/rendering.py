from __future__ import annotations

from datetime import datetime, timezone

from .models import ChatState, Project, TaskRun


def tail_text(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


def _truncate_line(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 3:
        return collapsed[:limit]
    return f"{collapsed[: limit - 3]}..."


def _fmt_time(timestamp: float | None) -> str:
    if timestamp is None:
        return "n/a"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def render_help_text() -> str:
    return (
        "Commands:\n"
        "/project [name]  list active project and recent sessions, or switch active project\n"
        "/new [prompt]    start a new Codex session, or open a project picker\n"
        "/continue <prompt>  continue the latest session\n"
        "/last            show the latest task for the active project\n"
        "/status          show active project and running state\n"
        "/help            show this message\n"
        "Plain text is treated as /continue."
    )


def _render_session_overview(
    *,
    active_label: str,
    instruction: str,
    sessions: list[tuple[str, str, float]],
    max_chars: int,
    prefix: str | None,
) -> str:
    lines: list[str] = []
    if prefix:
        lines.append(prefix)
    lines.append(active_label)
    lines.append(instruction)
    if not sessions:
        lines.extend(["", "Recent sessions: none"])
        return "\n".join(lines)

    lines.extend(["", "Recent sessions:"])
    omitted = 0
    for index, (project_name, title, updated_at) in enumerate(sessions, start=1):
        line = f"{index}. {project_name} | {_fmt_time(updated_at)} | {_truncate_line(title, 32)}"
        candidate = "\n".join(lines + [line])
        if len(candidate) > max_chars - 32:
            omitted = len(sessions) - index + 1
            break
        lines.append(line)
    if omitted:
        lines.append(f"... {omitted} more")
    return "\n".join(lines)


def render_project_sessions(
    active_project_name: str | None,
    sessions: list[tuple[str, str, float]],
    max_chars: int = 4000,
    prefix: str | None = None,
) -> str:
    return _render_session_overview(
        active_label=f"Active project: {active_project_name or 'n/a'}",
        instruction="Tap a button below or use /project <name> to switch.",
        sessions=sessions,
        max_chars=max_chars,
        prefix=prefix,
    )


def render_new_task_picker(
    active_project_name: str | None,
    sessions: list[tuple[str, str, float]],
    max_chars: int = 4000,
    prefix: str | None = None,
) -> str:
    return _render_session_overview(
        active_label=f"New task project: {active_project_name or 'n/a'}",
        instruction="Tap a project below, then send the prompt for a fresh session.",
        sessions=sessions,
        max_chars=max_chars,
        prefix=prefix,
    )


def render_status(chat: ChatState | None, project: Project | None, task: TaskRun | None) -> str:
    if chat is None or project is None:
        return "No active project. Use /project <name> first."
    lines = [
        f"Project: {project.name}",
        f"Repo: {project.repo_path}",
        f"Last active: {_fmt_time(chat.last_active_at)}",
    ]
    if task is None:
        lines.append("Task: idle")
    else:
        lines.append(f"Task: {task.status} ({task.mode}) {task.task_id}")
        if task.status in {"queued", "running"}:
            lines.append("Task state: running")
        else:
            lines.append(f"Task finished: {_fmt_time(task.ended_at)}")
    return "\n".join(lines)


def render_last_task(task: TaskRun | None) -> str:
    if task is None:
        return "No task history for the active project."
    lines = [
        f"Task: {task.task_id}",
        f"Project: {task.project_name}",
        f"Mode: {task.mode}",
        f"Status: {task.status}",
        f"Started: {_fmt_time(task.started_at)}",
    ]
    if task.ended_at is not None:
        lines.append(f"Ended: {_fmt_time(task.ended_at)}")
    if task.return_code is not None:
        lines.append(f"Return code: {task.return_code}")
    if task.summary:
        lines.append("")
        lines.append(task.summary)
    return "\n".join(lines)


def render_task_result(task: TaskRun, max_chars: int = 4000) -> str:
    lines = [
        f"Project: {task.project_name}",
        f"Mode: {task.mode}",
        f"Status: {task.status}",
        f"Return code: {task.return_code if task.return_code is not None else 'n/a'}",
        f"Duration: {max(0.0, (task.ended_at or task.started_at) - task.started_at):.1f}s",
    ]
    if task.summary:
        lines.extend(["", task.summary])
    details = ""
    if task.status != "done":
        details = task.stderr_tail or task.stdout_tail
    if details:
        lines.extend(["", "Details", details])
    text = "\n".join(lines).strip()
    return tail_text(text, max_chars)
