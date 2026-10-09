"""Amber console presentation; no Telegram or application state dependencies."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import sys
import textwrap

BACKGROUND = (10, 15, 22)
ACCENT = (255, 190, 86)
SECONDARY = (227, 141, 116)
WHITE = (230, 238, 247)
MUTED = (140, 159, 181)
GREEN = (99, 220, 148)
RED = (255, 119, 135)
RESET = "\x1b[0m"
MOSCOW = timezone(timedelta(hours=3), "Europe/Moscow")

# The approved preview artwork, kept local so the bot needs no preview folder.
JARVIS = (
    "     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗",
    "     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝",
    "     ██║███████║██████╔╝██║   ██║██║███████╗",
    "██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║",
    "╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║",
    " ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝",
)
IZI = (
    "██╗ ███████╗ ██╗",
    "██║ ╚══███╔╝ ██║",
    "██║   ███╔╝  ██║",
    "██║  ███╔╝   ██║",
    "██║ ███████╗ ██║",
    "╚═╝ ╚══════╝ ╚═╝",
)


def safe_text(value: object) -> str:
    """A Telegram title must not inject ANSI commands/newlines into a prompt."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
    return "".join(" " if char in "\n\r\t" else char for char in text
                   if char in "\n\r\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


def rgb(value: tuple[int, int, int], background: bool = False) -> str:
    return f"\x1b[{48 if background else 38};2;{value[0]};{value[1]};{value[2]}m"


def enable_colors() -> bool:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None:
        return False
    if os.name != "nt":
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleMode.restype = wintypes.BOOL
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetConsoleMode.restype = wintypes.BOOL
    handle = kernel.GetStdHandle(-11 & 0xFFFFFFFF)
    mode = wintypes.DWORD()
    return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)) and
                kernel.SetConsoleMode(handle, mode.value | 0x0004))


class ConsoleUI:
    def __init__(self, colors: bool = False, stream=None, width: int | None = None):
        self.colors = colors
        self.stream = stream
        self.fixed_width = width

    @property
    def width(self) -> int:
        return self.fixed_width or max(30, min(78, shutil.get_terminal_size((100, 42)).columns - 4))

    def color(self, value: str, color=WHITE) -> str:
        clean = safe_text(value)
        return rgb(color) + clean + rgb(WHITE) if self.colors else clean

    def write(self, value: str = ""):
        print(value, file=self.stream or sys.stdout, flush=True)

    def line(self, value: str = "", color=WHITE):
        for part in textwrap.wrap(safe_text(value), width=self.width) or [""]:
            self.write("  " + self.color(part, color))

    def rule(self):
        self.write("  " + self.color("─" * self.width, MUTED))

    def section(self, title: str):
        self.write()
        self.line(title.upper(), ACCENT)
        self.write()

    def header(self, mode: str | None = None, clear: bool = True):
        if self.colors:
            # Clearing is startup-only; never erase the running event journal.
            self.write(rgb(BACKGROUND, True) + rgb(WHITE) + ("\x1b[2J\x1b[H" if clear else ""))
        self.write()
        self.write("  " + self.color("J / ", ACCENT) + self.color("CONCERT ASSISTANT") +
                   self.color("  //  AMBER", MUTED))
        self.rule()
        self.write()
        if self.width >= 68:
            for left, right in zip(JARVIS, IZI):
                self.write("  " + self.color(left.ljust(51), ACCENT) + self.color(right, SECONDARY))
        elif self.width >= max(map(len, JARVIS)):
            for banner, color in ((JARVIS, ACCENT), (IZI, SECONDARY)):
                for line in banner:
                    self.write("  " + self.color(line, color))
        else:
            self.line("JARVIS / IZI", ACCENT)
        self.write()
        self.line("CONCERTS. SHIFTS. ONE PLUS.", MUTED)
        if mode:
            self.line("РЕЖИМ  " + mode, GREEN if "ТЕСТ" in mode else ACCENT)
        self.rule()

    def menu(self):
        self.section("Выбор режима")
        self.choice("1", "Тест без отправки")
        self.choice("2", "Автоматическая отправка +")
        self.choice("3", "Выбрать / изменить автора объявлений")
        self.choice("0", "Выход", MUTED)
        self.write()

    def choice(self, key: str, description: str, color=ACCENT):
        self.write("  " + self.color(f"[{key}]", color) + " " + self.color(description))

    def connection(self, saved: dict | None = None):
        self.section("Подключение к Telegram")
        self.line("Открываю Telegram в отдельном окне Edge.")
        self.write()
        self.line("1. Войди в аккаунт, если Telegram попросит.")
        self.line("   Для QR-входа: на телефоне Настройки → Устройства →", MUTED)
        self.line("   Подключить устройство.", MUTED)
        self.write()
        self.line("2. Открой группу, в которой нужно отслеживать объявления,")
        self.line("   и перейди к последним сообщениям.")
        self.write()
        self.line("3. Оставь поле ввода пустым.")
        self.line("   Отмени ответ, пересылку или редактирование, если они открыты.", MUTED)
        if saved:
            self.write()
            self.line("Последняя выбранная группа: " + safe_text(saved["title"]), SECONDARY)
            self.line("Открой её или выбери другую вручную.", MUTED)
        self.write()
        self.line("Вернись в эту консоль, когда всё будет готово.")
        self.write()
        self.choice("Enter", "Продолжить")
        self.choice("0", "Выход", MUTED)

    def selected_group(self, info: dict, saved: dict | None = None):
        self.section("Выбранная группа")
        self.line(safe_text(info["title"]), SECONDARY)
        if saved and saved["peer"] != info["peer"]:
            self.line("ВНИМАНИЕ: это другая группа, не выбранная в предыдущем запуске.", ACCENT)
        self.line("Проверь группу и отправителя: должен быть выбран твой личный аккаунт.", MUTED)

    def selected_author(self, author_id: int):
        self.section("Автор объявлений")
        self.line(f"Telegram ID: {author_id}", SECONDARY)
        self.line("Только этот автор в выбранной группе. Формат объявления также проверяется.", MUTED)
        self.line("При ручном вводе проверь ID: само число не подтверждает аккаунт.", MUTED)

    def author_menu(self, current: int | None = None):
        self.section("Выбор автора")
        if current is not None:
            self.line(f"Сохранённый Telegram ID: {current}", SECONDARY)
        self.choice("1", "Выбрать по сообщению в беседе")
        self.choice("2", "Ввести Telegram ID вручную")
        self.choice("0", "Отмена", MUTED)

    def author_messages(self, choices: list[dict]):
        self.section("Сообщения для выбора автора")
        if not choices:
            self.line("Не нашёл загруженных сообщений с определяемым автором.")
        else:
            for index, item in enumerate(choices, 1):
                self.choice(str(index), f"{safe_text(item['name'])} · ID {item['author_id']}")
                self.line(safe_text(item['preview']), MUTED)
        self.line("Найди сообщение нужного человека. Можно прокрутить беседу и обновить список.", MUTED)
        self.line("После выбора вернись к последним сообщениям перед запуском.", MUTED)

    def author_confirmation(self, title: str, author_id: int):
        self.section("Сохранение автора")
        self.line(f"Сохранить Telegram ID {author_id} для группы «{safe_text(title)}»?")
        self.line("Это настройка, а не запуск наблюдения. Сообщения не отправляются.", MUTED)
        self.choice("1", "Сохранить")
        self.choice("0", "Отмена", MUTED)

    def confirmation(self, title: str, live: bool, author_id: int | None = None):
        self.section("Подтверждение")
        operation = "Включить автоматическую отправку +" if live else "Запустить тест без отправки"
        self.line(f"{operation} в группу «{safe_text(title)}»?")
        if author_id is not None:
            self.line(f"Принимаются объявления только от Telegram ID {author_id}.", SECONDARY)
        if not live:
            self.line("Сообщения не отправляются: программа только проверяет объявления.", MUTED)
        self.write()
        self.choice("1", "Включить" if live else "Начать тест")
        self.choice("0", "Отмена", MUTED)

    def prompt(self, text: str = "Твой выбор") -> str:
        return "\n  " + self.color(safe_text(text) + " > ", ACCENT)

    def event(self, tag: str, message: str, timestamp: str | None = None):
        palette = {"READY": ACCENT, "MATCH": GREEN, "SENT": GREEN, "SKIP": MUTED,
                   "INFO": MUTED, "STOP": ACCENT, "WARN": ACCENT, "ERROR": RED}
        tag = safe_text(tag)
        clock = f"{timestamp}  " if timestamp else ""
        prefix = clock + f"{tag:<5}  "
        lines = textwrap.wrap(safe_text(message), width=max(16, self.width - len(prefix))) or [""]
        self.write("  " + self.color(clock, MUTED) + self.color(f"{tag:<5}", palette.get(tag, ACCENT)) +
                   "  " + self.color(lines[0]))
        for line in lines[1:]:
            self.write("  " + " " * len(prefix) + self.color(line))

    def restore(self):
        if self.colors:
            print(RESET, file=self.stream or sys.stdout, end="", flush=True)


class ConsoleLogHandler(logging.Handler):
    def __init__(self, ui: ConsoleUI):
        super().__init__()
        self.ui = ui

    def emit(self, record):
        try:
            message = record.getMessage()
            if message.startswith(("[ТЕСТ]", "[ОТПРАВЛЕНО]")):
                return  # The following decision summary has timing and outcome.
            tag = getattr(record, "ui_status", "ERROR" if record.levelno >= logging.ERROR else
                          "WARN" if record.levelno >= logging.WARNING else "INFO")
            decision = re.match(r"#(\d+) \| (\w+) \| ([\d.]+) мс после получения \| (.*)", message)
            if decision:
                mid, status, elapsed, reason = decision.groups()
                if status == "would_send":
                    tag, message = "MATCH", f"Объявление #{mid} подходит — отправил бы + · обработка {elapsed} мс"
                elif status == "sent":
                    tag, message = "SENT", f"+ отправлен для объявления #{mid} · обработка и подтверждение {elapsed} мс"
                elif status == "failed_or_uncertain":
                    tag, message = "ERROR", f"Отправка для #{mid} не подтверждена. Автоповтора нет."
                else:
                    tag, message = "SKIP", f"Сообщение #{mid} пропущено: {reason}"
            timestamp = datetime.fromtimestamp(record.created, MOSCOW).strftime("%H:%M:%S")
            self.ui.event(tag, message, timestamp)
        except Exception:
            self.handleError(record)


class PlainFileFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        value = datetime.fromtimestamp(record.created, MOSCOW)
        return value.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def configure_logging(ui: ConsoleUI, log_path: Path):
    log_path.parent.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(PlainFileFormatter("%(asctime)s | %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[ConsoleLogHandler(ui), file_handler], force=True)


def initialize_terminal() -> ConsoleUI:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt" and sys.stdout.isatty():
        ctypes.windll.kernel32.SetConsoleTitleW("JARVIS / IZI | Concert Assistant")
    return ConsoleUI(colors=enable_colors())
