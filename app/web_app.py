"""Local Telegram Web K automation with explicit numeric confirmation."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import json
import sys
import time

from runtime import DATA, ROOT, InstanceLock
from author_settings import AuthorStore, parse_author_id, valid_author_id
from console_ui import ConsoleUI, MUTED, configure_logging, initialize_terminal
from core import History, Processor
from rules import RuleMatcher, load_rules

LOG = logging.getLogger("concert")
DOM = (ROOT / "web_dom.js").read_text(encoding="utf-8")
URL = "https://web.telegram.org/k/"
RULES_FILE = ROOT / "rules.json"
GROUP_FILE = DATA / "web-group.json"
AUTHOR_FILE = DATA / "web-authors.json"
UI = ConsoleUI()


async def console(prompt: str = "Твой выбор") -> str:
    # Keep the browser event loop responsive while awaiting user input.
    return await asyncio.to_thread(input, UI.prompt(prompt))


async def confirm_start(info: dict, live: bool, author_id: int) -> bool:
    UI.confirmation(info["title"], live, author_id)
    while True:
        answer = (await console()).strip()
        if answer == "1":
            return True
        if answer == "0":
            UI.event("STOP", "Отмена. Наблюдение не запущено, отправка выключена.")
            return False
        UI.event("WARN", "Введи 1 для подтверждения или 0 для отмены.")


async def dom_call(page, method: str, *args):
    """Return our own safe diagnostics instead of Playwright's verbose call logs."""
    result = await page.evaluate("""async ({method, args}) => {
      try { return {ok: true, value: await window.__concertWeb[method](...args)}; }
      catch(e) { return {ok: false, error: e.message}; }
    }""", {"method": method, "args": list(args)})
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return result.get("value")


async def send_plus(page, message_date: int | None = None, *, message_id: int, author_id: int):
    peer = await dom_call(page, "preflight", message_id, author_id)
    # Bind the locator to the selected peer, not whichever chat becomes visible.
    composer = page.locator(f'.chat-input-main [contenteditable="true"][data-peer-id="{peer}"]:visible')
    await composer.fill("+", timeout=3000)
    # Browser-side observers wait for the button and acknowledgment in one call.
    timing = await dom_call(page, "sendPrepared", message_date, message_id, author_id)
    LOG.info("#%s · со страницы до клика %.1f мс · подготовка %.1f мс · после клика до подтверждения %.1f мс",
             message_id, timing["first_seen_to_click_ms"], timing["detection_ms"], timing["click_to_ack_ms"],
             extra={"ui_status": "INFO"})
    return timing


async def monitor(page, peer: int, live: bool, author_id: int, matcher: RuleMatcher | None = None):
    if not valid_author_id(author_id):
        raise ValueError("Не задан корректный Telegram ID автора. Наблюдение не запущено.")
    matcher = matcher or load_rules(RULES_FILE)
    baseline = await dom_call(page, "arm", peer, author_id)
    history = History(DATA / "web-history.sqlite3")
    processor = Processor(history, peer, baseline["baseline"],
                          datetime.fromtimestamp(baseline["since"], timezone.utc), live=live,
                          matcher=matcher, sender_ids=(author_id,))
    UI.section("Журнал событий")
    LOG.info("%s. Слушаю новые объявления. Остановка: Ctrl+C.",
             "Отправка включена" if live else "Тест без отправки", extra={"ui_status": "READY"})
    UI.line("Не переключай беседы и не печатай в управляемом окне Edge.", color=MUTED)
    last_wake = time.monotonic()
    try:
        while True:
            if time.monotonic() - last_wake > 60:
                raise RuntimeError("Длительная пауза/сон компьютера. Перезапусти программу; старые плюсы не отправляю.")
            last_wake = time.monotonic()
            if page.is_closed() or not page.url.startswith(URL):
                raise RuntimeError("Вкладка закрыта или адрес изменился. Наблюдение остановлено.")
            # Resolves immediately when an observer queues a message. The timeout is
            # only a health heartbeat; it never scans messages or delays an event.
            events = await dom_call(page, "nextEvents", 1000)
            if time.monotonic() - last_wake > 60:
                raise RuntimeError("Длительная пауза/сон компьютера. Перезапусти программу.")
            for event in events:
                event_author = event.get("sender_id")
                if not valid_author_id(event_author):
                    LOG.warning("Сообщение #%s: автор пока не определён. Плюс не отправляется.",
                                event["mid"], extra={"ui_status": "SKIP"})
                    continue
                status = await processor.handle(
                    peer, event["mid"], event["text"],
                    datetime.fromtimestamp(event["date"], timezone.utc), event_author, False,
                    lambda: send_plus(page, event["date"], message_id=event["mid"], author_id=author_id))
                if status == "wrong_sender":
                    LOG.info("Сообщение #%s пропущено: другой автор (Telegram ID %s).",
                             event["mid"], event_author, extra={"ui_status": "SKIP"})
                if status == "failed_or_uncertain":
                    raise RuntimeError("Отправка не подтверждена. Остановлено без повторного клика. Проверь чат вручную.")
                if status == "would_send" and "detection_ms" in event:
                    LOG.info("#%s · обнаружение и подготовка на странице %.1f мс · тест без клика",
                             event["mid"], event["detection_ms"], extra={"ui_status": "INFO"})
    finally:
        history.close()
        try:
            await dom_call(page, "stop")
        except Exception:
            pass  # A closed/navigated page has already discarded its observers.


def remember_group(info: dict):
    # This runtime state contains only a selected peer/title, never auth tokens.
    temporary = GROUP_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(GROUP_FILE)


def previous_group() -> dict | None:
    if not GROUP_FILE.exists():
        return None
    try:
        value = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
        if isinstance(value, dict) and type(value.get("peer")) is int and value["peer"] < 0 and isinstance(value.get("title"), str):
            return value
    except (OSError, ValueError):
        pass
    UI.event("WARN", "Сохранённую беседу прочитать не удалось. Выбери группу заново.")
    return None


async def enter_author_id(current: int | None = None) -> int | None:
    UI.section("Telegram ID автора")
    if current is not None:
        UI.line(f"Текущий ID: {current}")
    UI.line("Введи числовой ID человека, который публикует объявления.")
    UI.line("Не @username, не телефон и не ID группы. 0 — отмена.", color=MUTED)
    while True:
        answer = await console("Telegram ID автора")
        if answer.strip() == "0":
            UI.event("STOP", "Отмена. Настройка автора не изменена, наблюдение не запущено.")
            return None
        try:
            return parse_author_id(answer)
        except ValueError as exc:
            UI.event("WARN", str(exc))


async def choose_author(page, peer: int, current: int | None = None) -> int | None:
    UI.author_menu(current)
    while True:
        mode = (await console()).strip()
        if mode == "0":
            UI.event("STOP", "Отмена. Настройка автора не изменена.")
            return None
        if mode == "2":
            return await enter_author_id(current)
        if mode == "1":
            break
        UI.event("WARN", "Введи 1, 2 или 0.")
    while True:
        choices = await dom_call(page, "authorMessages", peer)
        UI.author_messages(choices)
        while True:
            answer = (await console("Номер сообщения / Enter — обновить / 0 — отмена")).strip()
            if answer == "0":
                return None
            if answer == "":
                break
            if len(answer) > 2 or not answer.isascii() or not answer.isdigit() or not 1 <= int(answer) <= len(choices):
                UI.event("WARN", "Выбери номер из списка, Enter для обновления или 0 для отмены.")
                continue
            chosen = choices[int(answer) - 1]
            try:
                verified = await dom_call(page, "selectAuthor", peer, chosen["mid"], chosen["author_id"])
            except RuntimeError as exc:
                UI.event("WARN", str(exc))
                break
            if not valid_author_id(verified.get("author_id")):
                raise ValueError("Автор не подтверждён. Настройка не сохранена.")
            UI.event("INFO", f"Выбран автор: {verified['name']} · Telegram ID {verified['author_id']}")
            return verified["author_id"]


async def run(live: bool, configure_only: bool = False):
    from playwright.async_api import async_playwright
    matcher = None if configure_only else load_rules(RULES_FILE)
    DATA.mkdir(exist_ok=True)
    UI.header("НАСТРОЙКА АВТОРА" if configure_only else
              "АВТОМАТИЧЕСКАЯ ОТПРАВКА +" if live else "ТЕСТ / БЕЗ ОТПРАВКИ")
    saved = previous_group()
    UI.connection(saved)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(DATA / "web-profile"), channel="msedge", headless=False,
            viewport={"width": 1280, "height": 850},
            args=["--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            while True:
                answer = (await console("Готово? Нажми Enter или введи 0 для выхода")).strip()
                if answer == "0":
                    UI.event("STOP", "Выход. Наблюдение не запущено.")
                    return
                if answer:
                    UI.event("WARN", "Нажми Enter для продолжения или введи 0 для выхода.")
                    continue
                try:
                    await page.evaluate(DOM)
                    info = await dom_call(page, "inspect")
                    break
                except RuntimeError as exc:
                    UI.event("WARN", str(exc))
                except Exception:
                    UI.event("WARN", "Не удалось проверить беседу. Открой обычную группу в Telegram Web K, "
                             "дождись загрузки сообщений и оставь поле ввода пустым. Отмени ответ или пересылку.")
            UI.selected_group(info, saved)
            authors = AuthorStore(AUTHOR_FILE)
            author_id = authors.get(info["peer"])
            if configure_only or author_id is None:
                author_id = await choose_author(page, info["peer"], author_id)
            if author_id is None:
                return
            UI.selected_author(author_id)
            if configure_only:
                UI.author_confirmation(info["title"], author_id)
                while True:
                    answer = (await console()).strip()
                    if answer == "0":
                        UI.event("STOP", "Отмена. Настройка автора не изменена.")
                        return
                    if answer == "1":
                        authors.set(info["peer"], author_id)
                        remember_group(info)
                        UI.event("INFO", "Telegram ID автора сохранён. Наблюдение и отправка не запускались.")
                        return
                    UI.event("WARN", "Введи 1 для сохранения или 0 для отмены.")
            if not await confirm_start(info, live, author_id):
                return
            authors.set(info["peer"], author_id)
            remember_group(info)
            await monitor(page, info["peer"], live, author_id, matcher)
        finally:
            await context.close()


def show_preview():
    """Show the production presentation without reading profiles or connecting."""
    UI.header("ПРЕВЬЮ / TELEGRAM НЕ ПОДКЛЮЧЁН")
    UI.selected_group({"title": "Бармены — пример оформления", "peer": -42})
    UI.selected_author(123456789)
    UI.confirmation("Бармены — пример оформления", True, 123456789)
    UI.section("Пример журнала — вымышленные события")
    UI.event("READY", "Ожидание новых объявлений", "21:08:12")
    UI.event("MATCH", "Объявление подходит — отправил бы +", "21:09:04")
    UI.event("SKIP", "Повтор сообщения пропущен", "21:09:05")
    UI.write()
    UI.line("Это макет. Telegram не подключён, сообщения не отправляются.")


def choose_mode() -> bool | str | None:
    UI.header()
    UI.menu()
    while True:
        mode = input(UI.prompt()).strip()
        if mode == "0":
            return None
        if mode in {"1", "2"}:
            return mode == "2"
        if mode == "3":
            return "configure_author"
        UI.event("WARN", "Введи 1, 2, 3 или 0.")


def main():
    global UI
    UI = initialize_terminal()
    parser = argparse.ArgumentParser(description="Telegram Web: автоответ + на объявления о концертах")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true", help="Отправка после выбора 1 в подтверждении группы")
    modes.add_argument("--dry-run", action="store_true", help="Тест без отправки")
    modes.add_argument("--check-rules", action="store_true", help="Проверить rules.json на примере, без браузера")
    modes.add_argument("--preview", action="store_true", help="Показать оформление без подключения к Telegram")
    modes.add_argument("--configure-author", action="store_true", help="Выбрать автора для выбранной группы")
    args = parser.parse_args()
    try:
        if args.preview:
            show_preview()
            return 0
        if args.check_rules:
            from runtime import EXAMPLE
            match = load_rules(RULES_FILE)(EXAMPLE)
            UI.header("ПРОВЕРКА ПРАВИЛ")
            UI.event("INFO", "Файл правил корректен. Пример: " + ("ПОДХОДИТ" if match.accepted else "НЕ ПОДХОДИТ"))
            UI.line(match.reason)
            return 0
        configure_logging(UI, DATA / "bot.log")
        if not args.live and not args.dry_run and not args.configure_author:
            selected = choose_mode()
            if selected is None:
                UI.event("STOP", "Выход. Наблюдение не запущено.")
                return 0
            args.configure_author = selected == "configure_author"
            args.live = selected is True
        with InstanceLock():
            asyncio.run(run(args.live, configure_only=args.configure_author))
    except KeyboardInterrupt:
        UI.event("STOP", "Остановлено.")
    except (ValueError, EOFError) as exc:
        UI.event("ERROR", str(exc) if isinstance(exc, ValueError) else "Ввод закрыт. Запусти программу в обычном окне консоли.")
        return 1
    except Exception as exc:
        # Avoid Playwright call logs: these can include complete message texts.
        LOG.error("Работа остановлена (%s). Проверь окно Telegram и перезапусти программу.", type(exc).__name__)
        if isinstance(exc, RuntimeError):
            LOG.error("%s", str(exc))
        UI.event("INFO", "Проверь, что установлен Microsoft Edge и другое окно программы закрыто.")
        return 1
    finally:
        if sys.stdin.isatty():
            UI.write()
            UI.line("Нажми любую клавишу, чтобы закрыть окно.")
        UI.restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
