"""Обращение по имени: «Карина, открой миксер» / «открой миксер, Карина»."""
from __future__ import annotations
from rapidfuzz import fuzz
from .textutil import norm


# Настоящие имена людей, которые по звучанию близки к «Карина»: в фоновом разговоре или фильме они не должны будить ассистента.
NOT_NAMES = {"карен", "карим", "карл", "карла", "карло", "кирина", "корин", "карем", "кара", "каре", "кари",
             "коран", "каран", "киран", "кирана", "корина́"}


# Ниже этой длины слово с «похожестью» к имени не принимаем: короткие обрывки («кар», «рина») из шума не должны будить ассистента.
MIN_FUZZY_LEN = 5


_VOWELS = set("аеёиоуыэюяьъ")


def _skeleton(word: str) -> str:
    """Согласные слова без гласных и без удвоений: «карина» и «корина» и «каррина» → «крн», а «марина» → «мрн», «кармина» → «крмн»."""
    out = ""
    for ch in word:
        if ch not in _VOWELS and (not out or out[-1] != ch):
            out += ch
    return out


def _sounds_like(word: str, name: str) -> bool:
    """Распознавалка путает безударные гласные («Карина» → «Корина», «Каррина», «Карино»), а согласные держит. Принимаем слово, у которого
    та же первая буква, тот же костяк согласных и общая похожесть не ниже 70 (этого мало для «корона», «коран»): «марина», «ирина», «кармина» не проходят."""
    return (len(word) >= MIN_FUZZY_LEN and len(name) >= 4 and word[0] == name[0] and _skeleton(word) == _skeleton(name)
            and fuzz.ratio(word, name) >= 70)


def is_name(word: str, names: list[str], threshold: int = 94) -> bool:
    """Обращение по имени. Точное совпадение и перечисленные формы (name_aliases) принимаются всегда. Остальное: строго по порогу похожести, но
    с поправкой на то, как распознавалка ослышивается в безударных гласных. В 1.20.1 порог подняли до 94, и «Корина», «Каррина» не принимались:
    Карина «не слышала» имя даже при чётком произнесении."""
    if word in NOT_NAMES and word not in names:
        return False
    if word in names:
        return True
    if len(word) < MIN_FUZZY_LEN:
        return False
    return any(fuzz.ratio(word, n) >= threshold for n in names) or any(_sounds_like(word, n) for n in names[:1])      # по звучанию — только от самого имени, не от его форм


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
