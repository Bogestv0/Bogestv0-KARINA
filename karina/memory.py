"""Память Карины: чему она научилась у вас. Лежит в data/learned.json рядом с настройками.

* apps — «в каком приложении» вы обычно делаете то или иное (музыка, видео): спросила один раз — дальше не спрашивает.
* phrases — фразы, которые Карина поняла и выполнила (в том числе с помощью языковой модели): в следующий раз она узнаёт их сразу, без разбора и без интернета.
Опасные действия (выключение, текст в чужое окно, поиск в интернете) не запоминаются. Всё можно посмотреть и стереть голосом: «что ты выучила», «забудь выученное».
"""
from __future__ import annotations
import json, threading, time
from pathlib import Path

# Что можно выполнять по памяти без повторного разбора. Остальное (выключение, печать, поиск с подтверждением) каждый раз разбирается заново.
LEARN_OK = {"open_app", "focus_app", "close_app", "mixer_volume", "mixer_scene", "volume_set", "volume_up", "volume_down", "mute", "unmute",
            "media_pause", "media_next", "media_prev", "music", "show_desktop", "minimize", "maximize", "screenshot", "vpn_on", "vpn_off", "vpn_toggle"}
MAX_PHRASES = 400
CATEGORY_LABEL = {"music": "музыки", "video": "видео"}


class Memory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data: dict = {"apps": {}, "phrases": {}}
        self.load()

    # ---------- файл ----------
    def load(self):
        with self._lock:
            try:
                d = json.loads(self.path.read_text("utf-8"))
                if isinstance(d, dict):
                    self.data = {"apps": dict(d.get("apps") or {}), "phrases": dict(d.get("phrases") or {})}
            except (OSError, ValueError):
                self.data = {"apps": {}, "phrases": {}}

    def save(self):
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
                tmp.replace(self.path)
            except OSError:
                pass          # память — удобство, а не необходимость: не получилось сохранить — работаем дальше

    # ---------- любимые приложения ----------
    def app(self, category: str) -> dict | None:
        with self._lock:
            a = self.data["apps"].get(category)
            return dict(a) if isinstance(a, dict) and a.get("key") else None

    def set_app(self, category: str, key: str, name: str):
        with self._lock:
            self.data["apps"][category] = {"key": key, "name": name, "ts": int(time.time())}
            self.save()

    def forget_app(self, category: str) -> bool:
        with self._lock:
            had = self.data["apps"].pop(category, None) is not None
            if had:
                self.save()
            return had

    # ---------- фразы ----------
    def recall(self, phrase: str) -> tuple[str, dict] | None:
        """Точное совпадение нормализованной фразы. Нечёткого нет намеренно: «убавь» и «прибавь» похожи на 92 %."""
        with self._lock:
            e = self.data["phrases"].get(phrase)
            if not e or e.get("cmd") not in LEARN_OK:
                return None
            return e["cmd"], dict(e.get("args") or {})

    def remember(self, phrase: str, cmd: str, args: dict | None = None) -> bool:
        if cmd not in LEARN_OK or len(phrase) < 3 or len(phrase) > 160:
            return False
        args = {k: str(v) for k, v in (args or {}).items() if v is not None}
        with self._lock:
            old = self.data["phrases"].get(phrase)
            n = int(old.get("n", 0)) + 1 if old else 1
            self.data["phrases"][phrase] = {"cmd": cmd, "args": args, "n": n, "ts": int(time.time())}
            if len(self.data["phrases"]) > MAX_PHRASES:         # самые давние забываем первыми
                for k, _ in sorted(self.data["phrases"].items(), key=lambda kv: kv[1].get("ts", 0))[:len(self.data["phrases"]) - MAX_PHRASES]:
                    self.data["phrases"].pop(k, None)
            self.save()
            return old is None

    def forget(self, phrase: str):
        with self._lock:
            if self.data["phrases"].pop(phrase, None) is not None:
                self.save()

    def clear(self):
        with self._lock:
            self.data = {"apps": {}, "phrases": {}}
            self.save()

    def summary(self) -> str:
        with self._lock:
            apps = [f"для {CATEGORY_LABEL.get(c, c)} — {a.get('name')}" for c, a in self.data["apps"].items() if isinstance(a, dict)]
            n = len(self.data["phrases"])
        if not apps and not n:
            return "Пока ничего не запомнила. Чем чаще мы общаемся, тем больше я узнаю."
        bits = []
        if apps:
            bits.append("Приложения: " + ", ".join(apps) + ".")
        if n:
            bits.append(f"Фраз, которые узнаю сразу: {n}.")
        return " ".join(bits)


_INSTANCES: dict[str, Memory] = {}


def get(cfg) -> Memory:
    """Одна память на настройки (рядом с config.json)."""
    key = str(Path(cfg.path).parent)
    m = _INSTANCES.get(key)
    if m is None:
        m = _INSTANCES[key] = Memory(Path(cfg.path).parent / "learned.json")
    return m
