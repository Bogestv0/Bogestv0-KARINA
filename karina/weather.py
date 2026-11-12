"""Погода через Open-Meteo (бесплатно, без ключа и регистрации): сначала ищем город, потом прогноз."""
from __future__ import annotations
import json, re, urllib.parse, urllib.request

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "Bogestv0-KARINA"}

_CODES = {0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь", 51: "лёгкая морось", 53: "морось",
          55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
          67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа", 80: "кратковременный дождь", 81: "ливень",
          82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом"}
WET = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}


class WeatherError(Exception):
    """Текст можно сразу произнести."""


def _get(url: str, params: dict, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=UA)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
    except Exception:
        raise WeatherError("Не получилось связаться с сервисом погоды. Проверьте интернет.") from None


def city_variants(word: str) -> list[str]:
    """«в Красноярске» → Красноярске, Красноярск…; «в Москве» → Москва; «в Казани» → Казань."""
    w = word.strip().strip(".,!?").lower()
    out = [w]
    if len(w) > 3:
        out += [w[:-1], w[:-1] + "а", w[:-1] + "я", w[:-1] + "ь", w[:-2], w[:-2] + "а", w[:-2] + "ь"]
    return list(dict.fromkeys(x for x in out if len(x) >= 3))


def find_city(name: str) -> dict:
    words = name.split()
    cands = [name] + (city_variants(" ".join(words)) if len(words) == 1 else [" ".join(words[:-1] + [v]) for v in city_variants(words[-1])])
    for c in dict.fromkeys(cands):
        res = (_get(GEO, {"name": c, "count": 1, "language": "ru", "format": "json"}).get("results") or [])
        if res and res[0].get("name", "").lower()[:4] == c.lower()[:4]:
            return res[0]
    raise WeatherError(f"Не нашла город «{name}». Назовите его иначе или поправьте в профиле.")


def temp_words(t: float) -> str:
    n = round(t)
    return "ноль градусов" if n == 0 else f"{'плюс' if n > 0 else 'минус'} {abs(n)} {_deg(abs(n))}"


def _deg(n: int) -> str:
    return "градус" if n % 10 == 1 and n % 100 != 11 else "градуса" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "градусов"


def forecast(city: dict) -> dict:
    return _get(FORECAST, {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": "auto", "forecast_days": 2, "wind_speed_unit": "ms",
                           "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                           "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})


def describe(city: dict, data: dict, day: str = "today") -> tuple[str, bool]:
    """→ (фраза, будут ли осадки). day: today | tomorrow."""
    place = city.get("name", "")
    d = data["daily"]
    i = 1 if day == "tomorrow" else 0
    code, lo, hi = d["weather_code"][i], d["temperature_2m_min"][i], d["temperature_2m_max"][i]
    prob = (d.get("precipitation_probability_max") or [None, None])[i]
    wet = code in WET or bool(prob and prob >= 50)
    rng = f"от {temp_words(lo)} до {temp_words(hi)}" if round(lo) != round(hi) else temp_words(hi)
    if day == "tomorrow":
        s = f"{place}, завтра: {_CODES.get(code, 'облачно')}, {rng}."
    else:
        c = data["current"]
        s = (f"{place}, сейчас {temp_words(c['temperature_2m'])}, {_CODES.get(c['weather_code'], 'облачно')}, ветер {round(c['wind_speed_10m'])} м/с. "
             f"Днём {rng}.")
    if prob and prob >= 30:
        s += f" Вероятность осадков {int(prob)} процентов."
    return s, wet


def report(city_name: str, day: str = "today") -> tuple[str, bool]:
    city = find_city(city_name)
    return describe(city, forecast(city), day)


CITY_RE = re.compile(r"\b(?:в|во|для|по)\s+(?:городе\s+)?(?P<c>[а-яёa-z][а-яёa-z\- ]{2,40}?)(?=\s+(?:сегодня|завтра|сейчас|на\s+завтра)\b|$)")
