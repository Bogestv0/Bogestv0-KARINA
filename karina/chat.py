"""Чат: диалоги с историей на диске, потоковый ответ модели, голосовой вызов («Карина, найди и напиши отчёт про…»)."""
from __future__ import annotations
import datetime, json, random, re, threading, time, uuid
from pathlib import Path
from . import coder as codermod, files as filesmod, images as imagesmod, imgdirector, imgpower, localimg, net, prompts, research, websearch
from .brain import Brain, BrainUnavailable
from .config import DATA
from .engine import EngineError
from .textutil import FEMALE, norm, plural

CHATS_DIR = DATA / "chats"
MAX_TOKENS = 1500
LIMITS = {"local": (1500, 3500), "claude": (3000, 8000), "cloud": (1500, 3500)}     # (обычный ответ, ответ с файлом) в токенах
CLAUDE_CTX = 32768                                          # сколько истории и найденного отдаём Claude (токенов)
CHARS_PER_TOKEN = 2.0          # для русского с запасом: лучше обрезать историю раньше, чем переполнить контекст

ATTACH_CHARS = {"local": 20_000, "claude": 90_000, "cloud": 30_000}          # сколько текста приложенных файлов отдаём модели при ответе «только прочитать»
MODES = ("auto", "code", "ask")                              # авто (Код включён: агент берёт задачи с кодом и файлами) / всегда агент / только ответ в чате (Код выключен)

# Просьба что-то СДЕЛАТЬ с кодом (нужен кодер) — в отличие от «посмотри», «объясни», «найди ошибку» (достаточно прочитать и ответить).
CODE_VERB = re.compile(
    r"\b(?:напиши|написать|создай|сделай|реализуй|разработай|исправь|почини|поправь|доработай|дополни|добавь|измени|переделай|перепиши|рефактор\w*|"
    r"оптимизируй|внеси|запусти|протестируй|покрой|обнови|удали|убери|замени|сгенерируй|допиши|доделай|собери|оформи|нарисуй макет|верстай|сверстай)\b")
CODE_HINT = re.compile(r"(?:\bкод|файл|функци|класс|тест|скрипт|ошибк|баг|програм|приложен|проект|модул|страниц|шаблон|макет|\.py\b|\.js\b|\.html\b|\bpython|\bnode)")
# просьба «только посмотреть, объяснить, разобрать»: изменений не нужно
READ_ONLY = re.compile(r"\b(?:посмотри|глянь|взгляни|объясни|поясни|расскажи|опиши|что делает|как работает|почему|зачем|проверь|найди (?:ошибк\w*|баг\w*|проблем\w*)|разбер\w*|ревью|оцени|прокомментируй|что (?:тут|здесь|в нем|не так))\b")
# новая программа или оформление без приложенных файлов: достаточно крупное, чтобы его вёл кодер (с проверкой запуском)
CODE_TARGET = re.compile(r"(?:програм|приложен|проект|\bбот\b|\bбота\b|парсер|утилит|\bсайт|\bигр[уы]|модул|библиотек|\bтест(?:ы|ами|ов)?\b|\bapi\b|"
                         r"скрипт|функци|\bкласс|\bкод\b|калькулятор|страниц|лендинг|верстк|шаблон|макет|интерфейс|\bформ[ауы]\b|\bhtml\b|\bcss\b|\bpython\b|\bnode\b|\bjs\b|расширени)")
# что в этих сообщениях значит «просто файл» (документ, таблица, PDF), а не программа
PLAIN_DOC = re.compile(r"(?:документ|\bворд|\bword|\bdocx|таблиц\w* в excel|\bэксел|\bexcel|\bpdf|\bпдф|реферат|доклад|эссе|резюме|письм)")
LIGHT_EXT = {"py", "js", "mjs", "ts", "html", "css", "json", "txt", "md", "csv", "xml", "yaml", "yml", "ini", "toml", "sql", "log", "java", "c", "cpp", "h", "cs", "go", "rs", "php", "rb"}
LIGHT_BYTES = 9000


def light_attachments(paths: list[str]) -> bool:
    """Приложено немного обычных текстовых файлов (не архив, не папка): их можно просто прочитать и ответить, не заводя проект."""
    from pathlib import Path as _P
    if not paths or len(paths) > 3:
        return False
    total = 0
    for raw in paths:
        p = _P(raw)
        try:
            if not p.is_file() or p.suffix[1:].lower() not in LIGHT_EXT:
                return False
            total += p.stat().st_size
        except OSError:
            return False
    return total <= LIGHT_BYTES


# короткое продолжение вроде «сделай файлы», «напиши это» — смысл берётся из предыдущих реплик
CODE_FILES = re.compile(r"(?:\bфайл\w*|\bэто\b|\bвсе\b|\bготов\w*|\bпроект\w*|\bкод\b|\bбота?\b|\bтак\b)")


QUESTION_COUNT = "Сколько вариантов нарисовать? Выберите от 1 до 4 — или напишите число."


def route(text: str, has_paths: bool, has_project: bool, mode: str = "auto", voice: bool = False, light: bool = False,
          last_image: bool = False, context: str = "") -> str:
    """Куда пойдёт сообщение: "image" — картинка; "code" — агент пишет, запускает и проверяет код в папке проекта; "chat" — обычный ответ.
    «Код» включён (mode "auto") — агент берёт файлы, архивы и просьбы написать или оформить что-то: он сам читает проект по частям, правит, проверяет и отдаёт готовое.
    Только когда приложено немного обычных файлов и просят «посмотреть / объяснить» без изменений (light), хватает ответа в чате.
    Голосовые вопросы идут в чат (или на картинку); «Код» выключен (mode "ask") — только чат и картинки."""
    if imagesmod.wants_image(text) or (last_image and imagesmod.is_follow_up(text)):
        return "image"
    if voice or mode == "ask":
        return "chat"
    if mode == "code":
        return "code"
    t = norm(text)
    verb = bool(CODE_VERB.search(t))
    if has_paths:
        if light and READ_ONLY.search(t) and not verb:
            return "chat"
        return "code"
    if has_project and verb and CODE_HINT.search(t):
        return "code"                                  # продолжение работы над проектом этого диалога
    if has_project and READ_ONLY.search(t) and CODE_HINT.search(t) and not PLAIN_DOC.search(t):
        return "code"                                  # «посмотри, что в проекте / что не так с тестом» — агент видит файлы проекта
    if verb and not PLAIN_DOC.search(t):
        if CODE_TARGET.search(t):
            return "code"
        c = norm(context)
        if c and CODE_TARGET.search(c) and not PLAIN_DOC.search(t) and (CODE_FILES.search(t) or len(t.split()) <= 4):
            return "code"                              # «сделай файлы» после разговора про бота или программу
    return "chat"


# Фразы, которые сразу идут в чат (раньше обычных команд: «найди и напиши…» иначе стало бы поиском в браузере).
CHAT_PREFIX = re.compile(
    r"^(?:найди и (?:напиши|составь|подготовь|расскажи|собери)|найди информацию|напиши (?:мне )?(?:отчет|реферат|статью|эссе|пост|план|письмо|резюме|обзор|конспект|сочинение|доклад|инструкцию|краткое)|"
    r"составь|подготовь|расскажи|объясни|что такое|кто такой|кто такая|почему|зачем|как работает|в чем разница|сравни|посоветуй|придумай|переведи|"
    r"помоги (?:мне )?(?:с|со|написать|разобраться)|давай обсудим|вопрос|чат\b|"
    r"создай (?:мне )?(?:файл|документ|таблиц\w*|схем\w*|диаграмм\w*|pdf|пдф|ворд|word|эксель|excel|отчет|иллюстраци\w*|рисунок|скрипт|программу|код)|"
    r"сделай (?:мне )?(?:файл|документ|таблиц\w*|схем\w*|диаграмм\w*|pdf|пдф|ворд|word|эксель|excel|отчет)|нарисуй|"
    r"сохрани (?:это|ответ|результат|в файл|в (?:word|ворд|эксель|excel|pdf|пдф)))")
OPEN_CHAT = re.compile(r"^(?:открой|покажи|включи)\s+(?:окно\s+)?чат\b")
OPEN_FILES = re.compile(r"^(?:открой|покажи)\s+(?:мои\s+)?(?:папку\s+(?:с\s+)?(?:карин\w*|файлами)|файлы\s+карин\w*)")


def is_chat_request(text: str) -> bool:
    """text — нормализованная фраза без имени."""
    return bool(CHAT_PREFIX.search(text))


def wants_open_chat(text: str) -> bool:
    return bool(OPEN_CHAT.search(text))


def wants_open_files(text: str) -> bool:
    return bool(OPEN_FILES.search(text))


PROFILE_PROVIDER = lambda: ""      # noqa: E731  Ассистент подставляет сюда справку о пользователе (profile.prompt_section)


def system_prompt(voice: bool = False, web: bool = False, files: bool = False, attached: bool = False) -> str:
    today = datetime.date.today().strftime("%d.%m.%Y")
    access = prompts.CHAT_ACCESS_WEB if web else prompts.CHAT_ACCESS_NO_WEB
    access += prompts.CHAT_FILES_ATTACHED if attached else prompts.CHAT_FILES_NONE
    p = prompts.fill(prompts.CHAT_SYSTEM, female=FEMALE, today=today, access=access)
    p += PROFILE_PROVIDER()
    if files:
        p += filesmod.prompt_section()
    if voice:
        p += prompts.CHAT_VOICE
    p += prompts.forbidden_block(prompts.FORBIDDEN_ALL, prompts.FORBIDDEN_CHAT)
    return p


def parse_voice(text: str) -> tuple[str, str]:
    """→ (что произнести, что показать в окне). Если модель не соблюла формат — берём начало ответа."""
    m = re.match(r"\s*ГОЛОС\s*:\s*(.+?)(?:\n\s*\n|\n|$)(.*)", text, re.S | re.I)
    if m and m.group(1).strip():
        return m.group(1).strip(), (m.group(2).strip() or m.group(1).strip())
    clean = re.sub(r"[#*`>_\[\]]", "", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    spoken = " ".join(sentences[:2])[:300].strip()
    return spoken or "Готово.", text.strip()


class ChatStore:
    """Диалоги в папке data/chats, по файлу на диалог."""

    def __init__(self, folder: Path | None = None):
        self.dir = folder or CHATS_DIR
        self._lock = threading.RLock()

    def _path(self, cid: str) -> Path:
        return self.dir / (re.sub(r"[^0-9a-zA-Z_-]", "", cid) + ".json")

    def new(self) -> dict:
        c = {"id": uuid.uuid4().hex[:12], "title": "Новый чат", "created": time.time(), "updated": time.time(), "messages": []}
        self.save(c)
        return c

    def get(self, cid: str) -> dict | None:
        try:
            return json.loads(self._path(cid).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, c: dict):
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            p = self._path(c["id"])
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(p)

    def delete(self, cid: str) -> bool:
        try:
            self._path(cid).unlink()
            return True
        except OSError:
            return False

    def list(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json") if self.dir.exists() else []:
            try:
                c = json.loads(p.read_text("utf-8"))
                out.append({"id": c["id"], "title": c.get("title", ""), "updated": c.get("updated", 0), "count": len(c.get("messages", []))})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(out, key=lambda x: -x["updated"])


def fit_history(messages: list[dict], budget_chars: int) -> list[dict]:
    """Самые свежие сообщения, которые помещаются в бюджет; обрезка идёт с начала, первое сообщение — от пользователя."""
    keep, used = [], 0
    for m in reversed(messages):
        n = len(m["content"]) + 20
        if keep and used + n > budget_chars:
            break
        keep.append(m)
        used += n
    keep.reverse()
    while keep and keep[0]["role"] != "user":
        keep.pop(0)
    return keep


def codermod_light(paths: list[str]) -> bool:
    return light_attachments(paths)


class _NoLocalCfg:
    """Настройки без встроенного генератора: запасной путь рисует свой сервер или Pollinations, а не те же модели ещё раз."""

    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, key, default=None):
        v = self._cfg.get(key, default)
        if key == "images":
            v = dict(v or {})
            v["provider"] = "sd" if v.get("url") else "pollinations"
        return v


class ChatService:
    def __init__(self, cfg, engine, emit, speak=None, store: ChatStore | None = None, brain: Brain | None = None,
                 files: filesmod.FileService | None = None, coder=None):
        self.cfg, self.engine, self.emit, self.speak = cfg, engine, emit, speak
        self.coder = coder
        self.store = store or ChatStore()
        self.brain = brain or Brain(cfg, engine)
        self.files = files or filesmod.FileService()
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self.current: str | None = None

    # ---------- диалоги ----------
    def voice_chat_id(self) -> str:
        """Голосовые вопросы продолжают недавний диалог (в пределах получаса), иначе начинается новый."""
        lst = self.store.list()
        if lst and time.time() - lst[0]["updated"] < 1800:
            return lst[0]["id"]
        return self.store.new()["id"]

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def stop(self):
        self._stop.set()
        if self.coder:
            self.coder.stop()

    # ---------- ответ ----------
    def send(self, cid: str | None, text: str, voice: bool = False, wait: bool = False, search: bool | None = None,
             paths: list[str] | None = None, mode: str = "auto") -> str | None:
        """Запускает ответ в фоне. Возвращает id диалога или None, если уже идёт другой ответ.
        search: True — искать в интернете, False — не искать, None — по настройке («Авто»: когда просьба этого требует).
        paths — приложенные файлы и папки; mode: "auto" — сама решу, "code" — всегда кодер, "ask" — только ответ в чате."""
        text = (text or "").strip()
        if not text:
            return cid
        if self.busy:
            return None
        chat = (cid and self.store.get(cid)) or self.store.new()
        paths = [p for p in (paths or []) if isinstance(p, str) and p][:200]
        mode = mode if mode in MODES else "auto"
        t = threading.Thread(target=self._run, args=(chat, text, voice, search, paths, mode), daemon=True, name="karina-chat")
        t.start()
        if wait:
            t.join()
        return chat["id"]

    @staticmethod
    def _api_content(m: dict) -> str:
        """Текст сообщения для модели: к нему добавляются приложенные файлы и пометки о созданных файлах и проектах."""
        c = m["text"]
        if m.get("attach"):
            c += "\n\n[Приложенные файлы]\n" + "\n\n".join(f"=== {a['name']} ===\n{a['text']}" for a in m["attach"])
        elif m.get("files_in"):
            c += f"\n[Приложены файлы: {', '.join(m['files_in'])}]"
        if m.get("files"):
            c += f"\n[Создан файл: {', '.join(f['name'] for f in m['files'])}]"
        if m.get("project"):
            c += f"\n[Кодер работал над проектом «{m['project']['name']}»]"
        return c

    # ---------- кодер внутри диалога ----------
    def _code_turn(self, chat: dict, reply: dict, text: str, paths: list[str], step) -> tuple[str, str, list[dict]]:
        """Кодер ведёт задачу прямо в этом диалоге. Ход работы — лента действий (что сказала, что записала, что запустила и что получилось),
        она приходит в окно событиями chat_activity и сохраняется вместе с ответом; итог приходит текстом ответа.
        → (текст ответа, ошибка, лента действий)."""
        cid = chat["id"]
        log: list[dict] = []
        done: dict = {}

        def push(entry: dict):
            log.append(entry)
            self.emit({"type": "chat_activity", "chat": cid, "id": reply["id"], "op": "add", "index": len(log) - 1, "entry": entry})

        def update(i: int, **kw):
            log[i].update(kw)
            self.emit({"type": "chat_activity", "chat": cid, "id": reply["id"], "op": "update", "index": i, "entry": log[i]})

        think = [None]                  # номер «живой» записи «думаю…» в ленте

        def finish_think():
            if think[0] is not None:
                update(think[0], live=False)
                think[0] = None

        def sink(ev: dict):
            if ev.get("type") == "coder_done":
                finish_think()
                done.update(ev)
                return
            k, t = ev.get("kind"), ev.get("text", "")
            if k == "think":
                finish_think()
                think[0] = len(log)
                push({"k": "think", "text": t, "live": True, "say": "", "doing": ""})
                step(t)
                return
            if k == "stream":
                if think[0] is not None:
                    update(think[0], say=ev.get("say", ""), doing=ev.get("doing", ""))
                return
            if k in ("say", "write", "read", "find", "run", "result", "warn"):
                finish_think()
            if k == "say":
                push({"k": "say", "text": t[:1500]})
            elif k == "write":
                push({"k": "write", "text": t, "path": ev.get("path", ""), "lines": ev.get("lines", 0), "preview": ev.get("preview", "")[:1500]})
            elif k == "read":
                push({"k": "read", "text": t, "path": ev.get("path", "")})
            elif k == "find":
                push({"k": "find", "text": t, "output": ev.get("output", "")[:1500]})
            elif k == "run":
                push({"k": "run", "text": t, "cmd": ev.get("cmd", ""), "ok": None})
            elif k == "result":
                i = next((j for j in range(len(log) - 1, -1, -1) if log[j]["k"] == "run" and log[j].get("ok") is None), None)
                if i is not None:
                    update(i, ok=bool(ev.get("ok")), code=ev.get("code"), output=(ev.get("output") or "")[-1500:])
                else:
                    push({"k": "run", "text": t, "ok": bool(ev.get("ok")), "output": (ev.get("output") or "")[-1500:]})
            elif k == "warn":
                push({"k": "warn", "text": t})
            elif k == "start":
                push({"k": "start", "text": t})
            if k in ("step", "start", "run"):
                step(t)

        ctx = "\n".join(f"{'Пользователь' if m['role'] == 'user' else 'Карина'}: {m['text'][:400]}"
                        for m in chat["messages"][:-2] if m.get("text"))[-2500:]
        r = self.coder.start(text, paths, wait=True, sink=sink, project=chat.get("project", ""), context=ctx)
        if not r.get("ok"):
            return "⚠ " + r.get("error", "Кодер не смог начать."), r.get("error", "Кодер не смог начать."), log
        reply["brain"] = r.get("brain", "")
        if not done:
            return "⚠ Кодер завершился без результата.", "Кодер завершился без результата.", log
        err = done.get("error", "")
        name = done.get("project", r.get("project", ""))
        icon = "✅" if done.get("ok") else ("⚠" if err or "не прошла" in done.get("verdict", "") else "ℹ")
        parts = [f"**{icon} {err or done.get('verdict', '')}**"]
        if done.get("summary"):
            parts.append(done["summary"])
        files = done.get("files") or []
        if done.get("zip"):
            parts.append(f"📦 Всё одним архивом: `{done['zip']}` (рядом с папкой проекта).")
        parts.append("Можно писать дальше в этом же чате: «добавь…», «исправь…» — я продолжу в этом проекте." if files else "")
        reply["project"] = {"name": name, "zip": done.get("zip", ""), "ok": bool(done.get("ok")), "files": files[:60]}
        return "\n\n".join(x for x in parts if x), err, log

    # ---------- картинки ----------
    def _image_turn(self, chat: dict, reply: dict, text: str, step, count: int | None = None) -> tuple[str, str, list[dict]]:
        """Рисует картинку. Если скачаны встроенные картиночные модели (NoobAI, Pony, FLUX.2 Klein …) — работает «режиссёр» (_image_set_turn):
        текстовая модель пишет описание под каждую из них и несколько вариантов рисуются в стилях разных моделей.
        Иначе (или если выбран внешний сервис) — прежний путь: одна картинка. → (текст ответа, ошибка, созданные файлы)."""
        ids = imgdirector.available(self.cfg)
        if ids:
            return self._image_set_turn(chat, reply, text, step, ids, count)
        return self._image_single_turn(chat, reply, text, step)

    def _image_set_turn(self, chat: dict, reply: dict, text: str, step, ids: list[str], count: int | None = None) -> tuple[str, str, list[dict]]:
        """Режиссёр картинок. Текстовая модель (например, Qwen 3.5 9B) пишет описание — тегами и предложениями — и называет лучшего художника;
        дальше несколько вариантов рисуют разные модели (по одному варианту на каждую, лучшие первыми), каждый появляется в чате, как только готов.
        Правка («поверни её», «на втором варианте …», «во всех моделях …») идёт к подходящей модели или ко всем сразу.
        → (текст ответа, ошибка, созданные файлы)."""
        cid, rid = chat["id"], reply["id"]
        prev = next((m["image_set"] for m in reversed(chat["messages"][:-1]) if m.get("image_set")), None)
        intent = imgdirector.parse_intent(text, ids, prev is not None, imgdirector.settings(self.cfg)["variants"])
        edit = prev is not None and intent.kind in ("edit", "more")
        if not edit:
            intent.kind = "new"
        if count and not edit:                                   # число выбрано в ответ на вопрос «сколько вариантов?»
            intent.count, intent.explicit_count = max(1, min(imgdirector.MAX_VARIANTS, count)), True
        style_text = imgdirector.style_text_for(prev, text, edit)
        notes: list[str] = []
        if intent.refs and edit and not any(v.get("n") in intent.refs for v in prev.get("variants", [])):
            notes.append(f"ⓘ Варианта {intent.refs[0]} в последнем наборе нет — правлю общее описание.")
            intent.refs = []

        # 1. описание от текстовой модели («ещё вариант» берёт прежнее описание, модель не нужна)
        prev_brief = (prev or {}).get("brief") if edit else None
        if intent.kind == "more" and prev_brief:
            brief = dict(prev_brief, best="", why="")
        else:
            step("Продумываю картинку и выбираю, какая модель будет рисовать…")
            try:
                choice = self.brain.choose(text)
                reply["brain"] = choice.label
                raw = choice.backend.chat(imgdirector.brief_request(text, ids, prev_brief, edit), max_tokens=imgdirector.BRIEF_TOKENS, temperature=0.6, stop=self._stop)
                brief = imgdirector.parse_brief(raw, text, ids)
            except (BrainUnavailable, EngineError) as e:
                brief = dict(prev_brief, best="", why="") if prev_brief else imgdirector.fallback_brief(text)
                notes.append(f"ⓘ Модель сейчас не отвечает ({e}) — " + ("рисую с прежним описанием, правку применить не смогла." if prev_brief else "рисую по вашему тексту как есть, результат может быть хуже."))
            if imagesmod.has_cyrillic(brief["tags"]) and imagesmod.ANIME.search(norm(style_text)):
                fb = imgdirector.fallback_brief(text if not edit else style_text)       # слабая модель не справилась — собираем теги сами
                brief["tags"] = fb["tags"]
        if self._stop.is_set():
            return "*(остановлено)*", "", []

        # 2. кто рисует: подходящие модели по порядку (первой — та, что назвала текстовая модель), варианты раздаются по кругу
        pool, pool_note = imgdirector.pool_for(style_text, ids)
        order = imgdirector.rank(text, pool, style_text)
        llm_first = brief.get("best") in pool
        if llm_first:
            order = [brief["best"]] + [m for m in order if m != brief["best"]]
        pl = imgpower.plan(self.cfg)
        order, fast_note = imgpower.prefer_fast(order, pl, localimg.by_id)           # слабая видеокарта: сначала быстрая модель
        rng = random.Random()
        reason = ""
        if intent.kind == "edit":
            variants, reason = imgdirector.plan_edit(intent, order, prev, rng)
            if not reason:
                reason = brief.get("why") if llm_first and brief.get("why") else imgdirector.artist(variants[0].model)["why"]
        elif intent.kind == "more":
            old = [v["model"] for v in prev.get("variants", []) if v.get("model") in ids] or order
            n = intent.count if intent.explicit_count else len(old)
            variants = [imgdirector.Variant(i + 1, old[i % len(old)], rng.randint(1, 2**31 - 1)) for i in range(n)]
        else:
            variants = imgdirector.plan_new(intent, order, rng)
            if pool_note:
                notes.append("ⓘ " + pool_note)
            if fast_note:
                notes.append("ⓘ " + fast_note)
            elif pl["auto"] and pl["mode"] in ("low", "cpu") and not any(imgpower.is_fast(localimg.by_id(m)) for m in ids):
                notes.append("ⓘ Видеопамяти на этом ПК мало, и модели SDXL (Pony, NoobAI) рисуют долго. FLUX.2 Klein рисует в несколько раз быстрее — "
                             "«Настройки → Модели → Картинки → FLUX.2 Klein → Скачать».")
        miss = imgdirector.missing_models(text, ids) if intent.kind != "more" else []
        if miss:
            notes.append("ⓘ Модель " + ", ".join(imgdirector.title(m) for m in miss) + " не скачана («Настройки → Модели → Картинки») — рисуют скачанные.")
        size = tuple(prev["size"]) if edit and prev.get("size") else imagesmod.pick_size(style_text)
        for v in variants:
            v.prompt = imgdirector.prompt_for(v.model, brief, style_text)
            v.negative = imgdirector.negative_for(v.model, style_text)
        total = len(variants)
        self.emit({"type": "chat_image_plan", "chat": cid, "id": rid, "items": [{"n": v.n, "label": imgdirector.title(v.model)} for v in variants]})

        # 3. рисуем по очереди (одна видеокарта): каждый готовый вариант сразу уходит в окно
        stem = (prev or {}).get("stem") if edit else imagesmod.file_stem(text)
        stem = stem or imagesmod.file_stem(text)
        made: list[dict] = []
        done: list[dict] = []
        errors: list[str] = []
        self._free_vram(step, pl)
        timing: list[str] = []
        for v in variants:
            if self._stop.is_set():
                break
            label = imgdirector.title(v.model)
            vpl = imagesmod.local_plan(self.cfg, v.model)
            step(f"Рисую вариант {v.n} из {total}: {label}… ({imgpower.eta_text(vpl, localimg.by_id(v.model))})")
            self.emit({"type": "chat_image", "chat": cid, "id": rid, "n": v.n, "status": "start", "label": label})
            try:
                data, ext = localimg.generate(v.prompt, size, v.seed, v.model, v.negative, self._stop, plan=vpl, progress=self._progress(cid, rid, v.n, total, label))
                f = self.files.create_bytes(f"{stem} - {v.n} {v.model}.{ext}", data)
            except Exception as e:           # не вышло у одной модели — остальные всё равно рисуем
                if self._stop.is_set() or str(e) == "остановлено":
                    break
                msg = str(e) or e.__class__.__name__
                errors.append(f"вариант {v.n} ({label}): {msg}")
                self.emit({"type": "chat_image", "chat": cid, "id": rid, "n": v.n, "status": "fail", "label": label, "error": msg})
                continue
            f.update(variant=v.n, model=v.model, label=label)
            made.append(f)
            done.append({"n": v.n, "model": v.model, "title": label, "seed": v.seed, "prompt": v.prompt, "file": f["name"]})
            timing.append(self._timing(v.n, vpl))
            self.emit({"type": "chat_image", "chat": cid, "id": rid, "n": v.n, "status": "done", "label": label, "file": f})

        # 4. ни одна встроенная модель не нарисовала: запасной путь (свой сервер / Pollinations), как раньше
        if not made and not self._stop.is_set() and imagesmod.settings(self.cfg)["provider"] == "auto" and variants:
            step("Встроенные модели не справились — пробую запасной генератор…")
            try:
                data, ext, who = imagesmod.generate(_NoLocalCfg(self.cfg), variants[0].prompt, size, stop=self._stop, request=style_text)
                f = self.files.create_bytes(f"{stem} - 1.{ext}", data)
                f.update(variant=1, model="", label=who)
                made.append(f)
                done.append({"n": 1, "model": "", "title": who, "seed": 0, "prompt": variants[0].prompt, "file": f["name"]})
                notes.append("ⓘ Встроенные модели не смогли нарисовать (" + "; ".join(errors)[:300] + f") — картинку нарисовал запасной генератор: {who}.")
                errors = []
                self.emit({"type": "chat_image", "chat": cid, "id": rid, "n": 1, "status": "done", "label": who, "file": f})
            except imagesmod.ImageError:
                pass
        if self._stop.is_set() and not made:
            return "*(остановлено)*", "", []
        if not made:
            why = "; ".join(errors) or "нет ответа от генератора"
            return ("".join(n + "\n\n" for n in notes) + f"⚠ Не получилось нарисовать: {why}"), why, []

        reply["image_set"] = {"request": style_text, "stem": stem, "size": list(size), "variants": done,
                              "brief": {"tags": brief["tags"], "scene": brief["scene"]}}
        reply["image_prompt"] = done[0]["prompt"]
        who = ", ".join(f"{d['n']} — {d['title']}" for d in done)
        if intent.kind == "edit" and len(done) == 1:
            lead = f"**Готово — поправила.** Рисовала **{done[0]['title']}**" + (f": {reason}." if reason else ".")
        elif intent.kind == "edit":
            lead = f"**Готово — поправила в {len(done)} моделях:** {who}." + (f" Почему так: {reason}." if reason else "")
        else:
            lead = f"**Готово — {len(done)} {plural(len(done), ('вариант', 'варианта', 'вариантов'))}** (каждый в стиле своей модели): {who}." if len(done) > 1 else f"**Готово — нарисовала.** Рисовала **{done[0]['title']}**."
        reply["brain"] = ((reply.get("brain") or "") + " · " if reply.get("brain") else "") + "рисуют: " + ", ".join(dict.fromkeys(d["title"] for d in done))
        out = ["".join(n + "\n\n" for n in notes) + lead,
               f"**Описание тегами:** {brief['tags'][:420]}\n**Описание текстом:** {brief['scene'][:420]}"]
        if timing:
            out.append("⏱ " + "; ".join(timing))
        if errors:
            out.append("⚠ Не получились: " + "; ".join(errors))
        if self._stop.is_set():
            out.append("*(остановлено)*")
        out.append("Картинки сохранены в папке «Документы\\Карина». Можно попросить: «поверни её вполоборота», «на втором варианте сделай волосы длиннее», "
                   "«пусть она машет рукой» (выберу подходящую модель), «во всех моделях — ночью» или «ещё варианты».")
        return "\n\n".join(out), "", made

    def _free_vram(self, step, plan: dict) -> None:
        """Текстовая модель (например, Qwen 9B) лежит в видеопамяти и отнимает её у художника: на 3–8 ГБ карте он тогда не помещается и падает
        в медленный режим. Выгружаем её на время рисования; следующий ответ в чате загрузит её сама (несколько секунд)."""
        eng = self.engine
        if not plan.get("unload_llm") or getattr(eng, "flavor", "") != "vulkan" or getattr(eng, "state", "") not in ("ready", "starting"):
            return
        step("Освобождаю видеопамять: текстовая модель на время рисования выгружается…")
        try:
            eng.stop()
        except Exception:
            pass

    def _progress(self, cid: str, rid: str, n: int, total: int, label: str):
        """Обратный вызов для localimg: шаг N из M и сколько осталось (плитка варианта + строка состояния). Не чаще раза в секунду."""
        last = [0.0]

        def prog(i: int, steps: int, per: float):
            now = time.time()
            if i < steps and now - last[0] < 1.0:
                return
            last[0] = now
            eta = max(0, int((steps - i) * per)) if per else 0
            self.emit({"type": "chat_image", "chat": cid, "id": rid, "n": n, "status": "progress", "label": label, "step": i, "steps": steps, "eta": eta})
            self.emit({"type": "chat_step", "chat": cid, "text": f"Вариант {n} из {total}: {label} — шаг {i} из {steps}" + (f", осталось около {imgpower.fmt_sec(eta)}" if eta else "")})
        return prog

    @staticmethod
    def _timing(n: int, plan: dict) -> str:
        r = localimg.LAST_RUN
        if not r:
            return f"вариант {n}"
        how = "на процессоре" if r.get("flavor") == "cpu" else f"{imgpower.MODE_LABEL.get(plan['mode'], plan['mode'])}"
        slip = " — видеопамяти не хватило, рисовала процессором" if r.get("flavor") == "cpu" and plan["mode"] != "cpu" else ""
        return f"вариант {n} — {imgpower.fmt_sec(r.get('sec', 0))} ({how}, {r.get('w')}×{r.get('h')}, {r.get('steps')} шагов){slip}"

    def _image_single_turn(self, chat: dict, reply: dict, text: str, step) -> tuple[str, str, list[dict]]:
        """Прежний путь: модель пишет описание, один генератор (встроенный, свой сервер или Pollinations) рисует одну картинку."""
        prev = next((m.get("image_prompt", "") for m in reversed(chat["messages"][:-1]) if m.get("image_prompt")), "")
        prompt, note = text.strip(), ""
        step("Придумываю описание картинки…")
        try:
            choice = self.brain.choose(text)
            reply["brain"] = choice.label
            raw = choice.backend.chat(imagesmod.prompt_request(text, prev if imagesmod.is_follow_up(text) else ""), max_tokens=260, temperature=0.7, stop=self._stop)
            prompt = imagesmod.clean_prompt(raw, text)
        except (BrainUnavailable, EngineError) as e:
            note = f"ⓘ Модель сейчас не отвечает ({e}) — рисую по вашему тексту как есть, результат может быть хуже.\n\n"
        if imagesmod.has_cyrillic(prompt) and imagesmod.ANIME.search(norm(text)):
            prompt = imagesmod.ru_prompt(text)                      # слабая модель не справилась — собираем теги сами
        prompt = imagesmod.finish_prompt(imagesmod.style_prompt(prompt, text), text)
        if self._stop.is_set():
            return "*(остановлено)*", "", []
        spl = imagesmod.local_plan(self.cfg, imagesmod.settings(self.cfg)["local_model"])
        if localimg.ready() and imagesmod.chain(self.cfg, text)[0] == "local":
            self._free_vram(step, spl)
            step(f"Рисую картинку… ({imgpower.eta_text(spl, localimg.pick_installed(imagesmod.settings(self.cfg)['local_model']))})")
        else:
            step("Рисую картинку…")
        try:
            data, ext, who = imagesmod.generate(self.cfg, prompt, imagesmod.pick_size(text), stop=self._stop, request=text,
                                                progress=self._progress(chat["id"], reply["id"], 1, 1, "картинка"))
        except imagesmod.ImageError as e:
            if self._stop.is_set():
                return "*(остановлено)*", "", []
            return (note + f"⚠ Не получилось нарисовать: {e}"), str(e), []
        made = self.files.create_bytes(imagesmod.file_stem(text) + "." + ext, data)
        reply["image_prompt"] = prompt
        reply["brain"] = (reply.get("brain") or "") + f" · рисует: {who}" if reply.get("brain") else f"рисует: {who}"
        return (note + f"**Готово — нарисовала.** Картинка сохранена в папке «Документы\\Карина» (`{made['name']}`).\n\n"
                f"**Описание для генератора:** {prompt}\n\nМожно попросить изменить: «сделай волосы длиннее», «ещё вариант», «в стиле акварели» — я составлю новое описание."), "", [made]

    def _history_budget(self, max_tokens: int = MAX_TOKENS, claude: bool = False, ctx: int = 0) -> int:
        ctx = ctx or (CLAUDE_CTX if claude else int((self.cfg.get("models", {}) or {}).get("ctx") or 0))
        if not ctx:
            from . import models
            m = models.by_id((self.cfg.get("models", {}) or {}).get("active", ""))
            ctx = int(m["ctx"]) if m else 8192
        return int(max(1000, ctx - max_tokens - 600) * CHARS_PER_TOKEN)

    def _search_cfg(self) -> dict:
        c = self.cfg.get("search", {}) or {}
        mode = c.get("mode") if c.get("mode") in ("auto", "always", "off") else "auto"
        depth = c.get("depth") if c.get("depth") in research.DEPTH else research.DEFAULT_DEPTH
        return {"mode": mode, "depth": depth}

    def _use_search(self, text: str, override: bool | None) -> bool:
        if override is not None:
            return override
        mode = self._search_cfg()["mode"]
        return mode == "always" or (mode == "auto" and research.wants_search(norm(text)))

    def will_search(self, text: str) -> bool:
        return self._use_search(text, None) and net.is_online()

    def _choose(self, text: str, search: bool | None = None, hard: bool = False):
        """Какой «мозг» ответит. → (выбор, нужны ли файлы, искать ли в интернете). Бросает BrainUnavailable, если никто не может."""
        want_files = filesmod.wants_file(norm(text))
        use_search = self._use_search(text, search)
        deep = use_search and self._search_cfg()["depth"] in ("deep", "max")
        return self.brain.choose(text, deep_search=deep or hard, want_files=want_files), want_files, use_search

    def preflight(self, text: str) -> str:
        """Пусто, если ответить можно; иначе причина по-русски (чтобы сказать о ней сразу)."""
        try:
            self._choose(text)
        except BrainUnavailable as e:
            return str(e)
        return ""

    def _run(self, chat: dict, text: str, voice: bool, search: bool | None = None, paths: list[str] | None = None, mode: str = "auto"):
        if not self._busy.acquire(blocking=False):
            return
        cid = chat["id"]
        self._stop.clear()
        self.current = cid
        now = time.time()
        paths = paths or []
        umsg = {"id": uuid.uuid4().hex[:8], "role": "user", "text": text, "ts": now, "voice": voice}
        if paths:
            umsg["files_in"] = [Path(x).name for x in paths][:20]
        chat["messages"].append(umsg)
        last_img = any(m.get("image_prompt") for m in chat["messages"][-3:-1])
        recent = " ".join(m["text"][:400] for m in chat["messages"][-5:-1] if m.get("text"))
        way = route(text, bool(paths), bool(chat.get("project")) and self.coder is not None, mode, voice, codermod_light(paths), last_img, recent)
        if way == "code" and self.coder is None:
            way = "chat"
        # «Сколько вариантов?»: просьба нарисовать ждёт ответа; ответ-число возвращает её в работу, любое другое сообщение отменяет ожидание
        img_text, img_count, ask_count = text, None, False
        pend = chat.pop("pending_image", None)
        if pend and not paths:
            n = imgdirector.parse_answer(text)
            if n:
                img_text, img_count, way = str(pend.get("request") or text), n, "image"
        elif way == "image" and not paths:
            prev_set = any(m.get("image_set") for m in chat["messages"][:-1])
            ask_count = imgdirector.needs_count_question(self.cfg, text, prev_set)
        if chat["title"] == "Новый чат":
            chat["title"] = (text[:48] + "…") if len(text) > 48 else text
        reply = {"id": uuid.uuid4().hex[:8], "role": "assistant", "text": "", "ts": now}
        self.emit({"type": "chat_start", "chat": cid, "user": chat["messages"][-1], "reply_id": reply["id"], "title": chat["title"]})
        err, spoken = "", ""
        steps: list[str] = []

        def step(t: str):
            if not steps or steps[-1] != t:
                steps.append(t)
            self.emit({"type": "chat_step", "chat": cid, "text": t})

        made: list[dict] = []
        ferrs: list[str] = []
        try:
            if way == "image" and ask_count:
                chat["pending_image"] = {"request": text}
                reply["text"] = QUESTION_COUNT
                reply["choices"] = [str(i) for i in range(1, imgdirector.ASK_MAX + 1)]
                spoken = QUESTION_COUNT
            elif way == "image":
                out_text, err_img, made_img = self._image_turn(chat, reply, img_text, step, img_count)
                reply["text"] = out_text
                if made_img:
                    made = made_img
                    reply["files"] = made_img
                if err_img:
                    err = err_img
                spoken = ("Готово, нарисовала несколько вариантов, смотрите в окне чата." if len(made_img) > 1
                          else "Готово, картинка сохранена в папке Карина в документах.") if made_img else ""
            elif way == "code":
                out_text, err_code, activity = self._code_turn(chat, reply, text, paths, step)
                reply["text"] = out_text
                activity = [a for a in activity if a.get("k") != "think"]          # «думаю…» нужно только пока идёт работа
                if activity:
                    reply["activity"] = activity[-80:]
                if err_code:
                    err = err_code
            else:
                note_ = ""
                choice, want_files, use_search = self._choose(text, search, hard=bool(paths))     # недоступный мозг — сразу понятная ошибка
                backend, is_claude = choice.backend, choice.name == "claude"
                reply["brain"] = choice.label
                max_tokens = LIMITS[choice.name][1 if (want_files or paths) else 0]
                if backend.state != "ready":
                    step("Загружаю модель в память…")
                budget = self._history_budget(max_tokens, is_claude, backend.ctx_tokens if choice.name == "cloud" else 0)
                if paths:                         # ответ «только прочитать»: тексты приложенных файлов идут в это сообщение
                    step("Изучаю приложенные файлы…")
                    texts, notes = codermod.read_attachments(paths, min(ATTACH_CHARS[choice.name], int(budget * 0.7)))
                    umsg["attach"] = texts
                    if notes:
                        umsg["attach_notes"] = notes
                        note_ += "ⓘ " + "\nⓘ ".join(notes) + "\n\n"
                api_msgs = [{"role": m["role"] if m["role"] == "user" else "assistant", "content": self._api_content(m)}
                            for m in chat["messages"] if m["text"]]
                hist = fit_history(api_msgs, budget)
                last = [0.0]

                def on_delta(t: str):
                    if not last[0]:
                        step("Пишу ответ…")
                        last[0] = 1e-9
                    if time.time() - last[0] > 0.12:
                        last[0] = time.time()
                        self.emit({"type": "chat_delta", "chat": cid, "id": reply["id"], "text": filesmod.display_text(t) if want_files else t})

                note, sources, out = note_ + (("ⓘ " + choice.note + "\n\n") if choice.note else ""), [], ""
                if choice.note:
                    step(choice.note)
                if use_search:
                    if not net.is_online():
                        note += "⚠ Нет интернета, поэтому поиск не выполнен. Отвечаю из собственных знаний — возможны неточности и устаревшие данные.\n\n"
                        step("Нет интернета — отвечаю без поиска")
                    else:
                        sc = self._search_cfg()
                        rs = research.Researcher(backend, step, self._stop, sc["depth"], budget, system=system_prompt(voice, web=True, files=want_files, attached=bool(paths)))
                        try:
                            ctx_q = "; ".join(m["content"][:120] for m in api_msgs[:-1] if m["role"] == "user")[-400:]
                            rs.collect(text, ctx_q)
                            out = rs.answer(text, fit_history(api_msgs, int(budget * 0.25)), on_delta, max_tokens)
                            sources = rs.res.sources
                        except websearch.Cancelled:
                            self._stop.set()
                        except websearch.SearchError as e:
                            note += f"⚠ Не удалось найти информацию в интернете ({e}). Отвечаю из собственных знаний — возможны неточности.\n\n"
                            step("Поиск не удался — отвечаю без него")
                if not out and not self._stop.is_set():
                    messages = [{"role": "system", "content": system_prompt(voice, files=want_files, attached=bool(paths))}] + hist
                    out = backend.chat(messages, max_tokens=max_tokens, on_delta=on_delta, stop=self._stop)
                if choice.name == "cloud" and getattr(backend, "last_label", ""):
                    reply["brain"] = backend.last_label            # какой именно провайдер ответил (после перехода при лимите это может быть не первый)
                if want_files and out:
                    out, reqs = filesmod.extract(out)                 # файл создаёт Карина, и только если ответ не остановлен
                    if reqs and not self._stop.is_set():
                        step("Создаю файлы…")
                        made, ferrs = self.files.create_all(reqs)
                if voice and out:
                    spoken, out = parse_voice(out)
                if sources and out:
                    out = research.format_sources(out, sources)
                if self._stop.is_set():
                    out = (out + "\n\n*(остановлено)*").strip()
                if made and not out:
                    out = "Готово — файл сохранён в папке «Документы\\Карина»." if len(made) == 1 else f"Готово — файлов: {len(made)}, они в папке «Документы\\Карина»."
                if ferrs:
                    out = (out + "\n\n" if out else "") + "\n".join("⚠ " + e for e in ferrs)
                reply["text"] = (note + out) if out else "Модель вернула пустой ответ. Попробуйте переформулировать."
                if made:
                    reply["files"] = made
                if sources:
                    reply["sources"] = [{"n": x.n, "url": x.url, "title": x.title} for x in sources]
                if any(x.startswith(("Ищу", "Читаю")) for x in steps):
                    reply["steps"] = steps[-40:]          # «Ход работы» сохраняется вместе с ответом
        except EngineError as e:
            err = str(e)
            reply["text"] = "⚠ " + err
        except Exception as e:      # любая неожиданность не должна оставлять окно «в ожидании»
            err = f"{e.__class__.__name__}: {e}"
            reply["text"] = "⚠ Ошибка: " + err
        chat["messages"].append(reply)
        if reply.get("project"):
            chat["project"] = reply["project"]["name"]
        chat["updated"] = time.time()
        self.store.save(chat)
        self.emit({"type": "chat_done", "chat": cid, "id": reply["id"], "text": reply["text"], "error": err, "title": chat["title"],
                   "steps": reply.get("steps"), "files": reply.get("files"), "brain": reply.get("brain"), "project": reply.get("project"),
                   "activity": reply.get("activity"), "choices": reply.get("choices")})
        self._busy.release()
        self.current = None
        if voice and self.speak:
            if err:
                self.speak("Не получилось: " + err[:160])
            elif way == "image":
                self.speak(spoken if spoken else "Картинку нарисовать не получилось, подробности в окне чата.")
            elif made:           # о файле говорим только то, что действительно создано
                self.speak("Готово, файл сохранён в папке Карина в документах." if len(made) == 1 else "Готово, файлы сохранены в папке Карина в документах.")
            elif ferrs:
                self.speak("Не удалось создать файл. " + ferrs[0][:150])
            else:
                self.speak(spoken or "Готово, смотрите в окне чата.")
