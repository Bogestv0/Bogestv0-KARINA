"""Голосовое управление соседними программами Bogestv0: микшер (громкость дорожек, сцены) и расширение VPN.

Команды стоят в начале реестра (first=True): «сделай тише музыку» и «включи впн» не должны попасть в общие «тише» (громкость всего
компьютера) и «включи …» (открыть программу).
"""
from __future__ import annotations
import re
from rapidfuzz import fuzz
from .actions import command, NOT_HANDLED, Ctx
from .mixer_remote import MixerRemote, MixerError, read_mixer_settings, pct_to_db, db_to_pct, clamp_db
from .vpn_link import VpnError
from . import tunnel_cmds as tc
from .diag import trace
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
    for tok in text.replace("%", " % ").split() + [""]:
        if tok == "%":
            continue                                        # «20%» = число 20 и знак процента; знак ищут отдельно
        if tok and (tok.isdigit() or tok in _NUM_WORDS):
            run.append(tok)
            continue
        if run:
            n = parse_number(" ".join(run))
            if n is not None:
                return n
            run = []
    return None


def _name_in(name: str, tokens: list[str]) -> bool:
    """Название дорожки есть во фразе (в любом падеже): «Браузер» ← «браузера», «Игры» ← «в игре». Все слова названия должны встретиться."""
    words = name.split()
    if not words or len(name) < 3:
        return False
    for w in words:
        stem = w if len(w) <= 3 or w.isdigit() else w[:-2] if len(w) >= 6 else w[:-1]
        if not any(t == w or (not w.isdigit() and t.startswith(stem)) for t in tokens):
            return False
    return True


_AND = re.compile(r"\b(?:и|оба|обе|обоих|все|всё|вместе)\b")


def resolve_strips(text: str, strips: list[dict], aliases: dict | None = None) -> list[dict]:
    """Какие дорожки имеются в виду во фразе. strips: [{i, name, tag, hidden}].

    Дорожка получает оценку: имя дорожки или своё слово пользователя во фразе — 3, слова по тегу («браузер» для V5) — 2,
    похожее слово (распознали «барузер») — 1,7 для названия и 1,5 для слов тега, слово из длинного названия («Донаты в браузере») — 1. Берутся только дорожки с самой высокой оценкой:
    «громкость браузера» меняет браузер, а не заодно и «Донаты». Несколько дорожек сразу — только если в фразе есть «и / оба / все»
    или у них общее слово («микрофон»)."""
    tokens = text.split()
    aliases = {norm(k): [norm(a) for a in v] for k, v in (aliases or {}).items()}

    def hit(stems) -> bool:
        return any(t.startswith(s) for t in tokens for s in stems if s)

    def near(stems) -> bool:
        return any(len(s) >= 5 and len(t) >= max(6, len(s)) and fuzz.ratio(t[:len(s)], s) >= 84 for t in tokens for s in stems)

    scored: dict[str, tuple[float, dict]] = {}
    for s in strips:
        if s.get("hidden"):
            continue
        key = s.get("tag") or str(s["i"])
        name = norm(s["name"])
        by_tag = list(STEMS.get(s.get("tag", ""), []))
        own = aliases.get(name, [])
        words = [w[:5] for w in name.split() if len(w) >= 4 and not w.isdigit()]       # слова из названия дорожки: «Warzone»
        phrase = _name_in(name, tokens)
        if phrase or hit(own):
            sc = 3.0
        elif hit(by_tag):
            sc = 2.0
        elif hit(words):
            sc = 1.0 * (sum(1 for w in words if hit([w])) / max(1, len(name.split())))
        elif near([w for w in name.split() if len(w) >= 5]):
            sc = 1.7                              # по названию дорожки с опечаткой распознавания: «барузер» → «Браузер»
        elif near(by_tag + own):
            sc = 1.5
        else:
            continue
        scored[key] = (sc, s)
    if not scored:
        return []
    top = max(sc for sc, _ in scored.values())
    keep = {k for k, (sc, _) in scored.items() if sc == top or (_AND.search(text) and sc >= 2.0 and top >= 2.0)}
    for a, b in _PAIRS:                          # «второй браузер»: V6 узнаётся по слову слабее, чем V5, но пара решается ниже
        for x, y in ((a, b), (b, a)):
            if x in keep and y in scored and scored[y][0] <= 1.0:      # напарника берём, только если его имя содержит то же слово
                keep.add(y)
    found: dict[str, dict] = {k: scored[k][1] for k in keep}
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
        is_db = num is not None and re.search(r"\b(?:дб|децибел\w*)\b", text)
        db = num if is_db else None
        pct = num if (num is not None and not is_db and 0 < num <= 100) else None      # «тише на 20%» — на 20 процентных пунктов, а не на шаг
        return {"op": "down" if down else "up", "mult": mult, "db": db, "pct": pct}
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
    if not mcfg.get("enabled", True) or getattr(ctx, "skip_mixer", False):
        return NOT_HANDLED
    mixer = _mixer(ctx)
    intent = intent or parse_intent(text)
    if not intent:
        return NOT_HANDLED
    aliases = mcfg.get("aliases") or {}
    pre = resolve_strips(target if target is not None else text, _strips_local(ctx.cfg, mixer), aliases)
    if not pre:
        if mcfg.get("prefer") and target is None and intent["op"] in ("down", "up", "set") and is_bare_volume(text):
            return _ask_track(ctx, mixer, intent, text)         # включён режим «через микшер»: «сделай тише» без названия — уточняем дорожку
        return NOT_HANDLED                       # про дорожку ни слова — это обычная «тише / громче» для всего компьютера
    try:
        strips = mixer.strips()                  # подключаемся и берём настоящие имена и громкость
        hit = resolve_strips(target if target is not None else text, strips, aliases) or [s for s in strips if s["i"] in {p["i"] for p in pre}]
        if not hit:
            return NOT_HANDLED
        trace("Микшер: «%s» → %s (дорожки микшера: %s)" % (text, _names(hit), "; ".join(f"{s['name']}[{s.get('tag', '')}]" for s in strips)))
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
        if intent.get("pct") and not intent.get("db"):          # «на 20%»: от нынешнего уровня (100 % = 0 дБ) вычитаем/прибавляем 20 пунктов
            n_moved = 0
            for s in hit:
                cur = db_to_pct(s["gain"])
                new = max(0, cur + sign * int(intent["pct"]))
                if new != cur:
                    mixer.set_gain(s["i"], pct_to_db(new))
                    n_moved += 1
                    if s["mute"] and sign > 0:
                        mixer.set_mute(s["i"], False)
            if not n_moved:
                return f"{name} уже на {'минимуме' if sign < 0 else 'максимуме'}."
            return f"{name} {'тише' if sign < 0 else 'громче'} на {intent['pct']} {plural(int(intent['pct']), ('процент', 'процента', 'процентов'))}."
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


_FILLER = {"сделай", "сделайте", "поставь", "установи", "выставь", "пожалуйста", "давай", "можешь", "можно", "а", "ну", "чуть", "немного", "слегка", "ещё", "еще", "капельку", "намного",
           "гораздо", "сильно", "звук", "звука", "громкость", "громкости", "на", "до", "процентов", "процента", "процент", "дб", "децибел", "децибела", "децибелов", "побольше", "поменьше",
           "вообще", "значительно", "резко", "немножко", "полегче", "тихо"}


def is_bare_volume(text: str) -> bool:
    """«сделай тише», «погромче на 20%», «громкость пятьдесят» — про громкость, но без названия дорожки (кроме слов-связок)."""
    if not (_RE_DOWN.search(text) or _RE_UP.search(text) or parse_intent(text)):
        return False
    t = text.replace("%", " ")
    for tok in t.split():
        if tok in _FILLER or tok in _NUM_WORDS or tok.isdigit() or _RE_DOWN.fullmatch(tok) or _RE_UP.fullmatch(tok):
            continue
        if tok in ("карина", "кариночка"):
            continue
        return False
    return True


_GENERAL = re.compile(r"\b(?:общ\w+|всю|весь|все|всё|компьютер\w*|систем\w*|винд\w*|windows|главн\w+)\b")
_CANCEL = re.compile(r"\b(?:отмена|отмени|не надо|не нужно|ничего|забудь|стоп|неважно)\b")


def _ask_track(ctx: Ctx, mixer: MixerRemote, intent: dict, text: str) -> str | object:
    if not ctx.assistant or not hasattr(ctx.assistant, "ask_choice"):
        return NOT_HANDLED
    names = ", ".join(s["name"].lower() for s in _strips_local(ctx.cfg, mixer) if not s.get("hidden") and s.get("tag", "") not in ("HW1", "HW2"))
    verb = {"down": "сделать тише", "up": "сделать громче", "set": "изменить"}[intent["op"]]

    def answer(said: str):
        said = norm(said)
        if _CANCEL.search(said):
            return "Хорошо, ничего не меняю.", False
        if _GENERAL.search(said):
            sub = Ctx(ctx.cfg, ctx.apps, ctx.assistant, raw=ctx.raw)
            sub.skip_mixer = True
            from . import actions
            ok, reply = actions.dispatch(sub, text)
            return (reply if ok else "Не получилось изменить общую громкость."), False
        r = apply_mixer(ctx, said, intent=intent, target=said)
        if r is NOT_HANDLED:
            return f"Не поняла, какую дорожку. Скажите, например: {names}. Или «общая» — громкость компьютера.", True
        return r, False

    ctx.assistant.ask_choice(answer)
    return f"Что {verb} в микшере? Дорожки: {names}. Или скажите «общая» — тогда изменю громкость компьютера."


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


_PROXY = r"(?:прокси\w*|proxy)"
_ON = r"(?:включи|включить|подключи|подключить|запусти|запустить|вруби|активируй)"
_OFF = r"(?:выключи|выключить|отключи|отключить|останови|выруби|деактивируй)"


def _kind(word: str) -> str:
    return "proxy" if re.match(r"прокс|proxy", word or "") else "vpn"


@command("tunnel_app_on", "Включить встроенный VPN только для приложения (для хрома, гугла, дискорда)",
         ["включи впн только для хрома", "включи впн для дискорда", "включи прокси для гугла"],
         rf"^{_ON}\s+(?:мне\s+)?(?P<kind>{_VPN}|{_PROXY})\s+(?:только\s+)?(?:для|на)\s+(?P<app>.+)$", first=True)
def _tunnel_app_on(ctx, kind, app):
    return tc.on(ctx, _kind(kind), app)


_APPS_W = r"(?:приложени\w*|программ\w*)"


@command("vpn_apps_add", "Добавить приложения в список VPN: сама спрашивает какие (да/нет/стоп) и добавляет",
         ["добавь приложения в впн", "добавь хром в впн", "настрой впн"],
         rf"^(?:добавь|добавить|добавляй|внеси|запиши|закинь)\s+(?P<app>.+?)\s+(?:в|для|во)\s+(?:список\s+)?(?:{_APPS_W}\s+)?{_VPN}(?:\s+.*)?$",
         rf"^(?:хочу|давай|надо|нужно)\s+(?:добавить|настроить)\s+(?P<app>.*?){_APPS_W}?\s*(?:в|для|во)?\s*{_VPN}(?:\s+.*)?$",
         rf"^(?:настрой|настроить|настроим)\s+(?:{_APPS_W}\s+)?(?:для\s+|в\s+)?{_VPN}(?:\s+.*)?$", first=True, llm_args="app")
def _vpn_apps_add(ctx, app=""):
    return tc.add_apps(ctx, app or "")


@command("vpn_apps_remove", "Убрать приложение из списка VPN", ["убери хром из впн"],
         rf"^(?:убери|удали|выкинь|исключи)\s+(?P<app>.+?)\s+(?:из|с)\s+(?:списка\s+)?(?:{_APPS_W}\s+)?{_VPN}(?:\s+.*)?$", first=True, llm_args="app")
def _vpn_apps_remove(ctx, app=""):
    return tc.remove_apps(ctx, app or "")


@command("vpn_apps_list", "Какие приложения в списке VPN", ["какие приложения в впн"],
         rf"^(?:какие|что)\s+(?:за\s+)?{_APPS_W}\s+(?:сейчас\s+)?(?:в|для|есть\s+в)\s+(?:списке\s+)?{_VPN}", rf"^(?:список|перечисли)\s+(?:{_APPS_W}\s+)?(?:для\s+|в\s+)?{_VPN}", first=True)
def _vpn_apps_list(ctx):
    return tc.list_apps(ctx)


@command("vpn_settings", "Открыть настройки VPN в окне Карины", ["открой настройки впн"],
         rf"^(?:открой|покажи|перейди\s+в|перейди\s+к|зайди\s+в)\s+(?:мне\s+)?(?:настройки|настройку)\s+{_VPN}\b", rf"^настройки\s+{_VPN}\b", first=True)
def _vpn_settings(ctx):
    return tc.open_settings(ctx)


@command("proxy_on", "Включить встроенный прокси (системный прокси Windows)", ["включи прокси"],
         rf"^{_ON}\s+(?:мне\s+)?{_PROXY}\b", first=True)
def _proxy_on(ctx):
    return tc.on(ctx, "proxy")


@command("proxy_off", "Выключить встроенный прокси", ["выключи прокси"], rf"^{_OFF}\s+{_PROXY}\b", first=True)
def _proxy_off(ctx):
    return tc.off(ctx, "proxy")


@command("proxy_status", "Узнать, включён ли прокси", ["включён ли прокси"], rf"^(?:включен|включён|работает)\s+ли\s+{_PROXY}\b", rf"^(?:статус|состояние)\s+{_PROXY}\b", first=True)
def _proxy_status(ctx):
    return tc.status(ctx, "proxy")


@command("vpn_server", "Сменить сервер встроенного VPN: следующий или по названию", ["смени сервер", "переключи сервер на германию"],
         rf"^(?:смени|поменяй|переключи|выбери|поставь)\s+(?:мне\s+)?(?:другой\s+|следующий\s+)?сервер(?:\s+(?:{_VPN}|{_PROXY}))?(?:\s+(?:на|в)\s+(?P<name>.+))?$",
         r"^(?P<name>следующий|другой)\s+сервер$", first=True, llm_args="name")
def _vpn_server(ctx, name=""):
    return tc.set_server(ctx, name or "")


@command("vpn_on", "Включить VPN (свой, а если не настроен — в расширении браузера)", ["включи впн", "подключи vpn"],
         rf"^(?:включи|включить|подключи|подключись\s+к|запусти|активируй|вруби)\s+(?:мне\s+)?{_EXT}{_VPN}\b", rf"^{_VPN}\s+(?:включи|вкл)\b", first=True)
def _vpn_on(ctx):
    mine = tc.on(ctx, "vpn")
    if mine is not None:
        return mine
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
    mine = tc.off(ctx, "vpn")
    if mine is not None:
        return mine
    res, err = _vpn_do(ctx, "off")
    if err:
        return err
    good = [r for r in res if r.get("ok")]
    if good:
        return "Впн уже выключен." if all(r.get("already") for r in good) else "Выключила впн."
    return "Не получилось выключить впн: " + str(res[0].get("error") or "неизвестная ошибка").rstrip(".") + "."


@command("vpn_toggle", "Переключить VPN", ["переключи впн"], rf"^(?:переключи|поменяй)\s+{_EXT}{_VPN}\b", first=True)
def _vpn_toggle(ctx):
    mine = tc.toggle(ctx)
    if mine is not None:
        return mine
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
    mine = tc.status(ctx, "vpn")
    if mine is not None:
        return mine
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
