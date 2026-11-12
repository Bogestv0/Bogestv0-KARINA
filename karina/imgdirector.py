"""Режиссёр картинок: текстовая модель (например, Qwen 3.5 9B) сама не рисует, зато управляет «художниками» —
встроенными картиночными моделями (NoobAI XL, Pony XL, FLUX.2 Klein, Animagine, Illustrious).

Как это работает:
  1. Текстовая модель читает просьбу и пишет описание дважды: тегами (для NoobAI / Pony / Animagine / Illustrious)
     и обычными английскими предложениями (для FLUX.2 Klein), а заодно называет, какой художник подходит лучше всего.
  2. Режиссёр раздаёт варианты по художникам (по умолчанию 4: у каждой скачанной модели — свой стиль) и каждому даёт описание на его «языке».
  3. Правки («поверни её», «пусть она машет рукой», «на втором варианте сделай волосы длиннее», «во всех моделях — ночью»):
     модель переписывает описание, режиссёр выбирает художника, который лучше справится с этой правкой, либо обращается ко всем.
     Тот же художник с тем же seed рисует почти ту же картинку, поэтому персонаж остаётся узнаваемым.

Модуль не рисует и не ходит в сеть: он только планирует. Рисует localimg, а порядок событий и файлы ведёт chat.py.
"""
from __future__ import annotations
import random, re
from dataclasses import dataclass, field
from . import images as imagesmod, localimg
from .textutil import norm

DEFAULT_VARIANTS = 4
MAX_VARIANTS = 6

# Профили художников: на каком «языке» им писать и в чём они сильны.
# dialect: "tags" — теги danbooru; "text" — обычные предложения.  hint — подсказка текстовой модели.
ARTISTS: dict[str, dict] = {
    "noobai": {"dialect": "tags", "anime": True, "real": False, "pose": 0.5, "why": "лучше всего рисует персонажей, лица, причёски и одежду в аниме-стиле",
               "hint": "anime model with danbooru tags: clean lineart, beautiful faces, hair, outfits, known characters and art styles. Best for character looks."},
    "ponyxl": {"dialect": "tags", "anime": True, "real": False, "pose": 2.0, "why": "лучше всего слушается просьб про позы, действия и сложные сцены",
               "hint": "very flexible model: poses, actions, interactions between objects and people, busy scenes. Understands danbooru-like tags. Best for pose and action changes."},
    "flux2klein": {"dialect": "text", "anime": False, "real": True, "pose": 0.0, "why": "лучше всего понимает обычные предложения, фон, свет, предметы и надписи",
                   "hint": "understands plain English sentences; best for photos and realism, backgrounds, lighting, objects, text on images and precise instructions; it also draws anime."},
    "animagine4": {"dialect": "tags", "anime": True, "real": False, "pose": 0.3, "why": "рисует чистое аниме «как в кадрах сериала»",
                   "hint": "anime model with danbooru tags: clean look of TV anime screenshots, good faces and hands."},
    "illustrious1": {"dialect": "tags", "anime": True, "real": False, "pose": 0.3, "why": "знает много стилей и персонажей аниме",
                     "hint": "anime model with danbooru tags: many art styles and characters."},
}
GENERIC = {"dialect": "text", "anime": False, "real": False, "pose": 0.0, "why": "универсальная модель", "hint": "general image model."}
# при равных баллах: для аниме первым идёт NoobAI, для остального — FLUX.2 Klein
ORDER_ANIME = ("noobai", "ponyxl", "animagine4", "illustrious1", "flux2klein")
ORDER_OTHER = ("flux2klein", "ponyxl", "noobai", "animagine4", "illustrious1")

ANIME_EXTRA_NEG = "3d, realistic, photo, photorealistic, semi-realistic, render, doll, plastic skin"


def artist(mid: str) -> dict:
    return ARTISTS.get(mid, GENERIC)


def title(mid: str) -> str:
    m = localimg.by_id(mid)
    return m["title"] if m else mid


# ---------------------------------------------------------------- какие художники доступны
def settings(cfg) -> dict:
    c = (cfg.get("images", {}) or {}) if cfg is not None else {}
    try:
        n = int(c.get("variants") or DEFAULT_VARIANTS)
    except (TypeError, ValueError):
        n = DEFAULT_VARIANTS
    return {"director": c.get("director", True) is not False, "variants": max(1, min(MAX_VARIANTS, n)),
            "ask_count": c.get("ask_count", True) is not False}


ASK_MAX = 4
_ANSWER_W = {"один": 1, "одну": 1, "одно": 1, "одного": 1, "два": 2, "две": 2, "три": 3, "четыре": 4}
_ANSWER = re.compile(r"^\W*(\d|один|одну|одно|одного|два|две|три|четыре)\W*(?:вариант\w*|картин\w*|штук\w*|шт|изображени\w*|фото)?\W*$")


def parse_answer(text: str) -> int | None:
    """Ответ на вопрос «сколько вариантов?»: «3», «три», «2 варианта». Иное (в том числе число вне 1–4) — None."""
    m = _ANSWER.match(norm(text))
    if not m:
        return None
    w = m.group(1)
    n = int(w) if w.isdigit() else _ANSWER_W.get(w, 0)
    return n if 1 <= n <= ASK_MAX else None


def needs_count_question(cfg, text: str, has_prev: bool) -> bool:
    """Спрашивать ли «сколько вариантов?»: режиссёр включён, картинка новая (не правка и не «ещё вариант») и число в просьбе не названо."""
    if not settings(cfg)["ask_count"]:
        return False
    ids = available(cfg)
    if not ids:
        return False
    it = parse_intent(text, ids, has_prev, settings(cfg)["variants"])
    return it.kind == "new" and not it.explicit_count


def available(cfg) -> list[str]:
    """Id скачанных картиночных моделей, которыми можно рисовать прямо сейчас. Пусто — режиссёр не нужен, работает прежний путь (одна картинка)."""
    if not settings(cfg)["director"]:
        return []
    if imagesmod.settings(cfg)["provider"] not in ("auto", "local"):
        return []                                    # выбран внешний сервис: он рисует по-старому
    if not localimg.engine_ready():
        return []
    out = []
    for mid in localimg.installed_ids():
        m = localimg.by_id(mid)
        if m and localimg.engine_supports(m):
            out.append(mid)
    return out


# ---------------------------------------------------------------- что именно просит пользователь
_NUMW = {"один": 1, "одну": 1, "одно": 1, "1": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6}
_COUNT = re.compile(r"\b(\d|два|две|три|четыре|пять|шесть)\s+(?:варианта|вариантов|картинки|картинок|изображения|изображений|фото|фотографии|фотографий|рисунка|рисунков|арта|артов)\b"
                    r"|\b(один|одну|одно|1)\s+(?:вариант|картинк\w*|изображени\w*|фото|рисун\w*)\b")
_ORD = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6}
_REF_NUM = re.compile(r"\b(?:вариант\w*|картинк\w*|изображени\w*|фото|рисун\w*|номер)\s*(?:номер\s*|№\s*|#\s*)?([1-6])\b|\b([1-6]) (?:й|м|го|ом|ой) (?:вариант\w*|картинк\w*)\b|\b([1-6]) вариант\b|\b(?:на|в|во|у) ([1-6]) вариант\w*")
_REF_ORD = re.compile(r"\b(перв|втор|трет|четверт|пят|шест)\w*\s+(?:вариант\w*|картинк\w*|изображени\w*|фото)\b|\b(?:на|в|во|у) (перв|втор|трет|четверт|пят|шест)(?:ом|ой|ем|ьем|ьей)\b")
_EVERYONE = re.compile(r"\b(?:во всех|у всех|на всех|для всех|ко всем|по всем|через все|всеми|все модели|всех моделей|каждой модел\w*|каждый художник\w*|всех художник\w*|всех вариант\w*)\b")
_MODEL_LAT = {"noobai": r"noob\w*", "ponyxl": r"pony\w*", "flux2klein": r"flux\w*|klein\w*", "animagine4": r"animagine\w*", "illustrious1": r"illustrious\w*"}
# русские написания узнаём только после предлога («через пони», «моделью флюкс»): иначе «нарисуй пони» — это лошадка, а не модель
_MODEL_CYR = {"noobai": r"нуб\w*|нооб\w*", "ponyxl": r"пони", "flux2klein": r"флюкс\w*|флакс\w*|клейн\w*", "animagine4": r"анимаджин\w*", "illustrious1": r"иллюстриус\w*"}
_MODEL_PREP = r"(?:через|с помощью|моделью|в модели|модели|используй|используя)\s+(?:модел\w+\s+)?"
_PURE_MORE = re.compile(r"^(?:а )?(?:еще|другой|другие|другую|другое|перерисуй|повтори|переделай|снова|давай)"
                       r"(?: (?:один|разок|раз|вариант\w*|картинк\w*|набор|все|заново|такой же|\d|два|три|четыре|пять|шесть))*$")
_REAL = re.compile(r"фото(?!граф\w* аниме)|реалист\w*|реальн\w*|как на фото|photo\w*|realistic|кинематограф\w*|интерьер|пейзаж|натюрморт|продукт\w* фот\w*|надпис\w*|вывеск\w*|логотип")
_POSE = re.compile(r"поз[аеуыо]\w*|повер\w+|повернис\w*|разверни\w*|сид\w+|стоя\w*|стоит|бежит|бегущ\w*|прыга\w*|прыжк\w*|танц\w*|дерет\w*|дерутся|бьет|бьёт|машет|обним\w*|держит|держа\w*|целу\w*|ракурс\w*|вполоборота|спин\w*|профил\w*|лежит|лежа\w*|тянет\w*|тянуть|действи\w*|сража\w*|кида\w*|бросает|руку|руки|рукой|руками|ногу|ноги|наклон\w*|присед\w*|улыба\w*|смотр\w*|взгляд\w*")
_LOOK = re.compile(r"лиц\w+|глаз\w*|волос\w*|прическ\w*|одежд\w*|наряд\w*|форм\w*|костюм\w*|платье\w*|шляп\w*|очки|цвет\w*|персонаж\w*")
_SCENE = re.compile(r"фон\w*|свет\w*|освещени\w*|закат\w*|рассвет\w*|ноч\w*|дожд\w*|снег\w*|погод\w*|предмет\w*|надпис\w*|комнат\w*|город\w*|лес\w*|пляж\w*|мор[еяю]\w*|небо\w*")


@dataclass
class Intent:
    kind: str = "new"                      # new — новая картинка; edit — правка прежней; more — «ещё вариант» тем же описанием
    count: int = DEFAULT_VARIANTS
    refs: list[int] = field(default_factory=list)       # «на втором варианте» → [2]
    models: list[str] = field(default_factory=list)     # художники, названные прямо
    everyone: bool = False                 # «во всех моделях», «у всех вариантов»
    explicit_count: bool = False


def _named_models(t: str, ids: list[str]) -> list[str]:
    out = []
    for mid in ids:
        lat, cyr = _MODEL_LAT.get(mid), _MODEL_CYR.get(mid)
        if (lat and re.search(r"\b(?:" + lat + r")", t)) or (cyr and re.search(_MODEL_PREP + r"(?:" + cyr + r")\b", t)):
            out.append(mid)
    return out


def missing_models(text: str, ids: list[str]) -> list[str]:
    """Модели, которые пользователь назвал, но они не скачаны («через pony», а Pony нет)."""
    return _named_models(norm(text), [m for m in _MODEL_LAT if m not in ids])


def parse_intent(text: str, ids: list[str], has_prev: bool, default_count: int = DEFAULT_VARIANTS) -> Intent:
    """Что просит пользователь: новую картинку, правку прежней (какой вариант, каким художником) или «ещё вариант»."""
    t = norm(text)
    it = Intent(count=default_count)
    m = _COUNT.search(t)
    if m:
        w = m.group(1) or m.group(2)
        it.count = max(1, min(MAX_VARIANTS, int(w) if w.isdigit() else _NUMW.get(w, default_count)))
        it.explicit_count = True
    it.models = _named_models(t, ids)
    if not has_prev:
        return it
    it.everyone = bool(_EVERYONE.search(t))
    for m in _REF_NUM.finditer(t):
        n = int(next(g for g in m.groups() if g))
        if n not in it.refs:
            it.refs.append(n)
    for m in _REF_ORD.finditer(t):
        n = _ORD[m.group(1) or m.group(2)]
        if n not in it.refs:
            it.refs.append(n)
    if imagesmod.is_follow_up(text) or it.refs or it.everyone:
        it.kind = "more" if _PURE_MORE.match(t) else "edit"
    return it


# ---------------------------------------------------------------- кому что поручить
def _flags(style_text: str) -> tuple[bool, bool]:
    t = norm(style_text)
    return bool(imagesmod.ANIME.search(t)), bool(_REAL.search(t))


def rank(change_text: str, ids: list[str], style_text: str = "") -> list[str]:
    """Художники по убыванию пригодности для этой просьбы (запасной выбор, если текстовая модель не назвала лучшего).
    change_text — что просят сейчас; style_text — вся история просьб (для понимания «аниме это или фото»)."""
    t = norm(change_text)
    anime, real = _flags(style_text or change_text)
    order = ORDER_ANIME if anime else ORDER_OTHER
    score: dict[str, float] = {}
    for mid in ids:
        a = artist(mid)
        s = 0.0
        s += (len(order) - order.index(mid)) * 0.1 if mid in order else 0.0
        if real:
            s += 3.0 if a["real"] else -2.0
        elif anime and a["anime"]:
            s += 1.0
        if _POSE.search(t):
            s += a["pose"]
        if _LOOK.search(t) and mid == "noobai":
            s += 1.2
        if _SCENE.search(t) and a["real"] and not _POSE.search(t):
            s += 1.2
        score[mid] = s
    return sorted(ids, key=lambda m: -score[m])


def pool_for(style_text: str, ids: list[str]) -> tuple[list[str], str]:
    """Какими художниками можно рисовать: для фото и реализма — только те, кто это умеет (если такие скачаны)."""
    _, real = _flags(style_text)
    if real:
        capable = [m for m in ids if artist(m)["real"]]
        if capable and len(capable) < len(ids):
            return capable, "Для фото подходит только " + ", ".join(title(m) for m in capable) + " — остальные модели рисуют в стиле аниме."
    return list(ids), ""


@dataclass
class Variant:
    n: int
    model: str
    seed: int
    prompt: str = ""
    negative: str = ""


def _seed(rng: random.Random) -> int:
    return rng.randint(1, 2**31 - 1)


def plan_new(intent: Intent, order: list[str], rng: random.Random | None = None) -> list[Variant]:
    """Новая картинка: варианты раздаются по кругу — у каждого художника свой стиль, лучшие идут первыми, при нехватке моделей повторяются с другим seed."""
    rng = rng or random.Random()
    models = intent.models or order
    return [Variant(i + 1, models[i % len(models)], _seed(rng)) for i in range(intent.count)]


def plan_edit(intent: Intent, order: list[str], prev: dict, rng: random.Random | None = None) -> tuple[list[Variant], str]:
    """Правка: один подходящий художник (или тот, что нарисовал выбранный вариант) либо все сразу.
    Тот же художник с прежним seed рисует почти ту же картинку — персонаж не «уплывает».
    → (варианты, почему выбраны именно они)."""
    rng = rng or random.Random()
    pv = {v["n"]: v for v in prev.get("variants", [])}
    base = [pv[n] for n in intent.refs if n in pv]

    def seed_of(mid: str) -> int:
        for v in base or list(pv.values()):
            if v.get("model") == mid and v.get("seed"):
                return int(v["seed"])
        return _seed(rng)

    if intent.models:
        chosen, why = intent.models, "вы сами назвали " + ", ".join(title(m) for m in intent.models)
    elif intent.everyone:
        chosen, why = list(order), "вы попросили сделать это во всех моделях"
    elif base:
        chosen, why = [v["model"] for v in base if v.get("model")], "правка нужна на выбранном варианте — рисует тот же художник, что и раньше"
    else:
        chosen, why = [order[0]], ""
    seen, models = set(), []
    for m in chosen:
        if m not in seen:
            seen.add(m)
            models.append(m)
    models = [m for m in models if m in order or m in {v.get("model") for v in base}] or [order[0]]
    # у выбранного варианта берём его seed (картинка меняется минимально), остальным — прежний seed этого художника или новый
    variants = []
    for i, m in enumerate(models):
        s = int(base[i]["seed"]) if base and i < len(base) and base[i].get("model") == m and base[i].get("seed") else seed_of(m)
        variants.append(Variant(i + 1, m, s))
    return variants, why


# ---------------------------------------------------------------- описание от текстовой модели
BRIEF_TOKENS = 420


def brief_request(user_text: str, ids: list[str], prev_brief: dict | None = None, edit: bool = False) -> list[dict]:
    """Сообщения текстовой модели: написать описание картинки — тегами и предложениями — и назвать лучшего художника."""
    lines = "\n".join(f"- {mid}: {artist(mid)['hint']}" for mid in ids)
    best_rule = "Choose BEST for the kind of change: " if edit else "Choose BEST for this exact request: "
    best_rule += " ".join(x for x in (
        "poses, actions and interactions → ponyxl." if "ponyxl" in ids else "",
        "how the character looks (face, hair, outfit) in anime style → noobai." if "noobai" in ids else "",
        "photos, realism, background, lighting, objects, text → flux2klein." if "flux2klein" in ids else "") if x)
    sysmsg = (
        "You are the art director of an image studio. You never draw: you write prompts for the studio's image models, and the best-suited model then paints.\n"
        f"Available models:\n{lines}\n"
        "Reply with EXACTLY these four lines and nothing else (no markdown, no quotes, no explanations):\n"
        "TAGS: comma-separated English danbooru-style tags: 1girl/1boy, hair colour and style, eye colour, expression, clothing, pose, camera angle, setting, lighting, art style. At most 60 tags.\n"
        "SCENE: the same picture in 1-3 plain English sentences (subject, appearance, clothing, pose, setting, lighting, style).\n"
        f"BEST: one of {', '.join(ids)}. {best_rule}\n"
        "WHY: one short sentence in Russian saying why that model fits.\n"
        "Rules: the user may write in Russian. Keep EVERY concrete detail the user named (colours, clothing, weapon, setting, mood) and add nothing that contradicts it. "
        "If the user asks for anime, begin TAGS with 'anime illustration, 2D, cel shading, flat colors, lineart' and never use the words realistic, photo, 3D, render. "
        "Hands cause artifacts: unless the user asks for a pose with hands or an object in hands, frame the character as 'portrait, upper body' and keep hands out of frame; "
        "do not invent held objects. "
        "Turning the character: 'turn around' → 'from behind, looking back'; 'turn to the side' → 'profile, from side'; 'half-turn' → 'three-quarter view'; "
        "'look away' → 'facing away'; camera angle → 'from above' / 'from below'. At most 70 words in SCENE.")
    user = user_text.strip()[:1500]
    if prev_brief:
        user = (f"The current picture was described as:\nTAGS: {prev_brief.get('tags', '')[:700]}\nSCENE: {prev_brief.get('scene', '')[:500]}\n"
                "The user now asks for a CHANGE. Rewrite TAGS and SCENE so the picture matches the change: keep the same character (hair, eyes, clothing, setting, style) and "
                f"everything the user did not ask to change.\nChange requested: {user}")
    else:
        user = "Request: " + user
    return [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]


_LABEL = re.compile(r"^[\s>*_#-]*(TAGS|SCENE|BEST|WHY)[\s*_]*[:：][\s*_]*(.*)$", re.I | re.M)
_ID_WORDS = {"noobai": "noob", "ponyxl": "pony", "flux2klein": "flux", "animagine4": "animagine", "illustrious1": "illustrious"}


def _strip(raw: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    return re.sub(r"<think>.*", "", s, flags=re.S | re.I).strip()       # размышление, оборванное на середине


def _one_line(s: str, limit: int) -> str:
    s = re.sub(r"\s+", " ", s.replace("`", "")).strip().strip("\"'«»")
    return s[:limit].rstrip(" ,;")


def parse_brief(raw: str, fallback: str, ids: list[str] | None = None) -> dict:
    """Ответ модели → {"tags","scene","best","why","ok"}. Не по формату — берём весь ответ как описание; пусто — сама просьба."""
    s = _strip(raw)
    got = {k.upper(): v for k, v in _LABEL.findall(s)}
    tags, scene = _one_line(got.get("TAGS", ""), 700), _one_line(got.get("SCENE", ""), 600)
    ok = bool(tags or scene)
    if not ok:
        whole = _one_line(s.split("\n")[0] if s else "", 700)
        tags = scene = whole if len(whole) >= 8 else _one_line(fallback, 400)
    tags, scene = tags or scene, scene or tags
    best = ""
    v = (got.get("BEST") or "").lower()
    for mid in (ids or list(ARTISTS)):
        if mid.lower() in v or _ID_WORDS.get(mid, mid) in v:
            best = mid
            break
    return {"tags": tags, "scene": scene, "best": best, "why": _one_line(got.get("WHY", ""), 200), "ok": ok}


def fallback_brief(user_text: str) -> dict:
    """Текстовая модель недоступна или ответила мусором: собираем теги по словам запроса (аниме) или берём просьбу как есть."""
    text = user_text.strip()[:700]
    if imagesmod.ANIME.search(norm(text)) and imagesmod.has_cyrillic(text):
        tags = imagesmod.ru_prompt(text)
        return {"tags": tags, "scene": tags, "best": "", "why": "", "ok": False}
    return {"tags": text, "scene": text, "best": "", "why": "", "ok": False}


# ---------------------------------------------------------------- готовое описание для каждого художника
def prompt_for(mid: str, brief: dict, style_text: str) -> str:
    """Описание на «языке» художника: теги — для моделей danbooru, предложения — для FLUX; стиль и хвост из исходной просьбы."""
    base = brief["tags"] if artist(mid)["dialect"] == "tags" else brief["scene"]
    return imagesmod.finish_prompt(imagesmod.style_prompt(base, style_text), style_text)


def negative_for(mid: str, style_text: str) -> str:
    """Негативный промпт именно этого художника (у FLUX его нет); для аниме — ещё и против «кукольной» рисовки."""
    m = localimg.by_id(mid) or {}
    neg = m.get("negative", "")
    if neg and imagesmod.ANIME.search(norm(style_text)) and artist(mid)["dialect"] == "tags":
        neg += ", " + ANIME_EXTRA_NEG
    return neg


def style_text_for(prev: dict | None, text: str, edit: bool) -> str:
    """Общий «стиль» просьбы: для правки — исходная просьба плюс то, что просят сейчас («аниме» из первой просьбы не теряется)."""
    if edit and prev:
        return (str(prev.get("request", "")) + " " + text)[-700:]
    return text


def short_note(variants: list[Variant]) -> str:
    return ", ".join(f"{v.n} — {title(v.model)}" for v in variants)
