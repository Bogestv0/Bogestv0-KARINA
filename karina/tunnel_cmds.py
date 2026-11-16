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
    if app:
        found, unknown = resolve_apps(ctx, app)
        if not found:
            return ("Не знаю, какой у «" + unknown[0] + "» процесс. Добавьте его вручную в настройках VPN, в списке приложений.") if unknown else "Не поняла, для какого приложения."
        mode, scope, apps = "vpn", "apps", [x[1] for x in found]
        label = ", ".join(x[0] for x in found)
        note = f" Не нашла процесс для: {', '.join(unknown)}." if unknown else ""
        if kind == "proxy":
            note += " Для отдельных приложений работает только режим VPN, он и будет."
    elif kind == "proxy":
        mode, scope, apps, label, note = "proxy", "all", [], "", ""
    else:
        mode, scope, apps, label, note = s["mode"], s["scope"], clean_apps(s["apps"]), "", ""
        if scope == "apps":
            if not apps:
                return "В настройках выбран режим «только для приложений», но приложений нет. Добавьте их в настройках VPN или скажите, например: включи впн только для хрома."
            label = ", ".join(apps)
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
