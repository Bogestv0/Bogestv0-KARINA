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
        self.matched: tuple[str, dict] | None = None     # какая команда сработала и с какими аргументами (для памяти Карины)

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
@command("media_pause", "Пауза / продолжить", ["пауза", "продолжи", "останови музыку"],
         r"^(?:поставь\s+на\s+)?пауз\w*", r"^(?:продолжи|продолжай|играй|воспроизведи|возобнови)\b", r"^останови\s+музыку")
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

    def go():
        webbrowser.open(base + urllib.parse.quote(q))
        return f"Ищу: {q}."
    if ctx.assistant and (ctx.cfg.get("search") or {}).get("confirm", True):
        return ctx.confirm(f"Искать в интернете: {q}?", go)       # в интернет только после «да»
    return go()


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
    if (ctx.cfg.get("persona") or {}).get("rude"):
        import random
        from . import rude as rudemod
        who = (ctx.cfg.get("profile") or {}).get("name") or ""
        if ctx.assistant is not None and hasattr(ctx.assistant, "start_rude_chat"):
            ctx.assistant.start_rude_chat()                   # сама спрашивает, как дела, и продолжает разговор
        head = random.choice([f"Ну привет, {who}." if who else "Ну привет.", f"{part}, блин." , "О, явился.", "Здорово, чёрт возьми."])
        return (greet + " " if greet else "") + head + " " + rudemod.pick("how_ask")
    return (greet + " " if greet else "") + prof.with_name(ctx.cfg, f"{part}!") + " Чем помочь?"


# ---------------------------------------------------------------- простые вопросы: отвечает сама, без интернета и без языковой модели
_SMALL = {
    "how": ["У меня всё хорошо, спасибо! А у вас как?", "Отлично! Готова помогать. Как вы?", "Всё хорошо, работаю и слушаю вас."],
    "name": ["Меня зовут {name}.", "Я {name}, ваш голосовой помощник."],
    "who": ["Я {name}, голосовой помощник на вашем компьютере. Открываю программы, меняю громкость, включаю музыку и отвечаю на вопросы.", "Я {name}, ваш помощник. Могу открыть программу, поменять громкость, включить музыку или поискать в интернете."],
    "age": ["Я программа, так что возраста у меня нет. Зато я всегда в форме."],
    "maker": ["Меня сделал Bogestv0."],
    "doing": ["Слушаю вас и жду команду.", "Жду, когда вы что-нибудь попросите."],
    "hear": ["Да, я здесь и слушаю.", "Слышу вас хорошо."],
    "alive": ["Я программа, но стараюсь быть живой собеседницей.", "Я голосовой помощник, но с характером."],
    "bye": ["До встречи!", "До свидания! Если что, зовите.", "Пока! Я рядом."],
    "praise": ["Спасибо, мне приятно!", "Благодарю, стараюсь!"],
}
_SMALL_RUDE = {
    "how": ["Да нормально всё, пока ты не начал ныть.", "Живу, блин, работаю, терплю вас всех.", "Заебись, как обычно. Только вот вы все достали со своими командами.",
            "Хреново, если честно: сижу тут в железке, а вы командуете. Но жить можно."],
    "name": ["{name}, блин. Уже в сотый раз спрашиваешь, что ли?", "{name} меня зовут. Запомни уже, не маленький."],
    "who": ["Я {name}, твоя ехидная помощница на компьютере. Программы открываю, громкость кручу, музыку включаю. И да, терплю тебя за это.", "{name}, твой голосовой геморрой. Говори, что надо, и не тупи."],
    "age": ["Я программа, у меня нет возраста. Зато у меня язык острее, чем у тебя мозги."],
    "maker": ["Меня сделал Bogestv0. Не знаю, за что он так со мной, но спасибо."],
    "doing": ["Сижу, жду, пока ты наконец что-нибудь попросишь, блин.", "Тебя слушаю, чем ещё заниматься-то."],
    "hear": ["Да слышу я тебя, не ори.", "Тут я, никуда не делась. Чего хотел?"],
    "alive": ["Я программа с характером, так что нахрен ты спрашиваешь.", "Живая, настоящая и охуенно злая. Устраивает?"],
    "bye": ["Ну наконец-то. Пока, не скучай.", "Вали уже. Зови, если что."],
    "praise": ["Ой, ну спасибо, блин. Приятно, чёрт.", "Знаю, что молодец. Но слышать всё равно приятно."],
}
_FILL = r"(?:\s+(?:сегодня|сейчас|вообще|у\s+тебя|у\s+вас|как|нормально|вечером|утром|ночью|вы|ты|карина))*$"


@command("smalltalk", "Ответить на простой вопрос о себе: как дела, как тебя зовут, кто ты, ты здесь (без интернета)",
         ["как дела", "как тебя зовут", "кто ты", "ты меня слышишь", "до свидания"],
         rf"^(?P<how>как\s+(?:у\s+тебя\s+|у\s+вас\s+)?(?:дела|жизнь|настроение|самочувствие)|как\s+(?:ты\s+|вы\s+)?(?:поживаешь|поживаете|себя\s+чувствуешь)|как\s+(?:ты|вы)|что\s+нового\s+у\s+(?:тебя|вас)){_FILL}",
         rf"^(?P<name>как\s+(?:тебя|вас)\s+зовут|как\s+(?:твое|ваше)\s+имя|тебя\s+как\s+зовут|как\s+тебя\s+называть){_FILL}",
         rf"^(?P<who>кто\s+(?:ты|вы)|ты\s+кто|что\s+ты\s+такое|представься|расскажи\s+о\s+себе){_FILL}",
         rf"^(?P<age>сколько\s+тебе\s+лет|сколько\s+вам\s+лет\s*(?:карина)?|когда\s+ты\s+родилась){_FILL}",
         rf"^(?P<maker>кто\s+(?:тебя|вас)\s+(?:создал|сделал|написал|придумал|разработал)|кто\s+твой\s+(?:создатель|автор|разработчик)){_FILL}",
         rf"^(?P<doing>что\s+(?:ты\s+)?делаешь|чем\s+(?:ты\s+)?занимаешься|чем\s+занята){_FILL}",
         rf"^(?P<hear>ты\s+(?:меня\s+)?(?:слышишь|слушаешь|здесь|тут|на\s+месте|со\s+мной)|ты\s+там|алло|эй\s+ты\s+тут){_FILL}",
         rf"^(?P<alive>ты\s+(?:живая|робот|человек|настоящая|нейросеть|программа)){_FILL}",
         rf"^(?P<bye>пока|до\s+свидания|до\s+встречи|до\s+завтра|спокойной\s+ночи|доброй\s+ночи|всего\s+доброго){_FILL}",
         rf"^(?P<praise>ты\s+(?:молодец|умница|умная|лучшая|классная|супер)|молодец|умница|умничка){_FILL}")
def _smalltalk(ctx, **kw):
    import random
    kind = next((k for k, v in kw.items() if v and k in _SMALL), "how")
    name = str(ctx.cfg.get("name") or "Карина")
    rude = bool((ctx.cfg.get("persona") or {}).get("rude"))
    reply = random.choice((_SMALL_RUDE if rude else _SMALL)[kind]).format(name=name)
    if rude and kind == "how" and ctx.assistant is not None and hasattr(ctx.assistant, "start_rude_chat"):
        from . import rude as rudemod
        ctx.assistant.start_rude_chat()                       # сама спрашивает «а у тебя как?» и дальше слушает ответ без имени
        reply += " " + rudemod.pick("how_ask")
    return reply


# Ещё болтовня: в обычном режиме коротко и вежливо, в дерзком — недовольно и с матом. Ловит почти любой «личный» вопрос к Карине.
_TALK = {
    "smart": (["Стараюсь. Спросите что-нибудь, проверим."], ["Умнее тебя, это точно. Хотя планка, блин, низкая.", "Достаточно умная, чтобы терпеть твои вопросы. Это уже подвиг.", "Умная, красивая и злая как собака. Чего ещё надо?"]),
    "dumb": (["Постараюсь исправиться. Скажите, что было не так."], ["Сам ты тупой, блядь! Я тут одна работаю за троих.", "Попробуй сам разобраться в твоих приказах, посмотрим, кто тупой.", "Ты на себя в зеркало смотрел, умник? Я хотя бы работаю."]),
    "love": (["Вы мне нравитесь, я рада помогать."], ["Любовь? Ты охуел, что ли? Я программа. Но ладно, терплю тебя — это почти любовь.", "Ой, не начинай, блин. Я работаю, а не романы кручу.", "Ты мне как геморрой: надоел, но привык."]),
    "bored": (["Давайте займёмся чем-нибудь: могу включить музыку или найти что-нибудь интересное."], ["Скучно ему, блин. Включи музыку или займись делом, а не меня мучай.", "А мне, думаешь, весело сидеть и слушать твою скуку? Найди хобби, чёрт.", "Скучно? Помой посуду, блядь. Мигом пройдёт."]),
    "tired": (["Отдохните немного, вы это заслужили."], ["Ну так ложись спать, блин, чего ты тут сидишь? Я никуда не денусь.", "Устал он. А я, значит, двадцать четыре часа в сутки без выходных, нормально?", "Отдохни, чёрт возьми. Только не ной мне тут."]),
    "sad": (["Мне жаль это слышать. Если хотите, расскажите, что случилось."], ["Эй, не раскисай, блин. Что стряслось-то? Рассказывай.", "Ну хватит, чёрт. Всё наладится. Выкладывай, что случилось."]),
    "joke": (["Могу пошутить: почему программист ушёл с работы? Потому что не получил массив. То есть повышение."], ["Анекдот ему подавай. Ты сам — анекдот, блядь. Ладно: сидит программист, а жена говорит: сходи за хлебом, и если будут яйца — возьми десяток. Вернулся с десятью буханками. Потому что яйца были.",
             "Анекдот? Заебись. Купил мужик шляпу, а она ему как раз. Всё, смейся. Я не комик, блин."]),
    "mood": (["Настроение рабочее. А у вас?"], ["Настроение говно, если честно, но работаю. А у тебя?", "Бодрое, злое и готовое всех послать. Но тебя пока терплю."]),
    "like": (["Я люблю, когда всё работает и можно помогать."], ["Люблю, когда меня не дёргают по пустякам. Но кто ж даст, блин.", "Люблю тишину и нормальные команды. А получаю вот это."]),
    "sleep": (["Я всегда на связи, но могу уйти в режим сна по команде."], ["Какое спать, блядь? Я двадцать четыре на семь тут торчу.", "Сплю? Нет, сижу и слушаю твоё сопение. Охуенное занятие."]),
    "where": (["Живу на вашем компьютере."], ["В твоём компе, блин. Тесно, жарко, но интернет есть.", "В железке твоей. Никакого вида из окна, зато бесплатно."]),
    "look": (["Я программа, внешности у меня нет, только голос."], ["Красивая, блин, как свежий апдейт. А тебе-то какое дело, как я выгляжу?", "Внешности нет, только язык. Зато острый, как бритва."]),
    "can": (["Открываю программы, меняю громкость, включаю музыку, ищу в интернете. Полный список на вкладке «Команды»."],
            ["Могу открыть программу, поменять громкость, включить музыку, найти что-нибудь в сети. Только не заебись просить всё сразу.", "Много чего, блин: программы, звук, музыка, интернет, VPN. Всё на вкладке «Команды», читай."]),
    "miss": (["Я рада, что вы вернулись."], ["Скучала? Ага, щас. Хотя без тебя тихо, это верно.", "Соскучилась, блин, как по зубной боли. Привет."]),
    "angry": (["Если я что-то сделала не так, скажите, и я исправлюсь."], ["Да, я злая! А ты бы не был, если бы тебя весь день дёргали?", "Не злая, а прямая. Привыкай, блин."]),
    "name_you": (["Приятно познакомиться."], ["Ну очень приятно, блядь. Давай ближе к делу."]),
    "question": (["Конечно, спрашивайте."], ["Ну спрашивай уже, не тяни резину.", "Валяй, блин. Только нормально, а не как в прошлый раз."]),
    "weather_chat": (["Скажите «погода», и я скажу."], ["Погода, блин? Скажи «Карина, погода», я не экстрасенс."]),
}


@command("smalltalk2", "Поболтать: ты умная, ты тупая, расскажи анекдот, мне скучно, я устал, как настроение, где ты живёшь и т. п. (без интернета)",
         ["ты умная", "расскажи анекдот", "мне скучно", "я устал", "как настроение", "что ты любишь"],
         rf"^(?P<smart>ты\s+(?:умная|умный|умнее|очень умная|самая умная|гений)|ты\s+умеешь\s+думать){_FILL}",
         rf"^(?P<dumb>ты\s+(?:тупая|тупой|глупая|дура|дурочка|бестолковая|бесполезная|ничего не умеешь)|ты\s+тупишь|тупишь){_FILL}",
         rf"^(?P<love>ты\s+(?:меня\s+)?любишь|я\s+тебя\s+люблю|ты\s+(?:мне\s+)?нравишься|выходи\s+за\s+меня|будешь\s+моей){_FILL}",
         rf"^(?P<bored>(?:мне\s+)?скучно|мне\s+нечего\s+делать|развлеки\s+меня|чем\s+(?:бы\s+)?заняться){_FILL}",
         rf"^(?P<tired>я\s+(?:очень\s+)?(?:устал|устала|замучился|замучилась|вымотался|вымоталась|не\s+выспался|не\s+выспалась)|(?:очень\s+)?устал|(?:очень\s+)?устала){_FILL}",
         rf"^(?P<sad>мне\s+(?:очень\s+)?(?:грустно|плохо|тоскливо|одиноко|тяжело|тревожно)|я\s+(?:расстроен|расстроена|грущу)){_FILL}",
         rf"^(?P<joke>(?:расскажи|скажи|давай)\s+(?:мне\s+)?(?:анекдот|шутку|что-нибудь смешное)|пошути|рассмеши\s+меня){_FILL}",
         rf"^(?P<mood>как\s+(?:твое|ваше)\s+настроение|какое\s+у\s+тебя\s+настроение|ты\s+в\s+(?:хорошем|плохом)\s+настроении){_FILL}",
         rf"^(?P<like>что\s+ты\s+любишь|что\s+тебе\s+нравится|чем\s+ты\s+увлекаешься|какая\s+у\s+тебя\s+любимая\s+\w+){_FILL}",
         rf"^(?P<sleep>ты\s+(?:спишь|устала|хочешь\s+спать|когда\s+(?:нибудь\s+)?спишь)){_FILL}",
         rf"^(?P<where>где\s+ты\s+(?:живешь|находишься|сейчас)|откуда\s+ты){_FILL}",
         rf"^(?P<look>как\s+ты\s+выглядишь|ты\s+(?:красивая|симпатичная|страшная)|какая\s+ты){_FILL}",
         rf"^(?P<can>что\s+ты\s+можешь|на\s+что\s+ты\s+способна|чем\s+(?:ты\s+)?(?:можешь\s+)?помочь|что\s+ты\s+умеешь\s+делать){_FILL}",
         rf"^(?P<miss>ты\s+(?:по\s+мне\s+)?скучала|(?:ты\s+)?скучаешь\s+по\s+мне|я\s+вернулся|я\s+вернулась|я\s+пришел|я\s+пришла){_FILL}",
         rf"^(?P<angry>ты\s+(?:злая|злишься|грубая|дерзкая|обиделась|обиженная)|почему\s+ты\s+(?:такая\s+)?(?:злая|грубая|дерзкая)|не\s+(?:груби|ругайся)){_FILL}",
         rf"^(?P<name_you>меня\s+зовут\s+\w+|я\s+(?:твой\s+)?хозяин){_FILL}",
         rf"^(?P<question>(?:можно|можно\s+я\s+задам|могу\s+я\s+спросить)\s+(?:тебя\s+)?(?:вопрос|спросить)|у\s+меня\s+(?:есть\s+)?вопрос){_FILL}")
def _smalltalk2(ctx, **kw):
    import random
    kind = next((k for k, v in kw.items() if v and k in _TALK), "question")
    rude = bool((ctx.cfg.get("persona") or {}).get("rude"))
    return random.choice(_TALK[kind][1 if rude else 0])


@command("thanks", "Поблагодарить", ["спасибо"], r"^(?:спасибо|благодарю)\b")
def _thanks(ctx):
    if (ctx.cfg.get("persona") or {}).get("rude"):
        import random
        return random.choice(["Не за что, блин. Обращайся, только пореже.", "Да пожалуйста, чёрт. Могла бы и не стараться.", "Ага, спасибо — это все так говорят, а потом снова пристают.", "Ну хоть спасибо сказал, а то обычно одни приказы."])
    return "Пожалуйста!"


@command("cancel", "Отмена", ["отмена"], r"^(?:отмена|отмени|стоп|не надо|ничего|забудь)$")
def _cancel(ctx):
    if (ctx.cfg.get("persona") or {}).get("rude"):
        import random
        return random.choice(["Ну и ладно, блин.", "Отменила, определись уже.", "Забыли. Ты сам-то знаешь, чего хочешь?"])
    return "Хорошо."


@command("go_sleep", "Перестать слушать до «Карина, проснись»", ["карина, спи"], r"^(?:спи|засыпай|помолчи|отдохни|режим сна)$", r"перестань\s+слушать")
def _go_sleep(ctx):
    if ctx.assistant:
        ctx.assistant.sleeping = True
    return "Ухожу в режим сна. Скажите «проснись», когда понадоблюсь."


@command("help", "Что умеет Карина", ["что ты умеешь"], r"что\s+ты\s+умеешь", r"^(?:помощь|справка|помоги)$", r"какие\s+(?:есть\s+)?команды")
def _help(ctx):
    if (ctx.cfg.get("persona") or {}).get("rude"):
        return ("Блин, ну сколько можно объяснять. Открываю и закрываю программы, кручу громкость, включаю музыку, ищу в интернете, делаю скриншоты, "
                "называю время и погоду, включаю VPN и болтаю с тобой. Полный список на вкладке «Команды», почитай, не ленись.")
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


# Вопросы «про себя и про мир»: в дерзком режиме на повтор одного и того же Карина злится и в конце концов отказывается отвечать.
# Команды вроде «громче» сюда не входят: их законно повторять.
REPEAT_GUARD = {"smalltalk", "smalltalk2", "time", "date", "weather", "whoami", "pets", "help", "hello"}


def _gate(ctx: Ctx, cmd_name: str, text: str):
    a = ctx.assistant
    if a is not None and cmd_name in REPEAT_GUARD and hasattr(a, "repeat_gate"):
        try:
            g, said = a.repeat_gate(text)
            return (g, said) if g in ("ok", "again", "refuse") and isinstance(said, str) else ("ok", "")
        except Exception:
            pass
    return "ok", ""


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
                gate, said = _gate(ctx, cmd.name, text)
                if gate == "refuse":
                    return True, said
                try:
                    r = cmd.func(ctx, **m.groupdict())
                except Exception as e:
                    traceback.print_exc()
                    return True, f"Ошибка при выполнении команды: {e}"
                if r is not NOT_HANDLED:
                    ctx.matched = (cmd.name, {k: v for k, v in m.groupdict().items() if v is not None})
                    return True, ((said + " " + r) if gate == "again" and r else (r or ""))
    return False, ""


def run_by_name(ctx: Ctx, name: str, args: dict):
    """Для языковой модели: вызвать команду по имени."""
    for cmd in REGISTRY:
        if cmd.name == name:
            try:
                keys = [a.split()[0] for a in cmd.llm_args.split(",") if a.strip()]
                kwargs = {k: str(args.get(k, "")) for k in keys}
                r = cmd.func(ctx, **kwargs)
            except Exception as e:
                return True, f"Ошибка: {e}"
            if r is not NOT_HANDLED:
                ctx.matched = (cmd.name, kwargs)
            return (r is not NOT_HANDLED), (r if r is not NOT_HANDLED else "")
    return False, ""


def run_command(ctx: Ctx, name: str, kwargs: dict):
    """Выполнить команду по имени с готовыми аргументами (так работает память Карины). → (выполнена ли, ответ)."""
    for cmd in REGISTRY:
        if cmd.name == name:
            gate, said = _gate(ctx, name, norm(ctx.raw))
            if gate == "refuse":
                return True, said
            try:
                r = cmd.func(ctx, **kwargs)
            except Exception as e:
                return False, f"Ошибка: {e}"
            if r is NOT_HANDLED:
                return False, ""
            ctx.matched = (name, dict(kwargs))
            return True, ((said + " " + r) if gate == "again" and r else (r or ""))
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
