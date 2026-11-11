"""Чат: диалоги с историей на диске, потоковый ответ модели, голосовой вызов («Карина, найди и напиши отчёт про…»)."""
from __future__ import annotations
import datetime, json, re, threading, time, uuid
from pathlib import Path
from . import coder as codermod, files as filesmod, net, research, websearch
from .brain import Brain, BrainUnavailable
from .config import DATA
from .engine import EngineError
from .textutil import norm

CHATS_DIR = DATA / "chats"
MAX_TOKENS = 1500
LIMITS = {"local": (1500, 3500), "claude": (3000, 8000), "cloud": (1500, 3500)}     # (обычный ответ, ответ с файлом) в токенах
CLAUDE_CTX = 32768                                          # сколько истории и найденного отдаём Claude (токенов)
CHARS_PER_TOKEN = 2.0          # для русского с запасом: лучше обрезать историю раньше, чем переполнить контекст

ATTACH_CHARS = {"local": 20_000, "claude": 90_000, "cloud": 30_000}          # сколько текста приложенных файлов отдаём модели при ответе «только прочитать»
MODES = ("auto", "code", "ask")                              # авто / всегда кодер / только ответ в чате

# Просьба что-то СДЕЛАТЬ с кодом (нужен кодер) — в отличие от «посмотри», «объясни», «найди ошибку» (достаточно прочитать и ответить).
CODE_VERB = re.compile(
    r"\b(?:напиши|написать|создай|сделай|реализуй|разработай|исправь|почини|поправь|доработай|дополни|добавь|измени|переделай|перепиши|рефактор\w*|"
    r"оптимизируй|внеси|запусти|протестируй|покрой|обнови|удали|убери|замени|сгенерируй|допиши|доделай)\b")
CODE_HINT = re.compile(r"(?:\bкод|файл|функци|класс|тест|скрипт|ошибк|баг|програм|приложен|проект|модул|\.py\b|\.js\b|\bpython|\bnode)")
# новая программа без приложенных файлов: достаточно крупная, чтобы её вёл кодер
CODE_TARGET = re.compile(r"(?:програм|приложен|проект|\bбот\b|\bбота\b|парсер|утилит|\bсайт|\bигр[уы]|модул|библиотек|\bтест(?:ы|ами|ов)?\b|\bapi\b)")


def route(text: str, has_paths: bool, has_project: bool, mode: str = "auto", voice: bool = False) -> str:
    """Куда пойдёт сообщение: "code" — кодер пишет и проверяет код в папке проекта; "chat" — обычный ответ
    (приложенные файлы при этом просто читаются). Голосовые вопросы всегда идут в чат."""
    if voice or mode == "ask":
        return "chat"
    if mode == "code":
        return "code"
    t = norm(text)
    verb = bool(CODE_VERB.search(t))
    if has_paths:
        return "code" if verb else "chat"
    if verb and has_project and CODE_HINT.search(t):
        return "code"                                  # продолжение работы над проектом этого диалога
    return "code" if verb and CODE_TARGET.search(t) else "chat"


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


def system_prompt(voice: bool = False, web: bool = False, files: bool = False, attached: bool = False) -> str:
    today = datetime.date.today().strftime("%d.%m.%Y")
    access = ("Ты можешь пользоваться материалами из интернета, которые тебе дадут. " if web
              else "Сейчас у тебя нет доступа к интернету: не утверждай, что что-то нашла в сети. ")
    access += ("Пользователь приложил файлы: их содержимое дано в сообщении (раздел «Приложенные файлы»). Других файлов на компьютере ты не видишь и не меняешь; "
               "ты можешь только прочитать приложенное и ответить в чате (разобрать код, найти ошибки, объяснить, предложить правки текстом). "
               "Если нужно изменить файлы или написать проект — скажи, что для этого есть режим «Кодер» (кнопка «💻 Код»). "
               if attached else
               "Читать и открывать файлы на компьютере ты не можешь: не утверждай, что открыла или прочитала файл. ")
    p = ("Ты — Карина, помощник на компьютере пользователя. Отвечай по-русски (если не просят иначе), по делу и без воды. "
         f"Сегодня {today}. {access}"
         "Не выдумывай факты, цифры, имена и ссылки; если не уверена или не знаешь — скажи об этом прямо. "
         "Знания могут быть устаревшими, для свежих событий предупреждай об этом. "
         "Оформление: короткие абзацы, списки и **жирный** можно, код — в блоках ```.")
    if files:
        p += filesmod.prompt_section()
    if voice:
        p += ("\nЭто голосовой запрос. Первой строкой напиши «ГОЛОС: » и 1–2 коротких предложения, которые Карина произнесёт вслух "
              "(без списков, символов и ссылок). Затем пустая строка и подробный ответ.")
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
    def _code_turn(self, chat: dict, reply: dict, text: str, paths: list[str], step) -> tuple[str, str, list[str]]:
        """Кодер ведёт задачу прямо в этом диалоге: ход работы виден в сообщении, итог приходит ответом.
        → (текст ответа, ошибка, шаги для «Хода работы»)."""
        cid = chat["id"]
        log: list[dict] = []                    # {'text', 'output'?}
        done: dict = {}

        def show():
            md = []
            for e in log[-30:]:
                md.append("- " + e["text"].replace("\n", " "))
                if e.get("output"):
                    md.append("```\n" + e["output"].strip()[-500:] + "\n```")
            self.emit({"type": "chat_delta", "chat": cid, "id": reply["id"], "text": "🛠 **Работаю над задачей**\n\n" + "\n".join(md)})

        def sink(ev: dict):
            if ev.get("type") == "coder_done":
                done.update(ev)
                return
            k, t = ev.get("kind"), ev.get("text", "")
            if k == "say":
                log.append({"text": "💬 " + t[:400]})
            elif k == "write":
                log.append({"text": "✏ " + t})
            elif k == "read":
                log.append({"text": "📖 " + t})
            elif k == "run":
                log.append({"text": "▶ " + t})
            elif k == "result":
                log.append({"text": ("✔ " if ev.get("ok") else "✖ ") + t, "output": "" if ev.get("ok") else (ev.get("output") or "")})
            elif k == "warn":
                log.append({"text": "⚠ " + t})
            else:
                log.append({"text": t})
            if k in ("step", "start", "run"):
                step(t)
            show()

        ctx = "\n".join(f"{'Пользователь' if m['role'] == 'user' else 'Карина'}: {m['text'][:400]}"
                        for m in chat["messages"][:-2] if m.get("text"))[-2500:]
        r = self.coder.start(text, paths, wait=True, sink=sink, project=chat.get("project", ""), context=ctx)
        if not r.get("ok"):
            return "⚠ " + r.get("error", "Кодер не смог начать."), r.get("error", "Кодер не смог начать."), []
        reply["brain"] = r.get("brain", "")
        steps_out = [e["text"] for e in log]
        if not done:
            return "⚠ Кодер завершился без результата.", "Кодер завершился без результата.", steps_out
        err = done.get("error", "")
        name = done.get("project", r.get("project", ""))
        icon = "✅" if done.get("ok") else ("⚠" if err or "не прошла" in done.get("verdict", "") else "ℹ")
        parts = [f"**{icon} {err or done.get('verdict', '')}**"]
        if done.get("summary"):
            parts.append(done["summary"])
        files = done.get("files") or []
        if files:
            parts.append("**Файлы проекта** (`Документы\\Карина\\Проекты\\" + name + "`):\n" + "\n".join(f"- `{f['path']}` — {f['size']} Б" for f in files[:40]))
        if done.get("zip"):
            parts.append(f"📦 Всё одним архивом: `{done['zip']}` (рядом с папкой проекта).")
        parts.append("Можно писать дальше в этом же чате: «добавь…», «исправь…» — я продолжу в этом проекте." if files else "")
        reply["project"] = {"name": name, "zip": done.get("zip", ""), "ok": bool(done.get("ok"))}
        return "\n\n".join(x for x in parts if x), err, steps_out

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
        way = route(text, bool(paths), bool(chat.get("project")) and self.coder is not None, mode, voice) if self.coder else "chat"
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
            if way == "code":
                out_text, err_code, steps_code = self._code_turn(chat, reply, text, paths, step)
                reply["text"] = out_text
                if steps_code:
                    reply["steps"] = steps_code[-60:]
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
                   "steps": reply.get("steps"), "files": reply.get("files"), "brain": reply.get("brain"), "project": reply.get("project")})
        self._busy.release()
        self.current = None
        if voice and self.speak:
            if err:
                self.speak("Не получилось: " + err[:160])
            elif made:           # о файле говорим только то, что действительно создано
                self.speak("Готово, файл сохранён в папке Карина в документах." if len(made) == 1 else "Готово, файлы сохранены в папке Карина в документах.")
            elif ferrs:
                self.speak("Не удалось создать файл. " + ferrs[0][:150])
            else:
                self.speak(spoken or "Готово, смотрите в окне чата.")
