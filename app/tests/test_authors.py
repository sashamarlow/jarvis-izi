from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from author_settings import AuthorStore, MAX_SAFE_ID, parse_author_id, valid_author_id
from core import History, Processor
from runtime import EXAMPLE
import web_app


class AuthorSettingsTests(unittest.TestCase):
    def test_numeric_id_and_surrounding_spaces(self):
        self.assertEqual(parse_author_id(' 123456789 '), 123456789)
        self.assertEqual(parse_author_id(str(MAX_SAFE_ID)), MAX_SAFE_ID)

    def test_reject_username_phone_sign_group_decimal_unicode_and_overflow(self):
        for value in ('', '0', '-42', '@alex', '+79991234567', '12 34', '1.2', '1e9', '١٢٣', '１２３', str(MAX_SAFE_ID + 1), '9'*40):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_author_id(value)

    def test_reject_boolean_and_non_integer_ids(self):
        for value in (True, False, '42', 42.0, None, -42, 0, MAX_SAFE_ID + 1):
            self.assertFalse(valid_author_id(value))

    def test_per_group_persistence_and_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'web-authors.json'
            store = AuthorStore(path)
            self.assertIsNone(store.get(-42))
            self.assertFalse(path.exists())
            store.set(-42, 123)
            store.set(-43, 456)
            self.assertEqual(AuthorStore(path).get(-42), 123)
            store.set(-42, 789)
            self.assertEqual(store.get(-43), 456)
            self.assertEqual(store.get(-42), 789)
            self.assertIsNone(store.get(-44))
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_corruption_is_not_silently_reset(self):
        invalid = [None, [], {}, {'schema_version': True, 'authors': {}},
                   {'schema_version': 2, 'authors': {}}, {'schema_version': 1, 'authors': []},
                   {'schema_version': 1, 'authors': {'-42': True}},
                   {'schema_version': 1, 'authors': {'-42': '123'}},
                   {'schema_version': 1, 'authors': {'-42': -123}},
                   {'schema_version': 1, 'authors': {'42': 123}},
                   {'schema_version': 1, 'authors': {'-0': 123}},
                   {'schema_version': 1, 'authors': {str(-MAX_SAFE_ID - 1): 123}},
                   {'schema_version': 1, 'authors': {}, 'extra': 1}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'web-authors.json'
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    original = path.read_bytes()
                    with self.assertRaises(ValueError):
                        AuthorStore(path).get(-42)
                    with self.assertRaises(ValueError):
                        AuthorStore(path).set(-42, 123)
                    self.assertEqual(path.read_bytes(), original)

    def test_invalid_pair_never_creates_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'web-authors.json'
            for peer, author in ((42, 123), (0, 123), (-42, 0), (-42, True), (True, 123)):
                with self.subTest(peer=peer, author=author), self.assertRaises(ValueError):
                    AuthorStore(path).set(peer, author)
            self.assertFalse(path.exists())


class AuthorFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_or_unknown_author_skips_before_text_matching(self):
        with tempfile.TemporaryDirectory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            try:
                now = datetime.now(timezone.utc)
                matcher = Mock()
                sender = AsyncMock()
                processor = Processor(history, -42, 100, now, live=True, sender_ids=(123,), matcher=matcher)
                for author in (None, 456):
                    self.assertEqual(await processor.handle(-42, 101, EXAMPLE, now, author, False, sender), 'wrong_sender')
                matcher.assert_not_called()
                sender.assert_not_awaited()
            finally:
                history.close()

    async def test_selected_author_still_needs_announcement(self):
        with tempfile.TemporaryDirectory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            try:
                now = datetime.now(timezone.utc)
                sender = AsyncMock()
                processor = Processor(history, -42, 100, now, live=True, sender_ids=(123,))
                self.assertEqual(await processor.handle(-42, 101, 'Всем привет', now, 123, False, sender), 'rejected')
                sender.assert_not_awaited()
                self.assertEqual(await processor.handle(-42, 102, EXAMPLE, now, 123, False, sender), 'sent')
                self.assertEqual(await processor.handle(-42, 102, EXAMPLE, now, 123, False, sender), 'duplicate')
                sender.assert_awaited_once()
            finally:
                history.close()

    async def test_monitor_never_arms_without_author(self):
        for author in (None, 0, True, -42, '123'):
            with self.subTest(author=author), patch.object(web_app, 'dom_call', new=AsyncMock()) as call:
                with self.assertRaises(ValueError):
                    await web_app.monitor(Mock(), -42, True, author)
                call.assert_not_awaited()

    async def test_monitor_passes_only_selected_author_and_message_to_send(self):
        now = int(datetime.now(timezone.utc).timestamp())
        baseline = {'baseline': 100, 'since': now}
        events = [
            {'mid': 101, 'date': now, 'text': EXAMPLE, 'sender_id': None},
            {'mid': 102, 'date': now, 'text': EXAMPLE, 'sender_id': 456},
            {'mid': 103, 'date': now, 'text': EXAMPLE, 'sender_id': 123},
        ]
        page = Mock(url=web_app.URL)
        page.is_closed.return_value = False
        delivered = False
        async def dom(page, method, *args):
            nonlocal delivered
            if method == 'arm':
                self.assertEqual(args, (-42, 123))
                return baseline
            if method == 'stop':
                return None
            self.assertEqual(method, 'nextEvents')
            if delivered:
                raise RuntimeError('end test')
            delivered = True
            return events
        with tempfile.TemporaryDirectory() as folder, patch.object(web_app, 'DATA', Path(folder)), \
             patch.object(web_app, 'dom_call', new=dom), patch.object(web_app, 'send_plus', new=AsyncMock()) as send:
            with self.assertRaisesRegex(RuntimeError, 'end test'):
                await web_app.monitor(page, -42, True, 123)
            send.assert_awaited_once_with(page, now, message_id=103, author_id=123)


if __name__ == '__main__':
    unittest.main()
