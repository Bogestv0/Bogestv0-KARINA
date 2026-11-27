"""Нагрузка CPU/GPU/RAM и определение игры для игрового режима острова.

Только стандартная библиотека: psutil, WMI и pynvml в проект не добавляем.
CPU — счётчики GetSystemTimes, RAM — GlobalMemoryStatusEx, GPU и его температура —
nvidia-smi, температура CPU — датчик OpenHardwareMonitor/LibreHardwareMonitor, если он есть.
FPS берём только из внешнего источника (PresentMon или команда из настройки окружения):
у Windows самого по себе «FPS игры» нет, поэтому без такого источника плашка покажет «—».
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes

NO_WINDOW = 0x08000000

# Игры, которые узнаём по имени процесса. Своего список можно задать в настройках
# острова (island.game_apps) — имена процессов или куски пути.
GAMES = (
    "cyberpunk2077", "witcher3", "cs2", "csgo", "hl2", "dota2", "arma3", "gta5", "gtaiv",
    "rdr2", "fortnite", "valorant", "eldenring", "sekiro", "darksouls", "liesofp",
    "forzahorizon4", "forzahorizon5", "assettocorsa", "beamng", "minecraft", "roblox",
    "pubg", "apex", "overwatch", "destiny2", "warframe", "warthunder", "battlefield",
    "starfield", "skyrim", "fallout4", "hogwartslegacy", "baldursgate3", "stardewvalley",
    "terraria", "suicidebarbarian", "hades", "hollowknight", "kingdomcome", "thefinals",
    "carbon", "trackmania", "dirt", "f1_", "nba2k", "fifa", "madden",
)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
_u32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class Snapshot:
    """Держит прошлые значения счётчиков и кэш редких/медленных опросов."""

    def __init__(self):
        self._cpu_prev = None
        self._gpu = (None, None, 0.0)
        self._cpu_t = (None, 0.0)
        self._cpu_t_ns = "root\\OpenHardwareMonitor"
        self._fps = (None, 0.0)
        self._gpu_cmd = 0.0
        self._lock = threading.Lock()

    # ---- CPU / RAM ----
    def cpu(self):
        if not _k32:
            return None
        ft = wintypes.FILETIME
        idle, kern, user = ft(), ft(), ft()
        # GetSystemTimes(idle, kernel, user): kernel уже включает idle
        if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None
        q = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
        total = q(kern) + q(user)
        busy = total - q(idle)
        with self._lock:
            prev = self._cpu_prev
            self._cpu_prev = (busy, total)
        if not prev or total <= prev[1]:
            return None
        return max(0.0, min(100.0, (busy - prev[0]) * 100.0 / (total - prev[1])))

    def ram(self):
        if not _k32:
            return None
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if not _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return float(m.dwMemoryLoad)

    # ---- GPU: загрузка и температура из nvidia-smi ----
    def gpu(self):
        now = time.monotonic()
        if now - self._gpu[2] < 1.5:
            return self._gpu[0], self._gpu[1]
        util = temp = None
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, errors="replace", timeout=1.5, creationflags=NO_WINDOW)
            if r.returncode == 0:
                nums = re.findall(r"\d+(?:\.\d+)?", r.stdout.splitlines()[0] if r.stdout else "")
                if nums:
                    util = float(nums[0])
                if len(nums) > 1:
                    temp = float(nums[1])
        except Exception:
            pass
        self._gpu = (util, temp, now)
        return util, temp

    # ---- температура CPU: только если стоит OHM/LHM ----
    def cpu_temp(self):
        now = time.monotonic()
        if now - self._cpu_t[1] < 10:
            return self._cpu_t[0]
        val = None
        template = ("Get-CimInstance -Namespace {ns} -ClassName Sensor "
                    "| Where-Object {{$_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU|Package'}} "
                    "| Select-Object -First 1 -ExpandProperty Value")
        order = [self._cpu_t_ns] + [n for n in ("root\\OpenHardwareMonitor", "root\\LibreHardwareMonitor") if n != self._cpu_t_ns]
        for ns in order:
            try:
                r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                    template.format(ns=ns)],
                                   capture_output=True, text=True, errors="replace", timeout=1.5, creationflags=NO_WINDOW)
                s = r.stdout.strip().splitlines()
                if s:
                    val = float(re.findall(r"\d+(?:\.\d+)?", s[-1])[0])
                    self._cpu_t_ns = ns
                    break
            except Exception:
                pass
        self._cpu_t = (val, now)
        return val

    # ---- FPS: внешний источник ----
    def fps(self):
        now = time.monotonic()
        if now - self._fps[1] < 0.9:
            return self._fps[0]
        val = None
        fixed = os.environ.get("KARINA_FPS", "").strip()
        cmd = os.environ.get("KARINA_FPS_CMD", "").strip()
        csv = os.environ.get("KARINA_FPS_CSV", "").strip()
        try:
            if fixed:
                val = float(fixed)
            elif cmd:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                   errors="replace", timeout=2, creationflags=NO_WINDOW)
                nums = re.findall(r"\d+(?:\.\d+)?", r.stdout.strip())
                if nums:
                    val = float(nums[-1])
            elif csv and os.path.isfile(csv):
                with open(csv, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-8:]
                for line in reversed(lines):
                    nums = re.findall(r"\d+(?:\.\d+)?", line)
                    # PresentMon: первые поля — ProcessID/FrameTime/FPS
                    if len(nums) >= 3:
                        val = float(nums[2])
                        break
        except Exception:
            pass
        self._fps = (val, now)
        return val

    # ---- какое окно сейчас сверху ----
    @staticmethod
    def foreground():
        if not _u32 or not _k32:
            return "", ""
        hwnd = _u32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        n = _u32.GetWindowTextLengthW(hwnd) or 0
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = _k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                size = wintypes.DWORD(32768)
                pbuf = ctypes.create_unicode_buffer(size.value)
                if _k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                    exe = pbuf.value
            finally:
                _k32.CloseHandle(h)
        return exe, title


def is_game(exe: str, title: str, settings=None) -> bool:
    low = (exe or "").lower().replace("\\", "/")
    name = low.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    extra = []
    if settings:
        v = settings.get("game_apps") or []
        extra = [str(x).lower() for x in v if str(x).strip()]
    for key in list(extra) + list(GAMES):
        if key in name or key in low:
            return True
    if "minecraft" in (title or "").lower() and name in ("javaw", "minecraftwindows"):
        return True
    return False


def snapshot(settings=None, cache=None):
    """Словарь для事件的 острова: всё JSON-сериализуемо."""
    cache = cache or _CACHE
    cpu = cache.cpu()
    ram = cache.ram()
    gpu, gpu_t = cache.gpu()
    exe, title = cache.foreground()
    enabled = not settings or settings.get("game_stats", True) is not False
    active = enabled and is_game(exe, title, settings)
    return {"ok": True, "active": active,
            "name": (title or exe.rsplit("\\", 1)[-1])[:60] if active else "",
            "fps": cache.fps() if active else None,
            "cpu": cpu, "ram": ram, "gpu": gpu,
            "cpu_t": cache.cpu_temp(), "gpu_t": gpu_t}


_CACHE = Snapshot()
