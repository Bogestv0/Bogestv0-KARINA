"""Картинки по запросу: «нарисуй аниме-девушку с белыми волосами», «сделай обои с горами».

Текстовая модель (локальная, Claude или облако) сама рисовать не умеет, поэтому делаем так:
  1. модель переписывает просьбу в подробное английское описание (так работают все генераторы картинок);
  2. описание уходит в генератор по выбору (Настройки → «Изображения»):
       auto        — свой сервер, если адрес указан, иначе Pollinations (без регистрации и ключа);
       pollinations — бесплатный сервис Pollinations (нужен интернет);
       sd          — свой сервер Stable Diffusion с API AUTOMATIC1111 / Forge / SD.Next (например, http://127.0.0.1:7860) — работает без интернета;
       openai      — любой сервис с OpenAI-совместимым /v1/images/generations (нужен адрес и, возможно, ключ);
  3. картинка проверяется (это действительно PNG/JPEG/WebP/GIF, а не страница с ошибкой) и сохраняется в «Документы\\Карина».
Описание картинки уходит выбранному сервису: для Pollinations это сторонний сервер, для «своего сервера» — ваш компьютер.
"""
from __future__ import annotations
import base64, json, random, re, threading, urllib.error, urllib.parse, urllib.request
from .textutil import norm

MAX_BYTES = 20 * 1024 * 1024
TIMEOUT = 150
PROVIDERS = ("auto", "pollinations", "sd", "openai")
PROVIDER_LABEL = {"pollinations": "Pollinations", "sd": "свой сервер Stable Diffusion", "openai": "свой сервер (OpenAI API)"}
POLLINATIONS_URLS = ("https://image.pollinations.ai/prompt/{prompt}", "https://gen.pollinations.ai/image/{prompt}")
UA = {"User-Agent": "Bogestv0-KARINA"}


class ImageError(Exception):
    """Понятная пользователю причина, по которой картинка не получилась."""


# ---------------------------------------------------------------- что считать просьбой нарисовать картинку
_DRAW = re.compile(r"(?:нарисуй\w*|нарисовать|изобрази\w*|сгенерируй\w*|генерируй\w*)")
_MAKE = re.compile(r"(?:создай\w*|создать|сделай\w*|сделать)")
_NOUN = re.compile(r"(?:изображени\w*|картинк\w*|картин[уы]|фото\w*|арт|арты|рисунок|рисунк\w*|иллюстраци\w*|аватар\w*|обои|обоев|постер\w*|портрет\w*|аниме)")
# слова, после которых «картинка» — часть другой задачи (документ, сайт, программа)
_OTHER = re.compile(r"(?:документ\w*|файл\w*|отчет\w*|таблиц\w*|pdf|пдф|word|ворд|excel|эксель|текст\w*|скрипт\w*|программ\w*|код\w*|сайт\w*|страниц\w*|лендинг\w*|презентац\w*|план\w*|список|описани\w*)")
_NOT_RASTER = re.compile(r"(?:схем\w*|диаграмм\w*|график\w*|чертеж\w*|блок схем\w*|архитектур\w*|карт\w* мысл\w*|svg)")


# продолжение разговора о картинке: «ещё вариант», «перерисуй», «сделай волосы длиннее»
_FOLLOW = re.compile(r"^(?:еще|другой|другую|другое|перерисуй|переделай (?:картинк|изображ|ее|её)|повтори|измени (?:картинк|изображ|ее|её)|добавь на (?:картинк|изображ|фон)|"
                     r"сделай (?:ее|её|его|их|фон|волосы|волос|глаза|одежду|стиль|светлее|темнее|ярче|больше|меньше))")


def is_follow_up(text: str) -> bool:
    return bool(_FOLLOW.search(norm(text)))


def wants_image(text: str) -> bool:
    """Просьба именно о картинке (а не о схеме SVG, документе или коде). text — как написал пользователь."""
    t = norm(text)
    if not t or _NOT_RASTER.search(t):
        return False
    words = t.split()
    for i, w in enumerate(words):
        if _DRAW.fullmatch(w):
            return True                                           # «нарисуй …», «сгенерируй …» — всегда картинка (схемы отсеяны выше)
        if _MAKE.fullmatch(w):
            for j in range(i + 1, min(i + 4, len(words))):         # «сделай [2 слова] картинку»
                if _NOUN.fullmatch(words[j]):
                    if not any(_OTHER.fullmatch(x) for x in words[i + 1:j]):
                        return True
                    break
                if _OTHER.fullmatch(words[j]):
                    break
    return False


def pick_size(text: str, default: int = 768) -> tuple[int, int]:
    t = norm(text)
    if re.search(r"обои|горизонтал|широк|16 ?[:х x] ?9|баннер|пейзаж|панорам", t):
        return 1024, 576
    if re.search(r"вертикал|постер|портрет|9 ?[:х x] ?16|в полный рост|телефон", t):
        return 768, 1024
    return default, default


# ---------------------------------------------------------------- описание для генератора
def prompt_request(user_text: str, previous: str = "") -> list[dict]:
    """Сообщения модели: переписать просьбу в английское описание для генератора картинок.
    previous — описание прошлой картинки: если пользователь просит её изменить, берём его за основу."""
    sysmsg = (
        "You write prompts for text-to-image models (Stable Diffusion / Flux). The user's request may be in Russian. "
        "Reply with ONE line only: a detailed English prompt of comma-separated phrases (subject, appearance, clothing, pose, expression, setting, lighting, "
        "art style, quality words like 'masterpiece, high detail'). Start with the art style and the main subject, then every concrete detail the user named "
        "(weapon, colors, wounds, clothing, setting) — never drop or soften them. Keep everything the user asked for and add nothing that contradicts it. "
        "If they ask for anime, begin with 'anime illustration, 2D, cel shading, flat colors, detailed anime eyes, lineart'. No explanations, no quotes, no markdown, no negative prompt, at most 70 words.")
    user = user_text.strip()[:1500]
    if previous:
        user = f"Previous prompt: {previous[:700]}\nThe user now asks (change the previous prompt accordingly, keep what they did not ask to change): {user}"
    return [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]


ANIME = re.compile(r"аниме|anime|манга|manga")
ANIME_TAGS = "anime illustration, 2D, cel shading, flat colors, lineart, detailed anime eyes"


def style_prompt(prompt: str, user_text: str) -> str:
    """Страховка: просили аниме, а модель не написала про стиль — добавляем, иначе генератор рисует полуреалистичное лицо."""
    if ANIME.search(norm(user_text)) and "anime" not in prompt.lower():
        return ANIME_TAGS + ", " + prompt
    if ANIME.search(norm(user_text)) and "2d" not in prompt.lower():
        return ANIME_TAGS + ", " + prompt
    return prompt


def pick_models(text: str) -> tuple[str, ...]:
    """Модели Pollinations по порядку: для аниме — специальная, затем обычная (если первой нет, берётся следующая)."""
    return ("flux-anime", "flux") if ANIME.search(norm(text)) else ("flux",)


def clean_prompt(raw: str, fallback: str) -> str:
    """Ответ модели → одна строка описания. Если модель ответила мусором (пусто, русский, слишком длинно) — используем исходную просьбу."""
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    s = re.sub(r"^\s*(?:prompt|промпт|описание)\s*:\s*", "", s.strip(), flags=re.I)
    s = s.strip().replace("`", "").strip("\"'«»").replace("\n", " ")
    s = re.sub(r"\s+", " ", s)
    if len(s) < 8:
        return fallback.strip()[:400]
    return s[:700]


def file_stem(text: str) -> str:
    """Имя файла по просьбе: «Картинка — белые волосы». Только буквы и цифры."""
    words = re.findall(r"[\w]+", re.sub(r"^(?:нарисуй|сделай|создай|сгенерируй)\w*\s+", "", norm(text)))
    stem = " ".join(w for w in words if len(w) > 2)[:40].strip()
    return "Картинка" + (f" - {stem}" if stem else "")


# ---------------------------------------------------------------- проверка полученного файла
def sniff(data: bytes) -> str:
    """→ "png" | "jpg" | "webp" | "gif" | "" по сигнатуре файла."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def _check(data: bytes, who: str) -> tuple[bytes, str]:
    if len(data) > MAX_BYTES:
        raise ImageError(f"{who} прислал слишком большой файл.")
    ext = sniff(data)
    if not ext:
        hint = data[:120].decode("utf-8", "replace").strip().replace("\n", " ")
        raise ImageError(f"{who} вернул не картинку" + (f": «{hint}»" if hint else "") + ".")
    return data, ext


# ---------------------------------------------------------------- запросы
def _http(req: urllib.request.Request, timeout: int = TIMEOUT, stop: threading.Event | None = None) -> bytes:
    """Запрос с понятными ошибками и возможностью остановить ожидание кнопкой «Стоп»."""
    box: dict = {}

    host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
    local = host in ("localhost", "::1") or host.startswith(("127.", "10.", "192.168.")) or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host) is not None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()      # свой сервер — напрямую, мимо системного прокси

    def work():
        try:
            with opener.open(req, timeout=timeout) as r:
                box["data"] = r.read(MAX_BYTES + 1)
        except Exception as e:           # разбираем ниже, в основном потоке
            box["err"] = e
    t = threading.Thread(target=work, daemon=True, name="karina-image")
    t.start()
    while t.is_alive():
        t.join(0.2)
        if stop is not None and stop.is_set():
            raise ImageError("остановлено")
    e = box.get("err")
    if e is None:
        return box.get("data", b"")
    if isinstance(e, urllib.error.HTTPError):
        body = ""
        try:
            body = e.read(300).decode("utf-8", "replace").strip()
        except Exception:
            pass
        if e.code in (401, 403):
            raise ImageError(f"сервис не пускает ({e.code}): нужен ключ или он не доступен из вашей страны. Можно подключить свой сервер в «Настройки → Изображения».")
        if e.code == 429:
            raise ImageError("сервис просит подождать (слишком много запросов) — попробуйте через минуту.")
        raise ImageError(f"сервис ответил ошибкой {e.code}" + (f": {body[:120]}" if body else ""))
    if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
        why = getattr(e, "reason", e)
        raise ImageError(f"нет связи с сервисом картинок ({why}). Проверьте интернет или адрес сервера в «Настройки → Изображения».")
    raise ImageError(f"{e.__class__.__name__}: {e}")


def _join(base: str, path: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not re.match(r"^https?://", base, re.I):
        raise ImageError("в «Настройки → Изображения» не указан адрес сервера (например, http://127.0.0.1:7860).")
    return base + path


def via_pollinations(prompt: str, size: tuple[int, int], seed: int, key: str = "", stop=None, models: tuple[str, ...] = ("flux",)) -> tuple[bytes, str]:
    last: Exception | None = None
    for model in models:
        q = urllib.parse.urlencode({"width": size[0], "height": size[1], "seed": seed, "nologo": "true", "model": model})
        for tpl in POLLINATIONS_URLS:
            url = tpl.format(prompt=urllib.parse.quote(prompt[:900], safe="")) + "?" + q
            h = dict(UA)
            if key:
                h["Authorization"] = "Bearer " + key          # с ключом (бесплатная регистрация на pollinations.ai) бесплатный водяной знак не ставится
            try:
                return _check(_http(urllib.request.Request(url, headers=h), stop=stop), "Pollinations")
            except ImageError as e:
                if str(e) == "остановлено":
                    raise
                last = e
    raise last or ImageError("Pollinations не ответил")


def via_sd(prompt: str, size: tuple[int, int], seed: int, url: str, stop=None) -> tuple[bytes, str]:
    body = {"prompt": prompt, "negative_prompt": "lowres, bad anatomy, bad hands, extra fingers, text, watermark, blurry, worst quality",
            "width": size[0], "height": size[1], "steps": 28, "cfg_scale": 7, "seed": seed, "sampler_name": "Euler a"}
    req = urllib.request.Request(_join(url, "/sdapi/v1/txt2img"), data=json.dumps(body).encode("utf-8"), headers={**UA, "Content-Type": "application/json"})
    raw = _http(req, stop=stop)
    try:
        imgs = json.loads(raw.decode("utf-8")).get("images") or []
        data = base64.b64decode(imgs[0].split(",", 1)[-1])
    except (ValueError, IndexError, KeyError, AttributeError):
        raise ImageError("сервер Stable Diffusion вернул неожиданный ответ — проверьте адрес и что он запущен с ключом --api.")
    return _check(data, "Сервер Stable Diffusion")


def via_openai(prompt: str, size: tuple[int, int], url: str, key: str, model: str, stop=None) -> tuple[bytes, str]:
    body: dict = {"prompt": prompt, "n": 1, "size": f"{size[0]}x{size[1]}", "response_format": "b64_json"}
    if model:
        body["model"] = model
    h = {**UA, "Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    base = (url or "").strip().rstrip("/")
    path = "/images/generations" if base.endswith("/v1") else "/v1/images/generations"
    raw = _http(urllib.request.Request(_join(url, path), data=json.dumps(body).encode("utf-8"), headers=h), stop=stop)
    try:
        item = json.loads(raw.decode("utf-8"))["data"][0]
    except (ValueError, IndexError, KeyError, TypeError):
        raise ImageError("сервер картинок вернул неожиданный ответ.")
    if item.get("b64_json"):
        return _check(base64.b64decode(item["b64_json"]), "Сервер картинок")
    if str(item.get("url", "")).startswith(("http://", "https://")):
        return _check(_http(urllib.request.Request(item["url"], headers=UA), stop=stop), "Сервер картинок")
    raise ImageError("сервер картинок не прислал изображение.")


def settings(cfg) -> dict:
    c = (cfg.get("images", {}) or {}) if cfg is not None else {}
    prov = c.get("provider") if c.get("provider") in PROVIDERS else "auto"
    return {"provider": prov, "url": str(c.get("url") or "").strip(), "key": str(c.get("key") or ""), "model": str(c.get("model") or "").strip(),
            "size": int(c.get("size") or 768)}


def provider_name(cfg) -> str:
    s = settings(cfg)
    p = s["provider"]
    if p == "auto":
        p = "sd" if s["url"] else "pollinations"
    return p


def generate(cfg, prompt: str, size: tuple[int, int] = (768, 768), seed: int | None = None, stop: threading.Event | None = None,
             request: str = "") -> tuple[bytes, str, str]:
    """→ (байты картинки, расширение, кто нарисовал). Бросает ImageError с понятной причиной."""
    s = settings(cfg)
    seed = random.randint(1, 2**31 - 1) if seed is None else seed
    p = provider_name(cfg)
    if p == "pollinations":
        data, ext = via_pollinations(prompt, size, seed, s["key"], stop, pick_models(request or prompt))
    elif p == "sd":
        data, ext = via_sd(prompt, size, seed, s["url"], stop)
    else:
        data, ext = via_openai(prompt, size, s["url"], s["key"], s["model"], stop)
    return data, ext, PROVIDER_LABEL[p]
