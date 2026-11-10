"""Чат: диалоги с историей на диске, потоковый ответ модели, голосовой вызов («Карина, найди и напиши отчёт про…»)."""
from __future__ import annotations
import datetime, json, re, threading, time, uuid
from pathlib import Path
from . import net, research, websearch
from .config import DATA
from .engine import EngineError
from .textutil import norm

CHATS_DIR = DATA / "chats"
MAX_TOKENS = 1500
CHARS_PER_TOKEN = 2.0          # для русского с запасом: лучше обрезать историю раньше, чем переполнить контекст

# Фразы, которые сразу идут в чат (раньше обычных команд: «найди и напиши…» иначе стало бы поиском в браузере).
CHAT_PREFIX = re.compile(
    r"^(?:найди и (?:напиши|составь|подготовь|расскажи|собери)|найди информацию|напиши (?:мне )?(?:отчет|реферат|статью|эссе|пост|план|письмо|резюме|обзор|конспект|сочинение|доклад|инструкцию|краткое)|"
    r"составь|подготовь|расскажи|объясни|что такое|кто такой|кто такая|почему|зачем|как работает|в чем разница|сравни|посоветуй|придумай|переведи|"
    r"помоги (?:мне )?(?:с|со|написать|разобраться)|давай обсудим|вопрос|чат\b)")
OPEN_CHAT = re.compile(r"^(?:открой|покажи|включи)\s+(?:окно\s+)?чат\b")


def is_chat_request(text: str) -> bool:
    """text — нормализованная фраза без имени."""
    return bool(CHAT_PREFIX.search(text))


def wants_open_chat(text: str) -> bool:
    return bool(OPEN_CHAT.search(text))


def system_prompt(voice: bool = False, web: bool = False) -> str:
    today = datetime.date.today().strftime("%d.%m.%Y")
    access = ("Ты можешь пользоваться материалами из интернета, которые тебе дадут. " if web
              else "Сейчас у тебя нет доступа к интернету и файлам: не утверждай, что что-то нашла или открыла. ")
    p = ("Ты — Карина, помощник на компьютере пользователя. Отвечай по-русски (если не просят иначе), по делу и без воды. "
         f"Сегодня {today}. {access}"
         "Не выдумывай факты, цифры, имена и ссылки; если не уверена или не знаешь — скажи об этом прямо. "
         "Знания могут быть устаревшими, для свежих событий предупреждай об этом. "
         "Оформление: короткие абзацы, списки и **жирный** можно, код — в блоках ```.")
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
    def __init__(self, cfg, engine, emit, speak=None, store: ChatStore | None = None):
        self.cfg, self.engine, self.emit, self.speak = cfg, engine, emit, speak
        self.store = store or ChatStore()
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

    # ---------- ответ ----------
    def send(self, cid: str | None, text: str, voice: bool = False, wait: bool = False, search: bool | None = None) -> str | None:
        """Запускает ответ в фоне. Возвращает id диалога или None, если уже идёт другой ответ.
        search: True — искать в интернете, False — не искать, None — по настройке («Авто»: когда просьба этого требует)."""
        text = (text or "").strip()
        if not text:
            return cid
        if self.busy:
            return None
        chat = (cid and self.store.get(cid)) or self.store.new()
        t = threading.Thread(target=self._run, args=(chat, text, voice, search), daemon=True, name="karina-chat")
        t.start()
        if wait:
            t.join()
        return chat["id"]

    def _history_budget(self) -> int:
        ctx = int((self.cfg.get("models", {}) or {}).get("ctx") or 0)
        if not ctx:
            from . import models
            m = models.by_id((self.cfg.get("models", {}) or {}).get("active", ""))
            ctx = int(m["ctx"]) if m else 8192
        return int(max(1000, ctx - MAX_TOKENS - 600) * CHARS_PER_TOKEN)

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

    def _run(self, chat: dict, text: str, voice: bool, search: bool | None = None):
        if not self._busy.acquire(blocking=False):
            return
        cid = chat["id"]
        self._stop.clear()
        self.current = cid
        now = time.time()
        chat["messages"].append({"id": uuid.uuid4().hex[:8], "role": "user", "text": text, "ts": now, "voice": voice})
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

        try:
            if self.engine.state != "ready":
                step("Загружаю модель в память…")
            api_msgs = [{"role": m["role"] if m["role"] == "user" else "assistant", "content": m["text"]} for m in chat["messages"] if m["text"]]
            budget = self._history_budget()
            hist = fit_history(api_msgs, budget)
            last = [0.0]

            def on_delta(t: str):
                if not last[0]:
                    step("Пишу ответ…")
                    last[0] = 1e-9
                if time.time() - last[0] > 0.12:
                    last[0] = time.time()
                    self.emit({"type": "chat_delta", "chat": cid, "id": reply["id"], "text": t})

            note, sources, out = "", [], ""
            if self._use_search(text, search):
                if not net.is_online():
                    note = "⚠ Нет интернета, поэтому поиск не выполнен. Отвечаю из собственных знаний — возможны неточности и устаревшие данные.\n\n"
                    step("Нет интернета — отвечаю без поиска")
                else:
                    sc = self._search_cfg()
                    rs = research.Researcher(self.engine, step, self._stop, sc["depth"], budget, system=system_prompt(voice, web=True))
                    try:
                        ctx_q = "; ".join(m["content"][:120] for m in api_msgs[:-1] if m["role"] == "user")[-400:]
                        rs.collect(text, ctx_q)
                        out = rs.answer(text, fit_history(api_msgs, int(budget * 0.25)), on_delta, MAX_TOKENS)
                        sources = rs.res.sources
                    except websearch.Cancelled:
                        self._stop.set()
                    except websearch.SearchError as e:
                        note = f"⚠ Не удалось найти информацию в интернете ({e}). Отвечаю из собственных знаний — возможны неточности.\n\n"
                        step("Поиск не удался — отвечаю без него")
            if not out and not self._stop.is_set():
                messages = [{"role": "system", "content": system_prompt(voice)}] + hist
                out = self.engine.chat(messages, max_tokens=MAX_TOKENS, on_delta=on_delta, stop=self._stop)
            if voice and out:
                spoken, out = parse_voice(out)
            if sources and out:
                out = research.format_sources(out, sources)
            if self._stop.is_set():
                out = (out + "\n\n*(остановлено)*").strip()
            reply["text"] = (note + out) if out else "Модель вернула пустой ответ. Попробуйте переформулировать."
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
        chat["updated"] = time.time()
        self.store.save(chat)
        self.emit({"type": "chat_done", "chat": cid, "id": reply["id"], "text": reply["text"], "error": err, "title": chat["title"], "steps": reply.get("steps")})
        self._busy.release()
        self.current = None
        if voice and self.speak:
            self.speak("Не получилось: " + err[:160] if err else spoken or "Готово, смотрите в окне чата.")
