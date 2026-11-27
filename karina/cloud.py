"""Облачные модели: Google Gemini (бесплатный ключ из AI Studio), DeepSeek (платный API по очень низкой цене, свой ключ) и «свой сервер» (любой OpenAI-совместимый адрес).

У каждого человека свои ключи и свои лимиты. Провайдеры идут по очереди; когда у одного кончился лимит (ошибка 429),
Карина сразу переходит к следующему, а исчерпанный откладывает на время, которое назвал сервер. Интерфейс такой же, как у
встроенного движка и у Claude (chat / state), поэтому чат, поиск и «Кодер» работают с ним одинаково.

Ключ уходит только в заголовок запроса к выбранному провайдеру и не попадает в журнал и тексты ошибок (см. _scrub)."""
from __future__ import annotations
import json, re, threading, time, urllib.error, urllib.request
from . import cloudnet, prompts
from .engine import EngineError

CHARS_PER_TOKEN = 2.6          # русский текст: оценка токенов по числу символов (с запасом)
MAX_COOLDOWN = 24 * 3600
RETRY_DELAYS = (1.5, 4.0)      # паузы (с) перед повтором, когда сервер перегружен (503 и др. 5xx): обычно хватает пары секунд
MAX_ALT_MODELS = 2             # сколько запасных моделей того же провайдера пробовать после перегрузки
UA = "Bogestv0-KARINA"          # Cloudflare у части провайдеров режет стандартный User-Agent Python


class CloudError(EngineError):
    pass


# Пресеты. url — базовый адрес OpenAI-совместимого API; max_in — сколько токенов запроса безопасно отправлять (у бесплатных
# тарифов бывает жёсткий минутный лимит); max_out — потолок ответа; prefer — какую модель брать, если не выбрана вручную.
PRESETS: dict[str, dict] = {
    "gemini": {
        "name": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "", "max_in": 120000, "max_out": 4000,
        "signup": "https://aistudio.google.com/apikey", "prefer": [r"gemini-[\d.]+-flash$", r"flash"], "newest": True,   # модель не зашита: Google снимает старые («gemini-2.5-flash» → 404), берём самую новую flash из списка
        "note": "Длинный контекст: хорош для больших текстов и глубокого поиска. Лимиты видны в Google AI Studio. Вне ЕС, Великобритании и Швейцарии бесплатные запросы могут использоваться Google для обучения — личное не отправляйте.",
    },
    "deepseek": {
        "name": "DeepSeek", "url": "https://api.deepseek.com", "model": "", "max_in": 60000, "max_out": 8000,
        "signup": "https://platform.deepseek.com/api_keys", "prefer": [r"^deepseek-chat$", r"deepseek-chat", r"deepseek"],
        "note": "Сильная и очень недорогая модель (платный API: пополните баланс на platform.deepseek.com, обычно хватает нескольких долларов на месяцы). «deepseek-chat» — быстрые ответы, «deepseek-reasoner» — думает дольше, лучше для сложного. Серверы DeepSeek находятся в Китае: личное не отправляйте.",
    },
    "custom": {
        "name": "Свой сервер", "url": "", "model": "", "max_in": 16000, "max_out": 2000, "signup": "", "prefer": [],
        "note": "Любой сервис с OpenAI-совместимым API (адрес вида https://…/v1): другие бесплатные облака из списков на GitHub, Ollama или LM Studio в вашей сети.",
    },
}
DEFAULT_ORDER = ["gemini", "deepseek", "custom"]
STRATEGIES = ("failover", "spread")
_NOT_CHAT = re.compile(r"embed|whisper|tts|guard|moderat|image|rerank|speech|orpheus|safeguard|transcri|audio|dall|veo|imagen", re.I)


# ---------- внутренние «причины отказа» ----------
class _Fail(Exception):
    """kind: limit (429) | auth | model (404) | big | busy (5xx: сервер перегружен, временно) | down (обрыв) | error"""
    def __init__(self, msg: str, cooldown: float = 0.0, kind: str = "error"):
        super().__init__(msg)
        self.msg, self.cooldown, self.kind = msg, cooldown, kind


def estimate_tokens(messages: list[dict]) -> int:
    return int(sum(len(m.get("content") or "") + 8 for m in messages) / CHARS_PER_TOKEN)


def fit_messages(messages: list[dict], max_in: int):
    """Запрос, влезающий в max_in токенов: system и последняя реплика остаются, самая старая переписка отбрасывается.
    None — даже так не влезает."""
    msgs = list(messages)
    while estimate_tokens(msgs) > max_in:
        idx = [i for i, m in enumerate(msgs) if m["role"] != "system"]
        if len(idx) <= 1:
            return None
        del msgs[idx[0]]
        idx = [i for i, m in enumerate(msgs) if m["role"] != "system"]
        while len(idx) > 1 and msgs[idx[0]]["role"] != "user":
            del msgs[idx[0]]
            idx = [i for i, m in enumerate(msgs) if m["role"] != "system"]
    return msgs


def force_fit(messages: list[dict], max_in: int) -> list[dict]:
    """Крайний случай: ни один провайдер не вмещает запрос — режем самое длинное сообщение посередине."""
    msgs = fit_messages(messages, max_in) or [dict(m) for m in messages]
    msgs = [dict(m) for m in msgs]
    for _ in range(8):
        over = estimate_tokens(msgs) - max_in
        if over <= 0:
            break
        i = max(range(len(msgs)), key=lambda k: len(msgs[k].get("content") or ""))
        c = msgs[i]["content"]
        cut = min(len(c) - 200, int(over * CHARS_PER_TOKEN) + 200)
        if cut <= 0:
            break
        head = int((len(c) - cut) * 0.6)
        msgs[i]["content"] = c[:head] + "\n[…часть текста пропущена…]\n" + c[head + cut:]
    return msgs


def strip_think(s: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)
    return re.sub(r"<think>.*$", "", s, flags=re.S).strip() if "<think>" in s else s.strip()


def _retry_seconds(headers, body: str) -> float:
    """Сколько сервер просит подождать: заголовок Retry-After или фраза в тексте ошибки."""
    try:
        v = headers.get("retry-after") if headers else None
        if v and re.fullmatch(r"\d+(\.\d+)?", v.strip()):
            return float(v)
    except Exception:
        pass
    m = re.search(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", body, re.I)
    if m and any(m.groups()):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    m = re.search(r'retryDelay\W+(\d+(?:\.\d+)?)s', body)
    return float(m.group(1)) if m else 0.0


class CloudPool:
    state = "ready"

    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._st: dict[str, dict] = {}
        self._auto: dict[str, str] = {}      # провайдер → автоматически подобранная модель
        self._dead: dict[str, set] = {}      # провайдер → модели, на которые сервер ответил 404 (сняты с обслуживания)
        self._rr = 0
        self.last_label = ""

    # ---------- настройки ----------
    def _conf(self) -> dict:
        return self.cfg.get("cloud") or {}

    @property
    def strategy(self) -> str:
        s = self._conf().get("strategy")
        return s if s in STRATEGIES else "failover"

    def order(self) -> list[str]:
        saved = [p for p in (self._conf().get("order") or []) if p in PRESETS]
        return list(dict.fromkeys(saved + DEFAULT_ORDER))

    def provider(self, pid: str) -> dict:
        base = PRESETS[pid]
        user = (self._conf().get("providers") or {}).get(pid) or {}
        p = dict(base)
        p.update(id=pid, enabled=bool(user.get("enabled")), key=str(user.get("key") or "").strip(),
                 model=str(user.get("model") or "").strip() or base["model"])
        if pid == "custom":
            p["url"] = str(user.get("url") or "").strip().rstrip("/")
            p["name"] = str(user.get("name") or "").strip() or base["name"]
        return p

    @staticmethod
    def usable(p: dict) -> bool:
        """Есть всё нужное для запросов: адрес и ключ (у сервисов «без ключа» ключ не нужен)."""
        return bool(p["url"]) and (bool(p["key"]) or bool(p.get("nokey")))

    def configured(self) -> list[dict]:
        out = []
        for pid in self.order():
            p = self.provider(pid)
            if p["enabled"] and self.usable(p):
                out.append(p)
        return out

    @property
    def ctx_tokens(self) -> int:
        """Сколько токенов запроса можно собирать (по самому «вместительному» из настроенных), не больше 32 тыс."""
        c = self.configured()
        return min(32768, max((p["max_in"] for p in c), default=8000))

    # ---------- состояние лимитов ----------
    def _s(self, pid: str) -> dict:
        today = time.strftime("%Y-%m-%d")
        s = self._st.setdefault(pid, {"until": 0.0, "err": "", "kind": "", "ok": 0, "day": today})
        if s["day"] != today:
            s.update(ok=0, day=today)
        return s

    def _cooling(self, pid: str) -> float:
        with self._lock:
            return max(0.0, self._s(pid)["until"] - time.time())

    def _mark_ok(self, p: dict, model: str = ""):
        with self._lock:
            s = self._s(p["id"])
            s.update(until=0.0, err="", kind="")
            s["ok"] += 1
            self.last_label = f"{p['name']} · {model or self._model_name(p)}"

    def _mark_fail(self, p: dict, f: _Fail):
        with self._lock:
            s = self._s(p["id"])
            s.update(err=f.msg, kind=f.kind)
            if f.cooldown:
                s["until"] = time.time() + min(MAX_COOLDOWN, f.cooldown)

    def available(self) -> list[dict]:
        with self._lock:
            out = [p for p in self.configured() if not self._cooling(p["id"])]
            if self.strategy == "spread" and len(out) > 1:
                k = self._rr % len(out)
                out = out[k:] + out[:k]
            return out

    def unavailable_reason(self) -> str:
        conf = self.configured()
        if not conf:
            return "Облачные модели не настроены: откройте вкладку «Облако», добавьте ключ хотя бы одного провайдера и включите его."
        left = sorted((self._cooling(p["id"]), p["name"]) for p in conf)
        if left[0][0] > 0 and not self.available():
            return f"Лимиты всех подключённых облачных моделей исчерпаны. Ближайшая ({left[0][1]}) освободится через {fmt_wait(left[0][0])}."
        return ""

    # ---------- модель ----------
    def _model_name(self, p: dict) -> str:
        return p["model"] or self._auto.get(p["id"], "")

    def pick_model(self, p: dict) -> str:
        dead = self._dead.get(p["id"], set())
        ids = [i for i in self.list_models(p["id"]) if i not in dead]
        for rx in p["prefer"]:
            found = [i for i in ids if re.search(rx, i, re.I)]
            if found:
                if p.get("newest"):      # список отсортирован по алфавиту: «2.5» раньше «3.8», поэтому берём наибольший номер версии
                    return max(found, key=lambda i: [int(x) for x in re.findall(r"\d+", i)])
                return found[0]
        if ids:
            return ids[0]
        raise _Fail("не удалось подобрать модель: сервер не вернул список", 300)

    def list_models(self, pid: str) -> list[str]:
        """Список моделей сервера (для выбора). У OpenRouter бесплатные (:free) идут первыми."""
        p = self.provider(pid)
        if not self.usable(p):
            raise CloudError("Сначала введите ключ" + (" и адрес" if pid == "custom" else "") + ".")
        req = urllib.request.Request(p["url"] + "/models", headers=self._headers(p, False))
        try:
            with cloudnet.open(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise CloudError(self._scrub(p, f"{p['name']}: {self._explain(e.code, e.read().decode('utf-8', 'replace'))}"))
        except (OSError, ValueError) as e:
            raise CloudError(f"{p['name']}: нет связи ({e.__class__.__name__}).{cloudnet.note()}")
        ids = [re.sub(r"^models/", "", str(m.get("id") or "")) for m in (data.get("data") or data.get("models") or []) if isinstance(m, dict)]
        ids = [i for i in ids if i and not _NOT_CHAT.search(i)]
        free = [i for i in ids if i.endswith(":free")]
        return sorted(free) + sorted(i for i in ids if i not in free) if pid == "openrouter" else sorted(ids)

    # ---------- запрос ----------
    @staticmethod
    def _headers(p: dict, stream: bool = True) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": UA,
             "HTTP-Referer": "https://github.com/Bogestv0/KARINA", "X-Title": "Bogestv0 KARINA"}
        if p.get("key"):
            h["Authorization"] = "Bearer " + p["key"]
        if stream:
            h["Accept"] = "text/event-stream"
        return h

    @staticmethod
    def _scrub(p: dict, s: str) -> str:
        k = p.get("key", "")
        return s.replace(k, "***") if k and len(k) > 6 else s

    @staticmethod
    def _explain(code: int, body: str) -> str:
        msg = ""
        try:
            j = json.loads(body)
            j = j[0] if isinstance(j, list) and j else j
            e = j.get("error") if isinstance(j, dict) else None
            msg = str(e.get("message") if isinstance(e, dict) else e or "")
        except (ValueError, AttributeError, IndexError):
            pass
        base = {401: "ключ не принят (401)",
                403: "доступ запрещён (403) — чаще всего сервис не пускает из вашей страны (нужен VPN/прокси) или ключ без прав",
                402: "нужна оплата или закончились кредиты (402)", 404: "модель или адрес не найдены (404)",
                429: "лимит исчерпан (429)",
                500: "сбой на стороне провайдера (500)", 502: "сервер провайдера не отвечает (502)",
                503: "сервер перегружен (503) — слишком много запросов у самого провайдера, это временно",
                504: "сервер провайдера не успел ответить (504)"}.get(code, f"ошибка {code}")
        keep = msg and code not in (401,) and code < 500          # у 5xx английский текст сервера только пугает: причина уже названа выше
        return base + (f": {msg[:160]}" if keep else "") + (cloudnet.note() if code == 403 else "")

    def _call(self, p: dict, msgs: list[dict], max_tokens: int, temperature: float, on_delta, stop, json_mode: bool, plain: bool = False, _again: bool = True) -> str:
        if p["model"] and p["model"] in self._dead.get(p["id"], set()):
            p = dict(p, model="")             # выбранную модель сервер снял — подбираем актуальную сами
        if not p["model"] and p["id"] not in self._auto:
            self._auto[p["id"]] = self.pick_model(p)
        model = self._model_name(p)
        if json_mode:
            msgs = [dict(m) for m in msgs]
            add = prompts.JSON_ONLY
            for m in msgs:
                if m["role"] == "system":
                    m["content"] += add
                    break
            else:
                msgs.insert(0, {"role": "system", "content": add.strip()})
        body = {"model": model, "messages": msgs, "max_tokens": min(int(max_tokens), p["max_out"]), "stream": True}
        if not plain:
            body["temperature"] = temperature
            if p.get("extra") and re.search(p.get("extra_if", ""), model, re.I):
                body.update(p["extra"])
        req = urllib.request.Request(p["url"] + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=self._headers(p))
        out: list[str] = []
        try:
            with cloudnet.open(req, timeout=90) as r:
                for raw in r:
                    if stop is not None and stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        e = ev["error"]
                        raise _Fail(self._scrub(p, "ошибка во время ответа: " + str(e.get("message") if isinstance(e, dict) else e)[:160]), 30, "down")
                    ch = (ev.get("choices") or [{}])[0]
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        out.append(piece)
                        if on_delta:
                            on_delta(strip_think("".join(out)))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            low = text.lower()
            if e.code == 400 and not plain:
                return self._call(p, msgs, max_tokens, temperature, on_delta, stop, False, plain=True, _again=_again)   # часть серверов не знает лишних параметров
            if e.code == 429:
                wait = _retry_seconds(e.headers, text)
                daily = bool(re.search(r"per day|daily|\brpd\b|\btpd\b|quota", low))
                raise _Fail(self._explain(429, text), wait + 1 if wait else (3600 if daily else 60), "limit")   # сколько просит сервер (+1 с), иначе минута или час
            if e.code in (401, 403, 402):
                raise _Fail(self._scrub(p, self._explain(e.code, text)), 3600, "auth")
            if e.code == 404:
                self._auto.pop(p["id"], None)
                if model and p["id"] != "custom":
                    self._dead.setdefault(p["id"], set()).add(model)
                    if _again:                # модель снята с обслуживания: один раз сразу пробуем подобрать другую
                        try:
                            return self._call(dict(p, model=""), msgs, max_tokens, temperature, on_delta, stop, json_mode, plain, _again=False)
                        except _Fail:
                            pass
                raise _Fail(self._explain(404, text) + f" (модель «{model}»)", 300, "model")
            if e.code == 413 or (e.code == 400 and re.search(r"context|too (long|large)|maximum", low)):
                raise _Fail("запрос слишком большой для этого сервера", 0, "big")
            if e.code >= 500:
                raise _Fail(self._explain(e.code, text), 20, "busy")
            raise _Fail(self._scrub(p, self._explain(e.code, text)), 60, "error")
        except _Fail:
            raise
        except (OSError, ValueError) as e:
            raise _Fail(f"нет связи ({e.__class__.__name__}){cloudnet.note()}", 30, "down")
        text = strip_think("".join(out))
        if not text and not (stop is not None and stop.is_set()):
            raise _Fail("пустой ответ", 15, "down")
        return text

    # ---------- повторы и запасные модели ----------
    @staticmethod
    def _wait(sec: float, stop) -> bool:
        """Пауза, которую можно прервать кнопкой «Стоп». True — прервали."""
        if sec <= 0:
            return bool(stop is not None and stop.is_set())
        if stop is not None:
            return stop.wait(sec)
        time.sleep(sec)
        return False

    def _alternatives(self, p: dict, used: str) -> list[str]:
        """Запасные модели того же провайдера на случай перегрузки основной (например, предыдущая версия flash или flash-lite).
        Если модель выбрана вручную или это «свой сервер» — запасных нет, уважаем выбор."""
        if p["model"] or p["id"] == "custom":
            return []
        try:
            ids = [i for i in self.list_models(p["id"]) if i != used and i not in self._dead.get(p["id"], set())]
        except CloudError:
            return []
        ranked: list[str] = []
        for rx in p["prefer"]:
            found = [i for i in ids if re.search(rx, i, re.I) and i not in ranked]
            ranked += sorted(found, key=lambda i: [int(x) for x in re.findall(r"\d+", i)], reverse=True)
        return ranked[:MAX_ALT_MODELS]

    def _run(self, p: dict, msgs: list[dict], max_tokens: int, temperature: float, on_delta, stop, json_mode: bool) -> tuple[str, str]:
        """Один провайдер целиком: при перегрузке (503) повторяет с паузой, затем пробует запасные модели.
        Возвращает (текст, модель). Любая другая причина отказа сразу уходит наверх — переходим к следующему провайдеру."""
        last: _Fail | None = None
        alts: list[str] | None = None
        cur = p
        while True:
            for delay in (0.0,) + tuple(RETRY_DELAYS):
                if self._wait(delay, stop):
                    return "", ""
                try:
                    return self._call(cur, msgs, max_tokens, temperature, on_delta, stop, json_mode), self._model_name(cur)
                except _Fail as f:
                    if f.kind == "busy" or (f.kind == "model" and cur is not p):     # у запасной модели 404 — просто берём следующую
                        last = f
                        if f.kind == "model":
                            break
                    else:
                        raise
            if alts is None:
                alts = self._alternatives(p, self._model_name(cur))
            if not alts:
                raise last
            cur = dict(p, model=alts.pop(0))

    def chat(self, messages: list[dict], max_tokens: int = 1024, temperature: float = 0.6, on_delta=None,
             stop: threading.Event | None = None, json_mode: bool = False) -> str:
        cands = self.available()
        if not cands:
            raise CloudError(self.unavailable_reason() or "Нет доступных облачных моделей.")
        with self._lock:
            self._rr += 1
        sized = [(p, fit_messages(messages, p["max_in"])) for p in cands]
        tries = [(p, m) for p, m in sized if m is not None]
        if not tries:        # слишком большой запрос: отправляем самому вместительному, обрезав середину
            big = max(cands, key=lambda p: p["max_in"])
            tries = [(big, force_fit(messages, big["max_in"]))]
        errors, kinds = [], []
        for p, msgs in tries:
            if stop is not None and stop.is_set():
                break
            try:
                text, used = self._run(p, msgs, max_tokens, temperature, on_delta, stop, json_mode)
                if stop is not None and stop.is_set() and not text:
                    break
                self._mark_ok(p, used)
                return text
            except _Fail as f:
                self._mark_fail(p, f)
                errors.append(f"{p['name']}: {f.msg}"); kinds.append(f.kind)
            except CloudError as e:
                errors.append(f"{p['name']}: {e}"); kinds.append("error")
        if stop is not None and stop.is_set():
            return ""
        raise CloudError(explain_failure(errors, kinds))

    # ---------- проверка и описание ----------
    def ping(self, pid: str) -> tuple[bool, str, float]:
        """Проверка ключа и модели одного провайдера коротким запросом (даже если он сейчас «на отдыхе»)."""
        t0 = time.time()
        p = self.provider(pid)
        if not self.usable(p):
            return False, "Введите ключ" + (" и адрес сервера." if pid == "custom" else "."), 0.0
        try:
            text = self._call(p, [{"role": "user", "content": "Ответь одним словом: работаю."}], 40, 0.2, None, None, False)
            self._mark_ok(p)
            return True, f"{p['name']} · {self._model_name(p)}: {text[:80]}", round(time.time() - t0, 1)
        except _Fail as f:
            self._mark_fail(p, f)
            return False, f"{p['name']}: {f.msg}", round(time.time() - t0, 1)
        except CloudError as e:
            return False, str(e), round(time.time() - t0, 1)

    def describe(self) -> list[dict]:
        out = []
        for pid in self.order():
            p = self.provider(pid)
            s = self._s(pid)
            wait = self._cooling(pid)
            if not self.usable(p):
                state, text = "nokey", "Нет ключа"
            elif not p["enabled"]:
                state, text = "off", "Выключен"
            elif wait:
                state = "cooldown" if s["kind"] == "limit" else "error"
                text = (f"Лимит исчерпан, вернусь через {fmt_wait(wait)}" if s["kind"] == "limit" else f"{s['err']} (повтор через {fmt_wait(wait)})")
            else:
                state, text = "ok", "Готов"
            out.append({"id": pid, "name": p["name"], "enabled": p["enabled"], "has_key": bool(p["key"]), "nokey": bool(p.get("nokey")), "model": p["model"],
                        "model_auto": self._auto.get(pid, ""), "url": p["url"] if pid == "custom" else "", "state": state, "text": text,
                        "used_today": s["ok"], "signup": p["signup"], "note": p["note"], "wait": int(wait)})
        return out


def explain_failure(errors: list[str], kinds: list[str]) -> str:
    """Понятный итог, когда не ответил никто: сначала что случилось и что делать, ниже по строке на провайдера."""
    if kinds and all(k == "busy" for k in kinds):
        head = "Серверы бесплатных моделей сейчас перегружены — это временно и не связано с вашим компьютером или ключом. Подождите минуту и повторите вопрос."
    elif kinds and all(k == "limit" for k in kinds):
        head = "Бесплатные лимиты у всех подключённых моделей исчерпаны. Подождите или добавьте ещё один ключ во вкладке «Облако»."
    else:
        head = "Бесплатные модели не смогли ответить."
    return head + "\n" + "\n".join("• " + e for e in errors[-6:])


def fmt_wait(sec: float) -> str:
    sec = int(sec + 0.5)
    if sec < 90:
        return f"{max(sec, 1)} с"
    if sec < 5400:
        return f"{round(sec / 60)} мин"
    return f"{round(sec / 3600)} ч"
