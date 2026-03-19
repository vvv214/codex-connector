from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path

from .models import ChatState, TaskRun


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._chats: dict[int, ChatState] = {}
        self._tasks: dict[str, TaskRun] = {}

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._chats = {}
                self._tasks = {}
                return
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            chats: dict[int, ChatState] = {}
            for key, raw in payload.get("chats", {}).items():
                chat = ChatState(
                    chat_id=int(raw["chat_id"]),
                    project_name=str(raw["project_name"]),
                    repo_path=str(raw["repo_path"]),
                    last_active_at=float(raw["last_active_at"]),
                    current_task_id=raw.get("current_task_id"),
                    active_project_name=raw.get("active_project_name"),
                    pending_mode=raw.get("pending_mode"),
                )
                chats[int(key)] = chat
            tasks: dict[str, TaskRun] = {}
            for raw in payload.get("tasks", []):
                task = TaskRun(
                    task_id=str(raw["task_id"]),
                    chat_id=int(raw["chat_id"]),
                    project_name=str(raw["project_name"]),
                    prompt=str(raw["prompt"]),
                    mode=str(raw["mode"]),
                    status=str(raw["status"]),
                    started_at=float(raw["started_at"]),
                    ended_at=(float(raw["ended_at"]) if raw.get("ended_at") is not None else None),
                    return_code=(int(raw["return_code"]) if raw.get("return_code") is not None else None),
                    summary=str(raw.get("summary", "")),
                    stdout_tail=str(raw.get("stdout_tail", "")),
                    stderr_tail=str(raw.get("stderr_tail", "")),
                    error=raw.get("error"),
                )
                tasks[task.task_id] = task
            self._chats = chats
            self._tasks = tasks

    def save(self) -> None:
        with self._lock:
            payload = {
                "chats": {str(chat_id): asdict(chat) for chat_id, chat in self._chats.items()},
                "tasks": [asdict(task) for task in self._tasks.values()],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def get_chat(self, chat_id: int) -> ChatState | None:
        with self._lock:
            return self._chats.get(chat_id)

    def chat_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._chats.keys())

    def set_chat(self, chat: ChatState) -> None:
        with self._lock:
            self._chats[chat.chat_id] = chat
            self.save()

    def upsert_chat(
        self,
        chat_id: int,
        project_name: str | None = None,
        repo_path: str | None = None,
        last_active_at: float | None = None,
        current_task_id: str | None = None,
        active_project_name: str | None = None,
    ) -> ChatState:
        with self._lock:
            chat = self._chats.get(chat_id)

            if chat is None:
                if project_name is None or repo_path is None or last_active_at is None:
                    raise ValueError(
                        "project_name, repo_path, and last_active_at are required for new chat state"
                    )
                chat = ChatState(
                    chat_id=chat_id,
                    project_name=project_name,
                    repo_path=repo_path,
                    last_active_at=last_active_at,
                    current_task_id=current_task_id,
                    active_project_name=active_project_name,
                )
            else:
                if project_name is not None:
                    chat.project_name = project_name
                if repo_path is not None:
                    chat.repo_path = repo_path
                if last_active_at is not None:
                    chat.last_active_at = last_active_at
                if current_task_id is not None:
                    chat.current_task_id = current_task_id
                if active_project_name is not None:
                    chat.active_project_name = active_project_name

            self._chats[chat_id] = chat
            self.save()
            return chat

    def set_chat_task(self, chat_id: int, task_id: str | None, last_active_at: float | None = None) -> None:
        with self._lock:
            chat = self._chats.get(chat_id)
            if chat is None:
                raise KeyError(f"unknown chat {chat_id}")
            chat.current_task_id = task_id
            if last_active_at is not None:
                chat.last_active_at = last_active_at
            self._chats[chat_id] = chat
            self.save()

    def add_task(self, task: TaskRun) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            self.save()

    def set_chat_pending_mode(self, chat_id: int, pending_mode: str | None) -> None:
        with self._lock:
            chat = self._chats.get(chat_id)
            if chat is None:
                raise KeyError(f"unknown chat {chat_id}")
            chat.pending_mode = pending_mode
            self._chats[chat_id] = chat
            self.save()

    def update_task(self, task: TaskRun) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            self.save()

    def get_task(self, task_id: str) -> TaskRun | None:
        with self._lock:
            return self._tasks.get(task_id)

    def tasks_for_project(self, project_name: str) -> list[TaskRun]:
        with self._lock:
            return [task for task in self._tasks.values() if task.project_name == project_name]

    def last_task_for_project(self, project_name: str) -> TaskRun | None:
        tasks = self.tasks_for_project(project_name)
        if not tasks:
            return None
        return max(tasks, key=lambda task: task.started_at)

    def running_task_for_chat(self, chat_id: int) -> TaskRun | None:
        with self._lock:
            chat = self._chats.get(chat_id)
            if chat is None or not chat.current_task_id:
                return None
            task = self._tasks.get(chat.current_task_id)
            if task and task.status in {"queued", "running"}:
                return task
            return None

    def get_recent_sessions(
        self, chat_id: int, project_name: str | None = None, limit: int = 5
    ) -> list[TaskRun]:
        with self._lock:
            sessions = [
                task
                for task in self._tasks.values()
                if task.chat_id == chat_id and (project_name is None or task.project_name == project_name)
            ]
            sessions.sort(key=lambda task: task.started_at, reverse=True)
            return sessions[:limit]
