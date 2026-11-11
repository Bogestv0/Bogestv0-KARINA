"""Настройки: data/config.json рядом с программой. Недостающие ключи берутся из DEFAULTS."""
from __future__ import annotations
import copy, json, threading
from pathlib import Path

import os, sys

FROZEN = getattr(sys, "frozen", False)
# ROOT — где лежат ресурсы программы (окно, плагины-примеры); DATA — куда писать настройки и модель.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) if FROZEN else Path(__file__).resolve().parent.parent
# Установленная версия лежит в Program Files/AppData\Programs и писать туда нельзя, поэтому данные — в AppData.
DATA = Path(os.environ.get("APPDATA", str(Path.home()))) / "Bogestv0 KARINA" if FROZEN else ROOT / "data"

DEFAULTS: dict = {
    "name": "Карина",
    # как распознавалка может «услышать» имя; к ним добавляется само имя
    "name_aliases": ["карина", "карену", "карине", "карины", "карину", "кариной", "каринка", "корина",
                      "карена", "каріна", "кариана", "карин", "каринна"],
    "wake_required": True,        # откликаться только на имя
    "followup_seconds": 8,        # после имени слушаем без повторного обращения
    "ui": {"theme": "auto"},
    "stt": {"model": "small", "mic_device": None, "vad_threshold": 0.012},
    "tts": {"enabled": True, "engine": "auto", "voice_online": "ru-RU-SvetlanaNeural", "rate": 0},
    "search_engine": "google",    # google | yandex
    "llm": {"enabled": False, "api_key": "", "model": "claude-haiku-5-5"},
    # встроенные модели (llama.cpp): active — id выбранной модели; gpu: auto | cpu; ctx/threads: 0 = по умолчанию
    # поиск в интернете для чата: mode — auto (когда просьба этого требует) | always | off; depth — quick | normal | deep | max
    "search": {"mode": "auto", "depth": "normal"},
    # «мозг» чата: mode — local (встроенная модель) | claude (по ключу API) | auto (простое локально, сложное в Claude)
    "brain": {"mode": "local", "claude_model": "claude-sonnet-5-5"},
    # «Кодер»: run — разрешить запускать написанный код для проверки (python / pytest / node в папке проекта); max_steps — шагов на задачу
    "coder": {"run": False, "max_steps": 12},
    "updates": {"auto": True},   # проверять новую версию при запуске
    "models": {"active": "", "gpu": "auto", "ctx": 0, "threads": 0},
    "apps": [],       # свои программы: {"name","aliases":[],"path","args","proc":[]}
    "commands": [],   # свои команды: {"phrases":[],"do":{"type","value"},"reply"}
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA / "config.json"
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        with self._lock:
            try:
                self.data = _merge(DEFAULTS, json.loads(self.path.read_text("utf-8")))
            except FileNotFoundError:
                self.data = copy.deepcopy(DEFAULTS)
            except (ValueError, OSError):
                # битый файл не должен ломать запуск: сохраняем копию и начинаем с настроек по умолчанию
                try:
                    self.path.replace(self.path.with_suffix(".broken.json"))
                except OSError:
                    pass
                self.data = copy.deepcopy(DEFAULTS)

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.path)

    def update(self, patch: dict):
        with self._lock:
            self.data = _merge(self.data, patch)
            self.save()

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, d=None):
        return self.data.get(k, d)

    def wake_names(self) -> list[str]:
        from .textutil import norm
        names = [norm(self.data["name"])] + [norm(a) for a in self.data.get("name_aliases", [])]
        return [n for n in dict.fromkeys(names) if n]
