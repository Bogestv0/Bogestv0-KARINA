"""Поток рисования: режим «рисую», подтверждение перед рисованием и цепочка «стиль → поза → проверка».

Три идеи в одном модуле (без обращений к нейросетям и диску, поэтому всё проверяется тестами):

1. РЕЖИМ РИСОВАНИЯ. После картинки чат остаётся «в рисовании»: любое следующее сообщение — правка этой картинки, а не поиск в интернете.
   Под картинкой три кнопки: «Продолжить редактировать», «Новый рисунок», «Закрыть рисование» (после закрытия Карина снова обычный помощник).
2. ПОДТВЕРЖДЕНИЕ. Перед тем как отдать описание художнику, Карина пишет, что собирается нарисовать (персонаж, поза, одежда, фон, формат, число вариантов),
   и ждёт «Рисовать», «Изменить» (или свою правку обычным текстом) либо «Отмена».
3. ЦЕПОЧКА МОДЕЛЕЙ. Один «стилист» (Mess Illustrious Anime Mix) рисует в фирменном стиле, второй «позировщик» (NoobAI) перерисовывает позу,
   затем снова стилист; после каждого круга «тегер» проверяет, нарисовано ли то, что просили (imgcheck.py). Максимум 3 круга: для медленной видеокарты
   каждый круг — минуты. Из всех кругов берётся лучший по совпадению с просьбой.
"""
from __future__ import annotations
import io, re
from dataclasses import dataclass, field
from pathlib import Path
from . import imgcheck, prompts
from .textutil import norm

MAX_ROUNDS = 3
IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "bmp", "gif"}

# ---- кнопки под ответами Карины (как они приходят обратно в чат, пока без эмодзи — norm() их убирает)
BTN_DRAW, BTN_EDIT, BTN_CANCEL = "✅ Рисовать", "✏️ Изменить", "✖ Отмена"
BTN_CONTINUE, BTN_NEW, BTN_CLOSE = "✏️ Продолжить редактировать", "🎨 Новый рисунок", "✖ Закрыть рисование"
DRAFT_BUTTONS = [BTN_DRAW, BTN_EDIT, BTN_CANCEL]
DONE_BUTTONS = [BTN_CONTINUE, BTN_NEW, BTN_CLOSE]


# ---------------------------------------------------------------- что сказал пользователь (кнопка или слова)
_CLOSE = re.compile(r"^(?:закр\w+|законч\w+|выйд\w+|выйти|выход\w*|хватит|стоп|прекрат\w+|отключ\w+)\b.*\b(?:рисован\w*|рисовать|рисунк\w*|режим\w*)\b|^больше не рисуй$")
_CONFIRM = re.compile(r"^(?:рисовать|рисуй|рисуйте|да|ага|угу|ок|окей|ok|okay|давай|верно|все верно|все правильно|правильно|подтверждаю|подтвердить|поехали|го|начинай|начни|"
                      r"запускай|делай|все хорошо|норм|нормально|так и рисуй|рисуй так|отлично|хорошо|согласна|согласен)(?: (?:пожалуйста|пож|уже|так|давай|все|быстрее|скорее|рисуй))*$")
_CANCEL = re.compile(r"^(?:отмена|отменить|отмени|не надо|не нужно|не рисуй|передумал\w*|стоп|хватит|не хочу)$")
_EDIT_BTN = re.compile(r"^изменить$")
_CONT_BTN = re.compile(r"^продолжить редактир\w*$")
_NEW_BTN = re.compile(r"^новый рисунок$")


def classify(text: str, has_draft: bool) -> str:
    """Служебные фразы режима рисования: close | confirm | cancel | edit_ask | continue | new | "" (обычное сообщение)."""
    t = norm(text)
    if not t:
        return ""
    if _CLOSE.search(t):
        return "close"
    if _CONT_BTN.match(t):
        return "continue"
    if _NEW_BTN.match(t):
        return "new"
    if has_draft:
        if _CONFIRM.match(t):
            return "confirm"
        if _CANCEL.match(t):
            return "cancel"
        if _EDIT_BTN.match(t):
            return "edit_ask"
    return ""


# ---------------------------------------------------------------- картинки пользователя
def is_image_path(p: str) -> bool:
    return str(p).rsplit(".", 1)[-1].lower() in IMAGE_EXT if "." in str(p) else False


def image_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if is_image_path(p)]


def only_images(paths: list[str]) -> bool:
    return bool(paths) and all(is_image_path(p) for p in paths)


def strength_for(text: str, default: float = 0.62) -> float:
    """Насколько сильно перерисовывать присланную картинку: «чуть-чуть, цвет, свет» — слабо; «позу, фон, одежду, в аниме» — сильнее."""
    t = norm(text)
    if re.search(r"чуть|слегка|немного|незначительно|аккуратно|только (?:цвет|свет)|освещени|ярче|темнее|светлее|контраст|цвет\w* ", t) and not re.search(r"поз[ауеы]|повер|сид|леж|фон|одежд|наряд|в аниме|в стиле", t):
        return 0.40
    if re.search(r"поз[ауеы]|повер|ракурс|сид\w+|леж\w+|стоя|одежд|наряд|переодень|прическ|совсем|полностью|с нуля|заново", t):
        return 0.72
    if re.search(r"фон|в аниме|в стиле|перерисуй|нарисуй по|как аниме|раскрась", t):
        return 0.62
    return default


# ---------------------------------------------------------------- кто что рисует
STYLE_ORDER = ("messillustrious", "noobai", "illustrious1", "animagine4", "ponyxl")
POSE_ORDER = ("noobai", "ponyxl", "illustrious1", "animagine4", "messillustrious")


def pick(order: tuple[str, ...], ids: list[str]) -> str:
    for m in order:
        if m in ids:
            return m
    return ""


def roles(ids: list[str]) -> dict[str, str]:
    """{"style": стилист, "pose": позировщик}. Скачана одна модель — она делает обе работы. Нет ни одной аниме-модели SDXL — пусто."""
    style = pick(STYLE_ORDER, ids)
    return {"style": style, "pose": pick(POSE_ORDER, ids) or style} if style else {}


def schedule(kind: str, rounds: int = MAX_ROUNDS) -> list[tuple[str, float | None]]:
    """Круги: (роль, сила перерисовки; None — рисуем с нуля).
    new — с нуля в стиле, потом поправить позу, потом вернуть стиль; edit — поправить позу (сильнее, если не вышло), вернуть стиль;
    upload — картинка пользователя: сначала в стиль, потом поза, потом стиль ещё раз."""
    base = {"new": [("style", None), ("pose", 0.55), ("style", 0.45)],
            "edit": [("pose", 0.65), ("pose", 0.78), ("style", 0.40)],
            "upload": [("style", 0.62), ("pose", 0.70), ("style", 0.40)]}.get(kind, [("style", None)])
    return base[:max(1, min(MAX_ROUNDS, int(rounds or MAX_ROUNDS)))]


def boost(tags: str, missing: list[str], weight: float = 1.4) -> str:
    """Теги, которых не хватило на картинке, ставятся вперёд и усиливаются: (lying:1.4)."""
    if not missing:
        return tags
    want = [m.replace("_", " ") for m in missing]
    have = {imgcheck.norm_tag(t) for t in want}
    rest = [t.strip() for t in tags.split(",") if t.strip() and imgcheck.norm_tag(t) not in have]
    first = rest[:1] if rest and re.fullmatch(r"\d+(?:girls?|boys?)|solo", imgcheck.norm_tag(rest[0])) else []
    return ", ".join(first + [f"({w}:{weight:g})" for w in want] + rest[len(first):])


@dataclass
class Pass:
    n: int
    role: str
    model: str
    strength: float | None
    data: bytes
    prompt: str
    verdict: dict | None = None


def pick_best(passes: list[Pass]) -> int:
    """Лучший круг по совпадению с просьбой (при равенстве — поздний: он ближе к фирменному стилю). Нет проверки — последний."""
    scored = [(p.verdict["coverage"], p.n, i) for i, p in enumerate(passes) if p.verdict]
    return max(scored)[2] if scored else len(passes) - 1


def run_variant(kind: str, model_of: dict[str, str], rounds: int, tags: str, make_prompt, draw, check=None, init: bytes | None = None, on_pass=None, stop=None,
                passes: list[Pass] | None = None) -> list[Pass]:
    """Один вариант картинки по кругам.
    make_prompt(model, tags) → (описание, негатив); draw(model, описание, негатив, init, strength, n, role) → байты PNG;
    check(байты, tags) → вердикт imgcheck.verdict или None (проверки нет — рисуем один круг); on_pass(Pass, всего кругов) — сообщить о готовом круге.
    passes — список, который заполняется по ходу: если какой-то круг упал с ошибкой, вызывающий всё равно видит уже готовые."""
    sched = schedule(kind, rounds)
    passes = [] if passes is None else passes
    cur, cur_tags, i = init, tags, 0
    while i < len(sched):
        if stop is not None and stop.is_set():
            break
        role, strength = sched[i]
        model = model_of[role]
        prompt, neg = make_prompt(model, cur_tags)
        data = draw(model, prompt, neg, cur, strength, len(passes) + 1, role)
        v = check(data, tags) if check else None
        p = Pass(len(passes) + 1, role, model, strength, data, prompt, v)
        passes.append(p)
        if on_pass:
            on_pass(p, len(sched))
        if v is None:
            break                                  # проверить нечем — один круг
        if v["ok"]:
            last_style = sched[-1][0] == "style" and i < len(sched) - 1
            if role == "pose" and last_style and model_of["style"] != model_of["pose"]:
                cur, cur_tags, i = data, tags, len(sched) - 1          # поза верна — остался круг «вернуть стиль»
                continue
            break
        cur, cur_tags = data, boost(tags, v["missing"])
        i += 1
    return passes


# ---------------------------------------------------------------- картинки: подготовка и обои
def _fit64(w: int, h: int, area_mp: float = 1.0) -> tuple[int, int]:
    import math
    k = min(1.0, math.sqrt(area_mp * 1e6 / (w * h)))
    return max(448, int(round(w * k / 64)) * 64), max(448, int(round(h * k / 64)) * 64)


def prepare_init(data: bytes, area_mp: float = 1.0) -> tuple[bytes, tuple[int, int]]:
    """Присланная картинка → основа для img2img: размер кратен 64, площадь до area_mp мегапикселей, пропорции сохраняются (лишнее обрезается по краям). → (PNG, (w, h))."""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = _fit64(im.width, im.height, area_mp)
    k = max(tw / im.width, th / im.height)
    im = im.resize((max(tw, round(im.width * k)), max(th, round(im.height * k))), Image.LANCZOS)
    left, top = (im.width - tw) // 2, (im.height - th) // 2
    im = im.crop((left, top, left + tw, top + th))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue(), (tw, th)


def cover(data: bytes, w: int, h: int) -> bytes:
    """Картинка нужного размера w×h: масштабируется «с запасом» и обрезается по краям (без искажения пропорций). PNG."""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    k = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * k)), max(h, round(im.height * k))), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    out = io.BytesIO()
    im.crop((left, top, left + w, top + h)).save(out, "PNG")
    return out.getvalue()


def size_of(data: bytes) -> tuple[int, int]:
    from PIL import Image
    return Image.open(io.BytesIO(data)).size


def to_wallpaper(data: bytes, target: tuple[int, int]) -> bytes:
    """Увеличивает картинку до размера обоев (Lanczos) и обрезает по краям до точного 16:9; PNG."""
    from PIL import Image, ImageFilter
    im = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = target
    k = max(tw / im.width, th / im.height)
    im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    left, top = (im.width - tw) // 2, (im.height - th) // 2
    im = im.crop((left, top, left + tw, top + th)).filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------- текст подтверждения
def ru_items(ru: str) -> list[tuple[str, str]]:
    """«Персонаж: девушка; Поза: лежит; …» → [("Персонаж", "девушка"), ("Поза", "лежит")]."""
    out = []
    for part in re.split(r";|\n", ru or ""):
        k, _, v = part.partition(":")
        if v.strip() and k.strip() and len(k) < 20:
            out.append((k.strip().capitalize(), v.strip()))
    return out


def draft_text(brief: dict, request: str, kind: str, style_title: str, size: tuple[int, int], count: int, rounds: int, check: str,
               wallpaper: tuple[int, int] | None = None, uploaded: bool = False, models_note: str = "", notes: list[str] | None = None) -> str:
    """Что Карина собирается нарисовать — для подтверждения. Всё на русском и коротко, чтобы глазами сверить с тем, что хотелось."""
    head = {"new": "Вот что я нарисую", "edit": "Вот как я поправлю картинку", "more": "Нарисую ещё варианты по прежнему описанию"}.get(kind, "Вот что я нарисую")
    if uploaded:
        head = "Вот как я переделаю вашу картинку"
    lines = [f"**{head}:**"]
    items = ru_items(brief.get("ru", ""))
    if items:
        lines += [f"• **{k}:** {v}" for k, v in items]
    else:
        lines.append(f"• **По вашему запросу:** {request.strip()[:240]}")
        lines.append(f"• **Теги:** {brief.get('tags', '')[:300]}")
    shape = "вертикальная" if size[1] > size[0] else "широкая" if size[0] > size[1] else "квадрат"
    if wallpaper:
        lines.append(f"• **Формат:** обои {wallpaper[0]}×{wallpaper[1]} (рисую широкий кадр {size[0]}×{size[1]} и увеличиваю)")
    else:
        lines.append(f"• **Формат:** {shape}, {size[0]}×{size[1]}")
    lines.append(f"• **Стиль:** аниме как в кадрах современных сериалов ({style_title})")
    if kind != "more" or count:
        lines.append(f"• **Вариантов:** {count}" + (f" · проверяю результат и при необходимости поправляю позу (до {rounds} кругов)" if check in ("on", "download") and rounds > 1 else ""))
    if check == "download":
        lines.append("• Для проверки результата один раз скачаю маленькую модель-«тегер» (~0,4 ГБ).")
    elif check == "nopkg":
        lines.append("• ⚠ Автопроверка недоступна: не установлен пакет onnxruntime (`pip install onnxruntime`) — рисую один круг.")
    elif check == "off":
        lines.append("• Автопроверка выключена — рисую один круг.")
    if models_note:
        lines.append("ⓘ " + models_note)
    for n in notes or []:
        lines.append(n)
    lines.append("\nВсё верно? Нажмите **«Рисовать»** — или напишите, что поменять: «сделай волосы длиннее», «пусть сидит», «ночью», «2 варианта», «обои 4k».")
    return "\n".join(lines)


def count_from(text: str, default: int, maximum: int = 6) -> tuple[int, bool]:
    """Число вариантов из фразы («3 варианта», «два варианта», «один вариант»). → (число, назвал ли пользователь)."""
    t = norm(text)
    words = {"один": 1, "одну": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6}
    m = re.search(r"\b(\d|один|одну|одно|два|две|три|четыре|пять|шесть)\s+(?:варианта|вариантов|вариант|картинки|картинок|картинку|изображения|изображений|рисунка|рисунков|рисунок|штуки|штук)\b", t)
    if not m:
        return default, False
    w = m.group(1)
    n = int(w) if w.isdigit() else words[w]
    return max(1, min(maximum, n)), True
