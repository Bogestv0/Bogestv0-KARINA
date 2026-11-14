"""«Карточка рисунка»: описание разложено по слотам — внешность, одежда, поза, фон, детали.

Зачем. Раньше при каждой правке текстовая модель заново писала всё описание целиком и могла «забыть» одежду или упростить фон
(«на фоне кровать, ламповая комната в фиолетовых тонах, компьютер с двумя мониторами, на мониторах кадры из фильма» → «кровать, ламповая атмосфера, компьютер»).
Теперь:
  • каждый слот хранит ваши слова дословно (по-русски) и их перевод в теги;
  • правка меняет ТОЛЬКО те слоты, о которых вы написали; остальные слоты не проходят через текстовую модель вообще — их теги остаются как были;
  • модель просят переводить каждую мелочь отдельным тегом (2monitors, purple_theme, movie_on_screen …), а не пересказывать;
  • мелкие детали (одежда, фон, «детали») усиливаются весом (tag:1.15), чтобы рисующая модель их не теряла.
Модуль ничего не рисует и не обращается к нейросетям сам — только собирает запрос и разбирает ответ (поэтому легко проверяется тестами).
"""
from __future__ import annotations
import re
from .textutil import norm

SLOTS: tuple[tuple[str, str], ...] = (("look", "Внешность"), ("outfit", "Одежда"), ("pose", "Поза"), ("scene", "Фон"), ("extra", "Детали"))
LABEL = dict(SLOTS)
WEIGHT = {"outfit": 1.1, "scene": 1.1, "extra": 1.2}             # во сколько раз усиливать теги слота (поза усиливается отдельно, imgdirector.emphasize)
_KEYS = {k.upper(): k for k, _ in SLOTS}

SYSTEM = (
    "You split a picture request into slots and translate each slot into English danbooru-style tags for an anime image model. You never draw.\n"
    "Reply with EXACTLY these five lines and nothing else (no markdown, no explanations):\n"
    "LOOK: <the user's own Russian words about the character: who, age, hair, eyes, expression> | <tags: 1girl/1boy, hair, eyes, expression>\n"
    "OUTFIT: <the user's own Russian words about clothing> | <tags>\n"
    "POSE: <the user's own Russian words about pose, action, camera angle> | <tags>\n"
    "SCENE: <the user's own Russian words about background and place> | <tags>\n"
    "EXTRA: <the user's own Russian words about any other detail: objects, lighting, mood, what is on screens> | <tags>\n"
    "Rules:\n"
    "1. Before the | copy the user's words as they wrote them (shortened only if very long). Never paraphrase, never add anything the user did not say.\n"
    "2. After the | write ONE tag per concrete detail. Keep every colour, number and object: 'two monitors' → '2monitors, multiple_monitors, computer, desk'; "
    "'purple tones' → 'purple_theme, purple_lighting'; 'cozy lamp-lit room' → 'cozy, warm_lighting, lamp, indoors, bedroom'; 'a movie on the screens' → 'movie_on_screen, screen_glow'. "
    "Up to 25 tags for one slot. Do not write style or quality tags.\n"
    "3. If the user said nothing about a slot write '-' for it.\n"
    "Pose cheat-sheet: 'лежит' → lying; 'на спине' → on_back; 'на боку' → on_side; 'на животе' → on_stomach; 'сидит' → sitting; 'на коленях' → kneeling; 'на четвереньках' → all_fours; "
    "'стоит' → standing; 'присела' → squatting; 'руки за головой' → arms_behind_head; 'подняла руки' → arms_up; 'вид сзади' → from_behind; 'вид сверху' → from_above; 'вид снизу' → from_below; "
    "'обернулась' → looking_back; 'на диване' → on_couch; 'на кровати' → on_bed.")

SYSTEM_EDIT = (SYSTEM + "\n4. This is a CHANGE of an existing picture. The current card is shown below. Write a slot line ONLY for the slots the user's new message changes or adds to; "
               "write '-' for every other slot (they stay exactly as they are). For EXTRA, if the user says 'add'/'ещё'/'добавь', write only the NEW details.")


def request(text: str, card: dict | None = None, edit: bool = False) -> list[dict]:
    """Сообщения текстовой модели: разложить просьбу по слотам и перевести слоты в теги."""
    user = text.strip()[:1500]
    if edit and card:
        cur = "\n".join(f"{LABEL[k]}: {card[k]['ru']}" for k, _ in SLOTS if card.get(k) and card[k].get("ru")) or "(пусто)"
        user = f"Current card:\n{cur}\n\nChange requested: {user}"
        return [{"role": "system", "content": SYSTEM_EDIT}, {"role": "user", "content": user}]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Request: " + user}]


_LINE = re.compile(r"^[\s>*_#-]*(LOOK|OUTFIT|POSE|SCENE|EXTRA)[\s*_]*[:：][\s*_]*(.*)$", re.I | re.M)


def _clean(s: str, n: int) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" \t\"'`*")[:n]


def parse(raw: str) -> dict[str, tuple[str, str]]:
    """Ответ модели → {слот: (слова пользователя, теги)}; слоты с «-» и пустые пропускаются."""
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    out: dict[str, tuple[str, str]] = {}
    for key, val in _LINE.findall(s):
        val = val.strip()
        if val in ("", "-", "—", "–", "none", "None", "n/a", "N/A") or norm(val) in ("не указано", "нет"):
            continue
        ru, sep, tags = val.partition("|")
        ru, tags = _clean(ru, 400), _clean(tags, 500)
        if not sep:                                              # модель забыла «|»: всё, что латиницей, — теги, иначе — слова пользователя
            tags, ru = (ru, "") if not re.search(r"[а-яё]", ru, re.I) else ("", ru)
        if re.search(r"[а-яё]", tags, re.I):
            tags = ""                                            # русский в тегах не нужен
        if ru or tags:
            out[_KEYS[key.upper()]] = (ru or tags, tags)
    return out


# ---------------------------------------------------------------- запасной разбор без нейросети
_KW = (
    ("outfit", r"одежд|плать|юбк|футболк|майк|купальник|бикини|форм[аыуе]|кимоно|свитер|куртк|худи|наряд|обув|чулк|носк|пальто|рубашк|шорт|джинс|костюм|переодень|одет|одела"),
    ("pose", r"поз[ауеы]|леж|сид[иея]|стоит|стоя|на коленях|четверен|повер|вид сзади|вид сверху|вид снизу|обернул|прыга|бежит|идёт|идет|руки|ноги|потягива|присел"),
    ("scene", r"фон|комнат|на фоне|улиц|пляж|лес[уеа]?|город|интерьер|помещени|кухн|спальн|парк|небо|закат|ночью|днём|днем|кроват|диван|окн"),
    ("look", r"волос|глаз|причёс|прическ|лиц|возраст|внешност|рост|улыб|взгляд|эмоци|выражени"),
)


def fallback(text: str, card: dict | None, edit: bool) -> dict[str, tuple[str, str]]:
    """Текстовая модель недоступна: слот выбираем по словам, теги — прежним словарным переводом (images.ru_prompt)."""
    from . import images as imagesmod
    t = norm(text)
    key = "look" if not edit else "extra"
    if edit:
        for k, rx in _KW:
            if re.search(rx, t):
                key = k
                break
    try:
        tags = imagesmod.ru_prompt(text)
    except Exception:
        tags = ""
    return {key: (_clean(text, 400), _clean(tags, 500))}


# ---------------------------------------------------------------- карточка
def card_from(brief: dict | None) -> dict:
    """Карточка из прежнего описания. У описаний старого вида слотов нет — всё прежнее остаётся одним куском («Внешность»), ничего не теряется."""
    if not brief:
        return {}
    if isinstance(brief.get("slots"), dict):
        return {k: dict(v) for k, v in brief["slots"].items() if isinstance(v, dict)}
    tags = str(brief.get("tags") or "").strip()
    return {"look": {"ru": str(brief.get("ru") or "")[:400] or "(прежнее описание)", "tags": tags}} if tags else {}


def _words(s: str) -> set[str]:
    return {w[:4] for w in re.findall(r"[a-zа-яё]{4,}", norm(s))}


def merge(card: dict, upd: dict[str, tuple[str, str]], text: str = "", edit: bool = False) -> dict:
    """Новые слоты заменяют старые; не названные остаются как были. «Детали» при «добавь/ещё» дописываются.
    В правке слот, о котором в вашей фразе нет ни слова, игнорируется: так модель не может «тихо переписать» одежду, когда вы просили фон."""
    out = {k: dict(v) for k, v in (card or {}).items()}
    said = _words(text)
    for key, (ru, tags) in upd.items():
        if edit and card and key in card and said and not (_words(ru) & said):
            continue
        if key == "extra" and card and card.get("extra") and re.search(r"добав|ещё|еще|также|плюс", norm(text)):
            old = card["extra"]
            out["extra"] = {"ru": (old["ru"] + ", " + ru)[:500], "tags": ", ".join(x for x in (old.get("tags", ""), tags) if x)}
        else:
            if key == "look" and card and card.get("look"):                       # «1girl / 2boys» не должен пропасть, когда меняют только волосы или глаза
                cnt = [t.strip() for t in card["look"].get("tags", "").split(",") if re.fullmatch(r"\d+(?:girls?|boys?|others?)|solo", t.strip().lower())]
                if cnt and not any(re.fullmatch(r"\d+(?:girls?|boys?|others?)|solo", t.strip().lower()) for t in tags.split(",")):
                    tags = ", ".join(cnt + ([tags] if tags else []))
            out[key] = {"ru": ru, "tags": tags}
    return out


def _weighted(tags: str, w: float) -> list[str]:
    res = []
    for t in (x.strip() for x in tags.split(",")):
        if not t:
            continue
        res.append(t if (":" in t or w == 1) else f"({t}:{w:g})")
    return res


def compose(card: dict) -> str:
    """Все теги карточки одной строкой: внешность, поза, одежда, детали, фон (мелочи усилены)."""
    parts: list[str] = []
    for key in ("look", "pose", "outfit", "extra", "scene"):
        c = card.get(key)
        if c and c.get("tags"):
            parts += _weighted(c["tags"], WEIGHT.get(key, 1.0))
    return ", ".join(dict.fromkeys(parts))


def to_brief(card: dict) -> dict:
    """Описание в прежнем формате (tags/scene/ru …) плюс сама карточка (slots) — чтобы следующая правка опиралась на неё."""
    tags = compose(card)
    ru = "; ".join(f"{LABEL[k]}: {card[k]['ru'].replace(';', ',')}" for k, _ in SLOTS if card.get(k) and card[k].get("ru"))
    return {"tags": tags, "scene": tags, "best": "", "why": "", "ok": bool(tags), "ru": ru, "slots": card}


def missing(card: dict) -> list[str]:
    """Названия слотов, о которых пользователь ничего не сказал (Карина может про них спросить)."""
    return [LABEL[k].lower() for k in ("outfit", "pose", "scene") if not (card.get(k) and (card[k].get("ru") or card[k].get("tags")))]
