"""«Мозг» Карины: кто отвечает в чате.
  local  — встроенная модель на этом компьютере (по умолчанию, офлайн, бесплатно);
  claude — Claude по вашему ключу API (онлайн, платно у Anthropic);
  auto   — простое отвечает локальная модель, сложное (отчёты, код, файлы, глубокий поиск) — Claude.
Если выбранный мозг недоступен, Карина говорит об этом сразу и понятно (BrainUnavailable), а не молча подменяет его.
Исключение — режим «авто»: там запасной вариант допустим, но в ответе всегда видно, кто отвечал."""
from __future__ import annotations
import re
from . import claude as claudemod, net
from .engine import EngineError
from .textutil import norm

MODES = ("local", "claude", "auto")
MODE_LABEL = {"local": "Локальная модель", "claude": "Claude", "auto": "Авто"}
LOCAL_NAME = "Локальная модель"

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
    def __init__(self, cfg, engine, claude: claudemod.ClaudeClient | None = None):
        self.cfg, self.engine = cfg, engine
        self.claude = claude or claudemod.ClaudeClient(cfg)

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

    def usable(self) -> bool:
        """Может ли чат ответить при текущем выборе (для «авто» — хотя бы одним из двух)."""
        m = self.mode
        if m == "local":
            return self.local_status()[0]
        if m == "claude":
            return self.claude_status()[0]
        return self.local_status()[0] or self.claude_status()[0]

    def unavailable_reason(self) -> str:
        m = self.mode
        if m == "local":
            return self.local_status()[1]
        if m == "claude":
            return self.claude_status()[1]
        return f"Сейчас не могу ответить. Локальная модель: {self.local_status()[1] or 'готова'} Claude: {self.claude_status()[1] or 'готов'}"

    # ---------- выбор ----------
    def _pick(self, name: str) -> Choice:
        if name == "claude":
            return Choice("claude", self.claude, self.claude_label())
        return Choice("local", self.engine, LOCAL_NAME)

    def choose(self, text: str, *, deep_search: bool = False, want_files: bool = False) -> Choice:
        m = self.mode
        if m in ("local", "claude"):
            ok, why = self.local_status() if m == "local" else self.claude_status()
            if not ok:
                raise BrainUnavailable(why)
            return self._pick(m)
        # авто
        status = {"local": self.local_status(), "claude": self.claude_status()}
        want = "claude" if is_complex(text, deep_search=deep_search, want_files=want_files) else "local"
        other = "local" if want == "claude" else "claude"
        if status[want][0]:
            return self._pick(want)
        if status[other][0]:
            who = lambda n: LOCAL_NAME if n == "local" else self.claude_label()
            return Choice(other, self._pick(other).backend, who(other), f"Для этого вопроса лучше подошёл бы вариант «{who(want)}», но он недоступен — отвечает «{who(other)}». {status[want][1]}")
        raise BrainUnavailable(f"Сейчас не могу ответить. Локальная модель: {status['local'][1]} Claude: {status['claude'][1]}")

    # ---------- для окна ----------
    def describe(self) -> dict:
        lok, lwhy = self.local_status()
        cok, cwhy = self.claude_status()
        return {"mode": self.mode, "label": MODE_LABEL[self.mode], "claude_model": self.claude.model_id,
                "claude_models": [{"id": k, "title": v} for k, v in claudemod.MODELS.items()],
                "local": {"ok": lok, "reason": lwhy},
                "claude": {"ok": cok, "reason": cwhy, "has_key": self.claude.has_key(), "label": self.claude_label()}}
