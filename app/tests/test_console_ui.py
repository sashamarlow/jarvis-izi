import asyncio
import io
import json
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from console_ui import ACCENT, ConsoleLogHandler, ConsoleUI, PlainFileFormatter, safe_text
import web_app
from author_settings import AuthorStore


class PresentationTests(unittest.TestCase):
    def test_amber_header_and_confirmation(self):
        output = io.StringIO()
        ui = ConsoleUI(colors=True, stream=output, width=78)
        ui.header()
        ui.confirmation('Бармены', live=True)
        rendered = output.getvalue()
        self.assertIn(f'\x1b[38;2;{ACCENT[0]};{ACCENT[1]};{ACCENT[2]}m', rendered)
        self.assertIn('AMBER', rendered)
        self.assertIn('███████╗', rendered)
        self.assertIn('Включить автоматическую отправку + в группу «Бармены»?', rendered)
        self.assertIn('[1]', rendered)
        self.assertIn('[0]', rendered)
        self.assertNotIn('SEND', rendered)

    def test_connection_and_saved_group_without_api_text(self):
        output = io.StringIO()
        ui = ConsoleUI(stream=output, width=78)
        ui.connection({'title': 'Бармены', 'peer': -42})
        text = output.getvalue()
        self.assertIn('ПОДКЛЮЧЕНИЕ К TELEGRAM', text)
        self.assertIn('если Telegram попросит', text)
        self.assertIn('Последняя выбранная группа: Бармены', text)
        self.assertIn('[Enter]', text)
        self.assertNotIn('API ID', text)
        self.assertNotIn('\x1b', text)

    def test_narrow_console_has_compact_banner(self):
        output = io.StringIO()
        ui = ConsoleUI(stream=output, width=36)
        ui.header()
        self.assertIn('JARVIS / IZI', output.getvalue())

    def test_title_cannot_inject_escape_codes_or_new_lines(self):
        self.assertEqual(safe_text('Бар\x1b[2J\nмены\x07'), 'Бар мены')
        output = io.StringIO()
        ui = ConsoleUI(stream=output)
        ui.confirmation('Бар\x1b[31mмены\r\n[1] fake', True)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertIn('Бармены  [1] fake', output.getvalue())

    def test_logs_are_colored_but_file_formatter_is_plain(self):
        output = io.StringIO()
        ui = ConsoleUI(colors=True, stream=output)
        handler = ConsoleLogHandler(ui)
        record = logging.LogRecord('concert', logging.INFO, '', 0,
            '#101 | sent | 15.0 мс после получения | подходит', (), None)
        handler.emit(record)
        self.assertIn('SENT', output.getvalue())
        self.assertIn('+ отправлен', output.getvalue())
        self.assertIn('\x1b', output.getvalue())
        plain = PlainFileFormatter('%(asctime)s | %(message)s').format(record)
        self.assertNotIn('\x1b', plain)
        self.assertIn('#101 | sent', plain)

    def test_moscow_log_clock(self):
        record = logging.LogRecord('concert', logging.INFO, '', 0, 'test', (), None)
        record.created = 0
        self.assertEqual(PlainFileFormatter().formatTime(record), '1970-01-01 03:00:00')

    def test_menu_only_selects_explicit_numeric_modes(self):
        ui = ConsoleUI(stream=io.StringIO())
        for answer, expected in [('0', None), ('1', False), ('2', True)]:
            with self.subTest(answer=answer), patch.object(web_app, 'UI', ui), patch('builtins.input', return_value=answer):
                self.assertIs(web_app.choose_mode(), expected)
        with patch.object(web_app, 'UI', ui), patch('builtins.input', side_effect=['SEND', '', '2']):
            self.assertTrue(web_app.choose_mode())

    def test_preview_never_loads_session_rules_or_starts_browser(self):
        ui = ConsoleUI(stream=io.StringIO())
        with patch.object(sys, 'argv', ['web_app.py', '--preview']), \
             patch.object(web_app, 'initialize_terminal', return_value=ui), \
             patch.object(web_app, 'configure_logging') as configure, \
             patch.object(web_app, 'load_rules') as rules, \
             patch.object(web_app, 'previous_group') as group, \
             patch.object(web_app, 'run') as run, \
             patch.object(web_app, 'InstanceLock') as lock:
            self.assertEqual(web_app.main(), 0)
        configure.assert_not_called()
        rules.assert_not_called()
        group.assert_not_called()
        run.assert_not_called()
        lock.assert_not_called()


class FakeManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, live, answers, saved=None, error=None, author=42, author_peer=-42,
                       configure_only=False, settings_raw=None, choices=None):
        # Replaces Playwright entirely: no browser or account can be touched.
        page = SimpleNamespace(goto=AsyncMock(), evaluate=AsyncMock())
        context = SimpleNamespace(pages=[page], close=AsyncMock())
        launch = AsyncMock(return_value=context)
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        module = ModuleType('playwright.async_api')
        module.async_playwright = Mock(return_value=FakeManager(playwright))
        parent = ModuleType('playwright')
        parent.async_api = module
        output = io.StringIO()
        ui = ConsoleUI(stream=output, width=78)
        monitor = AsyncMock()
        async def call(page, method, *args):
            if method == 'inspect':
                return {'title': 'Test group', 'peer': -42}
            if method == 'authorMessages':
                return choices or []
            if method == 'selectAuthor':
                peer, mid, author_id = args
                self.assertEqual(peer, -42)
                choice = next(c for c in choices if c['mid'] == mid and c['author_id'] == author_id)
                return {'name': choice['name'], 'author_id': author_id}
            raise AssertionError(method)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            group_file = data / 'web-group.json'
            author_file = data / 'web-authors.json'
            if author is not None:
                AuthorStore(author_file).set(author_peer, author)
            if settings_raw is not None:
                author_file.write_text(settings_raw, encoding='utf-8')
            if saved:
                group_file.write_text(json.dumps(saved), encoding='utf-8')
            with patch.dict(sys.modules, {'playwright': parent, 'playwright.async_api': module}), \
                 patch.object(web_app, 'DATA', data), patch.object(web_app, 'GROUP_FILE', group_file), \
                 patch.object(web_app, 'AUTHOR_FILE', author_file), \
                 patch.object(web_app, 'UI', ui), patch.object(web_app, 'console', new=AsyncMock(side_effect=answers)), \
                 patch.object(web_app, 'dom_call', new=call), \
                 patch.object(web_app, 'monitor', new=monitor):
                if error:
                    with self.assertRaises(error):
                        await web_app.run(live, configure_only=configure_only)
                else:
                    await web_app.run(live, configure_only=configure_only)
            remembered = json.loads(group_file.read_text()) if group_file.exists() else None
            self.last_authors_text = author_file.read_text() if author_file.exists() else None
        context.close.assert_awaited_once()
        return monitor, remembered, output.getvalue()

    async def test_live_cancel_never_monitors_or_remembers(self):
        monitor, remembered, output = await self.run_fake(True, ['', '0'])
        monitor.assert_not_awaited()
        self.assertIsNone(remembered)
        self.assertIn('Отмена', output)

    async def test_legacy_send_blank_yes_and_other_digits_never_enable(self):
        monitor, remembered, _ = await self.run_fake(True, ['', 'SEND', '', 'да', '2', '0'])
        monitor.assert_not_awaited()
        self.assertIsNone(remembered)

    async def test_live_confirm_runs_only_selected_group(self):
        monitor, remembered, _ = await self.run_fake(True, ['', '1'])
        monitor.assert_awaited_once()
        self.assertEqual(monitor.call_args.args[1:3], (-42, True))
        self.assertEqual(monitor.call_args.args[3], 42)
        self.assertEqual(remembered, {'title': 'Test group', 'peer': -42})

    async def test_dry_confirm_and_cancel(self):
        monitor, remembered, _ = await self.run_fake(False, ['', '1'])
        monitor.assert_awaited_once()
        self.assertEqual(monitor.call_args.args[1:3], (-42, False))
        self.assertIsNotNone(remembered)
        monitor, remembered, _ = await self.run_fake(False, ['', '0'])
        monitor.assert_not_awaited()
        self.assertIsNone(remembered)

    async def test_eof_never_arms_and_closes_browser(self):
        monitor, remembered, _ = await self.run_fake(True, ['', EOFError()], error=EOFError)
        monitor.assert_not_awaited()
        self.assertIsNone(remembered)

    async def test_cancellation_never_arms_and_closes_browser(self):
        monitor, remembered, _ = await self.run_fake(True, ['', asyncio.CancelledError()], error=asyncio.CancelledError)
        monitor.assert_not_awaited()
        self.assertIsNone(remembered)

    async def test_group_change_warns_and_cancellation_keeps_old_selection(self):
        saved = {'peer': -43, 'title': 'Previous group'}
        monitor, remembered, output = await self.run_fake(True, ['', '0'], saved=saved)
        monitor.assert_not_awaited()
        self.assertEqual(remembered, saved)
        self.assertIn('другая группа', output)

    async def test_exit_before_group_inspection_never_monitors(self):
        monitor, remembered, _ = await self.run_fake(True, ['0'])
        monitor.assert_not_awaited()
        self.assertIsNone(remembered)

    async def test_first_start_requires_author_and_saves_only_on_confirmation(self):
        monitor, remembered, output = await self.run_fake(True, ['', '2', '123456789', '1'], author=None)
        monitor.assert_awaited_once()
        self.assertEqual(monitor.call_args.args[3], 123456789)
        self.assertEqual(json.loads(self.last_authors_text)['authors'], {'-42': 123456789})
        self.assertIn('Telegram ID 123456789', output)

    async def test_first_start_cancel_does_not_create_settings(self):
        for answers in (['', '0'], ['', '2', '123', '0']):
            with self.subTest(answers=answers):
                monitor, remembered, _ = await self.run_fake(True, answers, author=None)
                monitor.assert_not_awaited()
                self.assertIsNone(remembered)
                self.assertIsNone(self.last_authors_text)

    async def test_invalid_author_input_never_enables_or_saves(self):
        monitor, remembered, _ = await self.run_fake(True, ['', '2', '', '@name', '-42', '+79991234567', '1.5', '0'], author=None)
        monitor.assert_not_awaited()
        self.assertIsNone(self.last_authors_text)

    async def test_configuration_saves_without_arming(self):
        monitor, remembered, output = await self.run_fake(False, ['', '2', '456', '1'], configure_only=True)
        monitor.assert_not_awaited()
        self.assertEqual(json.loads(self.last_authors_text)['authors'], {'-42': 456})
        self.assertIn('Наблюдение и отправка не запускались', output)

    async def test_configuration_cancel_keeps_old_author(self):
        monitor, remembered, _ = await self.run_fake(False, ['', '2', '456', '0'], configure_only=True)
        monitor.assert_not_awaited()
        self.assertEqual(json.loads(self.last_authors_text)['authors'], {'-42': 42})

    async def test_other_group_requires_its_own_author(self):
        monitor, remembered, _ = await self.run_fake(True, ['', '0'], author_peer=-43)
        monitor.assert_not_awaited()
        self.assertEqual(json.loads(self.last_authors_text)['authors'], {'-43': 42})

    async def test_corrupted_settings_never_arm_or_reset(self):
        monitor, remembered, _ = await self.run_fake(True, [''], settings_raw='broken', error=ValueError)
        monitor.assert_not_awaited()
        self.assertEqual(self.last_authors_text, 'broken')

    async def test_eof_during_author_entry_closes_without_saving(self):
        monitor, remembered, _ = await self.run_fake(True, ['', EOFError()], author=None, error=EOFError)
        monitor.assert_not_awaited()
        self.assertIsNone(self.last_authors_text)

    async def test_setup_menu_selects_configuration_not_live(self):
        with patch.object(web_app, 'UI', ConsoleUI(stream=io.StringIO())), patch('builtins.input', return_value='3'):
            self.assertEqual(web_app.choose_mode(), 'configure_author')

    async def test_select_author_by_message_and_save_without_monitoring(self):
        choices = [dict(mid=101, author_id=987654, name='Старший бармен', preview='Объявление о смене')]
        monitor, _, output = await self.run_fake(False, ['', '1', '1', '1'],
                                                 configure_only=True, choices=choices)
        monitor.assert_not_awaited()
        self.assertEqual(json.loads(self.last_authors_text)['authors'], {'-42': 987654})
        self.assertIn('Старший бармен', output)
        self.assertIn('Объявление о смене', output)

    async def test_selected_message_cancel_does_not_replace_author(self):
        choices = [dict(mid=101, author_id=987654, name='Старший бармен', preview='Смена')]
        monitor, _, _ = await self.run_fake(False, ['', '1', '1', '0'],
                                           configure_only=True, choices=choices)
        monitor.assert_not_awaited()
        self.assertEqual(json.loads(self.last_authors_text)['authors'], {'-42': 42})

    async def test_empty_or_invalid_message_selection_can_refresh_and_cancel(self):
        monitor, _, _ = await self.run_fake(False, ['', '1', '1', 'abc', '', '0'],
                                           author=None, configure_only=True, choices=[])
        monitor.assert_not_awaited()
        self.assertIsNone(self.last_authors_text)

    async def test_selection_for_first_live_start_requires_final_confirmation(self):
        choices = [dict(mid=101, author_id=987654, name='Старший бармен', preview='Смена')]
        monitor, _, _ = await self.run_fake(True, ['', '1', '1', '1'], author=None, choices=choices)
        self.assertEqual(monitor.call_args.args[3], 987654)

    async def test_changed_selection_refreshes_without_saving_or_guessing(self):
        choice = dict(mid=101, author_id=987654, name='Старший бармен', preview='Смена')
        outputs = [[choice], RuntimeError('Автор изменился'), []]
        with patch.object(web_app, 'UI', ConsoleUI(stream=io.StringIO())), \
             patch.object(web_app, 'console', new=AsyncMock(side_effect=['1', '1', '0'])), \
             patch.object(web_app, 'dom_call', new=AsyncMock(side_effect=outputs)):
            self.assertIsNone(await web_app.choose_author(Mock(), -42))


if __name__ == '__main__':
    unittest.main()
