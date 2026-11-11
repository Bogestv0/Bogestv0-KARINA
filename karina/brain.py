"""«Мозг» Карины: кто отвечает в чате.
  local  — встроенная модель на этом компьютере (по умолчанию, офлайн, бесплатно);
  claude — Claude по вашему ключу API (онлайн, платно у Anthropic);
  cloud  — бесплатные облачные модели (Groq, Gemini, OpenRouter…) по вашим ключам, с переходом к следующей при исчерпании лимита;
  auto   — простое отвечает локальная модель, сложное (отчёты, код, файлы, глубокий поиск) — Claude или бесплатное облако.
Если выбранный мозг недоступен, Карина говорит об этом сразу и понятно (BrainUnavailable), а не молча подменяет его.
Исключение — режим «авто»: там запасной вариант допустим, но в ответе всегда видно, кто отвечал."""
from __future__ import annotations
import re
from . import claude as claudemod, cloud as cloudmod, net
from .engine import EngineError
from .textutil import norm

MODES = ("local", "claude", "cloud", "auto")
MODE_LABEL = {"local": "Локальная модель", "claude": "Claude", "cloud": "Бесплатные облака", "auto": "Авто"}
LOCAL_NAME = "Локальная модель"
CLOUD_NAME = "Бесплатное облако"

# Признаки «сложной» просьбы для режима «авто». text — фраза пользователя как есть.
COMPLEX = re.compile(
    r"(?:отчет|реферат|статью|статья|эссе|доклад|анализ|проанализ|сравни|сравнен|подробн|развернут|пошагов|стратег|алгоритм|скрипт|функци|"
    r"программ|\bкод|оптимиз|ошибк|отладк|докажи|вычисли|реши|задач|договор|юридич|бизнес план|исследова|по источникам|с источниками|"
    r"\bплан\b|схем|диаграмм|курсов|диплом)")
LONG_REQUEST = 240            # символов: длинную просьбу тоже считаем сложной


class BrainUnavailable(EngineError):
    """Выбранный мозг сейчас не может ответить (причина — по-русски, для показа пользователю)."""


class Choice:
    def __init__(self, name: str, backend, label: str, note: str = ""):
        self.name, self.backend, self.label, self.note = name, backend, label, note


def is_complex(text: str, *, deep_search: bool = False, want_files: bool = False) -> bool:
    return bool(want_files or deep_search or len(text or "") >= LONG_REQUEST or COMPLEX.search(norm(text)))


class Brain:
    def __init__(self, cfg, engine, claude: claudemod.ClaudeClient | None = None, cloud: cloudmod.CloudPool | None = None):
        self.cfg, self.engine = cfg, engine
        self.claude = claude or claudemod.ClaudeClient(cfg)
        self.cloud = cloud or cloudmod.CloudPool(cfg)

    # ---------- настройки ----------
    @property
    def mode(self) -> str:
        m = (self.cfg.get("brain", {}) or {}).get("mode")
        return m if m in MODES else "local"

    def claude_label(self) -> str:
        return claudemod.model_label(self.claude.model_id)

    # ---------- доступность ----------
    def local_status(self) -> tuple[bool, str]:
        try:
            return self.engine.available()
        except AttributeError:
            return True, ""

    def claude_status(self) -> tuple[bool, str]:
        if not self.claude.has_key():
            return False, "Ключ Claude не задан. Введите его в «Настройки» (раздел «Claude»)."
        if not net.is_online():
            return False, "Нет интернета, поэтому Claude сейчас недоступен."
        return True, ""

    def cloud_status(self) -> tuple[bool, str]:
        if not self.cloud.configured():
            return False, self.cloud.unavailable_reason()
        if not net.is_online():
            return False, "Нет интернета, поэтому облачные модели сейчас недоступны."
        if not self.cloud.available():
            return False, self.cloud.unavailable_reason()
        return True, ""

    def _status(self, name: str) -> tuple[bool, str]:
        return {"local": self.local_status, "claude": self.claude_status, "cloud": self.cloud_status}[name]()

    def usable(self) -> bool:
        """Может ли чат ответить при текущем выборе (для «авто» — хотя бы одним из двух)."""
        m = self.mode
        if m == "local":
            return self.local_status()[0]
        if m in ("claude", "cloud"):
            return self._status(m)[0]
        return any(self._status(n)[0] for n in ("local", "claude", "cloud"))

    def unavailable_reason(self) -> str:
        m = self.mode
        if m == "local":
            return self.local_status()[1]
        if m in ("claude", "cloud"):
            return self._status(m)[1]
        return self._nobody()

    # ---------- выбор ----------
    def _label(self, name: str) -> str:
        return {"local": LOCAL_NAME, "claude": self.claude_label(), "cloud": CLOUD_NAME}[name]

    def _nobody(self) -> str:
        """Никто не может ответить: причины по каждому (облако — только если оно настроено)."""
        out = f"Сейчас не могу ответить. Локальная модель: {self.local_status()[1] or 'готова'} Claude: {self.claude_status()[1] or 'готов'}"
        if self.cloud.configured():
            out += f" Облако: {self.cloud_status()[1] or 'готово'}"
        return out

    def _pick(self, name: str) -> Choice:
        if name == "claude":
            return Choice("claude", self.claude, self.claude_label())
        if name == "cloud":
            return Choice("cloud", self.cloud, CLOUD_NAME)
        return Choice("local", self.engine, LOCAL_NAME)

    def choose(self, text: str, *, deep_search: bool = False, want_files: bool = False) -> Choice:
        m = self.mode
        if m != "auto":
            ok, why = self._status(m)
            if not ok:
                raise BrainUnavailable(why)
            return self._pick(m)
        # авто: простое — локально, сложное — Claude или бесплатное облако; если первого выбора нет, отвечает следующий
        prefs = ["claude", "cloud", "local"] if is_complex(text, deep_search=deep_search, want_files=want_files) else ["local", "cloud", "claude"]
        status = {n: self._status(n) for n in prefs}
        for n in prefs:
            if status[n][0]:
                if n == prefs[0]:
                    return self._pick(n)
                want = prefs[0]
                return Choice(n, self._pick(n).backend, self._label(n), f"Для этого вопроса лучше подошёл бы вариант «{self._label(want)}», но он недоступен — отвечает «{self._label(n)}». {status[want][1]}")
        raise BrainUnavailable(self._nobody())

    # ---------- для окна ----------
    def describe(self) -> dict:
        lok, lwhy = self.local_status()
        cok, cwhy = self.claude_status()
        kok, kwhy = self.cloud_status()
        return {"mode": self.mode, "label": MODE_LABEL[self.mode], "claude_model": self.claude.model_id,
                "claude_models": [{"id": k, "title": v} for k, v in claudemod.MODELS.items()],
                "local": {"ok": lok, "reason": lwhy},
                "claude": {"ok": cok, "reason": cwhy, "has_key": self.claude.has_key(), "label": self.claude_label()},
                "cloud": {"ok": kok, "reason": kwhy, "count": len(self.cloud.configured()), "strategy": self.cloud.strategy}}
