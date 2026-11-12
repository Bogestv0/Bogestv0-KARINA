"""Профиль пользователя: имя, день рождения, город, питомцы. Карина подстраивается: поздравляет, называет по имени, берёт город для погоды,
знает кличку питомца, а нейросеть получает короткую справку о вас (если не отключено в профиле)."""
from __future__ import annotations
import datetime, re
from . import prompts
from .textutil import plural

PET_KINDS = {"dog": "собака", "cat": "кошка", "parrot": "попугай", "hamster": "хомяк", "rabbit": "кролик", "fish": "рыбки", "other": "питомец"}
_KIND_WORDS = {  # как их называют голосом (основы слов)
    "dog": ["собак", "пес", "пёс", "щенок", "щенк"], "cat": ["кошк", "кот", "котик", "кошечк"], "parrot": ["попуга"],
    "hamster": ["хомяк"], "rabbit": ["кролик"], "fish": ["рыб"], "other": ["питом", "животн", "зверь"],
}
_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
_ORD = {1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого", 5: "пятого", 6: "шестого", 7: "седьмого", 8: "восьмого", 9: "девятого", 10: "десятого",
        11: "одиннадцатого", 12: "двенадцатого", 13: "тринадцатого", 14: "четырнадцатого", 15: "пятнадцатого", 16: "шестнадцатого", 17: "семнадцатого",
        18: "восемнадцатого", 19: "девятнадцатого", 20: "двадцатого", 21: "двадцать первого", 22: "двадцать второго", 23: "двадцать третьего",
        24: "двадцать четвёртого", 25: "двадцать пятого", 26: "двадцать шестого", 27: "двадцать седьмого", 28: "двадцать восьмого",
        29: "двадцать девятого", 30: "тридцатого", 31: "тридцать первого"}


def default() -> dict:
    return {"name": "", "birth_day": 0, "birth_month": 0, "birth_year": 0, "city": "", "pets": [], "google": {},
            "last_greeted": "", "share_with_llm": True}


def clean(p: dict) -> dict:
    """Проверка того, что пришло из окна: числа в пределах, строки короткие, питомцев не больше 12."""
    out = default()
    out.update({k: p[k] for k in ("google", "last_greeted") if k in p and isinstance(p[k], (dict, str))})
    out["name"] = str(p.get("name") or "").strip()[:40]
    out["city"] = str(p.get("city") or "").strip()[:60]
    out["share_with_llm"] = bool(p.get("share_with_llm", True))

    def num(v, lo, hi):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 0
        return n if lo <= n <= hi else 0
    out["birth_day"], out["birth_month"] = num(p.get("birth_day"), 1, 31), num(p.get("birth_month"), 1, 12)
    out["birth_year"] = num(p.get("birth_year"), 1900, datetime.date.today().year)
    if out["birth_day"] and out["birth_month"]:               # 31 февраля — не бывает
        try:
            datetime.date(2024, out["birth_month"], out["birth_day"])
        except ValueError:
            out["birth_day"] = out["birth_month"] = 0
    pets = []
    for x in (p.get("pets") or [])[:12]:
        name = str((x or {}).get("name") or "").strip()[:30]
        if not name:
            continue
        kind = x.get("kind") if x.get("kind") in PET_KINDS else "other"
        pet = {"name": name, "kind": kind}
        d, m = num(x.get("birth_day"), 1, 31), num(x.get("birth_month"), 1, 12)
        if d and m:
            pet.update(birth_day=d, birth_month=m)
        pets.append(pet)
    out["pets"] = pets
    return out


def get(cfg) -> dict:
    return {**default(), **(cfg.get("profile") or {})}


def first_name(cfg) -> str:
    return get(cfg)["name"].split()[0] if get(cfg)["name"].strip() else ""


def with_name(cfg, text: str) -> str:
    """«Привет!» → «Привет, Михаил!»"""
    n = first_name(cfg)
    return re.sub(r"([!.?]|$)", f", {n}\\1", text, count=1) if n else text


def age(p: dict, today: datetime.date | None = None) -> int | None:
    today = today or datetime.date.today()
    if not (p["birth_year"] and p["birth_month"] and p["birth_day"]):
        return None
    a = today.year - p["birth_year"] - ((today.month, today.day) < (p["birth_month"], p["birth_day"]))
    return a if a >= 0 else None


def years_word(n: int) -> str:
    return f"{n} {plural(n, ('год', 'года', 'лет'))}"


def _is_today(d: int, m: int, today: datetime.date) -> bool:
    return bool(d and m and (today.day, today.month) == (d, m))


def is_birthday(p: dict, today: datetime.date | None = None) -> bool:
    return _is_today(p["birth_day"], p["birth_month"], today or datetime.date.today())


def due_greeting(cfg, today: datetime.date | None = None) -> str:
    """Что сказать сегодня (один раз за день): поздравление пользователя и/или питомцев. Пусто, если нечего."""
    today = today or datetime.date.today()
    p = get(cfg)
    if p["last_greeted"] == today.isoformat():
        return ""
    parts = []
    if is_birthday(p, today):
        n, a = first_name(cfg), age(p, today)
        parts.append(f"С днём рождения{', ' + n if n else ''}! " + (f"Вам сегодня исполняется {years_word(a)}. " if a else "")
                     + "Желаю радости, здоровья и хорошего настроения!")
    for pet in p["pets"]:
        if _is_today(pet.get("birth_day", 0), pet.get("birth_month", 0), today):
            parts.append(f"А ещё у питомца по имени {pet['name']} сегодня день рождения. Поздравляю!")
    return " ".join(parts)


def mark_greeted(cfg, today: datetime.date | None = None):
    cfg.update({"profile": {"last_greeted": (today or datetime.date.today()).isoformat()}})


def days_to_birthday(p: dict, today: datetime.date | None = None) -> int | None:
    today = today or datetime.date.today()
    if not (p["birth_day"] and p["birth_month"]):
        return None
    for y in (today.year, today.year + 1):
        try:
            d = datetime.date(y, p["birth_month"], p["birth_day"])
        except ValueError:                                      # 29 февраля в невисокосный год
            d = datetime.date(y, 3, 1)
        if d >= today:
            return (d - today).days
    return None


def pets_text(p: dict) -> str:
    return ", ".join(f"{PET_KINDS.get(x['kind'], 'питомец')} по имени {x['name']}" for x in p["pets"])


def prompt_section(cfg) -> str:
    """Справка для нейросети. Пусто, если профиль пуст или пользователь запретил передавать его."""
    p = get(cfg)
    if not p["share_with_llm"]:
        return ""
    bits = []
    if p["name"]:
        bits.append(prompts.fill(prompts.PROFILE_NAME, name=p["name"]))
    a = age(p)
    if a is not None:
        bits.append(prompts.fill(prompts.PROFILE_AGE, age=years_word(a)))
    if p["city"]:
        bits.append(prompts.fill(prompts.PROFILE_CITY, city=p["city"]))
    if p["pets"]:
        bits.append(prompts.fill(prompts.PROFILE_PETS, pets=pets_text(p)))
    return (prompts.PROFILE_HEADER + " ".join(bits)) if bits else ""


def match_pet(p: dict, text: str):
    """Какого питомца имеют в виду: по кличке или по виду («моя собака»). None — не понятно / нет."""
    for pet in p["pets"]:
        if pet["name"].lower() in text:
            return pet
    for pet in p["pets"]:
        if any(w in text for w in _KIND_WORDS.get(pet["kind"], [])):
            return pet
    return None


def date_words(d: int, m: int) -> str:
    return f"{_ORD[d]} {_MONTHS[m]}"
