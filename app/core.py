"""Local recognition and at-most-once processing; no Telegram dependency."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import time
from typing import Awaitable, Callable

from rules import Match, recognize

LOG = logging.getLogger("concert")


class History:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS decisions (
            chat_id INTEGER, message_id INTEGER, mode TEXT, status TEXT,
            reason TEXT, created_at TEXT, elapsed_ms REAL,
            PRIMARY KEY(chat_id, message_id, mode))""")
        self.db.commit()

    def reserve(self, chat_id: int, message_id: int, mode: str, reason: str) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO decisions VALUES (?, ?, ?, 'reserved', ?, ?, NULL)",
            (chat_id, message_id, mode, reason, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def finish(self, chat_id: int, message_id: int, mode: str, status: str, reason: str, elapsed_ms: float):
        self.db.execute(
            "UPDATE decisions SET status=?, reason=?, elapsed_ms=? WHERE chat_id=? AND message_id=? AND mode=?",
            (status, reason, elapsed_ms, chat_id, message_id, mode),
        )
        self.db.commit()

    def close(self):
        self.db.close()


class Processor:
    def __init__(self, history: History, chat_id: int, baseline: int, armed_at: datetime,
                 live: bool = False, max_age: int = 60, sender_ids: tuple[int, ...] = (),
                 matcher: Callable[[str], Match] = recognize):
        self.history = history
        self.chat_id = chat_id
        self.baseline = baseline
        self.armed_at = armed_at
        self.live = live
        self.max_age = max_age
        self.sender_ids = sender_ids
        self.matcher = matcher
        self.lock = asyncio.Lock()
        self.cooldown_until = 0.0

    async def handle(self, chat_id: int, message_id: int, text: str, date: datetime,
                     sender_id: int | None, outgoing: bool,
                     send: Callable[[], Awaitable[None]], now: datetime | None = None) -> str:
        started = time.perf_counter()
        if chat_id != self.chat_id or outgoing:
            return "ignored"
        age = ((now or datetime.now(timezone.utc)) - date).total_seconds()
        if message_id <= self.baseline or date < self.armed_at or age > self.max_age or age < -5:
            return "stale"
        if self.sender_ids and sender_id not in self.sender_ids:
            return "wrong_sender"
        match = self.matcher(text)
        mode = "live" if self.live else "dry"
        async with self.lock:
            if not self.history.reserve(chat_id, message_id, mode, match.reason):
                return "duplicate"
            reason = match.reason
            if not match.accepted:
                status = "rejected"
            elif not self.live:
                status = "would_send"
                LOG.info("[ТЕСТ] Объявление #%s подходит — отправил бы +", message_id)
            elif time.monotonic() < self.cooldown_until:
                status, reason = "rate_limited", "Telegram ранее потребовал паузу; поздний + не отправляется"
            elif ((now or datetime.now(timezone.utc)) - date).total_seconds() > self.max_age:
                status, reason = "stale", "объявление устарело во время обработки"
            else:
                try:
                    await send()
                    status = "sent"
                    LOG.info("[ОТПРАВЛЕНО] + для объявления #%s", message_id)
                except Exception as exc:
                    # Never queue an old + or blindly retry an uncertain network result.
                    seconds = getattr(exc, "seconds", None)
                    if isinstance(seconds, int):
                        self.cooldown_until = time.monotonic() + seconds
                    status = "failed_or_uncertain"
                    reason = type(exc).__name__ + (f"; пауза {seconds} сек." if seconds is not None else "")
                    LOG.error("Не удалось подтвердить отправку для #%s: %s. Автоповтора нет.", message_id, reason)
            elapsed = (time.perf_counter() - started) * 1000
            self.history.finish(chat_id, message_id, mode, status, reason, elapsed)
            LOG.info("#%s | %s | %.1f мс после получения | %s", message_id, status, elapsed, reason)
            return status
