"""Команды, которые подстраиваются под профиль: погода (город из профиля), «как меня зовут», «когда мой день рождения», вопросы про питомцев."""
from __future__ import annotations
import re
from .actions import command, Ctx, NOT_HANDLED
from . import profile as prof, weather
from .textutil import norm

_WEATHER_WORD = r"(?:погод\w*|прогноз\w*|сколько\s+градусов|какая\s+температура|что\s+на\s+улице|холодно\s+ли|тепло\s+ли|дожд\w*\s+ли|будет\s+ли\s+дождь|зонт\w*)"


@command("weather", "Погода (в названном городе или в городе из профиля, сегодня или завтра)",
         ["скажи погоду", "какая погода в Москве", "погода завтра", "нужен ли зонт"],
         rf"^(?P<t>.*\b{_WEATHER_WORD}.*)$", llm_args="t (фраза целиком)", first=True)
def _weather(ctx: Ctx, t):
    if re.search(r"\b(?:найди|загугли|поищи|в\s+интернете)\b", t):
        return NOT_HANDLED     # «найди в интернете погоду» — это поиск, он обработается следующей командой
    day = "tomorrow" if re.search(r"\bзавтра\b", t) else "today"
    m = weather.CITY_RE.search(t)
    city = m.group("c").strip() if m else ""
    city = re.sub(r"\b(?:сегодня|завтра|сейчас)\b", "", city).strip()
    p = prof.get(ctx.cfg)
    if not city:
        city = p["city"]
    if not city:
        return "Не знаю, где вы живёте. Скажите «погода в Москве» или впишите город в профиле."
    try:
        text, wet = weather.report(city, day)
    except weather.WeatherError as e:
        return str(e)
    pet = next((x for x in p["pets"] if x["kind"] == "dog"), None)
    if re.search(r"\bзонт", t):
        text += " Зонт лучше взять." if wet else " Зонт, скорее всего, не понадобится."
    elif pet and wet and not m:
        text += f" На прогулку с питомцем по имени {pet['name']} возьмите дождевик."
    return text


@command("whoami", "Ответить, что Карина знает о пользователе: имя, возраст, город, день рождения",
         ["как меня зовут", "сколько мне лет", "когда у меня день рождения", "в каком я городе"],
         r"^(?:как\s+меня\s+зовут|ты\s+знаешь\s+как\s+меня\s+зовут|кто\s+я)\b", r"^сколько\s+мне\s+лет\b", r"^когда\s+(?:у\s+меня\s+)?(?:мой\s+)?день\s+рождения\b",
         r"^(?:в\s+каком\s+я\s+городе|где\s+я\s+живу|мой\s+город)\b", first=True)
def _whoami(ctx: Ctx):
    p, t = prof.get(ctx.cfg), norm(ctx.raw)
    if "как меня зовут" in t or re.search(r"\bкто я\b", t):
        return f"Вас зовут {p['name']}." if p["name"] else "Пока не знаю. Впишите имя на вкладке «Профиль»."
    if "лет" in t:
        a = prof.age(p)
        return f"Вам {prof.years_word(a)}." if a is not None else "Не знаю вашу дату рождения. Впишите её на вкладке «Профиль»."
    if "день рождения" in t:
        if not (p["birth_day"] and p["birth_month"]):
            return "Не знаю вашу дату рождения. Впишите её на вкладке «Профиль»."
        d = prof.days_to_birthday(p)
        when = "сегодня" if d == 0 else "завтра" if d == 1 else f"через {d} дн."
        return f"Ваш день рождения {prof.date_words(p['birth_day'], p['birth_month'])}, это {when}."
    return f"Вы живёте в городе {p['city']}." if p["city"] else "Пока не знаю, где вы живёте. Впишите город на вкладке «Профиль»."


@command("pets", "Рассказать о домашних животных пользователя (клички)",
         ["как зовут мою собаку", "как зовут мою кошку", "кто у меня дома", "какие у меня питомцы"],
         r"^(?:как\s+зовут\s+(?:мо\w+|нашу?|наш\w*)\s+\w+)\b", r"^(?:какие\s+у\s+меня\s+(?:домашние\s+)?(?:питомц\w+|животные)|кто\s+у\s+меня\s+дома|какие\s+у\s+меня\s+питомцы)\b")
def _pets(ctx: Ctx):
    p, t = prof.get(ctx.cfg), norm(ctx.raw)
    if not p["pets"]:
        return "Вы ещё не добавили питомцев. Это можно сделать на вкладке «Профиль»."
    if t.startswith("как зовут"):
        pet = prof.match_pet(p, t)
        if pet:
            return f"Зовут {pet['name']}."
    return "Дома у вас " + prof.pets_text(p) + "."


def morning_greeting(cfg) -> str:
    """Для приветствия «привет»: поздравление, если сегодня праздник и ещё не поздравляли."""
    g = prof.due_greeting(cfg)
    if g:
        prof.mark_greeted(cfg)
    return g
