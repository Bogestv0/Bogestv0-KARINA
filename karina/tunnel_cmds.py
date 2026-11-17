"""Голосовое управление встроенным VPN/прокси (см. tunnel.py). Сами команды зарегистрированы в integrations.py, здесь — логика.

Включение всегда переспрашивает «да/нет» (выключатель: tunnel.confirm в настройках). Выключение и смена сервера — без вопроса.
Если встроенный VPN не настроен (нет ни одного сервера), «включи впн» работает как раньше — через расширение Bogestv0 VPN в браузере.
"""
from __future__ import annotations
import re
from pathlib import Path
from rapidfuzz import fuzz

from .tunnel import TunnelError, clean_apps
from .textutil import norm, translit
from . import dialog

# Как страны называют вслух → как они обычно подписаны в списках серверов.
COUNTRIES = {
    "германия": "germany", "нидерланды": "netherlands", "голландия": "netherlands", "америка": "usa united states", "сша": "usa united states",
    "финляндия": "finland", "франция": "france", "польша": "poland", "турция": "turkey", "швеция": "sweden", "швейцария": "switzerland",
    "великобритания": "united kingdom uk", "англия": "united kingdom uk", "япония": "japan", "сингапур": "singapore", "казахстан": "kazakhstan",
    "армения": "armenia", "грузия": "georgia", "латвия": "latvia", "литва": "lithuania", "эстония": "estonia", "канада": "canada", "италия": "italy",
    "испания": "spain", "чехия": "czech", "австрия": "austria", "оаэ": "uae emirates", "индия": "india", "бразилия": "brazil", "гонконг": "hong kong",
}

# Что говорят вслух → процесс. Дополняет каталог программ Карины: «гугл» в нём нет, а все так говорят.
APP_ALIASES = {
    "гугл": "chrome.exe", "google": "chrome.exe", "гугл хром": "chrome.exe", "хром": "chrome.exe", "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "яндекс": "browser.exe", "яндекс браузер": "browser.exe", "yandex": "browser.exe", "яндекс браузер": "browser.exe",
    "эдж": "msedge.exe", "edge": "msedge.exe", "майкрософт эдж": "msedge.exe",
    "фаерфокс": "firefox.exe", "файрфокс": "firefox.exe", "firefox": "firefox.exe",
    "опера": "opera.exe", "opera": "opera.exe", "брейв": "brave.exe", "brave": "brave.exe",
    "дискорд": "Discord.exe", "discord": "Discord.exe",
    "телеграм": "Telegram.exe", "телега": "Telegram.exe", "telegram": "Telegram.exe",
    "стим": "steam.exe", "steam": "steam.exe", "спотифай": "Spotify.exe", "spotify": "Spotify.exe",
    "ватсап": "WhatsApp.exe", "whatsapp": "WhatsApp.exe", "вайбер": "Viber.exe", "viber": "Viber.exe",
}


# Как называть программы вслух (латиницу голос читает плохо).
EXE_LABELS = {"chrome.exe": "Гугл Хром", "browser.exe": "Яндекс Браузер", "msedge.exe": "Эдж", "firefox.exe": "Файрфокс", "opera.exe": "Опера", "brave.exe": "Брейв",
              "discord.exe": "Дискорд", "telegram.exe": "Телеграм", "steam.exe": "Стим", "spotify.exe": "Спотифай", "whatsapp.exe": "Ватсап", "viber.exe": "Вайбер"}

# «для выбранных приложений», «для моих приложений», «для списка» — значит: для тех, что уже записаны в настройках VPN
SAVED_APPS = re.compile(r"^(?:(?:выбранн\w*|сохранённ\w*|сохраненн\w*|добавленн\w*|указанн\w*|заданн\w*|настроенн\w*|нужн\w*|мо[ихеё]\w*|свои\w*|наши\w*|тех|этих|списка|из списка|списку)"
                        r"(?:\s+(?:приложени\w*|программ\w*|прилож\w*))?|приложени\w*|программ\w*)$")
_APP_FILLER = re.compile(r"\b(?:добавь|добавить|добавляй|давай|это|вот|ещё|еще|приложени\w*|программ\w*|в\s+список|пожалуйста|мне|нужно|надо|хочу|для\s+впн|для\s+vpn|и\s+ещё)\b")


def label_of(exe: str) -> str:
    return EXE_LABELS.get(exe.lower()) or re.sub(r"\.exe$", "", exe, flags=re.I)


def labels(apps) -> str:
    names = [label_of(a) for a in apps]
    return ", ".join(names[:-1]) + " и " + names[-1] if len(names) > 1 else (names[0] if names else "никого")


def tunnel_of(ctx):
    return getattr(ctx.assistant, "tunnel", None) if ctx.assistant else None


def resolve_apps(ctx, spoken: str) -> tuple[list[tuple[str, str]], list[str]]:
    """«гугл и дискорд» → ([(«Google Chrome», «chrome.exe»), …], [не узнала: …])."""
    found, unknown = [], []
    for part in re.split(r"\s*(?:,|\bи\b|\bда\b|\bплюс\b)\s*", norm(spoken)):
        part = part.strip()
        if not part:
            continue
        exe, label = APP_ALIASES.get(part), part
        if not exe and ctx.apps is not None:
            e = ctx.apps.resolve(part)
            if e:
                label = e.get("name") or part
                procs = [p for p in e.get("proc", []) if p]
                if procs:
                    exe = procs[0] if procs[0].lower().endswith(".exe") else procs[0] + ".exe"
                elif str(e.get("path") or "").lower().endswith(".exe"):
                    exe = Path(e["path"]).name
                elif e.get("exec") and str(e["exec"]).lower().endswith(".exe"):
                    exe = e["exec"]
        if exe and clean_apps([exe]):
            if exe.lower() not in [x[1].lower() for x in found]:
                found.append((label if label != part else part.capitalize(), exe))
        else:
            unknown.append(part)
    return found, unknown


def _run(fn) -> str:
    try:
        return fn()
    except TunnelError as e:
        return str(e)


def _what(mode: str, scope: str, apps: list[str]) -> str:
    if scope == "apps":
        return "VPN только для " + ", ".join(apps)
    return "прокси для всего компьютера" if mode == "proxy" else "VPN для всего компьютера"


def on(ctx, kind: str = "vpn", app: str = ""):
    """Включить. kind: vpn | proxy; app — что сказали после «для». None — встроенный VPN не настроен (пусть работает расширение)."""
    t = tunnel_of(ctx)
    if t is None or not t.configured():
        if app or kind == "proxy":
            return "Встроенный VPN и прокси ещё не настроены: добавьте ссылку на сервер в настройках, в разделе «VPN и прокси»."
        return None
    s = t.settings()
    srv = t.active_server()
    force_apps = False
    if app and SAVED_APPS.match(norm(app)):          # «включи впн для выбранных приложений» — берём список из настроек
        app, force_apps = "", True
    if force_apps and not clean_apps(s["apps"]):
        return apps_wizard(ctx, then_enable=True, intro="Список приложений для VPN пока пуст.")
    if app:
        found, unknown = resolve_apps(ctx, app)
        if not found:
            return ("Не знаю, какой у «" + unknown[0] + "» процесс. Добавьте его вручную в настройках VPN, в списке приложений. "
                    "Или скажите «добавь приложения в впн» — я спрошу, какие.") if unknown else "Не поняла, для какого приложения."
        mode, scope, apps = "vpn", "apps", [x[1] for x in found]
        label = ", ".join(x[0] for x in found)
        note = f" Не нашла процесс для: {', '.join(unknown)}." if unknown else ""
        if kind == "proxy":
            note += " Для отдельных приложений работает только режим VPN, он и будет."
    elif kind == "proxy":
        mode, scope, apps, label, note = "proxy", "all", [], "", ""
    else:
        mode, scope, apps, label, note = s["mode"], ("apps" if force_apps else s["scope"]), clean_apps(s["apps"]), "", ""
        if scope == "apps":
            if not apps:
                return apps_wizard(ctx, then_enable=True, intro="В настройках выбран режим «только для приложений», но приложений в списке нет.")
            label = labels(apps)
            mode = "vpn"
    st = t.status()
    if st["running"] and st.get("mode") == mode and st.get("scope") == scope and (scope == "all" or [a.lower() for a in st.get("apps", [])] == [a.lower() for a in apps]):
        return "Уже включено. " + t.describe()
    what = _what(mode, scope, [label] if label else apps)
    question = f"Включить {what}, сервер «{srv['name']}»?{note}"

    def go():
        return _run(lambda: "Включила. " + t.start(mode=mode, scope=scope, apps=apps))
    if (s.get("confirm", True)) and ctx.assistant:
        return ctx.confirm(question, go)
    return go()


def off(ctx, kind: str = "vpn"):
    t = tunnel_of(ctx)
    if t is not None and t.running():
        t.stop()
        return "Выключила " + ("прокси" if kind == "proxy" else "VPN") + ". Прежние настройки сети возвращены."
    if kind == "proxy":
        return "Прокси и так выключен." if t is not None and t.configured() else "Встроенный прокси не настроен."
    return None


def toggle(ctx):
    t = tunnel_of(ctx)
    if t is None or not t.configured():
        return None
    return off(ctx) if t.running() else on(ctx)


def status(ctx, kind: str = "vpn"):
    t = tunnel_of(ctx)
    if t is not None and t.running():
        return t.describe()
    if kind == "proxy":
        return "Прокси выключен." if t is not None and t.configured() else "Встроенный прокси не настроен."
    return None


def set_server(ctx, name: str = ""):
    t = tunnel_of(ctx)
    if t is None or not t.configured():
        return "Серверов нет: добавьте ссылку или подписку в настройках, в разделе «VPN и прокси»."
    servers = t.servers()
    cur = next((i for i, x in enumerate(servers) if x["id"] == t.settings()["active"]), 0)
    q = norm(name)
    if not q or q in ("другой", "следующий", "дальше", "новый"):
        pick = servers[(cur + 1) % len(servers)]
    else:
        best, score = None, 0.0
        asked = [q, translit(q)] + ([COUNTRIES[q]] if q in COUNTRIES else [])
        for x in servers:
            n = norm(x["name"])
            names = [n, translit(n)]
            s = 100.0 if n == q else max(max(fuzz.partial_ratio(a, b) * (1.0 if len(a) >= 3 else 0.5), fuzz.WRatio(a, b)) for a in asked for b in names)
            if s > score:
                best, score = x, s
        if not best or score < 70:
            return "Не нашла такой сервер. Есть: " + ", ".join(x["name"] for x in servers[:8]) + "."
        pick = best
    t.save(active=pick["id"])
    if t.running():
        st = t.status()
        return _run(lambda: "Переключила на сервер «" + pick["name"] + "». " + t.start(mode=st.get("mode"), scope=st.get("scope"), apps=st.get("apps")))
    return "Сервер «" + pick["name"] + "» выбран. Скажите «включи впн», когда понадобится."


# ---------------------------------------------------------------- голосовой мастер: какие приложения добавить в VPN
def _say_rude(ctx) -> bool:
    return bool((ctx.cfg.get("persona") or {}).get("rude"))


def apps_wizard(ctx, then_enable: bool = False, intro: str = "", direct: bool = False) -> str:
    """Карина сама спрашивает, какие приложения добавить в настройки VPN, и добавляет их:
      «Хотите добавить?» (да/нет/стоп) → «Какие приложения?» → «Это такие-то, верно?» (да/нет/стоп) → добавила → «Есть ещё?» (да/нет/стоп) → …
    Нигде не нужно говорить «Карина»: она слушает, пока не услышит да, нет или стоп (или не пройдёт 30 секунд тишины после её вопроса).
    then_enable — в конце предложить сразу включить VPN только для этих приложений. direct — сразу спросить названия (без «хотите ли»)."""
    t, a = tunnel_of(ctx), ctx.assistant
    if t is None or a is None or not hasattr(a, "ask_choice"):
        return "Встроенный VPN недоступен."
    if not t.configured():
        return "Сначала добавьте сервер: в настройках, в разделе «VPN и прокси», вставьте ссылку или адрес подписки."
    rude = _say_rude(ctx)
    OK_ASK = "Скажите «да» или «нет»."
    state = {"pending": [], "added": 0}

    def current() -> list[str]:
        return clean_apps(t.settings()["apps"])

    def finish(prefix: str = "") -> tuple[str, bool | object]:
        apps = current()
        head = (prefix + " ") if prefix else ""
        if then_enable and apps:
            def go():
                t.save(scope="apps", apps=apps)
                return _run(lambda: "Включила. " + t.start(mode="vpn", scope="apps", apps=apps))
            return head + "Включить VPN только для " + labels(apps) + "? " + OK_ASK, lambda tx: _enable_answer(tx, go)
        return head + ("Готово. В списке для VPN: " + labels(apps) + "." if apps else "Список приложений для VPN пока пуст."), False

    def _enable_answer(tx, go):
        k = dialog.classify(tx)
        if k == "yes":
            return go(), False
        if k in ("no", "stop"):
            return "Хорошо, не включаю. Включить можно командой «включи впн для выбранных приложений».", False
        return OK_ASK, True

    def ask_more(tx):
        k = dialog.classify(tx)
        if k == "yes":
            return "Назовите приложение.", ask_names
        if k in ("no", "stop"):
            return finish()
        return "Есть ещё приложения, которые хотите добавить? " + OK_ASK, True

    def confirm_names(tx):
        k = dialog.classify(tx)
        if k == "yes":
            found = state["pending"]
            merged = current() + [e for _, e in found if e.lower() not in [x.lower() for x in current()]]
            t.save(apps=clean_apps(merged))
            state["added"] += len(found)
            state["pending"] = []
            return "Добавила: " + labels([e for _, e in found]) + ". Есть ещё приложения, которые хотите добавить? " + OK_ASK, ask_more
        if k == "no":
            state["pending"] = []
            return "Хорошо, назовите приложения ещё раз. Или скажите «стоп».", ask_names
        if k == "stop":
            state["pending"] = []
            return finish("Хорошо, не добавляю.")
        return "Это " + labels([e for _, e in state["pending"]]) + "? " + OK_ASK, True

    def ask_names(tx):
        k = dialog.classify(tx)
        if k in ("stop", "no"):
            return finish("Хорошо.") if state["added"] else ("Хорошо, ничего не добавляю.", False)
        spoken = _APP_FILLER.sub(" ", norm(tx)).strip()
        found, unknown = resolve_apps(ctx, spoken) if spoken else ([], [])
        if not found:
            who = f"«{unknown[0]}»" if unknown else "это"
            return f"Не нашла программу {who}. Назовите ещё раз, например «хром» или «дискорд». Или скажите «стоп».", True
        state["pending"] = found
        note = (" Не нашла: " + ", ".join(unknown) + ".") if unknown else ""
        return "Это " + labels([e for _, e in found]) + "?" + note + " " + OK_ASK, confirm_names

    def want(tx):
        k = dialog.classify(tx)
        if k == "yes":
            return "Хорошо. Какие приложения добавить? Назовите их.", ask_names
        if k in ("no", "stop"):
            return ("Ну и ладно, блин." if rude else "Хорошо, не добавляю."), False
        return "Хотите добавить приложения для VPN? " + OK_ASK, True

    if direct:
        a.ask_choice(ask_names, tries=4)
        return (intro + " " if intro else "") + "Какие приложения добавить в VPN? Назовите их."
    a.ask_choice(want, tries=4)
    return (intro + " " if intro else "") + "Хотите добавить приложения для VPN сейчас? " + OK_ASK


def add_apps(ctx, app: str = "") -> str:
    """«Добавь хром в впн» — сразу (после подтверждения); «добавь приложения в впн» — запускает мастера."""
    t, a = tunnel_of(ctx), ctx.assistant
    if t is None or not t.configured():
        return "Сначала добавьте сервер: в настройках, в разделе «VPN и прокси», вставьте ссылку или адрес подписки."
    spoken = _APP_FILLER.sub(" ", norm(app)).strip()
    if not spoken:
        return apps_wizard(ctx, direct=True)
    found, unknown = resolve_apps(ctx, spoken)
    if not found:
        return f"Не нашла программу «{unknown[0] if unknown else app}». Скажите «добавь приложения в впн», и я спрошу, какие."
    exes = [e for _, e in found]

    def go():
        have = clean_apps(t.settings()["apps"])
        t.save(apps=clean_apps(have + [e for e in exes if e.lower() not in [x.lower() for x in have]]))
        return "Добавила: " + labels(exes) + ". Теперь в списке: " + labels(t.settings()["apps"]) + "."
    return ctx.confirm("Добавить в список приложений VPN: " + labels(exes) + "?", go)


def remove_apps(ctx, app: str = "") -> str:
    t = tunnel_of(ctx)
    if t is None:
        return "Встроенный VPN недоступен."
    have = clean_apps(t.settings()["apps"])
    spoken = _APP_FILLER.sub(" ", norm(app)).strip()
    found, unknown = resolve_apps(ctx, spoken) if spoken else ([], [])
    gone = [e for _, e in found if e.lower() in [x.lower() for x in have]]
    if not gone:
        return "Такого приложения нет в списке VPN. Сейчас там: " + labels(have) + "." if have else "Список приложений для VPN и так пуст."
    t.save(apps=[x for x in have if x.lower() not in [g.lower() for g in gone]])
    return "Убрала из списка VPN: " + labels(gone) + "."


def open_settings(ctx) -> str:
    """Быстрый переход: «открой настройки впн» — окно Карины открывается на карточке «VPN и прокси»."""
    a = ctx.assistant
    if a is None:
        return "Окно недоступно."
    a.emit({"type": "goto", "tab": "settings", "focus": "tnCard"})
    return "Открываю настройки VPN."


def list_apps(ctx) -> str:
    t = tunnel_of(ctx)
    if t is None or not t.configured():
        return "Встроенный VPN ещё не настроен."
    have = clean_apps(t.settings()["apps"])
    if not have:
        return "Список приложений для VPN пуст. Скажите «добавь приложения в впн», и я помогу."
    return "В списке приложений для VPN: " + labels(have) + "."
