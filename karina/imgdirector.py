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
from . import images as imagesmod, localimg, prompts
from .textutil import norm

DEFAULT_VARIANTS = 4
MAX_VARIANTS = 6

# Профили художников: на каком «языке» им писать и в чём они сильны.
# dialect: "tags" — теги danbooru; "text" — обычные предложения.  hint — подсказка текстовой модели.
ARTISTS: dict[str, dict] = {
    "messillustrious": {"dialect": "tags", "anime": True, "real": False, "pose": 0.4, "why": "рисует в фирменном стиле Карины — как кадры современного аниме",
                        "hint": prompts.ARTIST_HINTS["messillustrious"]},
    "noobai": {"dialect": "tags", "anime": True, "real": False, "pose": 0.5, "why": "лучше всего рисует персонажей, лица, причёски и одежду в аниме-стиле",
               "hint": prompts.ARTIST_HINTS["noobai"]},
    "ponyxl": {"dialect": "tags", "anime": True, "real": False, "pose": 2.0, "why": "лучше всего слушается просьб про позы, действия и сложные сцены",
               "hint": prompts.ARTIST_HINTS["ponyxl"]},
    "flux2klein": {"dialect": "text", "anime": False, "real": True, "pose": 0.0, "why": "лучше всего понимает обычные предложения, фон, свет, предметы и надписи",
                   "hint": prompts.ARTIST_HINTS["flux2klein"]},
    "animagine4": {"dialect": "tags", "anime": True, "real": False, "pose": 0.3, "why": "рисует чистое аниме «как в кадрах сериала»",
                   "hint": prompts.ARTIST_HINTS["animagine4"]},
    "illustrious1": {"dialect": "tags", "anime": True, "real": False, "pose": 0.3, "why": "знает много стилей и персонажей аниме",
                     "hint": prompts.ARTIST_HINTS["illustrious1"]},
}
GENERIC = {"dialect": "text", "anime": False, "real": False, "pose": 0.0, "why": "универсальная модель", "hint": prompts.ARTIST_HINT_GENERIC}
# при равных баллах: для аниме первым идёт NoobAI, для остального — FLUX.2 Klein
ORDER_ANIME = ("messillustrious", "noobai", "ponyxl", "animagine4", "illustrious1", "flux2klein")
ORDER_OTHER = ("flux2klein", "messillustrious", "noobai", "ponyxl", "animagine4", "illustrious1")

ANIME_EXTRA_NEG = prompts.ANIME_EXTRA_NEG      # см. prompts.py, раздел 7.3


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
_MODEL_LAT = {"messillustrious": r"mess\w*", "noobai": r"noob\w*", "ponyxl": r"pony\w*", "flux2klein": r"flux\w*|klein\w*", "animagine4": r"animagine\w*", "illustrious1": r"illustrious\w*"}
# русские написания узнаём только после предлога («через пони», «моделью флюкс»): иначе «нарисуй пони» — это лошадка, а не модель
_MODEL_CYR = {"messillustrious": r"мес+\w*", "noobai": r"нуб\w*|нооб\w*", "ponyxl": r"пони", "flux2klein": r"флюкс\w*|флакс\w*|клейн\w*", "animagine4": r"анимаджин\w*", "illustrious1": r"иллюстриус\w*"}
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
    """Новая картинка: все варианты рисует ОДИН художник (тот, что в фирменном стиле), у каждого свой seed — стиль не «прыгает» от варианта к варианту.
    Если пользователь сам назвал модели, варианты раздаются по ним по кругу."""
    rng = rng or random.Random()
    models = intent.models or order[:1]
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
    best_rule = prompts.DIRECTOR_BEST_EDIT if edit else prompts.DIRECTOR_BEST_NEW
    best_rule += " ".join(x for x in (
        prompts.DIRECTOR_BEST_PONY if "ponyxl" in ids else "",
        prompts.DIRECTOR_BEST_NOOBAI if "noobai" in ids else "",
        prompts.DIRECTOR_BEST_FLUX if "flux2klein" in ids else "") if x)
    sysmsg = prompts.fill(prompts.DIRECTOR_SYSTEM, models=lines, ids=", ".join(ids), best_rule=best_rule) + prompts.forbidden_inline(prompts.FORBIDDEN_IMAGE_WRITER)
    user = user_text.strip()[:1500]
    if prev_brief:
        user = prompts.fill(prompts.DIRECTOR_USER_EDIT, tags=prev_brief.get("tags", "")[:700], scene=prev_brief.get("scene", "")[:500], request=user)
    else:
        user = prompts.fill(prompts.DIRECTOR_USER_NEW, request=user)
    return [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]


_LABEL = re.compile(r"^[\s>*_#-]*(TAGS|SCENE|RU|BEST|WHY)[\s*_]*[:：][\s*_]*(.*)$", re.I | re.M)
_ID_WORDS = {"messillustrious": "mess", "noobai": "noob", "ponyxl": "pony", "flux2klein": "flux", "animagine4": "animagine", "illustrious1": "illustrious"}


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
    return {"tags": tags, "scene": scene, "best": best, "why": _one_line(got.get("WHY", ""), 200), "ok": ok, "ru": _one_line(got.get("RU", ""), 400)}


def fallback_brief(user_text: str) -> dict:
    """Текстовая модель недоступна или ответила мусором: собираем теги по словам запроса (аниме) или берём просьбу как есть."""
    text = user_text.strip()[:700]
    if imagesmod.ANIME.search(norm(text)) and imagesmod.has_cyrillic(text):
        tags = imagesmod.ru_prompt(text)
        return {"tags": tags, "scene": tags, "best": "", "why": "", "ok": False, "ru": ""}
    return {"tags": text, "scene": text, "best": "", "why": "", "ok": False, "ru": ""}


# ---------------------------------------------------------------- готовое описание для каждого художника
_COUNT_TAG = re.compile(r"^(?:\d+(?:girls?|boys?|others?)|solo|multiple (?:girls|boys))$")


def _core(tag: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^\(+|\)+$|:\d+(?:\.\d+)?\)*$", "", tag.strip())).strip().lower()


def emphasize(tags: str, weight: float = prompts.POSE_WEIGHT) -> str:
    """Поза и ракурс — самое важное в просьбе и то, что модели чаще всего «забывают»: выносим их вперёд (сразу после «1girl») и усиливаем (lying:1.25)."""
    pose_set = {p.lower() for p in prompts.POSE_TAGS}
    subject, pose, rest = [], [], []
    for t in (x.strip() for x in tags.split(",")):
        if not t:
            continue
        c = _core(t)
        if _COUNT_TAG.match(c):
            subject.append(t)
        elif c in pose_set:
            pose.append(t if ":" in t else f"({c}:{weight:g})")
        else:
            rest.append(t)
    return ", ".join(dict.fromkeys(subject + pose + rest))


def prompt_for(mid: str, brief: dict, style_text: str, force_style: bool = False) -> str:
    """Описание на «языке» художника: теги — для моделей danbooru (поза усилена), предложения — для FLUX; фирменный стиль добавляется всегда."""
    tags = artist(mid)["dialect"] == "tags"
    base = emphasize(brief["tags"]) if tags else brief["scene"]
    if tags:
        base = imagesmod.style_prompt(base, style_text, force_style)
    return imagesmod.finish_prompt(base, style_text, force_style and tags)


def negative_for(mid: str, style_text: str, force: bool = False) -> str:
    """Негативный промпт именно этого художника (у FLUX его нет); для аниме — ещё и против «кукольной» рисовки."""
    m = localimg.by_id(mid) or {}
    neg = m.get("negative", "")
    if neg and (force or imagesmod.ANIME.search(norm(style_text))) and artist(mid)["dialect"] == "tags":
        neg += ", " + ANIME_EXTRA_NEG
    return neg


def style_text_for(prev: dict | None, text: str, edit: bool) -> str:
    """Общий «стиль» просьбы: для правки — исходная просьба плюс то, что просят сейчас («аниме» из первой просьбы не теряется)."""
    if edit and prev:
        return (str(prev.get("request", "")) + " " + text)[-700:]
    return text


def short_note(variants: list[Variant]) -> str:
    return ", ".join(f"{v.n} — {title(v.model)}" for v in variants)
