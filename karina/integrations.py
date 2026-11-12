"""Голосовое управление соседними программами Bogestv0: микшер (громкость дорожек, сцены) и расширение VPN.

Команды стоят в начале реестра (first=True): «сделай тише музыку» и «включи впн» не должны попасть в общие «тише» (громкость всего
компьютера) и «включи …» (открыть программу).
"""
from __future__ import annotations
import re
from rapidfuzz import fuzz
from .actions import command, NOT_HANDLED, Ctx
from .mixer_remote import MixerRemote, MixerError, read_mixer_settings, pct_to_db, clamp_db
from .vpn_link import VpnError
from .textutil import norm, parse_number, plural, _UNITS, _TENS

# ======================================================================= микшер
# Дорожки по умолчанию (как в микшере). Если в микшере их переименовали, подходят и новые имена, и слова ниже.
DEFAULT_STRIPS = [
    ("Микрофон 1", "HW1"), ("Микрофон 2", "HW2"), ("Игры", "V1"), ("Музыка", "V2"),
    ("Discord", "V3"), ("Донаты", "V4"), ("Браузер", "V5"), ("Браузер 2", "V6"),
]
# Начала слов, по которым узнаём дорожку (по тегу). «игр» ловит игра, игры, игру, игре, играх.
STEMS = {
    "V1": ["игр", "гейм"],
    "V2": ["музык", "плеер", "плеир", "спотиф", "spotify"],
    "V3": ["дискорд", "discord"],
    "V4": ["донат", "donat", "оповещен", "алерт"],
    "V5": ["браузер", "хром", "chrome", "ютуб", "youtube"],
    "V6": ["firefox", "фаерфокс", "файрфокс", "фаерфокс"],
    "HW1": ["микрофон"],
    "HW2": ["микрофон"],
}
_PAIRS = [("V5", "V6"), ("HW1", "HW2")]     # дорожки с общим словом: «браузер» / «второй браузер», «микрофон» / «второй микрофон»

_RE_MUTE = re.compile(r"\b(?:заглуши\w*|замьют\w*|мьют\w*|mute|заткни\w*)\b|\b(?:выключи|отключи|убери|вырубь|отруби)\s+(?:весь\s+|полностью\s+)?звук\b|\bбез\s+звука\b")
_RE_UNMUTE = re.compile(r"\b(?:размьют\w*|unmute|анмьют\w*)\b|\b(?:включи|верни|вруби|возобнови)\s+(?:весь\s+)?звук\b")
_RE_DOWN = re.compile(r"\b(?:тише|потише|убав\w+|уменьш\w+|пониз\w+|приглуш\w+|сбав\w+|снизь|тихо)\b")
_RE_UP = re.compile(r"\b(?:громче|погромче|прибав\w+|добав\w+|увелич\w+|повыс\w+|усил\w+|подними|подкрути)\b")
_RE_SMALL = re.compile(r"\b(?:чуть|немного|слегка|капельку|немножко|полегче)\b")
_RE_BIG = re.compile(r"\b(?:намного|гораздо|сильно|значительно|резко|много)\b")
_NUM_WORDS = set(_UNITS) | set(_TENS) | {"сто"}


def find_number(text: str) -> int | None:
    """Первое число во фразе: «на 30», «на тридцать пять процентов»."""
    run: list[str] = []
    for tok in text.split() + [""]:
        if tok and (tok.isdigit() or tok in _NUM_WORDS):
            run.append(tok)
            continue
        if run:
            n = parse_number(" ".join(run))
            if n is not None:
                return n
            run = []
    return None


def resolve_strips(text: str, strips: list[dict], aliases: dict | None = None) -> list[dict]:
    """Какие дорожки имеются в виду во фразе. strips: [{i, name, tag, hidden}]."""
    tokens = text.split()
    aliases = {norm(k): [norm(a) for a in v] for k, v in (aliases or {}).items()}

    def hit(stems) -> bool:
        return any(t.startswith(s) for t in tokens for s in stems if s)

    found: dict[str, dict] = {}
    for s in strips:
        if s.get("hidden"):
            continue
        stems = list(STEMS.get(s.get("tag", ""), []))
        name = norm(s["name"])
        stems += aliases.get(name, [])
        stems += [w[:5] for w in name.split() if len(w) >= 4 and not w.isdigit()]     # новое имя дорожки: «Warzone»
        if hit(stems) or (len(name) >= 3 and name in text):
            found[s.get("tag") or str(s["i"])] = s
    for a, b in _PAIRS:
        if a in found and b in found:
            second = re.search(r"\bвтор\w+\s+(?:браузер|микрофон)\w*|\b(?:браузер|микрофон)\w*\s+(?:2|два|номер два)\b", text)
            first = re.search(r"\bперв\w+\s+(?:браузер|микрофон)\w*|\b(?:браузер|микрофон)\w*\s+(?:1|один|номер один)\b", text)
            if second:
                found.pop(a)
            elif first or a == "V5":
                found.pop(b)
        elif b in found and a not in found and b == "V6" and not re.search(r"firefox|фаерфокс|файрфокс|втор|\b2\b|\bдва\b", text):
            found.pop(b)
    return sorted(found.values(), key=lambda s: s["i"])


def parse_intent(text: str) -> dict | None:
    """{'op': 'down'|'up'|'set'|'mute'|'unmute', 'value': …, 'mult': …} или None."""
    if _RE_MUTE.search(text):
        return {"op": "mute"}
    if _RE_UNMUTE.search(text):
        return {"op": "unmute"}
    mult = 0.5 if _RE_SMALL.search(text) else 2.0 if _RE_BIG.search(text) else 1.0
    down, up = _RE_DOWN.search(text), _RE_UP.search(text)
    num = find_number(text)
    if (down or up) and not (down and up):
        db = num if (num is not None and re.search(r"\b(?:дб|децибел\w*)\b", text)) else None
        return {"op": "down" if down else "up", "mult": mult, "db": db}
    if num is not None and (re.search(r"\bгромкост\w*|\bпроцент\w*|%|\bна\s+\d|\bпоставь\b|\bустанови\b|\bвыставь\b", text)):
        return {"op": "set", "value": num}
    return None


def _strips_local(cfg, mixer) -> list[dict]:
    """Дорожки без подключения: из живого ответа микшера, из файла его настроек, иначе стандартные."""
    live = (mixer.meta or {}).get("strips")
    if live and len(live) >= 8:
        return [{"i": i, "name": str(d.get("name") or f"Дорожка {i + 1}"), "tag": str(d.get("tag") or ""), "hidden": bool(d.get("hidden"))} for i, d in enumerate(live[:8])]
    s = read_mixer_settings(mixer._paths)
    out = []
    for i, d in enumerate(s.get("strips") or []):
        if i < 8 and isinstance(d, dict):
            out.append({"i": i, "name": str(d.get("name") or f"Дорожка {i + 1}"), "tag": str(d.get("tag") or ""), "hidden": bool(d.get("hidden"))})
    if len(out) < 8:
        out = [{"i": i, "name": n, "tag": t, "hidden": False} for i, (n, t) in enumerate(DEFAULT_STRIPS)]
    return out


def _mixer(ctx: Ctx) -> MixerRemote:
    m = getattr(ctx.assistant, "mixer", None) if ctx.assistant else None
    if m is None:
        m = _FALLBACK.get(id(ctx.cfg))
        if m is None:
            m = _FALLBACK[id(ctx.cfg)] = MixerRemote(ctx.cfg)
    return m


_FALLBACK: dict[int, MixerRemote] = {}


def _names(strips: list[dict]) -> str:
    return " и ".join(s["name"] for s in strips)


def apply_mixer(ctx: Ctx, text: str, intent: dict | None = None, target: str | None = None) -> str | object:
    """Общая часть голосовой и LLM-команды. text — нормализованная фраза."""
    mcfg = ctx.cfg.get("mixer") or {}
    if not mcfg.get("enabled", True):
        return NOT_HANDLED
    mixer = _mixer(ctx)
    intent = intent or parse_intent(text)
    if not intent:
        return NOT_HANDLED
    aliases = mcfg.get("aliases") or {}
    pre = resolve_strips(target if target is not None else text, _strips_local(ctx.cfg, mixer), aliases)
    if not pre:
        return NOT_HANDLED                       # про дорожку ни слова — это обычная «тише / громче» для всего компьютера
    try:
        strips = mixer.strips()                  # подключаемся и берём настоящие имена и громкость
        hit = resolve_strips(target if target is not None else text, strips, aliases) or [s for s in strips if s["i"] in {p["i"] for p in pre}]
        if not hit:
            return NOT_HANDLED
        step = float(mcfg.get("step_db") or 3.0)
        op, name = intent["op"], _names(hit)
        if op in ("mute", "unmute"):
            for s in hit:
                mixer.set_mute(s["i"], op == "mute")
            return ("Выключила звук: " if op == "mute" else "Включила звук: ") + name + "."
        if op == "set":
            pct = intent["value"]
            if pct > 100:
                return "Больше ста процентов не ставлю: у дорожки запас только до плюс двенадцати децибел."
            db = pct_to_db(pct)
            for s in hit:
                mixer.set_gain(s["i"], db)
                if s["mute"]:
                    mixer.set_mute(s["i"], False)
            return f"{name}: {pct} {plural(pct, ('процент', 'процента', 'процентов'))}."
        sign = -1 if op == "down" else 1
        delta = sign * (float(intent["db"]) if intent.get("db") else step * intent.get("mult", 1.0))
        moved = 0
        for s in hit:
            new = clamp_db(s["gain"] + delta)
            if abs(new - s["gain"]) < 0.05:
                continue
            mixer.set_gain(s["i"], new)
            moved += 1
        if not moved:
            return f"{name} уже на {'минимуме' if sign < 0 else 'максимуме'}."
        return f"{name} {'тише' if sign < 0 else 'громче'}."
    except MixerError as e:
        return str(e)


_SCENE_STEMS = {"play": ["игра", "стрим"], "call": ["звон", "созвон"], "spk": ["колонк", "динамик"]}


@command("mixer_scene", "Переключить сцену микшера (куда идёт звук)", ["включи сцену звонок", "сцена играю"],
         r"^(?:(?:включи|переключи|поставь|активируй|запусти|перейди)\s+)?(?:на\s+)?сцен\w*\s+(?P<name>.+)$", first=True, llm_args="name")
def _mixer_scene(ctx: Ctx, name):
    if not (ctx.cfg.get("mixer") or {}).get("enabled", True):
        return NOT_HANDLED
    mixer = _mixer(ctx)
    try:
        scenes = mixer.scenes()
        if not scenes:
            return "В микшере нет сцен."
        want = norm(name)

        def score(sc):
            n = norm(sc["name"])
            s = max(fuzz.WRatio(want, n), fuzz.partial_ratio(want, n) if len(want) >= 4 else 0)
            if any(w.startswith(st) for w in want.split() for st in _SCENE_STEMS.get(sc["id"], [])):
                s = max(s, 95)
            return s
        best = max(scenes, key=score)
        if score(best) < 70:
            return "Не нашла такую сцену. Есть: " + ", ".join(s["name"] for s in scenes) + "."
        mixer.set_scene(best["id"])
        return f"Сцена «{best['name']}»."
    except MixerError as e:
        return str(e)


# ======================================================================= VPN
_VPN = r"(?:впн|vpn|в\s?п\s?н|ви\s?пи\s?эн|вэ\s?пэ\s?эн|в\s?пэ\s?эн|вепеен)"
_EXT = r"(?:(?:расширени\w+|плагин\w*)\s+)?"


def _vpn_link(ctx: Ctx):
    link = getattr(ctx.assistant, "vpn", None) if ctx.assistant else None
    if link is None:
        raise VpnError("Связь с расширением VPN не запущена.")
    return link


def _vpn_do(ctx: Ctx, cmd: str):
    try:
        link = _vpn_link(ctx)
        res = link.command(cmd)
    except VpnError as e:
        return None, str(e)
    if not res:
        return [], "Расширение не ответило вовремя. Возможно, оно ещё подключается; попробуйте ещё раз."
    return res, ""


@command("vpn_on", "Включить VPN в расширении браузера", ["включи впн", "подключи vpn"],
         rf"^(?:включи|включить|подключи|подключись\s+к|запусти|активируй|вруби)\s+(?:мне\s+)?{_EXT}{_VPN}\b", rf"^{_VPN}\s+(?:включи|вкл)\b", first=True)
def _vpn_on(ctx):
    res, err = _vpn_do(ctx, "on")
    if err:
        return err
    good = [r for r in res if r.get("ok")]
    if good:
        return "Впн уже включён." if all(r.get("already") for r in good) else "Включила впн."
    return "Не получилось включить впн: " + str(res[0].get("error") or "неизвестная ошибка").rstrip(".") + "."


@command("vpn_off", "Выключить VPN в расширении браузера", ["выключи впн", "отключи vpn"],
         rf"^(?:выключи|выключить|отключи|отключить|останови|выруби|деактивируй|закрой)\s+{_EXT}{_VPN}\b", rf"^{_VPN}\s+(?:выключи|выкл|стоп)\b", first=True)
def _vpn_off(ctx):
    res, err = _vpn_do(ctx, "off")
    if err:
        return err
    good = [r for r in res if r.get("ok")]
    if good:
        return "Впн уже выключен." if all(r.get("already") for r in good) else "Выключила впн."
    return "Не получилось выключить впн: " + str(res[0].get("error") or "неизвестная ошибка").rstrip(".") + "."


@command("vpn_toggle", "Переключить VPN", ["переключи впн"], rf"^(?:переключи|поменяй)\s+{_EXT}{_VPN}\b", first=True)
def _vpn_toggle(ctx):
    res, err = _vpn_do(ctx, "toggle")
    if err:
        return err
    r = next((x for x in res if x.get("ok")), None)
    if not r:
        return "Не получилось переключить впн: " + str(res[0].get("error") or "неизвестная ошибка").rstrip(".") + "."
    return "Включила впн." if r.get("connected") else "Выключила впн."


@command("vpn_status", "Узнать, включён ли VPN", ["включён ли впн", "статус впн"],
         rf"^(?:включен|включён|работает|подключен|подключён)\s+ли\s+{_VPN}\b", rf"^(?:статус|состояние)\s+{_VPN}\b", rf"^{_VPN}\s+(?:включен|включён|работает|подключен|подключён)\b", first=True)
def _vpn_status(ctx):
    res, err = _vpn_do(ctx, "status")
    if err:
        return err
    on = any(r.get("connected") for r in res)
    return "Впн включён." if on else "Впн выключен."


# Общая «тише / громче / заглуши …» для дорожек стоит ПОСЛЕДНЕЙ среди первых команд: она ловит любую фразу и сама решает, про дорожку ли она.
@command("mixer_volume", "Громкость дорожки микшера (игры, музыка, донаты, браузер, дискорд, микрофон): тише, громче, на N процентов, заглушить",
         ["сделай тише музыку", "убавь звук в игре", "громкость донатов тридцать процентов", "заглуши браузер", "верни звук в игре"],
         r"^(?P<t>.+)$", first=True, llm_args="t (фраза пользователя целиком, например «сделай музыку тише»)")
def _mixer_volume(ctx: Ctx, t):
    return apply_mixer(ctx, t)
