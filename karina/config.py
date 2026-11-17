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
    "followup_max_words": 10,     # в этом окне без имени принимаем только короткие фразы (команды); длинная речь из фона игнорируется
    "wake_tail_words": 7,         # имя в КОНЦЕ фразы («открой хром, Карина») считается обращением только в фразе до стольких слов
    "ui": {"theme": "auto", "background": True, "frameless": True,
           "admin": False, "admin_asked": False},   # admin: запускать Карину с правами администратора (через задачу планировщика)   # frameless: свой заголовок вместо рамки Windows   # background: закрытие окна сворачивает Карину в трей, а не выключает
    # mics — до 4 микрофонов сразу: {"enabled","device","name"} (пусто = старая настройка mic_device, слот 1);
    # assist — как микрофоны помогают друг другу: smart (лучший слушает, остальные помогают при плохом распознавании) | all (распознать со всех, выбрать лучшее) | best (только самый чистый)
    "stt": {"model": "small", "mic_device": None, "vad_threshold": 0.012, "mics": [], "assist": "smart",
            "vad": "neural", "min_conf": 0.42, "followup_conf": 0.55, "end_silence": 0.55, "end_silence_long": 0.8, "device": "auto", "prefilter": False},
    # vad — neural (Silero VAD, сам вернётся к energy, если нет модели) | energy; end_silence / end_silence_long — пауза (с) в конце короткой / долгой фразы;
    # device — auto (видеокарта NVIDIA, если сработает проба, иначе процессор) | cpu | cuda; prefilter — быстрая проверка имени моделью «tiny» до полного распознавания (экономит процессор)
    # music.services — свои сервисы музыки/видео: {"music": {"мой": {"name": "…", "aliases": ["…"], "kind": "web", "search": "https://…?q={q}"}}} (см. karina/music.py)
    "music": {"services": {}},
    # горячие клавиши (Windows): talk — говорить без имени, stop — замолчать
    "hotkeys": {"enabled": True, "talk": "ctrl+alt+a", "stop": "ctrl+alt+s"},
    # куда Карина выводит свой голос: device — номер устройства (None = по умолчанию в Windows), name — его имя (номера меняются), volume — громкость голоса 0–100 %
    "audio_out": {"device": None, "name": "", "volume": 100},
    # voice — всегда «karina» (Silero «Байя», единственный голос); auto_silero — сама скачать голос Silero; pronounce — словарь «слово → как произнести»
    # barge_in — можно ли перебить Карину словом «стоп»
    "tts": {"enabled": True, "voice": "karina", "rate": 0, "auto_silero": True, "barge_in": True,
            "pronounce": {"discord": "дискорд", "vpn": "вэ пэ эн", "впн": "вэ пэ эн", "youtube": "ютуб", "steam": "стим", "telegram": "телеграм", "chrome": "хром",
                          "карина": "Кари́на", "карины": "Кари́ны", "карине": "Кари́не", "карину": "Кари́ну", "кариной": "Кари́ной"}},
    "search_engine": "google",    # google | yandex
    "llm": {"enabled": False, "api_key": "", "model": "claude-haiku-5-5"},
    # встроенные модели (llama.cpp): active — id выбранной модели; gpu: auto | cpu; ctx/threads: 0 = по умолчанию
    # поиск в интернете для чата: mode — auto (только по явной просьбе: «найди в интернете …», «загугли …») | always | off; confirm — переспрашивать «Искать? Да/нет» перед поиском; depth — quick | normal | deep | max
    "search": {"mode": "auto", "depth": "normal", "confirm": True},
    # «мозг» чата: mode — local (встроенная модель) | claude (по ключу API) | auto (простое локально, сложное в Claude)
    "brain": {"mode": "local", "claude_model": "claude-sonnet-5-5"},
    # «Кодер»: run — запускать написанный код для проверки (python / pytest / node в папке проекта); max_steps — шагов на задачу
    # run включён по умолчанию (запуск — не песочница, но код перед запуском проверяется на опасные вещи); выключатель — в чате («▶ Запуск и проверка»)
    "coder": {"run": True, "max_steps": 16, "plan": True},
    # картинки по запросу: provider — auto (свой сервер, если указан адрес, иначе Pollinations) | pollinations | sd (AUTOMATIC1111 / Forge) | openai (свой сервер с OpenAI API);
    # url — адрес своего сервера (например http://127.0.0.1:7860), key — ключ (если нужен), model — имя модели (для openai)
    # мощность рисования (встроенные модели): power — auto (по видеокарте) | max | balanced | low | cpu; quality — auto | fast | normal | high;
    # vram_gb — объём видеопамяти вручную (0 = определить самой); threads — потоки процессора (0 = авто); unload_llm — выгружать текстовую модель из видеопамяти на время рисования: auto | yes | no
    "images": {"provider": "auto", "url": "", "key": "", "model": "", "local_model": "", "director": True, "variants": 4, "draw_variants": 1, "ask_count": True, "confirm": True, "check": True, "rounds": 3, "hires": True,
               "power": "auto", "quality": "auto", "vram_gb": 0, "threads": 0, "unload_llm": "auto"},
    # бесплатные облачные модели (свои ключи): strategy — failover (по порядку, при исчерпании лимита — следующий) | spread (по кругу);
    # order — порядок провайдеров; providers — {id: {"enabled","key","model","url","name"}} (см. cloud.PRESETS)
    "cloud": {"strategy": "failover", "order": [], "providers": {}},
    # persona.rude — дерзкая Карина 18+ (злится, матерится, шутит пошло) в обычном чате и в готовых ответах «как дела», «кто ты» (см. prompts.PERSONA_RUDE)
    "persona": {"rude": False},
    # свой VPN/прокси (karina/tunnel.py): servers — ссылки на серверы (с паролями, в окно не отдаются), mode — vpn | proxy, scope — all | apps, apps — имена exe, confirm — спрашивать «да/нет» голосом
    "tunnel": {"servers": [], "sub_url": "", "active": "", "mode": "vpn", "scope": "all", "apps": [], "confirm": True, "direct_ru": False, "port": 10808},
    "updates": {"auto": True},   # проверять новую версию при запуске
    "models": {"active": "", "gpu": "auto", "ctx": 0, "threads": 0},
    # Микшер Bogestv0 MIXER (удалённый пульт по WebSocket): host/port/pin пустые или 0 = взять из настроек самого микшера;
    # step_db — на сколько дБ меняет «тише/громче»; aliases — свои слова для дорожек {"Игры": ["варзон"]}
    "mixer": {"enabled": True, "prefer": False, "host": "127.0.0.1", "port": 0, "pin": "", "step_db": 3.0, "aliases": {}},
    # Bogestv0 VPN: расширение само подключается к Карине на 127.0.0.1:port (только с адреса расширения) и ждёт команды «включи / выключи впн»
    "vpn": {"enabled": True, "port": 47835},
    # Профиль: имя, дата рождения, город, питомцы. google.client_id / client_secret — ваш OAuth-клиент для входа через Google
    "profile": {"name": "", "birth_day": 0, "birth_month": 0, "birth_year": 0, "city": "", "pets": [], "google": {}, "last_greeted": "", "share_with_llm": True},
    "google": {"client_id": "", "client_secret": ""},
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
