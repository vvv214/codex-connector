from __future__ import annotations

import tempfile
import time
import unittest
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_connector.telegram_runtime import TelegramRuntimeStore


class TelegramRuntimeStoreTests(unittest.TestCase):
    def test_persists_poll_offset_and_processed_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime.sqlite3"
            store = TelegramRuntimeStore(path)

            self.assertIsNone(store.get_next_poll_offset())
            self.assertFalse(store.is_update_processed(10))

            store.set_next_poll_offset(11)
            store.mark_update_processed(10)

            restored = TelegramRuntimeStore(path)
            self.assertEqual(restored.get_next_poll_offset(), 11)
            self.assertTrue(restored.is_update_processed(10))

    def test_outbox_deduplicates_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime.sqlite3"
            store = TelegramRuntimeStore(path)

            first_id = store.enqueue_message(
                chat_id=42,
                text="hello",
                dedupe_key="reply:10",
            )
            second_id = store.enqueue_message(
                chat_id=42,
                text="hello",
                dedupe_key="reply:10",
            )
            self.assertEqual(first_id, second_id)

            messages = store.get_due_messages()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].text, "hello")

            store.mark_message_retry(first_id, error="temporary", delay_seconds=5, retry_at=time.time() + 5)
            self.assertEqual(store.get_due_messages(), [])

            store.mark_message_retry(first_id, error="temporary", delay_seconds=0, retry_at=time.time())
            messages = store.get_due_messages()
            self.assertEqual(len(messages), 1)
            self.assertGreaterEqual(messages[0].attempts, 2)

            store.mark_message_sent(first_id)
            self.assertEqual(store.pending_message_count(), 0)

    def test_recreates_schema_if_db_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime.sqlite3"
            store = TelegramRuntimeStore(path)
            path.unlink()

            self.assertEqual(store.get_due_messages(), [])

            message_id = store.enqueue_message(chat_id=7, text="hello again")
            messages = store.get_due_messages()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].id, message_id)


if __name__ == "__main__":
    unittest.main()
