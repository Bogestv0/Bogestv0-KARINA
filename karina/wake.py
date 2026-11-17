"""Обращение по имени: «Карина, открой миксер» / «открой миксер, Карина»."""
from __future__ import annotations
from rapidfuzz import fuzz
from .textutil import norm


# Настоящие имена людей, которые по звучанию близки к «Карина»: в фоновом разговоре или фильме они не должны будить ассистента.
NOT_NAMES = {"карен", "карим", "карл", "карла", "карло", "кирина", "корин", "карем", "кара", "каре", "кари"}


def is_name(word: str, names: list[str], threshold: int = 86) -> bool:
    if word in NOT_NAMES and word not in names:
        return False
    return any(word == n or fuzz.ratio(word, n) >= threshold for n in names)


FILLER = {"эй", "хей", "окей", "ок", "слушай", "алло", "ну", "привет", "послушай"}


def split_wake(text: str, names: list[str], tail_words: int = 7) -> tuple[bool, str]:
    """→ (обратились ли по имени, остаток фразы без имени). Имя в начале («Карина, открой …») — всегда; имя в конце («открой …, Карина») —
    только в короткой фразе (до tail_words слов): в длинной речи из фона имя в конце обычно случайность."""
    words = norm(text).split()
    j = 0
    while j < len(words) and words[j] in FILLER:
        j += 1
    if j < len(words) and is_name(words[j], names):          # «Карина, открой …»
        return True, " ".join(words[j + 1:])
    if 1 < len(words) <= tail_words and is_name(words[-1], names):          # «открой …, Карина»
        return True, " ".join(words[:-1])
    return False, " ".join(words)
