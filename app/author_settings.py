"""Validated, per-chat author IDs. No Telegram or browser dependencies."""
import json
from pathlib import Path
import re

MAX_SAFE_ID = 2**53 - 1


def parse_author_id(value: str) -> int:
    value = value.strip()
    if not re.fullmatch(r"[0-9]{1,16}", value):
        raise ValueError("Нужен числовой Telegram ID человека: только цифры, не @username, телефон или ID группы.")
    author_id = int(value)
    if not 1 <= author_id <= MAX_SAFE_ID:
        raise ValueError("Telegram ID автора должен быть положительным числом в допустимом диапазоне.")
    return author_id


def valid_author_id(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_SAFE_ID


class AuthorStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "authors": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if (not isinstance(data, dict) or set(data) != {"schema_version", "authors"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1
                    or not isinstance(data["authors"], dict)):
                raise ValueError
            for peer, author in data["authors"].items():
                if (not re.fullmatch(r"-[1-9][0-9]{0,15}", peer)
                        or abs(int(peer)) > MAX_SAFE_ID or not valid_author_id(author)):
                    raise ValueError
            return data
        except (OSError, ValueError, TypeError):
            raise ValueError("Настройка авторов повреждена или недоступна. Отправка выключена; проверь app/data/web-authors.json.") from None

    def get(self, peer: int) -> int | None:
        return self.load()["authors"].get(str(peer))

    def set(self, peer: int, author_id: int):
        if type(peer) is not int or not -MAX_SAFE_ID <= peer < 0 or not valid_author_id(author_id):
            raise ValueError("Неверный ID группы или автора. Настройка не сохранена.")
        data = self.load()  # Corrupted settings must never silently reset other groups.
        data["authors"][str(peer)] = author_id
        self.path.parent.mkdir(exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
