"""Подготовка текста к озвучке: цифры, знаки и латиница словами.

Голос Silero (и системный тоже) плохо читает цифры, «%», «°», «м/с» и латинские буквы: он их пропускает. Из-за этого «минус 8 градусов» звучало как
«мис градусов», а «Браузер: 30 процентов» — как «Браузер процентов». Перед озвучкой всё это переписывается русскими словами. В окне чата текст остаётся как есть.
"""
from __future__ import annotations
import re

_ONES_M = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять", "одиннадцать", "двенадцать", "тринадцать",
           "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUND = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
_SCALES = [("тысяча", "тысячи", "тысяч", True), ("миллион", "миллиона", "миллионов", False), ("миллиард", "миллиарда", "миллиардов", False)]

# существительные женского рода, после которых «один/два» → «одна/две» (по началу слова)
_FEM = ("минут", "секунд", "недел", "штук", "песн", "вкладк", "копейк", "гривн", "доз", "строк", "страниц", "картинк", "версии", "версия", "попытк", "ступен", "единиц", "сотых", "десятых")
_NEUT = ("окн", "слов", "место", "мест")


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


def _below_1000(n: int, gender: str) -> list[str]:
    out = []
    if n >= 100:
        out.append(_HUND[n // 100])
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
    if n or not out:
        if n == 1 and gender == "f":
            out.append("одна")
        elif n == 1 and gender == "n":
            out.append("одно")
        elif n == 2 and gender == "f":
            out.append("две")
        else:
            out.append(_ONES_M[n])
    return [w for w in out if w]


def number_words(n: int, gender: str = "m") -> str:
    """Целое число словами: 8 → «восемь», 21 (ж) → «двадцать одна», 2000 → «две тысячи»."""
    if n < 0:
        return "минус " + number_words(-n, gender)
    if n == 0:
        return "ноль"
    parts: list[str] = []
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    if len(groups) > 4:
        return " ".join(_ONES_M[int(d)] for d in str(sum(g * 1000 ** i for i, g in enumerate(groups))))      # совсем длинное число — по цифрам
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        if i == 0:
            parts += _below_1000(g, gender)
        else:
            one, two, five, fem = _SCALES[i - 1]
            parts += _below_1000(g, "f" if fem else "m")
            parts.append(_plural(g, (one, two, five)))
    return " ".join(parts)


def _gender_after(text: str, pos: int) -> str:
    m = re.match(r"\s*([а-яё]+)", text[pos:], re.I)
    if not m:
        return "m"
    w = m.group(1).lower()
    if w.startswith(_FEM):
        return "f"
    if w.startswith(_NEUT):
        return "n"
    return "m"


_UNITS = [
    (r"м\s*/\s*с", ("метр в секунду", "метра в секунду", "метров в секунду")),
    (r"км\s*/\s*ч", ("километр в час", "километра в час", "километров в час")),
    (r"°\s*[CcСс]", ("градус", "градуса", "градусов")),
    (r"°", ("градус", "градуса", "градусов")),
    (r"%", ("процент", "процента", "процентов")),
    (r"(?:гб|GB|Gb)(?![а-яa-z])", ("гигабайт", "гигабайта", "гигабайт")),
    (r"(?:мб|MB|Mb)(?![а-яa-z])", ("мегабайт", "мегабайта", "мегабайт")),
    (r"(?:дб|dB|дБ)(?![а-яa-z])", ("децибел", "децибела", "децибел")),
    (r"(?:мм|mm)(?![а-яa-z])", ("миллиметр", "миллиметра", "миллиметров")),
    (r"(?:км|km)(?![а-яa-z])", ("километр", "километра", "километров")),
    (r"(?:кг|kg)(?![а-яa-z])", ("килограмм", "килограмма", "килограммов")),
    (r"(?:гц|Hz|Гц)(?![а-яa-z])", ("герц", "герца", "герц")),
]

_NUM = r"(?P<sign>[−\-–+])?(?P<int>\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](?P<frac>\d{1,2}))?"
_NUM_RE = re.compile(r"(?<![\w.,])" + _NUM + r"(?:\s*(?P<unit>" + "|".join(u for u, _ in _UNITS) + r"))?(?![\d])")
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)")


def _time_words(m: re.Match) -> str:
    h, mi, se = int(m.group(1)), int(m.group(2)), m.group(3)
    s = number_words(h)
    if mi:
        s += " " + (("ноль " if mi < 10 else "") + number_words(mi, "f"))
    else:
        s += " ровно" if not se else ""
    if se:
        s += " " + (("ноль " if int(se) < 10 else "") + number_words(int(se), "f"))
    return s


def _num_repl(text: str):
    def repl(m: re.Match) -> str:
        raw = m.group("int").replace(" ", "").replace(" ", "")
        sign = m.group("sign")
        frac = m.group("frac")
        unit = m.group("unit")
        prefix = ""
        if sign:
            prefix = "плюс " if sign == "+" else "минус "
            # «5-6 минут», «с 10-го»: дефис между числами — не минус
            if sign in "-–" and m.start() > 0 and text[m.start() - 1].isdigit():
                return m.group(0)
        n = int(raw) if len(raw) < 16 else 0
        if frac:
            den = ("десятая", "десятых") if len(frac) == 1 else ("сотая", "сотых")
            fr = int(frac)
            words = f"{number_words(n, 'f')} {_plural(n, ('целая', 'целых', 'целых'))} {number_words(fr, 'f')} {den[0] if fr % 10 == 1 and fr % 100 != 11 else den[1]}"
            if unit:
                for pat, forms in _UNITS:
                    if re.fullmatch(pat, unit):
                        return f"{prefix}{words} {forms[1]}"
            return f"{prefix}{words}"
        if unit:
            for pat, forms in _UNITS:
                if re.fullmatch(pat, unit):
                    return f"{prefix}{number_words(n, 'f' if forms[0].startswith(('минут', 'секунд')) else 'm')} {_plural(n, forms)}"
        words = number_words(n, _gender_after(text, m.end()))
        if words.endswith("одна") and re.match(r"\s*[а-яё]+[ую]\b", text[m.end():], re.I):
            words = words[:-4] + "одну"                         # «через одну минуту» → «одну минуту» (винительный падеж)
        return f"{prefix}{words}"
    return repl


# ---------- латиница ----------
_LETTER_NAMES = {"a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай", "j": "джей", "k": "кей", "l": "эл",
                 "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар", "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл ю", "x": "икс",
                 "y": "уай", "z": "зед"}
_WORDS = {"windows": "виндоус", "google": "гугл", "yandex": "яндекс", "spotify": "спотифай", "twitch": "твич", "steam": "стим", "discord": "дискорд",
          "youtube": "ютуб", "telegram": "телеграм", "chrome": "хром", "edge": "эдж", "firefox": "файрфокс", "opera": "опера", "wifi": "вай фай", "wi-fi": "вай фай",
          "bluetooth": "блютус", "usb": "юэсби", "vpn": "вэ пэ эн", "gpu": "джи пи ю", "cpu": "си пи ю", "ram": "рэм", "ok": "окей", "online": "онлайн", "offline": "оффлайн",
          "mixer": "миксер", "karina": "Карина", "claude": "клод", "anthropic": "антропик", "whatsapp": "вотсап", "instagram": "инстаграм", "tiktok": "тикток",
          "netflix": "нетфликс", "wikipedia": "википедия", "github": "гитхаб", "python": "пайтон", "obs": "о би эс", "pc": "пи си", "ai": "эй ай", "tv": "ти ви"}
_DI = [("sch", "ск"), ("tch", "ч"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("kh", "х"), ("zh", "ж"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"),
       ("ck", "к"), ("qu", "кв"), ("oo", "у"), ("ee", "и"), ("ie", "и"), ("ou", "ау"), ("ow", "оу"), ("ay", "эй"), ("ey", "ей")]
_ONE = {"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
        "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з"}


def _translit(word: str) -> str:
    w, out, i = word.lower(), "", 0
    while i < len(w):
        for lat, ru in _DI:
            if w.startswith(lat, i):
                out += ru
                i += len(lat)
                break
        else:
            out += _ONE.get(w[i], "")
            i += 1
    return out


def _latin_repl(m: re.Match) -> str:
    w = m.group(0)
    low = w.lower()
    if low in _WORDS:
        return _WORDS[low]
    if w.isupper() and 2 <= len(w) <= 5:
        return " ".join(_LETTER_NAMES.get(ch.lower(), "") for ch in w)
    return _translit(w)


def numbers_to_words(text: str) -> str:
    """Только числа, время, проценты, градусы и единицы (без латиницы): сначала это, потом словарь произношений, потом латиница."""
    if not text:
        return text
    t = text.replace("\u00a0", " ")
    t = re.sub(r"(?<![\d.])\d+(?:\.\d+){2,}(?![\d.])", lambda m: " точка ".join(number_words(int(x)) for x in m.group(0).split(".")), t)      # версия 1.22.1
    t = _TIME_RE.sub(_time_words, t)
    return _NUM_RE.sub(_num_repl(t), t)


def latin_to_words(text: str) -> str:
    """Латинские слова русскими буквами (известные — по словарю, остальные — по звучанию, аббревиатуры — по буквам)."""
    t = re.sub(r"[A-Za-z][A-Za-z'’\-]*", _latin_repl, text or "")
    t = t.replace("&", " и ").replace("№", " номер ")
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def for_speech(text: str) -> str:
    """Текст для озвучки целиком: числа и латиница русскими словами. Остальное не трогаем."""
    return latin_to_words(numbers_to_words(text))
