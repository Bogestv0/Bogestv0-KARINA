"""Реестр команд. Каждая команда: имя, описание, примеры, регулярные выражения по нормализованному тексту."""
from __future__ import annotations
import datetime, importlib.util, json, os, re, subprocess, sys, traceback, urllib.parse, urllib.request, webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from rapidfuzz import fuzz
from .textutil import norm, parse_number, plural
from . import winutil, net
from .config import ROOT, DATA

NOT_HANDLED = object()
REGISTRY: list["Command"] = []


@dataclass
class Command:
    name: str
    desc: str
    examples: list
    patterns: list
    func: Callable
    llm_args: str = ""   # имена аргументов для разбора языковой моделью, через запятую
    first: bool = False


def command(name: str, desc: str, examples: list, *patterns: str, llm_args: str = "", first: bool = False):
    """first=True — проверять раньше остальных: узкие команды («тише в игре», «включи впн») должны обгонять общие
    («тише», «включи …» = открыть программу)."""
    def deco(fn):
        cmd = Command(name, desc, list(examples), [re.compile(p) for p in patterns], fn, llm_args, first)
        if first:
            REGISTRY.insert(sum(1 for c in REGISTRY if c.first), cmd)
        else:
            REGISTRY.append(cmd)
        return fn
    return deco


class Ctx:
    """Что команда может использовать: настройки, программы, подтверждение, сон."""
    def __init__(self, cfg, apps, assistant=None, raw: str = ""):
        self.cfg, self.apps, self.assistant, self.raw = cfg, apps, assistant, raw

    def confirm(self, question: str, action: Callable[[], str]) -> str:
        if self.assistant:
            self.assistant.ask_confirm(action)
        return question + " Скажите «да» или «нет»."


# ---------------------------------------------------------------- громкость
def _endpoint():
    try:
        from pycaw.pycaw import AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            return dev.EndpointVolume
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        from ctypes import cast, POINTER
        return cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def _get_volume() -> int | None:
    ep = _endpoint()
    try:
        return round(ep.GetMasterVolumeLevelScalar() * 100) if ep else None
    except Exception:
        return None


def _set_volume(pct: int) -> bool:
    ep = _endpoint()
    if not ep:
        return False
    try:
        ep.SetMasterVolumeLevelScalar(max(0, min(100, pct)) / 100.0, None)
        return True
    except Exception:
        return False


def _set_mute(on: bool):
    ep = _endpoint()
    try:
        if ep:
            ep.SetMute(1 if on else 0, None)
            return
    except Exception:
        pass
    winutil.press("mute")


@command("volume_set", "Установить громкость в процентах", ["громкость пятьдесят процентов", "сделай громкость на 30"],
         r"(?:громкост\w*|звук)\s+(?:на\s+|до\s+)?(?P<n>[\w\s%]+?)$", llm_args="n (0-100)")
def _volume_set(ctx, n):
    v = parse_number(n)
    if v is None or v > 100:
        return NOT_HANDLED
    if not _set_volume(v):
        winutil.press("vol_down", 50)
        winutil.press("vol_up", v // 2)
    return f"Громкость {v} процентов."


@command("volume_up", "Сделать громче", ["громче", "прибавь звук"], r"^(?:сделай\s+)?(?:по)?громче\b", r"(?:прибавь|добавь|увеличь|повысь)\s+(?:звук|громкость)")
def _vol_up(ctx):
    cur = _get_volume()
    if cur is not None and _set_volume(cur + 10):
        return f"Громкость {min(100, cur + 10)}."
    winutil.press("vol_up", 5)
    return "Погромче."


@command("volume_down", "Сделать тише", ["тише", "убавь звук"], r"^(?:сделай\s+)?(?:по)?тише\b", r"(?:убавь|уменьши|понизь|убери)\s+(?:звук|громкость)")
def _vol_down(ctx):
    cur = _get_volume()
    if cur is not None and _set_volume(cur - 10):
        return f"Громкость {max(0, cur - 10)}."
    winutil.press("vol_down", 5)
    return "Потише."


@command("mute", "Выключить звук", ["выключи звук", "без звука"], r"(?:выключи|отключи|заглуши)\s+звук", r"^без звука", r"\bмьют\b")
def _mute(ctx):
    _set_mute(True)
    return "Звук выключен."


@command("unmute", "Включить звук", ["включи звук", "верни звук"], r"(?:включи|верни|размуть)\s+звук")
def _unmute(ctx):
    _set_mute(False)
    return "Звук включён."


# ---------------------------------------------------------------- медиа
@command("media_pause", "Пауза / продолжить", ["пауза", "продолжи", "включи музыку"],
         r"^(?:поставь\s+на\s+)?пауз\w*", r"^(?:продолжи|продолжай|играй|воспроизведи|возобнови)\b", r"^(?:включи|запусти)\s+музыку$", r"^останови\s+музыку")
def _pause(ctx):
    winutil.press("play")
    return "Готово."


@command("media_next", "Следующий трек", ["следующий трек", "дальше"], r"следующ\w+\s+(?:трек|песн\w+|композици\w+)", r"^(?:дальше|следующий|вперед)$", r"переключи\s+(?:трек|песню)")
def _next(ctx):
    winutil.press("next")
    return "Следующий трек."


@command("media_prev", "Предыдущий трек", ["предыдущий трек"], r"предыдущ\w+\s+(?:трек|песн\w+|композици\w+)", r"^(?:назад|предыдущий)$")
def _prev(ctx):
    winutil.press("prev")
    return "Предыдущий трек."


# ---------------------------------------------------------------- время, дата
@command("time", "Сказать время", ["который час", "сколько времени"], r"который\s+час", r"сколько\s+(?:сейчас\s+)?времени", r"^(?:скажи\s+)?время$")
def _time(ctx):
    t = datetime.datetime.now()
    return f"Сейчас {t.hour} {plural(t.hour, ('час', 'часа', 'часов'))} {t.minute} {plural(t.minute, ('минута', 'минуты', 'минут'))}."


_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@command("date", "Сказать дату", ["какое сегодня число", "какой сегодня день"], r"как\w+\s+сегодня\s+(?:число|дата|день)", r"какое\s+число", r"какой\s+день\s+недели", r"^(?:скажи\s+)?дату$")
def _date(ctx):
    d = datetime.date.today()
    return f"Сегодня {_DAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year} года."


# ---------------------------------------------------------------- окна и система
@command("show_desktop", "Свернуть все окна", ["сверни все окна", "покажи рабочий стол"], r"сверни\s+все\s+окна", r"покажи\s+рабочий\s+стол", r"^рабочий\s+стол$")
def _desktop(ctx):
    winutil.hotkey("win", "d")
    return "Готово."


@command("minimize", "Свернуть окно", ["сверни окно"], r"^сверни(?:\s+(?:это|текущее))?\s+окно$", r"^сверни$")
def _min(ctx):
    winutil.hotkey("win", "down")
    return "Свернула."


@command("maximize", "Развернуть окно", ["разверни окно"], r"^разверни(?:\s+(?:это|текущее))?\s+окно$", r"^разверни$", r"на весь экран")
def _max(ctx):
    winutil.hotkey("win", "up")
    return "Развернула."


@command("close_window", "Закрыть текущее окно", ["закрой окно"], r"^закрой\s+(?:это\s+|текущее\s+)?окно$")
def _close_win(ctx):
    winutil.hotkey("alt", "f4")
    return "Закрываю окно."


@command("switch_window", "Переключить окно", ["переключи окно"], r"^(?:переключи|смени)\s+окно$", r"следующее\s+окно")
def _switch(ctx):
    winutil.hotkey("alt", "tab")
    return "Переключаю."


@command("screenshot", "Сделать скриншот", ["сделай скриншот"], r"скриншот", r"сними\s+экран", r"снимок\s+экрана")
def _shot(ctx):
    from PIL import ImageGrab
    pics = Path(os.path.expanduser("~")) / "Pictures" / "Karina"
    pics.mkdir(parents=True, exist_ok=True)
    p = pics / datetime.datetime.now().strftime("screenshot_%Y-%m-%d_%H-%M-%S.png")
    ImageGrab.grab(all_screens=True).save(p)
    return "Скриншот сохранён в папку Изображения, Карина."


@command("lock", "Заблокировать компьютер", ["заблокируй компьютер"], r"заблокируй\s+(?:компьютер|экран|пк)", r"^блокировка$")
def _lock(ctx):
    winutil.lock()
    return "Блокирую."


def _power(args: list[str]) -> Callable[[], str]:
    def run():
        subprocess.Popen(args, creationflags=winutil.NO_WINDOW)
        return "Выполняю."
    return run


@command("shutdown", "Выключить компьютер (с подтверждением)", ["выключи компьютер"], r"выключи\s+(?:компьютер|пк)", r"заверши\s+работу")
def _shutdown(ctx):
    return ctx.confirm("Выключить компьютер?", _power(["shutdown", "/s", "/t", "5"]))


@command("restart", "Перезагрузить компьютер (с подтверждением)", ["перезагрузи компьютер"], r"перезагрузи\s+(?:компьютер|пк)", r"^перезагрузка$")
def _restart(ctx):
    return ctx.confirm("Перезагрузить компьютер?", _power(["shutdown", "/r", "/t", "5"]))


@command("sleep_pc", "Спящий режим компьютера (с подтверждением)", ["усыпи компьютер"], r"спящий\s+режим", r"усыпи\s+(?:компьютер|пк)")
def _sleep_pc(ctx):
    return ctx.confirm("Перевести компьютер в спящий режим?", _power(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]))


@command("type_text", "Напечатать текст в активном окне", ["напиши привет, как дела"], r"^(?:напиши|набери|напечатай|введи)\s+(?P<t>.+)$", llm_args="t")
def _type(ctx, t):
    m = re.match(r"\s*(?:напиши|набери|напечатай|введи)\s+(.+)", ctx.raw, re.I | re.S)
    text = (m.group(1) if m else t).strip()
    winutil.paste_text(text[:1].upper() + text[1:] if text else text)
    return "Напечатала."


# ---------------------------------------------------------------- интернет
@command("search", "Поиск в интернете", ["найди в интернете погоду", "загугли рецепт борща"],
         r"^(?:найди|поищи|загугли|погугли|поиск|яндексни)\s+(?:в\s+(?:интернете|гугле|яндексе|google)\s+)?(?P<q>.+)$", llm_args="q")
def _search(ctx, q):
    if not net.is_online():
        return "Нет интернета, поиск сейчас недоступен."
    base = "https://yandex.ru/search/?text=" if ctx.cfg.get("search_engine") == "yandex" else "https://www.google.com/search?q="
    webbrowser.open(base + urllib.parse.quote(q))
    return f"Ищу: {q}."


# ---------------------------------------------------------------- программы
@command("focus_app", "Переключиться на программу", ["переключись на миксер", "покажи ремоут"],
         r"^(?:переключись|перейди|вернись)\s+(?:на|в|к)\s+(?P<target>.+)$", r"^(?:покажи|разверни)\s+(?P<target>.+)$", llm_args="target")
def _focus(ctx, target):
    return ctx.apps.focus(target)


@command("close_app", "Закрыть программу", ["закрой миксер", "выключи блокнот"],
         r"^(?:закрой|закрыть|выключи|останови|заверши)\s+(?:программу\s+|приложение\s+)?(?P<target>.+)$", llm_args="target")
def _close(ctx, target):
    return ctx.apps.close(target)


@command("open_app", "Открыть программу или сайт", ["открой миксер", "запусти блокнот", "открой youtube.com"],
         r"^(?:открой|открыть|запусти|запустить|включи|зайди\s+на|перейди\s+на)\s+(?:мне\s+)?(?:сайт\s+|программу\s+|приложение\s+)?(?P<target>.+)$", llm_args="target")
def _open(ctx, target):
    return ctx.apps.open(target)


# ---------------------------------------------------------------- разговор и сама Карина
@command("hello", "Поздороваться", ["привет"], r"^(?:привет|здравствуй|здравствуйте|хай|добрый\s+(?:день|вечер)|доброе\s+утро)\b")
def _hello(ctx):
    h = datetime.datetime.now().hour
    part = "Доброе утро" if 5 <= h < 12 else "Добрый день" if 12 <= h < 18 else "Добрый вечер" if h < 23 else "Привет"
    from . import profile as prof, personal
    greet = personal.morning_greeting(ctx.cfg)                  # день рождения: поздравляем один раз за день
    return (greet + " " if greet else "") + prof.with_name(ctx.cfg, f"{part}!") + " Чем помочь?"


@command("thanks", "Поблагодарить", ["спасибо"], r"^(?:спасибо|благодарю)\b")
def _thanks(ctx):
    return "Пожалуйста!"


@command("cancel", "Отмена", ["отмена"], r"^(?:отмена|отмени|стоп|не надо|ничего|забудь)$")
def _cancel(ctx):
    return "Хорошо."


@command("go_sleep", "Перестать слушать до «Карина, проснись»", ["карина, спи"], r"^(?:спи|засыпай|помолчи|отдохни|режим сна)$", r"перестань\s+слушать")
def _go_sleep(ctx):
    if ctx.assistant:
        ctx.assistant.sleeping = True
    return "Ухожу в режим сна. Скажите «проснись», когда понадоблюсь."


@command("help", "Что умеет Карина", ["что ты умеешь"], r"что\s+ты\s+умеешь", r"^(?:помощь|справка|помоги)$", r"какие\s+(?:есть\s+)?команды")
def _help(ctx):
    return ("Я умею открывать и закрывать программы, менять громкость, управлять музыкой, искать в интернете, "
            "делать скриншоты, называть время и дату, и выполнять ваши собственные команды. Полный список на вкладке «Команды».")


# ---------------------------------------------------------------- свои команды из настроек
def run_custom_do(do: dict, ctx: Ctx) -> None:
    t, v = do.get("type"), do.get("value", "")
    if t == "run":
        winutil.start(v, do.get("args", ""))
    elif t == "url":
        webbrowser.open(v)
    elif t == "keys":
        winutil.hotkey(*[k.strip().lower() for k in v.split("+") if k.strip()])
    elif t == "shell":
        subprocess.Popen(v, shell=True, creationflags=winutil.NO_WINDOW)
    elif t == "type":
        winutil.paste_text(v)
    elif t == "http":
        req = urllib.request.Request(v, method=do.get("method", "GET").upper(),
                                     data=do["body"].encode("utf-8") if do.get("body") else None,
                                     headers={"Content-Type": "application/json"} if do.get("body") else {})
        urllib.request.urlopen(req, timeout=5).read()
    else:
        raise ValueError(f"неизвестный тип действия: {t}")


def match_custom(ctx: Ctx, text: str):
    for c in ctx.cfg.get("commands", []):
        for ph in c.get("phrases", []):
            p = norm(ph)
            if p and (p == text or fuzz.ratio(p, text) >= 88):
                return c
    return None


def dispatch(ctx: Ctx, text: str):
    """text — нормализованная фраза. Возвращает (handled, reply)."""
    c = match_custom(ctx, text)
    if c:
        try:
            run_custom_do(c.get("do", {}), ctx)
            return True, c.get("reply", "Готово.")
        except Exception as e:  # ошибка своей команды не должна ронять ассистента
            return True, f"Не удалось выполнить команду: {e}"
    for cmd in REGISTRY:
        for p in cmd.patterns:
            m = p.search(text)
            if m:
                try:
                    r = cmd.func(ctx, **m.groupdict())
                except Exception as e:
                    traceback.print_exc()
                    return True, f"Ошибка при выполнении команды: {e}"
                if r is not NOT_HANDLED:
                    return True, r or ""
    return False, ""


def run_by_name(ctx: Ctx, name: str, args: dict):
    """Для языковой модели: вызвать команду по имени."""
    for cmd in REGISTRY:
        if cmd.name == name:
            try:
                keys = [a.split()[0] for a in cmd.llm_args.split(",") if a.strip()]
                r = cmd.func(ctx, **{k: str(args.get(k, "")) for k in keys})
            except Exception as e:
                return True, f"Ошибка: {e}"
            return (r is not NOT_HANDLED), (r if r is not NOT_HANDLED else "")
    return False, ""


# ---------------------------------------------------------------- плагины
class PluginAPI:
    """Передаётся в register(api) каждого плагина из папки plugins/."""
    command = staticmethod(command)
    winutil = winutil
    Ctx = Ctx


def load_plugins() -> list[str]:
    """Подключает plugins/*.py (кроме начинающихся с «_»). Возвращает список ошибок."""
    errs = []
    folders = [ROOT / "plugins"] + ([DATA / "plugins"] if DATA != ROOT / "data" else [])
    for f in sorted(f for d in folders if d.exists() for f in d.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"karina_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.register(PluginAPI)
        except Exception as e:
            errs.append(f"{f.name}: {e}")
    return errs
