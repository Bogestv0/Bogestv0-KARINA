"""Обращение по имени: «Карина, открой миксер» / «открой миксер, Карина»."""
from __future__ import annotations
from rapidfuzz import fuzz
from .textutil import norm


def is_name(word: str, names: list[str], threshold: int = 86) -> bool:
    return any(word == n or fuzz.ratio(word, n) >= threshold for n in names)


FILLER = {"эй", "хей", "окей", "ок", "слушай", "алло", "ну", "привет", "послушай"}


def split_wake(text: str, names: list[str]) -> tuple[bool, str]:
    """→ (обратились ли по имени, остаток фразы без имени)."""
    words = norm(text).split()
    j = 0
    while j < len(words) and words[j] in FILLER:
        j += 1
    if j < len(words) and is_name(words[j], names):          # «Карина, открой …»
        return True, " ".join(words[j + 1:])
    if len(words) > 1 and is_name(words[-1], names):          # «открой …, Карина»
        return True, " ".join(words[:-1])
    return False, " ".join(words)
