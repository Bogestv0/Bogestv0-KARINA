"""Инструменты острова: заметки, напоминания и таймер, погода на неделю, история буфера обмена, громкость,
переключатели системы, снимок экрана. Здесь нет окон: всё можно проверить тестами, системные вызовы работают только в Windows."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000

CHARACTERS = ("logo", "eyes", "spark", "bot", "cat", "blob", "pixel")
HOLIDAY_MODES = ("dates", "always", "off")
GARLANDS = ("off", "blink", "wave")
ISLAND_DEFAULTS = {"character": "logo", "holiday": "dates", "snow": True, "garland": "off", "city": ""}


# ---------------------------------------------------------------- настройки острова
def clean_settings(patch: dict, old: dict | None = None) -> dict:
    """Проверяем значения: в config.json не должно попасть мусора."""
    out = dict(ISLAND_DEFAULTS)
    out.update({k: v for k, v in (old or {}).items() if k in ISLAND_DEFAULTS})
    p = patch or {}
    if p.get("character") in CHARACTERS:
        out["character"] = p["character"]
    if p.get("holiday") in HOLIDAY_MODES:
        out["holiday"] = p["holiday"]
    if p.get("garland") in GARLANDS:
        out["garland"] = p["garland"]
    if "snow" in p:
        out["snow"] = bool(p["snow"])
    if "city" in p:
        out["city"] = str(p["city"] or "").strip()[:60]
    return out


# ---------------------------------------------------------------- хранилище (заметка, напоминания, таймер)
class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA / "island.json")
        self._lock = threading.Lock()
        self.data = {"note": "", "reminders": [], "timer": None, "seq": 0}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                self.data.update({k: d[k] for k in self.data if k in d})
        except Exception:
            pass

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    # заметка
    def note_get(self) -> str:
        return self.data["note"]

    def note_set(self, text: str):
        with self._lock:
            self.data["note"] = str(text or "")[:50000]
            self._save()

    # напоминания
    def reminders(self) -> list[dict]:
        return sorted(self.data["reminders"], key=lambda r: r["at"])

    def reminder_add(self, what: str, at: float, repeat: str = "") -> dict:
        with self._lock:
            self.data["seq"] += 1
            r = {"id": self.data["seq"], "what": what, "at": float(at), "repeat": repeat}
            self.data["reminders"].append(r)
            self._save()
            return r

    def reminder_del(self, rid: int) -> bool:
        with self._lock:
            n = len(self.data["reminders"])
            self.data["reminders"] = [r for r in self.data["reminders"] if r["id"] != rid]
            self._save()
            return len(self.data["reminders"]) != n

    def due_reminders(self, now: float | None = None) -> list[dict]:
        """Сработавшие напоминания: разовые удаляются, ежедневные переносятся на завтра."""
        now = now or time.time()
        fired = []
        with self._lock:
            keep = []
            for r in self.data["reminders"]:
                if r["at"] <= now:
                    fired.append(dict(r))
                    if r.get("repeat") == "daily":
                        while r["at"] <= now:
                            r["at"] += 86400
                        keep.append(r)
                else:
                    keep.append(r)
            if fired:
                self.data["reminders"] = keep
                self._save()
        return fired

    # таймер
    def timer_get(self) -> dict | None:
        t = self.data.get("timer")
        return dict(t) if t else None

    def timer_start(self, seconds: int) -> dict:
        seconds = max(1, min(int(seconds), 24 * 3600))
        with self._lock:
            self.data["timer"] = {"end": time.time() + seconds, "total": seconds}
            self._save()
            return dict(self.data["timer"])

    def timer_stop(self):
        with self._lock:
            self.data["timer"] = None
            self._save()

    def timer_due(self, now: float | None = None) -> bool:
        t = self.data.get("timer")
        if t and t["end"] <= (now or time.time()):
            self.timer_stop()
            return True
        return False


# ---------------------------------------------------------------- разбор напоминания из текста
_UNITS = (("сек", 1), ("мин", 60), ("час", 3600))


def _clean_what(text: str, spans: list[tuple[int, int]]) -> str:
    out, last = [], 0
    for a, b in sorted(spans):
        out.append(text[last:a])
        last = max(last, b)
    out.append(text[last:])
    s = " ".join("".join(out).split())
    s = re.sub(r"^(?:напомни(?:ть)?(?:\s+мне)?|не\s+забыть|надо|нужно)\b[\s,:-]*", "", s, flags=re.I).strip(" ,.:;-")
    return (s[:1].upper() + s[1:]) if s else "Напоминание"


def parse_reminder(text: str, now: float | None = None) -> dict | None:
    """«позвонить маме завтра в 9», «через 20 минут выключить плиту», «таблетки каждый день в 23:00»
    → {"what", "at", "repeat"} или None, если время не нашли."""
    t = " ".join(str(text or "").split())
    if not t:
        return None
    now = now or time.time()
    low = t.lower()
    spans: list[tuple[int, int]] = []
    repeat = ""
    m = re.search(r"\bкаждый\s+день\b|\bежедневно\b", low)
    if m:
        repeat = "daily"
        spans.append(m.span())
    m = re.search(r"\bчерез\s+(\d{1,4})\s*(сек\w*|мин\w*|час\w*)", low)
    if m:
        mult = next((k for p, k in _UNITS if m.group(2).startswith(p)), 60)
        spans.append(m.span())
        return {"what": _clean_what(t, spans), "at": now + int(m.group(1)) * mult, "repeat": repeat}
    m = re.search(r"\b(?:в|к|на)\s+(\d{1,2})(?:[:.\s](\d{2}))?(?!\d)(?:\s*(утра|вечера|дня|ночи))?(?!\s*(?:мин|час|сек))", low)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    part = m.group(3)
    if part in ("вечера", "дня") and h < 12:
        h += 12 if (part == "вечера" or h < 7) else 0
    if part == "ночи" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    spans.append(m.span())
    days = 0
    for word, d in (("послезавтра", 2), ("завтра", 1), ("сегодня", 0)):
        mm = re.search(r"\b" + word + r"\b", low)
        if mm:
            days = d
            spans.append(mm.span())
            break
    base = datetime.fromtimestamp(now)
    when = (base + timedelta(days=days)).replace(hour=h, minute=mi, second=0, microsecond=0)
    if when.timestamp() <= now:
        when += timedelta(days=1)
    return {"what": _clean_what(t, spans), "at": when.timestamp(), "repeat": repeat}


def when_text(at: float, now: float | None = None) -> str:
    now = now or time.time()
    d, n = datetime.fromtimestamp(at), datetime.fromtimestamp(now)
    day = "сегодня" if d.date() == n.date() else "завтра" if d.date() == (n + timedelta(days=1)).date() else d.strftime("%d.%m")
    return f"{day} в {d:%H:%M}"


# ---------------------------------------------------------------- погода на неделю
def _icon(code: int) -> str:
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "🌧️"
    if 71 <= code <= 77 or code in (85, 86):
        return "❄️"
    if code >= 95:
        return "⛈️"
    return "⛅"


def weather_week(city_name: str) -> dict:
    from . import weather
    city = weather.find_city(city_name)
    data = weather._get(weather.FORECAST, {
        "latitude": city["latitude"], "longitude": city["longitude"], "timezone": "auto", "forecast_days": 7, "wind_speed_unit": "ms",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
    c, d = data["current"], data["daily"]
    codes = getattr(weather, "_CODES", {})
    days = []
    for i, date in enumerate(d["time"]):
        dt = datetime.strptime(date, "%Y-%m-%d")
        days.append({"day": "Сегодня" if i == 0 else "Завтра" if i == 1 else ("пн вт ср чт пт сб вс".split())[dt.weekday()] + dt.strftime(" %d.%m"),
                     "icon": _icon(d["weather_code"][i]), "text": codes.get(d["weather_code"][i], "облачно"),
                     "hi": round(d["temperature_2m_max"][i]), "lo": round(d["temperature_2m_min"][i]),
                     "prob": (d.get("precipitation_probability_max") or [None] * 7)[i]})
    return {"city": city.get("name", city_name), "now": {"t": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "icon": _icon(c["weather_code"]), "text": codes.get(c["weather_code"], "облачно"), "wind": round(c["wind_speed_10m"])}, "days": days}


# ---------------------------------------------------------------- история буфера обмена
class ClipHistory:
    """Последние скопированные тексты. Хранятся только в памяти и пропадают при выходе из Карины."""

    def __init__(self, limit: int = 30):
        self.items: deque[str] = deque(maxlen=limit)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, text: str):
        text = str(text or "")
        if not text.strip() or len(text) > 4000:
            return
        if text in self.items:
            self.items.remove(text)
        self.items.appendleft(text)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="karina-clip")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        try:
            import pyperclip
        except Exception:
            return
        last = None
        while not self._stop.wait(1.2):
            try:
                cur = pyperclip.paste()
            except Exception:
                continue
            if cur != last:
                last = cur
                self.add(cur)

    def get(self) -> list[str]:
        return list(self.items)

    def clear(self):
        self.items.clear()


# ---------------------------------------------------------------- система (Windows)
def _run(args: list[str], timeout: float = 6.0) -> str:
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW if IS_WIN else 0)
        out = r.stdout or b""
        for enc in ("utf-8", "cp866", "cp1251"):
            try:
                return out.decode(enc)
            except UnicodeDecodeError:
                continue
        return out.decode("utf-8", "ignore")
    except Exception:
        return ""


def battery() -> dict | None:
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class SPS(ctypes.Structure):
            _fields_ = [("AC", wintypes.BYTE), ("Flag", wintypes.BYTE), ("Pct", wintypes.BYTE), ("Saver", wintypes.BYTE),
                        ("Life", wintypes.DWORD), ("Full", wintypes.DWORD)]
        s = SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
        if s.Flag == 128 or s.Pct == 255:                # батареи нет (стационарный компьютер)
            return {"has": False}
        return {"has": True, "pct": int(s.Pct), "plugged": s.AC == 1}
    except Exception:
        return None


_PERS = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def dark_get() -> bool | None:
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERS) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return None


def dark_set(on: bool) -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg, ctypes
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERS, 0, winreg.KEY_SET_VALUE) as k:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0 if on else 1)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 2, 2000, None)     # WM_SETTINGCHANGE
        return True
    except Exception:
        return False


def wifi_ssid() -> str | None:
    """Имя сети Wi-Fi, если подключены (строки netsh зависят от языка Windows, поэтому ищем по «SSID»)."""
    if not IS_WIN:
        return None
    for line in _run(["netsh", "wlan", "show", "interfaces"]).splitlines():
        m = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return None


SETTINGS_PAGES = {"wifi": "ms-settings:network-wifi", "bluetooth": "ms-settings:bluetooth"}


def open_settings(page: str) -> bool:
    uri = SETTINGS_PAGES.get(page)
    if not uri or not IS_WIN:
        return False
    try:
        os.startfile(uri)
        return True
    except Exception:
        return False


def power(action: str) -> bool:
    if not IS_WIN:
        return False
    try:
        if action == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
        elif action == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=NO_WINDOW)
        elif action == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "5"], creationflags=NO_WINDOW)
        elif action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "5"], creationflags=NO_WINDOW)
        else:
            return False
        return True
    except Exception:
        return False


def screenshots_dir() -> Path:
    return Path.home() / "Pictures" / "Karina"


def save_screenshot() -> str:
    """Снимок основного экрана в Изображения\\Karina; возвращает путь к файлу."""
    from PIL import ImageGrab
    d = screenshots_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / time.strftime("screen-%Y%m%d-%H%M%S.png")
    ImageGrab.grab().save(p)
    return str(p)
