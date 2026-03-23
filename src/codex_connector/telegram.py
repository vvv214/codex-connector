from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import parse, request


@dataclass(slots=True)
class TelegramUpdate:
    update_id: int
    chat_id: int
    text: str
    message_id: int | None = None
    kind: str = "message"
    callback_query_id: str | None = None


class TelegramApiError(RuntimeError):
    pass


class TelegramBotClient:
    _MAX_MESSAGE_CHARS = 4096
    _DEFAULT_COMMANDS: tuple[tuple[str, str], ...] = (
        ("project", "List projects or switch the active project"),
        ("new", "Start a fresh Codex session"),
        ("continue", "Continue the latest session"),
        ("last", "Show the latest task"),
        ("status", "Show active project and running state"),
        ("updates", "Toggle intermediate session updates"),
        ("help", "Show help"),
    )

    def __init__(self, token: str, timeout_seconds: int = 30):
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset: int | None = None, timeout: int = 20) -> list[TelegramUpdate]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        url = f"{self.base_url}/getUpdates?{parse.urlencode(params)}"
        payload = self._request_json(url)
        result = []
        for item in payload.get("result", []):
            callback = item.get("callback_query") or {}
            if callback:
                message = callback.get("message") or {}
                chat = message.get("chat") or {}
                data = callback.get("data")
                if data is None or chat.get("id") is None:
                    continue
                result.append(
                    TelegramUpdate(
                        update_id=int(item["update_id"]),
                        chat_id=int(chat["id"]),
                        text=str(data),
                        message_id=(int(message["message_id"]) if message.get("message_id") is not None else None),
                        kind="callback",
                        callback_query_id=str(callback.get("id") or ""),
                    )
                )
                continue
            message = item.get("message") or item.get("edited_message") or {}
            chat = message.get("chat") or {}
            text = message.get("text")
            if text is None:
                continue
            result.append(
                TelegramUpdate(
                    update_id=int(item["update_id"]),
                    chat_id=int(chat["id"]),
                    text=str(text),
                    message_id=(int(message["message_id"]) if message.get("message_id") is not None else None),
                )
            )
        return result

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        disable_notification: bool = False,
    ) -> None:
        chunks = self._chunk_text(text)
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if reply_to_message_id is not None and index == 0:
                payload["reply_to_message_id"] = reply_to_message_id
            if inline_keyboard is not None and index == len(chunks) - 1:
                payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard}, separators=(",", ":"))
            if disable_notification:
                payload["disable_notification"] = "true"
            data = parse.urlencode(payload).encode("utf-8")
            req = request.Request(f"{self.base_url}/sendMessage", data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            self._request_json(req)

    def answer_callback_query(self, callback_query_id: str) -> None:
        if not callback_query_id:
            return
        payload = {"callback_query_id": callback_query_id}
        data = parse.urlencode(payload).encode("utf-8")
        req = request.Request(f"{self.base_url}/answerCallbackQuery", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        self._request_json(req)

    def set_default_commands(self) -> None:
        payload = {
            "commands": json.dumps(
                [{"command": command, "description": description} for command, description in self._DEFAULT_COMMANDS],
                separators=(",", ":"),
            )
        }
        data = parse.urlencode(payload).encode("utf-8")
        req = request.Request(f"{self.base_url}/setMyCommands", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        self._request_json(req)

    def _chunk_text(self, text: str) -> list[str]:
        stripped = (text or "").strip()
        if not stripped:
            return [""]
        chunks: list[str] = []
        remaining = stripped
        while len(remaining) > self._MAX_MESSAGE_CHARS:
            split_at = self._best_split_index(remaining, self._MAX_MESSAGE_CHARS)
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _best_split_index(self, text: str, limit: int) -> int:
        window = text[:limit]
        for marker in ("\n\n", "\n", " "):
            idx = window.rfind(marker)
            if idx >= max(0, limit // 2):
                return idx + len(marker)
        return limit

    def _request_json(self, req_or_url: str | request.Request) -> dict[str, Any]:
        if isinstance(req_or_url, str):
            req = request.Request(req_or_url, method="GET")
        else:
            req = req_or_url
        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok", False):
            raise TelegramApiError(str(payload))
        return payload
