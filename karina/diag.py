"""Журнал хода запуска (karina.log рядом с настройками): по нему видно, на каком этапе остановилась программа."""
from __future__ import annotations
import time
from .config import DATA


def trace(msg: str) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(DATA / "karina.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except (OSError, ValueError):
        pass
