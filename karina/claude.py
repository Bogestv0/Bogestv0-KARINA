"""Claude как «мозг» чата: ответы по вашему ключу API (потоком). Интерфейс такой же, как у встроенного движка (engine.Engine.chat),
поэтому поиск в интернете и чат работают с обоими одинаково.

Ключ берётся из настроек (llm.api_key) или переменной ANTHROPIC_API_KEY и уходит только в заголовок запроса к api.anthropic.com.
Он не пишется в журнал и не попадает в тексты ошибок (см. _scrub)."""
from __future__ import annotations
import json, os, threading, time, urllib.error, urllib.request
from .engine import EngineError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
MODELS = {
    "claude-haiku-5-5": "Claude Haiku — самый быстрый и дешёвый",
    "claude-sonnet-5-5": "Claude Sonnet — баланс скорости и качества (рекомендуется)",
    "claude-opus-5-5": "Claude Opus — самый сильный, дороже",
}
SHORT = {"claude-haiku-5-5": "Claude Haiku", "claude-sonnet-5-5": "Claude Sonnet", "claude-opus-5-5": "Claude Opus"}


class ClaudeError(EngineError):
    pass


def model_label(mid: str) -> str:
    return SHORT.get(mid, mid or "Claude")


class ClaudeClient:
    state = "ready"                 # так же, как у Engine: чату не нужно «загружать модель»

    def __init__(self, cfg):
        self.cfg = cfg
        self._no_temperature = False

    # ---------- ключ и модель ----------
    def key(self) -> str:
        return ((self.cfg.get("llm", {}) or {}).get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()

    def has_key(self) -> bool:
        return bool(self.key())

    @property
    def model_id(self) -> str:
        m = ((self.cfg.get("brain", {}) or {}).get("claude_model") or DEFAULT_MODEL).strip()
        return m or DEFAULT_MODEL

    def _scrub(self, s: str) -> str:
        k = self.key()
        return s.replace(k, "***") if k and len(k) > 6 else s

    # ---------- ошибки понятным языком ----------
    def _explain(self, code: int, body: str) -> str:
        msg = ""
        try:
            msg = str((json.loads(body).get("error") or {}).get("message") or "")
        except (ValueError, AttributeError):
            pass
        low = msg.lower()
        if code == 401:
            return "Claude не принял ключ API. Проверьте ключ в «Настройки»."
        if code == 403:
            return "У этого ключа нет доступа к Claude (ошибка 403)."
        if code == 404:
            return f"Модель «{self.model_id}» недоступна для вашего ключа. Выберите другую в «Модели → Мозг»."
        if code == 429:
            return "Слишком много запросов к Claude или исчерпан лимит. Подождите немного и повторите."
        if code == 400 and ("credit balance" in low or "billing" in low):
            return "На счёте Claude API закончились средства. Пополните баланс в консоли Anthropic."
        if code in (500, 502, 503, 504, 529):
            return "Сервис Claude сейчас перегружен или недоступен. Попробуйте позже."
        return self._scrub(f"Claude вернул ошибку {code}: {msg[:200]}".strip(": "))

    # ---------- запрос ----------
    def _request(self, body: dict) -> urllib.request.Request:
        return urllib.request.Request(API_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
            "x-api-key": self.key(), "anthropic-version": API_VERSION, "content-type": "application/json", "accept": "text/event-stream"})

    @staticmethod
    def _split(messages: list[dict]) -> tuple[str, list[dict]]:
        """system отдельно; подряд идущие реплики одной роли склеиваются; начинаем с пользователя (требование API)."""
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        out: list[dict] = []
        for m in messages:
            if m["role"] == "system" or not (m.get("content") or "").strip():
                continue
            role = "user" if m["role"] == "user" else "assistant"
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n\n" + m["content"]
            else:
                out.append({"role": role, "content": m["content"]})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return system, out

    def chat(self, messages: list[dict], max_tokens: int = 1024, temperature: float = 0.6, on_delta=None,
             stop: threading.Event | None = None, json_mode: bool = False) -> str:
        if not self.has_key():
            raise ClaudeError("Ключ Claude не задан. Введите его в «Настройки».")
        system, msgs = self._split(messages)
        if json_mode:
            system += "\n\nОтвечай только корректным JSON без пояснений и без обрамляющих знаков."
        if not msgs:
            raise ClaudeError("Нечего отправлять в Claude.")
        body = {"model": self.model_id, "max_tokens": int(max_tokens), "messages": msgs, "stream": True}
        if system.strip():
            body["system"] = system.strip()
        if not self._no_temperature:
            body["temperature"] = temperature
        out: list[str] = []
        try:
            with urllib.request.urlopen(self._request(body), timeout=90) as r:
                for raw in r:
                    if stop is not None and stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    t = ev.get("type")
                    if t == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                        out.append(ev["delta"].get("text", ""))
                        if on_delta:
                            on_delta("".join(out))
                    elif t == "error":
                        raise ClaudeError(self._scrub("Claude: " + str((ev.get("error") or {}).get("message", "ошибка"))[:200]))
                    elif t == "message_stop":
                        break
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 400 and not self._no_temperature and "temperature" in text.lower():
                self._no_temperature = True          # новые модели не принимают этот параметр — повторяем без него
                return self.chat(messages, max_tokens, temperature, on_delta, stop, json_mode)
            raise ClaudeError(self._explain(e.code, text))
        except ClaudeError:
            raise
        except (OSError, ValueError) as e:
            raise ClaudeError("Нет связи с Claude: проверьте интернет." + (f" ({e.__class__.__name__})" if not isinstance(e, urllib.error.URLError) else ""))
        return "".join(out).strip()

    def ping(self) -> tuple[bool, str, float]:
        """Проверка ключа и модели: короткий запрос. → (успех, текст, секунды)."""
        t0 = time.time()
        try:
            text = self.chat([{"role": "user", "content": "Ответь одним словом: работаю."}], max_tokens=20)
            return True, text or "ответ пустой", round(time.time() - t0, 1)
        except EngineError as e:
            return False, str(e), round(time.time() - t0, 1)
