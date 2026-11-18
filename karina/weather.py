"""Погода через Open-Meteo (бесплатно, без ключа и регистрации): сначала ищем город, потом прогноз."""
from __future__ import annotations
import itertools, json, re, time, urllib.parse, urllib.request

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


class WeatherNetError(WeatherError):
    """Сервис погоды недоступен (сеть, блокировка, прокси): можно попробовать запасной."""


LAST_ERROR = ""          # технические подробности последней неудачи (для журнала и подсказки в панели)


def _reason(e: Exception) -> str:
    import socket, ssl, urllib.error
    r = getattr(e, "reason", e)
    if isinstance(r, (socket.timeout, TimeoutError)) or "timed out" in str(r):
        return "превышено время ожидания"
    if isinstance(r, ssl.SSLError):
        return "ошибка защищённого соединения (SSL)"
    if isinstance(e, urllib.error.HTTPError):
        return f"сервис ответил {e.code}"
    if isinstance(e, (ValueError, json.JSONDecodeError)):
        return "сервис вернул непонятный ответ"
    return str(r)[:80] or e.__class__.__name__


def _get(url: str, params: dict, timeout: float = 10.0) -> dict:
    """Запрос к Open-Meteo. Три попытки: как настроен системный прокси → напрямую (без прокси: у VPN или прокси иногда «залипает» настройка и
    всё через него не ходит) → ещё раз напрямую. Раньше любая неудача превращалась в «нет связи» без причины, и погода просто не показывалась."""
    global LAST_ERROR
    full = url + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for attempt, direct in enumerate((False, True, True)):
        req = urllib.request.Request(full, headers=UA)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
            data = json.loads(opener.open(req, timeout=timeout).read().decode("utf-8"))
            LAST_ERROR = ""
            return data
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(0.4)
    LAST_ERROR = f"{last.__class__.__name__}: {_reason(last) if last else ''}"
    raise WeatherNetError(f"Не получилось связаться с сервисом погоды ({_reason(last) if last else 'нет связи'}). Проверьте интернет или VPN.") from None


def _word_variants(w: str) -> list[str]:
    """Одно слово в падеже → возможные начальные формы: «красноярске» → красноярск, «москве» → москва, «казани» → казань."""
    w = w.strip().strip(".,!?").lower()
    out = [w]
    if len(w) > 3:
        out += [w[:-1], w[:-1] + "а", w[:-1] + "я", w[:-1] + "ь", w[:-1] + "о", w[:-2], w[:-2] + "а", w[:-2] + "ь", w[:-2] + "о"]
    return list(dict.fromkeys(x for x in out if len(x) >= 3))


def _adj_variants(w: str) -> list[str]:
    """«нижнем» → нижний, «старом» → старый/старой: прилагательные в названиях городов («в Нижнем Новгороде»)."""
    w = w.lower()
    if w.endswith("ем") and len(w) > 4:
        return [w, w[:-2] + "ий"]
    if w.endswith("ом") and len(w) > 4:
        return [w, w[:-2] + "ый", w[:-2] + "ой", w[:-2] + "ий"]
    if w.endswith("ей") and len(w) > 4:
        return [w, w[:-2] + "ья", w[:-2] + "ая"]
    return [w]


def city_variants(word: str) -> list[str]:
    """Название из одного слова или с дефисом («в Ростове-на-Дону», «в Санкт-Петербурге»): варианты начальной формы."""
    w = word.strip().strip(".,!?").lower()
    if "-" not in w:
        return _word_variants(w)
    parts = w.split("-")
    opts = [[p] for p in parts]
    opts[0] = _word_variants(parts[0])[:4]
    opts[-1] = _word_variants(parts[-1])[:4]
    out = ["-".join(c) for c in itertools.islice(itertools.product(*opts), 12)]
    return list(dict.fromkeys(out))


_TAIL_NOISE = re.compile(r"^(?:г|гор|город\w*|область|обл|край|республика|россия|рф)\.?$", re.I)


def clean_city(name: str) -> str:
    """То, что человек вписал в профиль, как есть: «г. Новокузнецк», «город Омск, Россия», «Томск (Томская обл.)» → «Новокузнецк», «Омск», «Томск»."""
    s = re.sub(r"\([^)]*\)", " ", str(name or ""))
    s = s.split(",")[0]
    words = [w for w in re.split(r"\s+", s.strip()) if w and not _TAIL_NOISE.match(w)]
    return " ".join(words).strip(" .,-")


def _pick(results: list[dict], wanted: list[str]) -> dict | None:
    """Из ответа сервиса выбираем самый похожий на названное (человек мог сказать с ошибкой или в другом падеже); при равенстве — самый людный."""
    from rapidfuzz import fuzz
    best, best_key = None, (0.0, 0)
    for r in results:
        nm = str(r.get("name", "")).lower().replace("ё", "е")
        score = max((fuzz.ratio(nm, v.replace("ё", "е")) for v in wanted), default=0.0)
        key = (score, int(r.get("population") or 0))
        if score >= 78 and key > best_key:
            best, best_key = r, key
    return best


def find_city(name: str) -> dict:
    name = clean_city(name) or name.strip()
    words = name.split()
    if len(words) == 1:
        cands = [name] + city_variants(name)
    else:
        last = city_variants(words[-1])
        heads = [list(dict.fromkeys(_adj_variants(w))) for w in words[:-1]]
        cands = [name] + [" ".join(list(h) + [v]) for h in itertools.islice(itertools.product(*heads), 4) for v in last]
    if len(words) > 1:                               # «санкт петербург», «ростове на дону» — в базе города с дефисами
        cands += city_variants("-".join(words))
    wanted = [c.lower() for c in cands]
    for c in dict.fromkeys(cands):
        res = (_get(GEO, {"name": c, "count": 10, "language": "ru", "format": "json"}).get("results") or [])
        hit = _pick(res, wanted)
        if hit:
            return hit
    raise WeatherError(f"Не нашла город «{name}». Назовите его иначе или поправьте в профиле.")


def temp_words(t: float) -> str:
    n = round(t)
    return "ноль градусов" if n == 0 else f"{'плюс' if n > 0 else 'минус'} {abs(n)} {_deg(abs(n))}"


def _deg(n: int) -> str:
    return "градус" if n % 10 == 1 and n % 100 != 11 else "градуса" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "градусов"


def forecast(city: dict) -> dict:
    return _get(FORECAST, {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": "auto", "forecast_days": 3, "wind_speed_unit": "ms",
                           "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                           "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})


def describe(city: dict, data: dict, day: str = "today") -> tuple[str, bool]:
    """→ (фраза, будут ли осадки). day: today | tomorrow."""
    place = city.get("name", "")
    d = data["daily"]
    i = {"tomorrow": 1, "aftertomorrow": 2}.get(day, 0)
    code, lo, hi = d["weather_code"][i], d["temperature_2m_min"][i], d["temperature_2m_max"][i]
    prob = (d.get("precipitation_probability_max") or [None, None])[i]
    wet = code in WET or bool(prob and prob >= 50)
    rng = f"от {temp_words(lo)} до {temp_words(hi)}" if round(lo) != round(hi) else temp_words(hi)
    if day in ("tomorrow", "aftertomorrow"):
        s = f"{place}, {'завтра' if day == 'tomorrow' else 'послезавтра'}: {_CODES.get(code, 'облачно')}, {rng}."
    else:
        c = data["current"]
        s = (f"{place}, сейчас {temp_words(c['temperature_2m'])}, {_CODES.get(c['weather_code'], 'облачно')}, ветер {round(c['wind_speed_10m'])} м/с. "
             f"Днём {rng}.")
    if prob and prob >= 30:
        s += f" Вероятность осадков {int(prob)} процентов."
    return s, wet


def report(city_name: str, day: str = "today") -> tuple[str, bool]:
    try:
        city = find_city(city_name)
        return describe(city, forecast(city), day)
    except WeatherNetError as e:
        fb = _fallback_report(city_name, day)        # Open-Meteo недоступен (у многих он заблокирован): пробуем wttr.in, как и вкладка «Погода» на острове
        if fb:
            return fb
        raise e


_WET_WORDS = ("дожд", "снег", "ливн", "гроз", "морос", "град", "ледян")


def _fallback_report(city_name: str, day: str):
    try:
        from . import island_tools
    except Exception:
        return None
    name = clean_city(city_name) or str(city_name).strip()
    for cand in dict.fromkeys([name] + city_variants(name.replace(" ", "-") if len(name.split()) > 1 else name)[:4]):
        try:
            w = island_tools._weather_week_wttr(cand)
        except Exception:
            continue
        days = w.get("days") or []
        i = {"tomorrow": 1, "aftertomorrow": 2}.get(day, 0)
        if i >= len(days):
            continue
        d, now = days[i], w.get("now") or {}
        place = str(w.get("city") or cand).strip().capitalize()
        rng = f"от {temp_words(d['lo'])} до {temp_words(d['hi'])}" if round(d["lo"]) != round(d["hi"]) else temp_words(d["hi"])
        if i == 0 and now:
            s = f"{place}, сейчас {temp_words(now['t'])}, {now.get('text', 'облачно')}, ветер {round(now.get('wind') or 0)} м/с. Днём {rng}."
        else:
            s = f"{place}, {'завтра' if i == 1 else 'послезавтра'}: {d.get('text', 'облачно')}, {rng}."
        prob = int(d.get("prob") or 0)
        if prob >= 30:
            s += f" Вероятность осадков {prob} процентов."
        wet = prob >= 50 or any(x in str(d.get("text", "")) for x in _WET_WORDS)
        return s, wet
    return None


CITY_RE = re.compile(r"\b(?:в|во|для|по)\s+(?:городе\s+)?(?P<c>[а-яёa-z][а-яёa-z\- ]{2,40}?)(?=\s+(?:сегодня|завтра|сейчас|на\s+завтра)\b|$)")   # старый разбор, оставлен для совместимости

# Слова, которыми кончается название города в фразе («погода в Омске сегодня вечером»), и слова, которые городом не являются.
_STOP = {"сегодня", "завтра", "послезавтра", "сейчас", "вечером", "утром", "днем", "ночью", "вечер", "утро", "день", "ночь", "на", "пожалуйста", "будет", "ли", "какая", "какой",
         "и", "а", "но", "что", "как", "там", "чтобы", "если", "карина", "скажи", "расскажи", "подскажи", "узнай", "нужен", "нужна", "надо", "стоит", "можно", "будут"}
_NOT_CITY = {"ближайшие", "ближайшее", "ближайший", "эти", "этот", "это", "выходные", "выходных", "понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье",
             "течение", "общем", "целом", "первую", "последние", "последнее", "мире", "стране", "интернете", "сети", "сторону", "случае", "дорогу", "поездку"}
_TIME_AFTER_NA = {"неделю", "неделе", "недели", "сутки", "день", "вечер", "ночь", "утро", "ближайшие", "ближайшую", "ближайший", "выходные", "завтра", "сегодня", "три", "пять", "семь", "два"}
_LOCAL = {"моем", "нашем", "своем", "родном", "этом", "здесь", "тут", "дома", "меня", "нас", "городе", "город", "города", "моего", "нашего", "моему"}
_LOCAL_PHRASE = re.compile(r"\b(?:у\s+меня|у\s+нас|около\s+меня|рядом(?:\s+со\s+мной)?|здесь|тут|дома|на\s+улице|в\s+(?:моем|нашем|своем|родном|этом)\s+город\w*|в\s+город[ео])\b")
_DAYS = (("послезавтра", "aftertomorrow"), ("завтра", "tomorrow"))


_HEARD_WEATHER = re.compile(r"\b(?:по\s+год\w*|п[ао]год\w*)")
_PREPOSITIONS = {"в", "во", "для", "на", "по", "за", "у", "с", "со", "из", "при", "от", "около", "рядом", "про", "об", "над", "под", "к", "ко"}
_WEATHER_TOKEN = re.compile(r"^(?:погода|прогноз\w*|температура|градусов)$")


def fix_heard(t: str) -> str:
    """Распознавалка слышит «погоду» как «по году», «погодов», «пагода»: приводим к «погода», чтобы и команда, и разбор города сработали."""
    return _HEARD_WEATHER.sub("погода", t)


def parse_request(t: str) -> tuple[str, str]:
    """Фраза «какая погода в Красноярске завтра вечером» → («красноярске», «tomorrow»). Город пустой — значит «мой», из профиля.
    Город можно назвать и без предлога («погода Кемерово»)."""
    t = fix_heard(t)
    day = next((d for w, d in _DAYS if re.search(rf"\b{w}\b", t)), "today")
    words = t.split()
    for i, w in enumerate(words):
        if w not in ("в", "во", "для", "по", "города", "городе", "город", "г"):
            continue
        j = i + 1
        while j < len(words) and words[j] in ("городе", "город", "города", "г", "гор"):
            j += 1                                   # «в городе Омске»
        name = []
        while j < len(words) and len(name) < 4 and (words[j] not in _STOP or (words[j] == "на" and name and j + 1 < len(words) and words[j + 1] not in _STOP | _NOT_CITY | _TIME_AFTER_NA)):
            if not name and (words[j] in _NOT_CITY or words[j] in _LOCAL):
                break
            name.append(words[j])
            j += 1
        if name:
            return " ".join(name), day
    for i, w in enumerate(words):                    # без предлога: «погода кемерова», «прогноз омск завтра»
        if not _WEATHER_TOKEN.match(w):
            continue
        j = i + 1
        name = []
        while j < len(words) and len(name) < 3 and words[j] not in _STOP | _NOT_CITY | _LOCAL | _TIME_AFTER_NA and words[j] not in _PREPOSITIONS:
            name.append(words[j])
            j += 1
        if name:
            return " ".join(name), day
    return "", day
