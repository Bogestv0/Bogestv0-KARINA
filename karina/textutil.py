"""Нормализация русского текста, числа словами, склонения."""
from __future__ import annotations
import re

_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def norm(s: str) -> str:
    """нижний регистр, ё→е, без знаков препинания, одиночные пробелы."""
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s%.]", " ", s)
    s = re.sub(r"(?<!\w)\.|\.(?!\w)", " ", s)  # точки оставляем только внутри слов (youtube.com)
    return re.sub(r"\s+", " ", s).strip()


def is_loop(text: str) -> bool:
    """Зацикливание распознавания: «закрой, закрой, закрой, закрой…» — Whisper на шуме повторяет слово или кусок фразы десятки раз.
    Человек так не говорит, поэтому такой текст не команда, а сбой: его отбрасываем."""
    w = norm(text).split()
    if len(w) < 4:
        return False
    run = best = 1
    for a, b in zip(w, w[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    if best >= 4:                                              # одно слово подряд 4+ раз
        return True
    if len(w) >= 8 and len(set(w)) / len(w) <= 0.3:            # длинная фраза почти без разных слов
        return True
    for n in (2, 3, 4):                                         # кусок из 2–4 слов подряд 3+ раза
        for i in range(len(w) - n * 3 + 1):
            if w[i:i + n] == w[i + n:i + 2 * n] == w[i + 2 * n:i + 3 * n]:
                return True
    return False


def translit(s: str) -> str:
    return "".join(_LAT.get(ch, ch) for ch in s.lower())


_UNITS = {"ноль": 0, "ноля": 0, "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
          "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
          "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
          "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
         "восемьдесят": 80, "девяносто": 90}


def parse_number(s: str) -> int | None:
    """'50' / 'пятьдесят' / 'двадцать пять' / 'сто' → число; иначе None."""
    s = norm(s)
    s = re.sub(r"\b(процент\w*|%)\b", " ", s).replace("%", " ").strip()
    if re.fullmatch(r"\d{1,3}", s):
        return int(s)
    total, seen = 0, False
    for w in s.split():
        if w == "сто":
            total += 100
        elif w in _TENS:
            total += _TENS[w]
        elif w in _UNITS:
            total += _UNITS[w]
        else:
            return None
        seen = True
    return total if seen else None


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(5, ('час','часа','часов')) → 'часов'."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


from .prompts import FEMALE   # noqa: E402,F401  правило женского рода теперь в prompts.py (реэкспорт для старых импортов)
