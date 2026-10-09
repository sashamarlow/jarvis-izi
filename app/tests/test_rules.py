import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from runtime import EXAMPLE, ROOT
from core import History, Processor
from rules import DEFAULT_RULES, RuleMatcher, load_rules


class RuleTests(unittest.TestCase):
    def config(self, **changes):
        value = copy.deepcopy(DEFAULT_RULES)
        value.update(changes)
        return value

    def test_shipped_rules_accept_sample_without_word_concert(self):
        self.assertEqual(json.loads((ROOT / 'rules.json').read_text(encoding='utf-8')), DEFAULT_RULES)
        self.assertTrue(load_rules(ROOT / 'rules.json')(EXAMPLE).accepted)
        self.assertNotIn('концерт', EXAMPLE.casefold())

    def test_conversation_is_not_an_announcement(self):
        matcher = RuleMatcher(DEFAULT_RULES)
        for text in ['Сегодня концерт!', 'Как прошёл концерт?', 'Я хочу + на концерт 04.10', '+']:
            self.assertFalse(matcher(text).accepted)

    def test_aliases_are_configurable(self):
        config = self.config()
        config['field_aliases']['launch'].append('начало концерта')
        changed = EXAMPLE.replace('Основной запуск', 'Начало концерта')
        self.assertTrue(RuleMatcher(config)(changed).accepted)
        self.assertFalse(RuleMatcher(DEFAULT_RULES)(changed).accepted)

    def test_required_keywords_all_and_any_keywords_one(self):
        matcher = RuleMatcher(self.config(required_keywords=['концерт', 'кухня нужна'], any_keywords=['рок', 'джаз']))
        self.assertFalse(matcher(EXAMPLE).accepted)
        self.assertFalse(matcher(EXAMPLE + '\nКонцерт поп').accepted)
        self.assertTrue(matcher(EXAMPLE + '\nКонцерт — РОК').accepted)

    def test_exclusions_are_literal_words_and_case_insensitive(self):
        matcher = RuleMatcher(self.config(excluded_keywords=['отмена']))
        self.assertFalse(matcher(EXAMPLE + '\nОТМЕНА').accepted)
        self.assertTrue(matcher(EXAMPLE + '\nБезотменный').accepted)
        literal = RuleMatcher(self.config(excluded_keywords=['.*']))
        self.assertTrue(literal(EXAMPLE).accepted)
        self.assertFalse(literal(EXAMPLE + '\n.*').accepted)

    def test_duplicate_additional_fields_do_not_raise_score(self):
        text = '04.10\nОсновной запуск 18:30\nКол-во гостей: 150\nСмета персонала:\nБармен - 3\n'
        matcher = RuleMatcher(DEFAULT_RULES)
        self.assertFalse(matcher(text + 'Публика возраст: 16+\nПублика: 16+').accepted)
        self.assertTrue(matcher(text + 'Публика возраст: 16+\nКухня нужна: да').accepted)

    def test_original_additional_labels_and_spacing(self):
        self.assertTrue(RuleMatcher(DEFAULT_RULES)(EXAMPLE.replace('Бар 2го', 'Бар 2-го')).accepted)
        self.assertTrue(RuleMatcher(DEFAULT_RULES)(EXAMPLE.replace('Кол-во', 'Кол - во')).accepted)

    def test_invalid_configs_fail_closed(self):
        bad = [self.config(schema_version=2), self.config(schema_version=True),
               self.config(required_keywords='концерт'), self.config(any_keywords=['']),
               self.config(minimum_additional_fields=0), self.config(minimum_additional_fields=True),
               self.config(field_aliases={}), self.config(additional_fields=[]),
               self.config(typo_keywords=[])]
        empty_alias = self.config()
        empty_alias['field_aliases']['launch'] = []
        bad.append(empty_alias)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RuleMatcher(value)

    def test_missing_and_invalid_json_do_not_silently_use_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rules.json'
            with self.assertRaises(ValueError):
                load_rules(path)
            path.write_text('{bad json}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_rules(path)


class CustomProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_matcher_controls_live_send(self):
        with tempfile.TemporaryDirectory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            try:
                config = copy.deepcopy(DEFAULT_RULES)
                config['excluded_keywords'] = ['отмена']
                now = datetime.now(timezone.utc)
                processor = Processor(history, -42, 100, now, live=True, matcher=RuleMatcher(config))
                send = AsyncMock()
                async def handle(mid, text):
                    return await processor.handle(-42, mid, text, now, None, False, send, now=now)
                self.assertEqual(await handle(101, EXAMPLE + '\nОтмена'), 'rejected')
                send.assert_not_awaited()
                self.assertEqual(await handle(102, EXAMPLE), 'sent')
                self.assertEqual(await handle(102, EXAMPLE), 'duplicate')
                send.assert_awaited_once()
            finally:
                history.close()


if __name__ == '__main__':
    unittest.main()
