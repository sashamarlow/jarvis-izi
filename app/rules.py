"""Configurable, deterministic announcement recognition. No network or AI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Match:
    accepted: bool
    reason: str


DEFAULT_RULES = {
    "schema_version": 1,
    "field_aliases": {
        "launch": ["основной запуск"],
        "guests": ["кол-во гостей", "количество гостей"],
        "staff": ["смета персонала"],
        "bartenders": ["бармен", "бармены", "барменов"],
    },
    "additional_fields": ["публика", "публика возраст", "заезд организаторов",
                          "конец", "выезд организаторов", "кухня нужна", "бар 2го этажа"],
    "minimum_additional_fields": 2,
    "required_keywords": [],
    "any_keywords": [],
    "excluded_keywords": [],
}


def normalize(text: str) -> str:
    return re.sub(r"[–—−]", "-", text.casefold().replace("ё", "е").replace("\u00a0", " "))


def phrase(value: str) -> str:
    """Literal labels, not user-supplied regular expressions."""
    parts = re.split(r"\s+", normalize(value).strip())
    return r"\s+".join(re.escape(part).replace(r"\-", r"\s*-?\s*") for part in parts)


def words(values: list[str]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(r"(?<!\w)" + phrase(value) + r"(?!\w)") for value in values)


def validate_list(value, name: str):
    if not isinstance(value, list) or len(value) > 100 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 200 for item in value
    ):
        raise ValueError(f"rules.json: {name} должен быть списком непустых строк (до 100 строк по 200 символов).")


class RuleMatcher:
    def __init__(self, config: dict):
        if not isinstance(config, dict) or set(config) != set(DEFAULT_RULES):
            raise ValueError("rules.json: неизвестные или отсутствующие поля. Сверь файл с описанием в README.")
        if type(config["schema_version"]) is not int or config["schema_version"] != 1:
            raise ValueError("rules.json: поддерживается schema_version 1.")
        aliases = config["field_aliases"]
        if not isinstance(aliases, dict) or set(aliases) != set(DEFAULT_RULES["field_aliases"]):
            raise ValueError("rules.json: field_aliases должен содержать launch, guests, staff, bartenders.")
        for name, values in aliases.items():
            validate_list(values, name)
            if not values:
                raise ValueError(f"rules.json: обязательное поле {name} не может быть пустым.")
        for name in ("additional_fields", "required_keywords", "any_keywords", "excluded_keywords"):
            validate_list(config[name], name)
        minimum = config["minimum_additional_fields"]
        if type(minimum) is not int or not 1 <= minimum <= len(config["additional_fields"]):
            raise ValueError("rules.json: minimum_additional_fields должен быть от 1 до числа дополнительных полей.")
        self.minimum = minimum
        label = lambda name: "(?:" + "|".join(phrase(v) for v in aliases[name]) + ")"
        self.required = {
            "основной запуск со временем": re.compile(r"(?m)^\s*" + label("launch") + r"\s*[:\-]?\s*(?:[01]?\d|2[0-3])[:.][0-5]\d(?!\d)"),
            "количество гостей": re.compile(r"(?m)^\s*" + label("guests") + r"\s*[:\-]\s*\d+\b"),
            "смета персонала": re.compile(r"(?m)^\s*" + label("staff") + r"\s*[:\-]?\s*$"),
            "положительное количество барменов": re.compile(r"(?m)^\s*" + label("bartenders") + r"\s*[:\-]\s*[1-9]\d*\b"),
        }
        # One logical field counts once, even when a label/line is repeated.
        self.extra = []
        for value in config["additional_fields"]:
            key = normalize(value).strip()
            prefix = phrase(value)
            tail = r"(?!\w)"
            if key == "публика":
                tail = r"(?:\s+возраст)?\s*[:\-]"
            elif key in {"публика возраст", "кухня нужна", "бар 2го этажа"}:
                tail = r"\s*[:\-]"
            elif key == "конец":
                tail = r"\s*[:\-]?\s*\d"
            if key == "бар 2го этажа":
                prefix = r"бар\s+2\s*-?\s*го\s+этажа"
            if key == "публика возраст":
                key = "публика"
            self.extra.append((key, re.compile(r"(?m)^\s*" + prefix + tail)))
        self.required_words = words(config["required_keywords"])
        self.any_words = words(config["any_keywords"])
        self.excluded_words = words(config["excluded_keywords"])

    def __call__(self, text: str) -> Match:
        text = normalize(text)
        if any(pattern.search(text) for pattern in self.excluded_words):
            return Match(False, "найдено исключающее ключевое слово")
        if any(not pattern.search(text) for pattern in self.required_words):
            return Match(False, "нет обязательного ключевого слова")
        if self.any_words and not any(pattern.search(text) for pattern in self.any_words):
            return Match(False, "нет ни одного ключевого слова из any_keywords")
        date = re.search(r"(?m)^\s*(?:дата\s*:\s*)?(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?!\d)", text)
        if not date:
            return Match(False, "нет даты вида 04.10 в начале строки")
        try:
            datetime(int(date[3] or 2000), int(date[2]), int(date[1]))
        except ValueError:
            return Match(False, "некорректная дата")
        missing = [name for name, pattern in self.required.items() if not pattern.search(text)]
        if missing:
            return Match(False, "нет полей: " + ", ".join(missing))
        score = len({key for key, pattern in self.extra if pattern.search(text)})
        if score < self.minimum:
            return Match(False, f"недостаточно дополнительных признаков объявления (нужно {self.minimum})")
        return Match(True, f"дата и 4 обязательных поля; дополнительных признаков: {score}")


def load_rules(path: Path) -> RuleMatcher:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать rules.json. Проверь наличие файла и формат JSON.") from exc
    return RuleMatcher(config)


recognize = RuleMatcher(DEFAULT_RULES)
