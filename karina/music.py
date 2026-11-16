"""Музыка и видео голосом: «включи музыку» → один раз спросит приложение и запомнит; «включи песню …» → найдёт и запустит; «переключи» → следующий трек.

Как это работает:
* Приложение выбирается один раз (Спотифай, Яндекс Музыка, YouTube Music, ВК, YouTube или любая ваша программа) и запоминается в learned.json. Сменить: «смени приложение для музыки».
* YouTube и YouTube Music: Карина сама находит первый подходящий ролик и открывает его — он сразу играет.
* Spotify, Яндекс Музыка, ВК: приложение открывается и включается воспроизведение; названную песню Карина ищет в нём (открывает поиск по запросу), но запустить именно эту дорожку без входа в ваш аккаунт нельзя, поэтому остаётся нажать на неё.
* «Переключи», «следующую», «пауза» работают с любым плеером: это мультимедийные клавиши Windows.
Свои сервисы можно добавить в config.json → music.services (см. SERVICES ниже).
"""
from __future__ import annotations
import json, re, subprocess, sys, time, urllib.parse, urllib.request, webbrowser
from rapidfuzz import fuzz
from .actions import command, NOT_HANDLED, Ctx
from . import memory, winutil
from .textutil import norm

IS_WIN = sys.platform == "win32"

# Сервисы по категориям. kind: desktop — программа на компьютере (запускается, управляется клавишами), web — сайт.
# search — адрес поиска ({q} — запрос), resolve: youtube — найти первый ролик и открыть его сразу, watch — адрес ролика ({id}), home — главная.
SERVICES: dict[str, dict[str, dict]] = {
    "music": {
        "spotify": {"name": "Spotify", "aliases": ["спотифай", "спотифае", "спотик", "spotify", "спотифаю"], "kind": "desktop", "find": "spotify", "proc": ["Spotify"],
                    "search_uri": "spotify:search:{q}"},
        "yandex": {"name": "Яндекс Музыка", "aliases": ["яндекс музыка", "яндекс музыку", "яндекс музыке", "яндекс музыки", "музыка яндекса", "яндекс"], "kind": "web",
                   "home": "https://music.yandex.ru", "search": "https://music.yandex.ru/search?text={q}"},
        "ytmusic": {"name": "YouTube Music", "aliases": ["ютуб музыка", "ютуб музыку", "ютуб музыке", "youtube music", "ютуб мьюзик", "ютьюб музыка"], "kind": "web", "resolve": "youtube",
                    "home": "https://music.youtube.com", "search": "https://music.youtube.com/search?q={q}", "watch": "https://music.youtube.com/watch?v={id}"},
        "youtube": {"name": "YouTube", "aliases": ["ютуб", "ютубе", "youtube", "ютьюб", "ютубу"], "kind": "web", "resolve": "youtube",
                    "home": "https://www.youtube.com", "search": "https://www.youtube.com/results?search_query={q}", "watch": "https://www.youtube.com/watch?v={id}"},
        "vk": {"name": "ВК Музыка", "aliases": ["вк музыка", "вк музыку", "вконтакте музыка", "вк", "вконтакте", "вк музыке"], "kind": "web",
               "home": "https://vk.com/audio", "search": "https://vk.com/audio?q={q}"},
    },
    "video": {
        "youtube": {"name": "YouTube", "aliases": ["ютуб", "ютубе", "youtube", "ютьюб", "ютубу"], "kind": "web", "resolve": "youtube",
                    "home": "https://www.youtube.com", "search": "https://www.youtube.com/results?search_query={q}", "watch": "https://www.youtube.com/watch?v={id}"},
        "rutube": {"name": "Rutube", "aliases": ["рутуб", "рутубе", "rutube"], "kind": "web", "home": "https://rutube.ru", "search": "https://rutube.ru/search/?query={q}"},
        "kinopoisk": {"name": "Кинопоиск", "aliases": ["кинопоиск", "кинопоиске", "кинопоиску"], "kind": "web", "home": "https://www.kinopoisk.ru",
                      "search": "https://www.kinopoisk.ru/index.php?kp_query={q}"},
        "twitch": {"name": "Twitch", "aliases": ["твич", "твиче", "twitch"], "kind": "web", "home": "https://www.twitch.tv", "search": "https://www.twitch.tv/search?term={q}"},
    },
}
NOUN = {"music": "музыку", "video": "видео"}
LABEL = {"music": "музыки", "video": "видео"}


def services(cfg, category: str) -> dict[str, dict]:
    """Встроенные сервисы плюс свои из config.json → music.services[категория]."""
    out = {k: dict(v) for k, v in SERVICES.get(category, {}).items()}
    for k, v in (((cfg.get("music") or {}).get("services") or {}).get(category) or {}).items():
        if isinstance(v, dict) and v.get("name"):
            out[k] = {**out.get(k, {}), **v}
    return out


def match_service(cfg, category: str, apps, said: str) -> tuple[str, dict] | None:
    """Что человек назвал: сервис из списка или любая его программа («фубар»). → (ключ, описание) или None."""
    q = norm(said)
    if not q:
        return None
    best, best_s = None, 0.0
    for key, svc in services(cfg, category).items():
        for a in [svc["name"], *svc.get("aliases", [])]:
            a = norm(a)
            s = 100.0 if a == q else 96.0 if (q.startswith(a) or a.startswith(q)) and min(len(a), len(q)) >= 4 else fuzz.ratio(q, a)
            if s > best_s:
                best, best_s = (key, svc), s
    if best and best_s >= 84:
        return best
    e = apps.resolve(said) if apps else None
    if e and e.get("name") and e.get("src") != "bogestv0":
        return "app:" + e["name"], {"name": e["name"], "kind": "desktop", "entry": e, "proc": list(e.get("proc", [])) or [e["name"]]}
    return None


def service_by_key(cfg, category: str, apps, key: str, name: str = "") -> dict | None:
    if key.startswith("app:"):
        e = apps.resolve(key[4:]) if apps else None
        return {"name": name or key[4:], "kind": "desktop", "entry": e, "proc": list((e or {}).get("proc", [])) or [name or key[4:]]}
    return services(cfg, category).get(key)


# ---------------------------------------------------------------- разбор фразы
_VERB = r"(?:включи|включить|включай|поставь|запусти|вруби|врубай|сыграй|воспроизведи|давай)"
_ANY = r"(?:какую\s+нибудь\s+|какую\s+то\s+|что\s+нибудь\s+|любую\s+|свою\s+|мою\s+|любимую\s+|хорошую\s+|приятную\s+|спокойную\s+|веселую\s+)?"
_APP_TAIL = r"(?:\s+(?:в|во|на|через|из|с|со)\s+(?P<app>.+))?"
_P_GENERIC = re.compile(rf"^{_VERB}\s+(?:мне\s+)?{_ANY}(?:музык\w+|музончик\w*|песни|песенки|плейлист\w*|что\s+нибудь\s+послушать){_APP_TAIL}$")
_P_WANT = re.compile(rf"^(?:хочу\s+(?:послушать\s+)?музык\w+|музык\w+\s+включи|давай\s+послушаем\s+музык\w+|хочу\s+послушать\s+что\s+нибудь|дай\s+что\s+нибудь\s+послушать){_APP_TAIL}$")
_P_SONG = re.compile(rf"^{_VERB}\s+(?:мне\s+)?(?:песню|песни|трек|композицию|клип|альбом|плейлист|исполнителя|группу|музыку\s+(?:группы|исполнителя|артиста)|музыку|песенку)\s+(?P<q>.+)$")
_P_LISTEN = re.compile(r"^(?:хочу\s+послушать|давай\s+послушаем|дай\s+послушать|послушаем|хочу\s+слушать)\s+(?P<q>.+)$")
_P_NEXT = (re.compile(r"^(?:переключи|пропусти|скипни|скип)(?:\s+(?:на\s+)?(?:следующую|другую|новую))?(?:\s+(?:песню|трек|композицию|музыку|мелодию))?(?:\s+дальше)?$"),
           re.compile(r"^(?:смени|поменяй|включи|давай|поставь)\s+(?:на\s+)?(?:другую|следующую|новую)(?:\s+(?:песню|трек|композицию|мелодию))?$"))
_P_PREV = re.compile(r"^(?:верни|включи|давай|поставь)\s+(?:предыдущую|прошлую|прежнюю)(?:\s+(?:песню|трек|композицию))?$")
_P_CHANGE = re.compile(r"^(?:смени|поменяй|измени|выбери|сбрось|забудь)\s+(?:приложение|плеер|программу|сервис)\s+(?:для\s+)?(?P<cat>музык\w+|видео)$")
_P_VIDEO = re.compile(rf"^{_VERB}\s+(?:мне\s+)?(?:видео|ролик|фильм|сериал|мультик)(?:\s+(?P<q>.+))?$")
_P_VIDEO_WANT = re.compile(r"^(?:хочу\s+(?:посмотреть|глянуть)|давай\s+посмотрим|дай\s+посмотреть|посмотрим)\s+(?P<q>.+)$")
_TAIL = re.compile(r"^(?P<q>.+?)\s+(?:в|во|на|через|из)\s+(?P<app>[\w\.]+(?:\s+[\w\.]+){0,2})$")


def parse(text: str) -> dict | None:
    """→ {'op': 'play'|'song'|'next'|'prev'|'change', 'cat': 'music'|'video', 'q': …, 'app': …} или None (фраза не про музыку)."""
    t = norm(text)
    if not t:
        return None
    for p in _P_NEXT:
        if p.match(t):
            return {"op": "next", "cat": "music"}
    if _P_PREV.match(t):
        return {"op": "prev", "cat": "music"}
    m = _P_CHANGE.match(t)
    if m:
        return {"op": "change", "cat": "video" if m.group("cat").startswith("видео") else "music"}
    for p in (_P_GENERIC, _P_WANT):
        m = p.match(t)
        if m:
            return {"op": "play", "cat": "music", "q": "", "app": (m.group("app") or "").strip()}
    for p, cat in ((_P_SONG, "music"), (_P_LISTEN, "music"), (_P_VIDEO, "video"), (_P_VIDEO_WANT, "video")):
        m = p.match(t)
        if m:
            q = (m.groupdict().get("q") or "").strip()
            return {"op": "song" if q else "play", "cat": cat, "q": q, "app": ""}
    return None


def split_app(cfg, category: str, apps, q: str) -> tuple[str, str]:
    """«кино группа крови в спотифай» → («кино группа крови», «спотифай»): хвост «в …» считаем приложением, только если оно нам известно."""
    m = _TAIL.match(q)
    if not m:
        return q, ""
    tail = m.group("app")
    svc = None
    for cut in range(len(tail.split())):                     # «в яндекс музыке», «на ютубе»
        part = " ".join(tail.split()[cut:])
        hit = match_service(cfg, category, None, part)       # без каталога программ: только известные сервисы, чтобы название песни не приняли за приложение
        if hit:
            svc = part
            break
    return (m.group("q").strip(), svc) if svc else (q, "")


# ---------------------------------------------------------------- Windows: процессы и звук
def _running(procs: list[str]) -> bool:
    if not IS_WIN or not procs:
        return False
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10, creationflags=winutil.NO_WINDOW).stdout.lower()
    except Exception:
        return False
    return any(f'"{p.lower()}.exe"' in out or f'"{p.lower()}"' in out for p in procs)


def _is_playing(procs: list[str]) -> bool | None:
    """Играет ли сейчас звук у программы (по сеансам звука Windows). None — узнать не удалось."""
    if not IS_WIN:
        return None
    try:
        from pycaw.pycaw import AudioUtilities
        names = {p.lower() + ".exe" for p in procs} | {p.lower() for p in procs}
        for s in AudioUtilities.GetAllSessions():
            pr = getattr(s, "Process", None)
            if pr is not None and pr.name().lower() in names and getattr(s, "State", 0) == 1:
                return True
        return False
    except Exception:
        return None


def _http_get(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                               "Accept-Language": "ru,en;q=0.8", "Cookie": "CONSENT=YES+cb; SOCS=CAI"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def resolve_youtube(q: str) -> tuple[str, str] | None:
    """Первый ролик по запросу: (id, название). Берётся со страницы результатов YouTube; если там что-то поменялось — None, и мы просто откроем поиск."""
    try:
        html = _http_get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
    except Exception:
        return None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)
    if not m:
        return None
    t = re.search(r'"videoId":"%s".{0,900}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"' % re.escape(m.group(1)), html, re.S)
    title = ""
    if t:
        try:
            title = json.loads('"' + t.group(1) + '"')           # в странице это строка JSON: \\u0026, \\" и т. п.
        except ValueError:
            title = t.group(1)
    return m.group(1), title


# ---------------------------------------------------------------- действия
def _launch_desktop(ctx: Ctx, svc: dict) -> bool:
    e = svc.get("entry") or (ctx.apps.resolve(svc["name"]) if ctx.apps else None) or svc
    try:
        return bool(ctx.apps.launch(e)) if ctx.apps else False
    except Exception:
        return False


def _wait_running(procs: list[str], seconds: float = 15.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if _running(procs):
            return True
        time.sleep(0.7)
    return False


def execute(ctx: Ctx, category: str, svc: dict, q: str = "") -> str:
    name, noun = svc["name"], NOUN.get(category, "музыку")
    if svc.get("kind") == "desktop":
        procs = list(svc.get("proc") or [])
        was_running = _running(procs)
        if not was_running:
            if not _launch_desktop(ctx, svc):
                return f"Не получилось запустить {name}. Проверьте приложение на вкладке «Программы»."
            _wait_running(procs)
            time.sleep(2.5)                                   # дать приложению прогрузиться
        else:
            winutil.focus_window(name)
        if q:
            uri = svc.get("search_uri")
            if uri:
                try:
                    winutil.start(uri.format(q=urllib.parse.quote(q)))
                except Exception:
                    return f"Открыла {name}, но поиск не получился."
                return f"Открыла поиск в {name}: {q}. Нажмите на нужный трек."
            return f"Открыла {name}. Поиска по названию в этой программе у меня нет, найдите трек вручную."
        if _is_playing(procs) is True:
            return f"В {name} уже играет."
        winutil.press("play")
        return f"Включаю {noun} в {name}."
    # сайт
    if q:
        if svc.get("resolve") == "youtube" and svc.get("watch"):
            hit = resolve_youtube(q)
            if hit:
                webbrowser.open(svc["watch"].format(id=hit[0]))
                return f"Включаю в {name}: {hit[1] or q}."
        if svc.get("search"):
            webbrowser.open(svc["search"].format(q=urllib.parse.quote(q)))
            return f"Открыла поиск в {name}: {q}. Нажмите на нужный трек."
    webbrowser.open(svc.get("home") or svc.get("search", "").split("?")[0] or "about:blank")
    return f"Открыла {name}. Выберите, что играть."


def _ask_text(cfg, category: str) -> str:
    names = ", ".join(s["name"] for s in services(cfg, category).values())
    return f"В каком приложении включать {NOUN.get(category, 'это')}? Например: {names} — или назовите свою программу. Скажу один раз, дальше запомню."


def run(ctx: Ctx, req: dict) -> str | object:
    cat, q = req["cat"], req.get("q", "")
    cfg, apps = ctx.cfg, ctx.apps
    mem = memory.get(ctx.cfg)
    if req["op"] == "change":
        mem.forget_app(cat)
        return "Хорошо, забыла. Скажите «включи музыку» — спрошу заново." if cat == "music" else "Хорошо, забыла выбранное приложение для видео."
    if q and not req.get("app"):
        q, app_hint = split_app(cfg, cat, apps, q)
        req = dict(req, q=q, app=app_hint)
    hint = req.get("app") or ""
    chosen = None
    if hint:
        found = match_service(cfg, cat, apps, hint)
        if found:
            chosen = found
            if not mem.app(cat):                               # приложение назвали вслух, а выбранного ещё нет — запоминаем
                mem.set_app(cat, found[0], found[1]["name"])
        else:
            return f"Не знаю приложение «{hint}». Назовите его по-другому или добавьте на вкладке «Программы»."
    if chosen is None:
        saved = mem.app(cat)
        svc = service_by_key(cfg, cat, apps, saved["key"], saved.get("name", "")) if saved else None
        if svc:
            chosen = (saved["key"], svc)
    if chosen is not None:
        return execute(ctx, cat, chosen[1], q)
    # приложение ещё не выбрано — спрашиваем один раз
    assistant = ctx.assistant
    if not assistant or not hasattr(assistant, "ask_choice"):
        return f"Не выбрано приложение для {LABEL.get(cat, 'этого')}. Скажите, например: «включи {NOUN.get(cat, 'музыку')} в Спотифай»."

    def answer(said: str):
        s = norm(said)
        if re.search(r"\b(?:отмена|отмени|не надо|не нужно|ничего|стоп|забудь|неважно)\b", s):
            return "Хорошо, ничего не включаю.", False
        found = match_service(cfg, cat, apps, re.sub(r"^(?:в|во|на|через|из)\s+", "", s))
        if not found:
            return f"Не поняла, что это за приложение. Скажите, например: {', '.join(x['name'] for x in services(cfg, cat).values())}.", True
        mem.set_app(cat, found[0], found[1]["name"])
        return f"Запомнила: {LABEL.get(cat, 'это')} — {found[1]['name']}. " + str(execute(ctx, cat, found[1], q)), False

    assistant.ask_choice(answer)
    return _ask_text(cfg, cat)


# ---------------------------------------------------------------- команды
@command("music", "Музыка и видео: включить музыку (спросит приложение один раз), включить песню или видео по названию, переключить трек, сменить приложение",
         ["включи музыку", "включи песню Группа крови", "переключи", "поставь Кино в Спотифай", "смени приложение для музыки", "включи видео про котов"],
         r"^(?P<t>.+)$", first=True, llm_args="t (фраза пользователя целиком)")
def _music(ctx: Ctx, t):
    req = parse(t)
    if not req:
        return NOT_HANDLED
    if req["op"] == "next":
        winutil.press("next")
        return "Следующий трек."
    if req["op"] == "prev":
        winutil.press("prev")
        return "Предыдущий трек."
    return run(ctx, req)


@command("learned_info", "Рассказать, чему Карина научилась (запомненные приложения и фразы)", ["что ты выучила", "чему ты научилась", "что ты запомнила"],
         r"^(?:что\s+ты\s+(?:выучила|запомнила|помнишь|знаешь\s+обо\s+мне)|чему\s+ты\s+(?:научилась|выучилась))\b", first=True)
def _learned(ctx: Ctx):
    return memory.get(ctx.cfg).summary()


@command("forget_learned", "Забыть всё выученное (приложения для музыки и запомненные фразы)", ["забудь выученное", "сотри память"],
         r"^(?:забудь|сотри|очисти|сбрось)\s+(?:всё\s+|все\s+)?(?:выученное|запомненное|память|что\s+выучила|что\s+запомнила)$", first=True)
def _forget(ctx: Ctx):
    return ctx.confirm("Стереть всё, чему я научилась: выбранные приложения и запомненные фразы?", lambda: (memory.get(ctx.cfg).clear(), "Всё забыла.")[1])
