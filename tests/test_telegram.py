from __future__ import annotations

import json
import unittest
import sys
from pathlib import Path
from urllib import parse

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.telegram import TelegramBotClient


class RecordingTelegramBotClient(TelegramBotClient):
    def __init__(self) -> None:
        super().__init__("test-token")
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.payload: dict[str, object] = {"ok": True, "result": []}

    def _request_json(self, req_or_url):  # type: ignore[override]
        if isinstance(req_or_url, str):
            return self.payload
        body = req_or_url.data.decode("utf-8") if req_or_url.data else ""
        self.requests.append((req_or_url.full_url, dict(parse.parse_qsl(body))))
        return {"ok": True, "result": []}


class TelegramClientTests(unittest.TestCase):
    def test_get_updates_parses_callback_query(self) -> None:
        bot = RecordingTelegramBotClient()
        bot.payload = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "callback_query": {
                        "id": "cb-1",
                        "data": "project:alpha",
                        "message": {
                            "message_id": 9,
                            "chat": {"id": 42},
                        },
                    },
                }
            ],
        }

        updates = bot.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].kind, "callback")
        self.assertEqual(updates[0].text, "project:alpha")
        self.assertEqual(updates[0].callback_query_id, "cb-1")
        self.assertEqual(updates[0].chat_id, 42)

    def test_send_message_splits_long_text(self) -> None:
        bot = RecordingTelegramBotClient()
        text = ("A" * 3000) + "\n\n" + ("B" * 3000)

        bot.send_message(42, text)

        self.assertEqual(len(bot.requests), 2)
        first = bot.requests[0][1]["text"]
        second = bot.requests[1][1]["text"]
        self.assertLessEqual(len(first), 4096)
        self.assertLessEqual(len(second), 4096)
        self.assertEqual(first + "\n\n" + second, text)

    def test_send_message_attaches_inline_keyboard_to_last_chunk(self) -> None:
        bot = RecordingTelegramBotClient()
        keyboard = [[{"text": "alpha", "callback_data": "project:alpha"}]]
        text = ("A" * 3000) + "\n\n" + ("B" * 3000)

        bot.send_message(42, text, inline_keyboard=keyboard)

        self.assertEqual(len(bot.requests), 2)
        self.assertNotIn("reply_markup", bot.requests[0][1])
        self.assertIn("reply_markup", bot.requests[1][1])
        self.assertEqual(
            json.loads(bot.requests[1][1]["reply_markup"]),
            {"inline_keyboard": keyboard},
        )


if __name__ == "__main__":
    unittest.main()
