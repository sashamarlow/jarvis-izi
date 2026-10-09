"""Shared paths, sample announcement and process lock for the Web-only app."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
EXAMPLE = """04.10 воскресенье
Публика возраст: 16+

Заезд организаторов 14:30
Основной запуск 18:30
Конец 22:00
Выезд организаторов 22:15
Кол-во гостей: 150

Кухня нужна: да
Бар 2го этажа: да
Чек - 15:30
Смета персонала:
Звук/свет - 1
Уборщицы - 3
Бармен - 3
Официант - 1
Повар - 1
кассир - 2
контролёр - 2
Гардероб - 1
Охрана - 4"""


class InstanceLock:
    """OS lock is released automatically, including after an abnormal exit."""
    def __enter__(self):
        DATA.mkdir(exist_ok=True)
        self.file = (DATA / "instance.lock").open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Программа уже открыта. Закрой другое окно перед запуском.") from None
        return self

    def __exit__(self, *args):
        self.file.close()
