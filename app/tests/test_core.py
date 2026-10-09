import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import runtime
from runtime import EXAMPLE, InstanceLock
from core import History, Processor, recognize


class FilterTests(unittest.TestCase):
    def test_original(self):
        self.assertTrue(recognize(EXAMPLE).accepted)

    def test_case_spacing_and_dash(self):
        text = EXAMPLE.upper().replace(" - ", " — ").replace("КОЛ-ВО", "КОЛИЧЕСТВО")
        self.assertTrue(recognize(text).accepted)

    def test_noise_and_incomplete(self):
        for text in ["+", "Кто может выйти 04.10?", "Я скопировал смету персонала", EXAMPLE.replace("Бармен - 3", "Бармен - 0"), EXAMPLE.replace("Основной запуск 18:30", "Основной запуск 99:99"), EXAMPLE.replace("04.10", "31.02")]:
            with self.subTest(text=text[:40]):
                self.assertFalse(recognize(text).accepted)

    def test_different_counts_and_date_format(self):
        self.assertTrue(recognize(EXAMPLE.replace("04.10", "4/11/2026").replace("Бармен - 3", "Барменов: 12")).accepted)


class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = History(Path(self.temp.name) / "history.sqlite3")
        self.now = datetime.now(timezone.utc)
        self.send = AsyncMock()

    async def asyncTearDown(self):
        self.history.close()
        self.temp.cleanup()

    def processor(self, **kwargs):
        return Processor(self.history, -123, 100, self.now - timedelta(seconds=1), **kwargs)

    async def call(self, p, **kwargs):
        args = dict(chat_id=-123, message_id=101, text=EXAMPLE, date=self.now,
                    sender_id=42, outgoing=False, send=self.send, now=self.now)
        args.update(kwargs)
        return await p.handle(**args)

    async def test_dry_never_sends_then_live_once(self):
        self.assertEqual(await self.call(self.processor()), "would_send")
        self.send.assert_not_awaited()
        live = self.processor(live=True)
        self.assertEqual(await self.call(live), "sent")
        self.assertEqual(await self.call(live), "duplicate")
        self.send.assert_awaited_once()
        self.history.close()
        self.history = History(Path(self.temp.name) / "history.sqlite3")
        self.assertEqual(await self.call(self.processor(live=True)), "duplicate")

    async def test_wrong_chat_own_old_sender_and_noise(self):
        p = self.processor(live=True, sender_ids=(42,))
        for changes in [dict(chat_id=-456), dict(outgoing=True), dict(message_id=99),
                        dict(date=self.now - timedelta(minutes=5)), dict(sender_id=43), dict(text="+")]:
            await self.call(p, **changes)
        self.send.assert_not_awaited()

    async def test_simultaneous_duplicates(self):
        p = self.processor(live=True)
        results = await asyncio.gather(self.call(p), self.call(p))
        self.assertCountEqual(results, ["sent", "duplicate"])
        self.send.assert_awaited_once()

    async def test_uncertain_send_not_retried(self):
        self.send.side_effect = TimeoutError()
        p = self.processor(live=True)
        self.assertEqual(await self.call(p), "failed_or_uncertain")
        self.assertEqual(await self.call(p), "duplicate")
        self.send.assert_awaited_once()

    async def test_flood_wait_respected_without_queue(self):
        class WaitError(Exception):
            seconds = 30
        self.send.side_effect = WaitError()
        p = self.processor(live=True)
        await self.call(p)
        self.assertEqual(await self.call(p, message_id=102), "rate_limited")
        self.send.assert_awaited_once()


class LockTests(unittest.TestCase):
    def test_second_instance_blocked_and_release(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, "DATA", Path(folder)):
            with InstanceLock():
                with self.assertRaises(RuntimeError):
                    with InstanceLock():
                        pass
            with InstanceLock():
                pass


if __name__ == "__main__":
    unittest.main()
