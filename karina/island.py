"""Динамический остров: маленькая плашка у верхнего края экрана, показывает, что делает Карина
(слушает, думает, говорит), и по нажатию раскрывается в панель с кнопками.

Остров — отдельное безрамочное окно pywebview (ui/island.html). Форму (скруглённую «таблетку») и плавное
изменение размера делаем через Win32 регион, поэтому прозрачность окна не нужна. Само окно при раскрытии и сворачивании
НЕ меняет размер: оно с запасом, под самую большую панель, а видна только та часть, что внутри региона. Анимируется один регион
(дёшево), а WebView2 не перерисовывается на каждом кадре, оттого плавно. Остров можно перетащить: он прилипает к ближайшему краю
экрана (у боковых краёв «стоит», панель открывается к центру экрана) и закрепляется кнопкой в панели.
Не на Windows модуль ничего не делает. Включается в «Настройки → Окно и оформление» (config: ui.island)."""
from __future__ import annotations
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from .config import ROOT
from . import island_tools as tools
from . import screen_tools as st
from . import shelf as shf
from .halo import Halo
from .media import Media

IS_WIN = sys.platform == "win32"
TITLE = "Остров Карины"
SNIP_TITLE = "Область экрана Карины"
SNIP_TIMEOUT = 180       # если выбор области забыли закончить, окно закрывается само

# режим -> (ширина, высота, радиус скругления) в логических пикселях
SIZES = {"mini": (150, 36, 18), "info": (232, 36, 18), "music": (300, 36, 18), "wide": (290, 40, 20), "drop": (300, 96, 30), "full": (480, 330, 30)}
# у левого и правого края плашка «стоит»: те же режимы, но вертикальные (высоту info/music задаёт содержимое). wide, drop и full остаются горизонтальными
VSIZES = {"mini": (36, 76, 18), "info": (48, 120, 24), "music": (48, 112, 24)}
WIDTH_MIN, WIDTH_MAX = 150, 420        # страница сама сообщает нужную ширину для режимов «info» и «music» (по содержимому)
VEXT_MIN, VEXT_MAX = 64, 260           # то же для вертикальной плашки: высота
RADIUS_MAX = 30
TOP_MARGIN = 6          # отступ от края экрана
ANIM_GROW, ANIM_SHRINK, ANIM_FLY = 0.36, 0.28, 0.26      # раскрытие, сворачивание, перелёт к другому краю (секунды)
ANIM_SECONDS = ANIM_GROW
HALO_ANIM_AREA = 60000  # пока контур больше этой площади (px²), на время анимации его прячем: пересчёт сглаженной кромки на каждом кадре слишком дорог
FORWARD_TYPES = ("state", "level", "log")      # какие события главного окна нужны острову
DOCKS = tools.DOCKS


def is_side(dock: str) -> bool:
    return dock in ("left", "right")


def is_vert(dock: str, mode: str) -> bool:
    """Плашка стоит вертикально: остров у бокового края и режим свёрнутый (mini, info, music)."""
    return is_side(dock) and mode in VSIZES


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def mode_size(dock: str, mode: str, ext=None):
    """(ширина, высота, радиус) в логических пикселях. ext — нужный размер по длинной стороне от содержимого (ширина или, у боковых краёв, высота)."""
    if is_vert(dock, mode):
        w, h, r = VSIZES[mode]
        if mode != "mini" and _num(ext):
            h = max(VEXT_MIN, min(VEXT_MAX, float(ext)))
        return w, h, r
    w, h, r = SIZES[mode]
    if mode in ("info", "music", "mini") and _num(ext):
        w = max(WIDTH_MIN, min(WIDTH_MAX, float(ext)))
    return w, h, r


def pill_xy(dock: str, pos: float, w: int, h: int, work, margin: int):
    """Левый верхний угол плашки w×h (физические пиксели): прижата к краю dock с отступом margin, центр на доле pos вдоль края,
    но целиком внутри рабочей области work = (left, top, right, bottom)."""
    L, T, R, B = work
    pos = min(1.0, max(0.0, float(pos)))

    def along(a0, a1, size):
        x = round(a0 + pos * (a1 - a0) - size / 2)
        lo, hi = a0 + margin, a1 - margin - size
        return (a0 + (a1 - a0 - size) // 2) if hi < lo else max(lo, min(hi, x))
    if dock == "bottom":
        return along(L, R, w), B - margin - h
    if dock == "left":
        return L + margin, along(T, B, h)
    if dock == "right":
        return R - margin - w, along(T, B, h)
    return along(L, R, w), T + margin


def snap_dock(cx: float, cy: float, work):
    """К какому краю рабочей области ближе всего точка (cx, cy) и где вдоль этого края она лежит (0..1; у середины прилипает к 0.5)."""
    L, T, R, B = work
    dist = {"left": cx - L, "right": R - cx, "top": cy - T, "bottom": B - cy}
    dock = min(("top", "bottom", "left", "right"), key=lambda k: dist[k])
    pos = (cx - L) / max(1, R - L) if dock in ("top", "bottom") else (cy - T) / max(1, B - T)
    pos = min(1.0, max(0.0, pos))
    return dock, (0.5 if abs(pos - 0.5) < 0.04 else pos)


def union_rect(rects):
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


if IS_WIN:
    import ctypes
    from ctypes import wintypes

    # свои копии библиотек (не ctypes.windll.*): общие функции делит весь процесс, и чужие argtypes ломали главное окно
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    GWL_STYLE, GWL_EXSTYLE = -16, -20
    WS_EX_APPWINDOW = 0x00040000
    WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
    SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_ASYNCWINDOWPOS, SWP_NOSIZE = 0x0010, 0x0020, 0x4000, 0x0001
    HWND_TOPMOST = -1

    class _RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]

    def _fix_types():
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN]
        gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    try:
        _fix_types()
    except Exception:
        pass


def _ease(p: float, grow: bool = True) -> float:
    """Плавное торможение без «перелёта»: раскрытие чуть мягче (четвёртая степень), сворачивание резвее (третья)."""
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** (4 if grow else 3)


class IslandApi:
    """Методы, которые остров вызывает из JavaScript (pywebview.api.<метод>). Ссылки хранятся с «_», чтобы pywebview их не показывал странице."""

    def __init__(self, island: "Island", api, show_main):
        self._island, self._api, self._show_main = island, api, show_main

    # ---- состояние и режимы ----
    def get_state(self):
        a = self._api.assistant
        return {"state": a.state, "sleeping": bool(a.sleeping), "listening": bool(a.listening), "name": a.cfg["name"],
                "settings": self._island.settings(), "volume": self.volume_get(), "admin": self._island.is_admin(),
                "layout": self._island.layout_info()}

    def set_mode(self, mode: str, width=None):
        """Страница просит режим; в ответ получает, где внутри окна рисовать плашку (см. Island._target)."""
        return self._island.set_mode(str(mode), width) or True

    # ---- положение: перетаскивание, закрепление ----
    def drag(self):
        """Страница заметила, что плашку потащили: пока кнопка мыши нажата, окно ходит за курсором, потом прилипает к краю."""
        return self._island.drag()

    def pin_set(self, on):
        return self._island.set_pinned(bool(on))

    def dock_reset(self):
        return self._island.dock_reset()

    def set_listening(self, on: bool):
        return self._api.set_listening(bool(on))

    def wake_up(self):
        return self._api.wake_up()

    def hush(self):
        threading.Thread(target=self._api.assistant.hush, daemon=True).start()
        return True

    def open_main(self):
        threading.Thread(target=self._show_main, daemon=True).start()
        return True

    # ---- полка и ярлыки ----
    def drop_target(self, name):
        """Страница сообщает, над чем сейчас файл: «link» — плитка «+» (ярлык), иначе полка."""
        self._island._drop_target = "link" if name == "link" else "shelf"
        return True

    def shelf_get(self):
        return {"items": self._island.shelf.view(), "admin": self._island.is_admin()}

    def shelf_thumb(self, item_id):
        it = self._island.shelf.get(int(item_id))
        return shf.thumb(it["path"]) if it and it.get("type") == "path" else ""

    def shelf_open(self, item_id):
        it = self._island.shelf.get(int(item_id))
        if not it:
            return {"ok": False, "error": "Нет такого элемента."}
        if it.get("type") == "text":
            st.clip_set_text(it.get("text", ""))
            return {"ok": True, "copied": True}
        try:
            if shf.open_target(it["path"]):
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__}
        return {"ok": False, "error": "Файл не найден: возможно, его переместили или удалили."}

    def shelf_reveal(self, item_id):
        it = self._island.shelf.get(int(item_id))
        return bool(it and it.get("type") == "path" and shf.reveal(it["path"]))

    def shelf_copy(self, item_id):
        it = self._island.shelf.get(int(item_id))
        if not it:
            return False
        return st.clip_set_text(it.get("text") if it.get("type") == "text" else it.get("path", ""))

    def shelf_del(self, item_id):
        return self._island.shelf.remove(int(item_id))

    def shelf_clear(self):
        self._island.shelf.clear()
        return True

    def shelf_paste(self):
        """«Из буфера»: файлы, скопированные в проводнике (Ctrl+C), либо путь или текст из буфера."""
        paths = shf.clipboard_paths()
        if paths:
            return {"ok": True, "n": self._island.shelf.add_paths(paths)}
        text = st.clip_get_text().strip()
        if not text:
            return {"ok": False, "error": "В буфере обмена пусто: скопируйте файл (Ctrl+C) или текст."}
        one = text.strip('"')
        if "\n" not in one and os.path.exists(one):
            return {"ok": True, "n": self._island.shelf.add_paths([one])}
        return {"ok": self._island.shelf.add_text(text), "n": 1, "text": True}

    def shelf_add_text(self, text):
        return self._island.shelf.add_text(str(text or ""))

    def shelf_pick(self, kind="file"):
        """Окно выбора файлов или папки → на полку."""
        got = self._island.pick(kind)
        return {"ok": True, "n": self._island.shelf.add_paths(got)} if got else {"ok": False, "n": 0}

    def shelf_drag(self, item_id):
        """Начать перетаскивание файла с полки в другое окно (письмо, чат, папка)."""
        return self._island.drag_out(int(item_id))

    def link_pick(self, kind="file"):
        got = self._island.pick(kind)
        return self._island.links_add(got) if got else {"ok": False, "n": 0}

    def link_add(self, text):
        t = str(text or "").strip()
        if shf.is_url(t) or os.path.exists(t.strip('"')):
            return self._island.links_add([t])
        return {"ok": False, "n": 0, "error": "Нужна ссылка (https://…) или путь к файлу или папке."}

    def link_run(self, i):
        links = self._island.settings().get("links", [])
        try:
            p = links[int(i)]["path"]
        except Exception:
            return {"ok": False, "error": "Нет такого ярлыка."}
        try:
            if shf.open_target(p):
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__}
        return {"ok": False, "error": "Не нашла: «" + p + "». Возможно, его переместили."}

    def links_set(self, items):
        return self._island.settings_set({"links": items if isinstance(items, list) else []}).get("links", [])

    def link_kinds(self):
        """Вид значка для каждого ярлыка (папка, программа, документ…)."""
        return [shf.path_kind(l["path"]) for l in self._island.settings().get("links", [])]

    def repaint(self):
        """Страница острова заметила, что не рисуется (нет кадров): просим окно перерисоваться."""
        threading.Thread(target=self._island.repaint, daemon=True, name="karina-island-repaint").start()
        return True

    def settings_set(self, patch: dict):
        return self._island.settings_set(patch if isinstance(patch, dict) else {})

    # ---- музыка, заряд ----
    def media_get(self):
        return self._island.media.info or {"status": "none"}

    def media_cmd(self, name: str):
        return self._island.media.command(str(name))

    def battery_get(self):
        return tools.battery()

    # ---- громкость ----
    def volume_get(self):
        try:
            from . import actions
            return actions._get_volume()
        except Exception:
            return None

    def volume_set(self, pct):
        try:
            from . import actions
            return bool(actions._set_volume(int(pct)))
        except Exception:
            return False

    def mute_set(self, on):
        try:
            from . import actions
            actions._set_mute(bool(on))
            return True
        except Exception:
            return False

    # ---- заметка, напоминания, таймер ----
    def note_get(self):
        return self._island.store.note_get()

    def note_set(self, text):
        self._island.store.note_set(text)
        return True

    def reminders_get(self):
        return [{**r, "when": tools.when_text(r["at"])} for r in self._island.store.reminders()]

    def reminder_add(self, text):
        r = tools.parse_reminder(str(text or ""))
        if not r:
            return {"ok": False, "error": "Не поняла время. Например: «позвонить маме завтра в 9» или «через 20 минут выключить плиту»."}
        r = self._island.store.reminder_add(r["what"], r["at"], r["repeat"])
        return {"ok": True, "reminder": {**r, "when": tools.when_text(r["at"])}}

    def reminder_del(self, rid):
        return self._island.store.reminder_del(int(rid))

    def timer_get(self):
        t = self._island.store.timer_get()
        return {"left": max(0, int(t["end"] - time.time())), "total": t["total"]} if t else None

    def timer_start(self, seconds):
        self._island.store.timer_start(int(seconds))
        return self.timer_get()

    def timer_stop(self):
        self._island.store.timer_stop()
        return True

    # ---- погода ----
    def weather_get(self, city=""):
        name = str(city or "").strip()
        if name:
            self._island.settings_set({"city": name})
        name = name or self._island.settings().get("city") or self._profile_city()
        if not name:
            return {"ok": False, "need_city": True}
        try:
            return {"ok": True, **tools.weather_week(name)}
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__, "city": name}

    def _profile_city(self):
        try:
            from . import profile as prof, weather
            return weather.clean_city(prof.get(self._api.cfg)["city"])
        except Exception:
            return ""

    # ---- система ----
    def sys_get(self):
        vpn = False
        try:
            vpn = bool(self._api.assistant.tunnel.status().get("running"))
        except Exception:
            pass
        return {"battery": tools.battery(), "dark": tools.dark_get(), "wifi": tools.wifi_ssid(), "vpn": vpn,
                "win": sys.platform == "win32"}

    def sys_action(self, name: str):
        if name == "dark":
            ok = tools.dark_set(not bool(tools.dark_get()))
            self._island.reassert()                  # после смены темы Windows у окна острова слетала форма: проверяем и возвращаем
            return ok
        if name in ("wifi", "bluetooth"):
            return tools.open_settings(name)
        if name == "vpn":
            on = bool(self._api.assistant.tunnel.status().get("running"))
            r = self._api.tunnel_stop() if on else self._api.tunnel_start()
            return {"ok": bool(r.get("ok")), "error": r.get("error", "")}
        if name in ("lock", "sleep", "restart", "shutdown"):
            return tools.power(name)
        return False

    # ---- буфер обмена и снимок экрана ----
    def clip_get(self):
        return self._island.clip.get()

    def clip_copy(self, i):
        items = self._island.clip.get()
        try:
            import pyperclip
            pyperclip.copy(items[int(i)])
            return True
        except Exception:
            return False

    def clip_clear(self):
        self._island.clip.clear()
        return True

    def screenshot(self):
        return self._island.screenshot()

    def open_shots(self):
        try:
            d = tools.screenshots_dir()
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))
            return True
        except Exception:
            return False

    # ---- инструменты: область, текст с экрана, пипетка ----
    def snip_start(self, mode: str):
        return self._island.snip(str(mode))

    def qr_make(self, text):
        try:
            return {"ok": True, "url": st.png_data_url(st.qr_png(str(text or "")))}
        except st.ToolError as e:
            return {"ok": False, "error": str(e)}

    def qr_clipboard(self):
        return st.clip_get_text()[:1500]

    def qr_copy(self, text):
        try:
            from PIL import Image
            import io
            ok = st.clip_set_image(Image.open(io.BytesIO(st.qr_png(str(text or "")))))
            return {"ok": ok, "error": "" if ok else "Не удалось скопировать картинку."}
        except st.ToolError as e:
            return {"ok": False, "error": str(e)}

    def qr_save(self, text):
        try:
            p = st.pictures_dir()
            p.mkdir(parents=True, exist_ok=True)
            path = p / time.strftime("qr-%Y%m%d-%H%M%S.png")
            path.write_bytes(st.qr_png(str(text or "")))
            return {"ok": True, "path": str(path)}
        except (st.ToolError, OSError) as e:
            return {"ok": False, "error": str(e)}

    def qr_scan(self):
        return self._island.qr_scan()

    def clean_paste(self):
        try:
            text = st.paste_clean(self._island.focus)
            return {"ok": True, "n": len(text)}
        except st.ToolError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": "Не получилось вставить: " + (str(e) or e.__class__.__name__)}

    def rec_status(self):
        return self._island.rec.status()

    def rec_toggle(self):
        return self._island.rec_toggle()

    def open_videos(self):
        try:
            d = st.videos_dir()
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))
            return True
        except Exception:
            return False

    def layout_get(self):
        return self._island.focus.layout_get()

    def layout_toggle(self):
        f = self._island.focus
        f.layout_set(st.next_layout(f.layout_get()))
        time.sleep(0.2)
        return f.layout_get()

    def command_run(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            return False
        return self._api.send_text(text)

    def custom_set(self, items):
        return self._island.settings_set({"tools": items if isinstance(items, list) else []}).get("tools", [])

    def custom_run(self, i):
        items = self._island.settings().get("tools", [])
        try:
            cmd = items[int(i)]["cmd"]
        except Exception:
            return False
        kind = st.custom_kind(cmd)
        try:
            if kind == "url":
                import webbrowser
                webbrowser.open(cmd if cmd.lower().startswith("http") else "https://" + cmd)
            elif kind == "path":
                os.startfile(os.path.expanduser(cmd.strip('"')))
            else:
                self._api.send_text(cmd)
            return True
        except Exception:
            return False

    def custom_open(self, url):
        u = str(url or "").strip()
        if not st.is_url(u):
            return False
        import webbrowser
        return bool(webbrowser.open(u if u.lower().startswith("http") else "https://" + u))

    def copy_text(self, text):
        return st.clip_set_text(str(text or ""))

    def speak(self, text):
        self._island._say(str(text or "")[:1200])
        return True

    def translate(self, text):
        t = str(text or "").strip()[:1500]
        if not t:
            return False
        import re
        to = "английский" if re.search(r"[а-яё]", t, re.I) else "русский"
        return self._api.send_text(f"переведи на {to}: {t}")


class SnipApi:
    """Методы окна выбора области (ui/snip.html)."""

    def __init__(self, island: "Island", snip: dict):
        self._island, self._snip = island, snip

    def image(self):
        s = self._snip
        return {"url": st.image_data_url(s["img"]), "mode": s["mode"]}

    def ready(self):
        self._snip["ready"].set()
        return True

    def done(self, payload):
        threading.Thread(target=self._island._snip_finish, args=(self._snip, payload if isinstance(payload, dict) else {}), daemon=True).start()
        return True


class Island:
    def __init__(self, cfg, api, show_main, trace=lambda s: None):
        self.cfg, self.api, self.show_main, self.trace = cfg, api, show_main, trace
        self.window = None
        self.ready = False
        self._hwnd = 0
        self._mode, self._ext = "mini", None  # какой режим просит страница и его размер по длинной стороне
        self._dock, self._pos, self._mon = "top", 0.5, None    # край, доля вдоль края, левый верхний угол монитора (None — основной)
        self._pinned = False                 # закреплён: стоит на своём месте и после перезапуска, перетащить нельзя
        self._rect_cur = None                # видимая плашка сейчас (x, y, w, h, радиус) в физических пикселях экрана; во время анимации меняется
        self._win = None                     # где стоит окно (x, y, w, h): с запасом, под самую большую панель
        self._busy = False                   # плашку тащат или она летит к краю: режимы пока только запоминаем
        self._animating = False
        self._last_fix = 0.0
        self._gen = 0
        self._lock = threading.Lock()
        self._q: queue.Queue | None = None
        self.store = tools.Store()
        self.clip = tools.ClipHistory()
        self._stop_tick = threading.Event()
        self._stopping = False               # True, пока мы сами закрываем окно: иначе «закрытие» отменяется
        self._halo: Halo | None = None       # сглаженный контур под островом (karina/halo.py)
        self.media = Media(self._on_media, trace)    # что играет сейчас (karina/media.py)
        self.rec = st.Recorder()                     # запись экрана (karina/screen_tools.py)
        self.focus = st.Focus()                      # последнее чужое окно: для вставки и раскладки
        self._rec_shown = False                      # страница уже знает, что запись идёт
        self._snip: dict | None = None               # открытое окно выбора области
        self._painting = False                       # окно показано средствами WinForms (иначе WebView2 считает себя невидимым и не рисует)
        self.shelf = shf.Shelf()                     # полка: файлы и текст «в руках» (karina/shelf.py)
        self._drop_target = "shelf"                  # куда упадёт файл: полка или новый ярлык («+»)
        self._drop_hooked = False
        self._drag_until = 0.0                       # пока тащим файл с полки наружу, свои же «падения» не принимаем
        self._hidden_by_us = False                   # остров спрятан намеренно (снимок экрана, выбор области): страница в это время «не рисуется» законно
        self._last_repaint = 0.0
        self._repaints = 0

    # ---- включение / выключение ----
    @property
    def running(self) -> bool:
        return self.window is not None

    def apply(self, enabled: bool):
        if enabled and not self.running:
            self.start()
        elif not enabled and self.running:
            self.stop()

    def start(self) -> bool:
        if not IS_WIN:
            self.trace("Остров: работает только в Windows")
            return False
        if self.window is not None:
            return True
        try:
            import webview
            # Окно создаём ПОКАЗАННЫМ (сразу наверху по центру), а не спрятанным: у спрятанного окна WebView2 запускается с выключенной
            # видимостью, страница работает (скрипт даже меняет размер плашки), но не рисуется: плашка остаётся чёрной и лишь на миг
            # вспыхивает при смене размера. Стили «не приложение» (без значка на панели задач) ставим сразу после создания.
            self._load_place()
            x0, y0 = self._start_xy()
            self._prev_fg = 0
            try:
                self._prev_fg = int(user32.GetForegroundWindow() or 0)
            except Exception:
                pass
            kw = dict(url=str(ROOT / "ui" / "island.html"), js_api=IslandApi(self, self.api, self.show_main),
                      width=SIZES["mini"][0], height=SIZES["mini"][1], x=x0, y=y0, min_size=(40, 20),
                      frameless=True, easy_drag=False, shadow=False, on_top=True, background_color="#000000", focus=False)
            w = None
            for drop in ((), ("focus",), ("shadow", "focus"), ("shadow", "easy_drag", "focus"),
                         ("shadow", "easy_drag", "min_size", "focus"), ("shadow", "easy_drag", "min_size", "on_top", "focus"),
                         ("x", "y", "shadow", "easy_drag", "min_size", "on_top", "focus")):
                try:
                    w = webview.create_window(TITLE, **{k: v for k, v in kw.items() if k not in drop})
                    break
                except TypeError as e:
                    self.trace(f"Остров: create_window без {', '.join(drop) or '-'} ({e})")
            if w is None:
                return False
            self.window, self.ready, self._hwnd, self._painting, self._drop_hooked = w, False, 0, True, False       # окно уже показано средствами WinForms
            self._halo = Halo(self.trace)
            self._q = queue.Queue(maxsize=200)
            threading.Thread(target=self._pump, args=(w, self._q), daemon=True, name="karina-island").start()
            self._stop_tick = threading.Event()
            threading.Thread(target=self._tick, args=(self._stop_tick,), daemon=True, name="karina-island-tick").start()
            threading.Thread(target=self._guard, args=(w, self._stop_tick), daemon=True, name="karina-island-guard").start()
            self.clip.start()
            self._sync_media()
            try:
                w.events.shown += self._on_shown
                w.events.loaded += self._on_loaded
                w.events.closing += self._on_closing          # крестик/Alt+F4 остров не закрывают (иначе он «пропадал» и больше не открывался)
                w.events.closed += lambda: self._on_closed(w)
            except Exception as e:
                self.trace(f"Остров: не подписался на события ({e})")
            # события «показано»/«загружено» могли сработать до подписки — поэтому ещё и сами проверяем окно в отдельном потоке
            threading.Thread(target=self._init_loop, args=(w,), daemon=True, name="karina-island-init").start()
            self.trace("Остров: окно создано")
            return True
        except Exception as e:
            w0, self.window = self.window, None
            self.trace(f"Остров: не удалось создать окно ({e.__class__.__name__}: {e})")
            if w0 is not None:
                try:
                    w0.destroy()
                except Exception:
                    pass
            return False

    # ---- полка: приём файлов, выбор файлов, перетаскивание наружу ----
    def is_admin(self) -> bool:
        try:
            from . import elevate
            return bool(elevate.is_admin())
        except Exception:
            return False

    def _hook_drop(self):
        """Приём файлов, брошенных на остров. Пути файлов даёт pywebview (pywebviewFullPath); без него (старая версия) полка принимает только текст."""
        if self._drop_hooked or self.window is None:
            return
        try:
            from webview.dom import DOMEventHandler
            self.window.dom.document.events.drop += DOMEventHandler(self._on_drop, True, True)
            self._drop_hooked = True
            self.trace("Остров: приём файлов подключён")
        except Exception as e:
            self.trace(f"Остров: приём файлов недоступен ({e.__class__.__name__}: {e}); нужен pywebview 5 или новее")

    def _on_drop(self, e):
        if time.time() < self._drag_until:
            return
        try:
            files = ((e or {}).get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files if isinstance(f, dict) and f.get("pywebviewFullPath")]
        except Exception:
            paths, files = [], []
        if not paths:
            if files:
                self.push({"type": "shelf", "n": 0, "error": "Не получилось узнать путь файла: обновите pywebview (нужна версия 5 или новее)."})
            return
        target, self._drop_target = self._drop_target, "shelf"
        if target == "link":
            r = self.links_add(paths)
            self.push({"type": "shelf", "n": r.get("n", 0), "link": True, "links": self.settings().get("links", [])})
        else:
            self.push({"type": "shelf", "n": self.shelf.add_paths(paths)})

    def links_add(self, paths) -> dict:
        cur = self.settings().get("links", [])
        new = [{"path": str(p).strip().strip('"')} for p in paths if p]
        out = self.settings_set({"links": cur + new}).get("links", [])
        return {"ok": len(out) > len(cur), "n": len(out) - len(cur), "links": out, "full": len(cur) >= shf.MAX_LINKS}

    def pick(self, kind="file") -> list:
        """Системное окно выбора: файлы (kind=file) или папка (kind=folder). Пустой список — отмена."""
        w = self.window
        if w is None:
            return []
        try:
            import webview
            fd = getattr(webview, "FileDialog", None)
            if kind == "folder":
                mode = fd.FOLDER if fd is not None else webview.FOLDER_DIALOG
                r = w.create_file_dialog(mode)
            else:
                mode = fd.OPEN if fd is not None else webview.OPEN_DIALOG
                r = w.create_file_dialog(mode, allow_multiple=True)
        except Exception as e:
            self.trace(f"Остров: окно выбора не открылось ({e})")
            return []
        return [str(x) for x in (r or [])]

    def drag_out(self, item_id: int) -> bool:
        """Перетаскивание файла с полки в другое окно: настоящий drag-and-drop Windows (WinForms DoDragDrop) на потоке окна."""
        it = self.shelf.get(item_id)
        if not IS_WIN or it is None or it.get("type") != "path" or not os.path.exists(it.get("path", "")):
            return False
        form = getattr(self.window, "native", None)
        if form is None:
            return False
        try:
            from System import Action
            from System.Collections.Specialized import StringCollection
            from System.Windows.Forms import DataObject, DragDropEffects
        except Exception as e:
            self.trace(f"Остров: перетаскивание недоступно ({e})")
            return False
        done = {"ok": False}

        def run():
            try:
                data, files = DataObject(), StringCollection()
                files.Add(it["path"])
                data.SetFileDropList(files)
                form.DoDragDrop(data, DragDropEffects.Copy | DragDropEffects.Link)
                done["ok"] = True
            except Exception as e:
                self.trace(f"Остров: DoDragDrop не удался ({e.__class__.__name__}: {e})")
        self._drag_until = time.time() + 600
        try:
            form.Invoke(Action(run))
        except Exception as e:
            self.trace(f"Остров: перетаскивание не началось ({e})")
        finally:
            self._drag_until = time.time() + 1.0
        return done["ok"]

    def _load_place(self):
        """Закреплённое положение берём из настроек; не закреплённый остров при каждом запуске стоит сверху по центру."""
        cfg = self.settings()
        if cfg.get("pinned"):
            self._pinned, self._dock, self._pos = True, cfg["dock"], cfg["pos"]
            self._mon = tuple(cfg["mon"]) if cfg.get("mon") else None
        else:
            self._pinned, self._dock, self._pos, self._mon = False, "top", 0.5, None
        self._mode, self._ext, self._rect_cur, self._win = "mini", None, None, None

    def _start_xy(self):
        """Где создать окно: у выбранного края (точное положение потом ставит _apply, когда известен масштаб экрана)."""
        try:
            _, work = self._monitor()
            x, y = self._rect_for("mini", None, 1.0, work)[:2]
            return x, y
        except Exception:
            return None, None

    def repaint(self) -> bool:
        """Страница перестала выдавать кадры (WebView2 считает окно скрытым или закрытым другим окном): прячем и снова показываем окно
        средствами WinForms и ставим на место. Не чаще раза в 3 с; пока остров спрятан нарочно (снимок, выбор области) — не трогаем."""
        if not IS_WIN or self.window is None or not self.ready or self._hidden_by_us or self._snip is not None:
            return False
        now = time.time()
        if now - self._last_repaint < 3.0:
            return False
        self._last_repaint = now
        self._repaints += 1
        if self._repaints <= 5 or self._repaints % 20 == 0:
            self.trace(f"Остров: страница не рисуется, перерисовываю (раз {self._repaints})")
        w = self.window
        try:
            prev = int(user32.GetForegroundWindow() or 0)
        except Exception:
            prev = 0
        try:
            w.hide()
            time.sleep(0.05)
            w.show()
        except Exception as e:
            self.trace(f"Остров: перерисовка не удалась ({e})")
            return False
        try:
            h = self._hwnd_of()
            if h and prev and prev != h and int(user32.GetForegroundWindow() or 0) == h:
                user32.SetForegroundWindow(prev)
        except Exception:
            pass
        self._painting = True
        self._setup_window()
        return True

    def stop(self):
        self._stopping = True
        try:
            self._stop()
        finally:
            self._stopping = False

    def _on_closing(self):
        """False — отменить закрытие. Остров закрывается только нами (выключили в настройках, выход из Карины, выключение Windows)."""
        if self._stopping or getattr(self.api, "quitting", False):
            return None
        self.trace("Остров: попытку закрыть окно отменила")
        return False

    def _on_closed(self, w):
        """Окно всё же закрылось (например, процесс завершают): сбрасываем состояние, чтобы остров можно было открыть заново."""
        if self.window is w:
            self.trace("Остров: окно закрыто, состояние сброшено")
            self._stopping = True
            try:
                self._stop()
            finally:
                self._stopping = False

    def _stop(self):
        w, self.window, self.ready, self._hwnd = self.window, None, False, 0
        self._gen += 1
        self._stop_tick.set()
        self._snip_close()
        if self.rec.on:
            self.rec.stop()
        self.clip.stop()
        self.media.stop()
        halo, self._halo = self._halo, None
        if halo is not None:
            halo.stop()
        q, self._q = self._q, None
        if q is not None:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        if w is not None:
            try:
                w.destroy()
            except Exception as e:
                self.trace(f"Остров: destroy не удался ({e})")
            self.trace("Остров: закрыт")

    # ---- события из Карины ----
    def forward(self, ev: dict):
        """Вызывается из очереди событий окна; никогда не блокирует: остров получает их в своём потоке."""
        q = self._q
        if q is None or not self.ready or ev.get("type") not in FORWARD_TYPES:
            return
        try:
            q.put_nowait(ev)
        except queue.Full:
            pass

    def push(self, ev: dict):
        """Своё событие для страницы острова (всплывающая подсказка и т. п.)."""
        q = self._q
        if q is not None and self.ready:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def settings(self) -> dict:
        return tools.clean_settings({}, self.cfg.get("island") if hasattr(self.cfg, "get") else {})

    def settings_set(self, patch: dict) -> dict:
        new = tools.clean_settings(patch, self.settings())
        self.cfg.update({"island": new})
        self._sync_media()
        return new

    def _sync_media(self):
        """Читать музыку только пока остров работает и показ музыки включён."""
        if self.window is not None and self.settings().get("idle_music", True):
            self.media.start()
        else:
            self.media.stop()
            self.push({"type": "media", "status": "none"})

    def _on_media(self, info):
        self.push({"type": "media", **(info or {"status": "none"})})

    def _say(self, text: str):
        try:
            threading.Thread(target=self.api.assistant.say, args=(text,), daemon=True).start()
        except Exception:
            pass

    def _tick(self, stop: threading.Event):
        """Раз в секунду: сработал ли таймер или напоминание. Говорит вслух и показывает подсказку на острове."""
        while not stop.wait(1.0):
            try:
                self.focus.poll()
                if self._rec_shown and not self.rec.on:                  # ffmpeg завершился сам (например, закончилось место)
                    self._rec_shown = False
                    self.push({"type": "rec", "on": False})
                    self.push({"type": "toast", "kind": "tool", "text": "Запись остановилась"})
                if self.store.timer_due():
                    self._say("Таймер закончился.")
                    self.push({"type": "toast", "text": "Таймер закончился", "kind": "timer"})
                for r in self.store.due_reminders():
                    self._say("Напоминаю: " + r["what"])
                    self.push({"type": "toast", "text": r["what"], "kind": "reminder"})
            except Exception as e:
                self.trace(f"Остров: ошибка таймера ({e})")

    def _show_native(self, h: int = 0) -> bool:
        """Показывает остров средствами WinForms (Form.Show), а не голым ShowWindow. Окно создаётся спрятанным, и если показать его через
        ShowWindow, WinForms считает его скрытым, WebView2 не рисует страницу: плашка остаётся чёрной, пока не изменится размер
        (то есть появляется только когда Карина что-то делает или играет музыка, и мигает). Фокус не отбираем: возвращаем прежнему окну."""
        h = h or self._hwnd_of()
        prev = 0
        try:
            prev = int(user32.GetForegroundWindow() or 0)
        except Exception:
            pass
        ok = False
        try:
            self.window.show()
            ok = True
        except Exception as e:
            self.trace(f"Остров: window.show() не удался ({e}), показываю через ShowWindow")
        try:
            if not ok and h:
                user32.ShowWindow(h, 8)                                                      # SW_SHOWNA
            if h and prev and prev != h and int(user32.GetForegroundWindow() or 0) == h:
                user32.SetForegroundWindow(prev)
        except Exception:
            pass
        self._painting = ok or self._painting
        return ok

    def _hide(self) -> bool:
        """Прячет остров на время снимка. Возвращает True, если что-то спрятали."""
        h = self._hwnd_of() if IS_WIN else 0
        if h:
            self._hidden_by_us = True
            try:
                self.window.hide()                              # через WinForms: WebView2 узнаёт, что страница скрыта
            except Exception:
                user32.ShowWindow(h, 0)
            if self._halo is not None:
                self._halo.show(False)
            time.sleep(0.3)
        return bool(h)

    def _unhide(self, hidden: bool):
        if hidden:
            self._hidden_by_us = False
            self._show_native(self._hwnd_of())
            if self._halo is not None:
                self._halo.show(True)

    def screenshot(self) -> dict:
        """Снимок экрана без самого острова: на мгновение прячем окно."""
        hidden = False
        try:
            hidden = self._hide()
            path = tools.save_screenshot()
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__}
        finally:
            self._unhide(hidden)

    def _toast(self, text: str):
        self.push({"type": "toast", "kind": "tool", "text": text})

    # ---- инструменты: область, текст, пипетка, QR, запись ----
    def qr_scan(self) -> dict:
        hidden = False
        try:
            hidden = self._hide()
            img, _ = st.grab_screen()
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__}
        finally:
            self._unhide(hidden)
        try:
            return {"ok": True, "codes": st.qr_read(img)}
        except st.ToolError as e:
            return {"ok": False, "error": str(e)}

    def rec_toggle(self) -> dict:
        try:
            if self.rec.on:
                r = self.rec.stop()
                self._rec_shown = False
                self.push({"type": "rec", "on": False})
                if r.get("path"):
                    self.push({"type": "toast", "kind": "tool", "text": f"Запись сохранена ({st.fmt_time(r['sec'])}): {Path(r['path']).name}"})
                return {"ok": True, **r}
            r = self.rec.start()
            self._rec_shown = True
            self.push({"type": "rec", "on": True, "t0": r["t0"]})
            return {"ok": True, **r}
        except st.ToolError as e:
            return {"ok": False, "error": str(e), "on": self.rec.on}

    def snip(self, mode: str) -> dict:
        """Снимок экрана → окно во весь рабочий стол, где выбирают область (area/text) или цвет (pick)."""
        if mode not in ("area", "text", "pick"):
            return {"ok": False, "error": "Неизвестный режим."}
        if not IS_WIN or self.window is None:
            return {"ok": False, "error": "Работает только в Windows."}
        if self._snip is not None:
            return {"ok": False, "error": "Выбор уже идёт: закройте его клавишей Esc."}
        hidden = False
        try:
            hidden = self._hide()
            img, origin = st.grab_screen()
        except Exception as e:
            self._unhide(hidden)
            return {"ok": False, "error": "Не удалось сделать снимок экрана: " + (str(e) or e.__class__.__name__)}
        # остров остаётся спрятанным, пока идёт выбор: вернём его в _snip_done
        s = {"mode": mode, "img": img, "origin": origin, "hidden": hidden, "ready": threading.Event(), "window": None}
        self._snip = s
        try:
            import webview
            kw = dict(url=str(ROOT / "ui" / "snip.html"), js_api=SnipApi(self, s), width=400, height=300, frameless=True, easy_drag=False,
                      shadow=False, on_top=True, background_color="#000000", hidden=True, focus=True)
            w = None
            for drop in ((), ("hidden",), ("shadow", "easy_drag", "hidden")):
                try:
                    w = webview.create_window(SNIP_TITLE, **{k: v for k, v in kw.items() if k not in drop})
                    break
                except TypeError as e:
                    self.trace(f"Область: create_window без {', '.join(drop) or '-'} ({e})")
            if w is None:
                raise RuntimeError("окно не создано")
            s["window"] = w
            threading.Thread(target=self._snip_place, args=(s,), daemon=True, name="karina-snip").start()
            return {"ok": True}
        except Exception as e:
            self._snip = None
            self._unhide(hidden)
            self.trace(f"Область: не удалось открыть окно ({e.__class__.__name__}: {e})")
            return {"ok": False, "error": "Не удалось открыть окно выбора."}

    def _snip_place(self, s: dict):
        """Растягивает окно выбора на весь рабочий стол (в физических пикселях снимка) и показывает, когда страница готова."""
        t0, hwnd = time.time(), 0
        while self._snip is s and not hwnd and time.time() - t0 < 10:
            try:
                hwnd = int(user32.FindWindowW(None, SNIP_TITLE) or 0)
            except Exception:
                hwnd = 0
            if not hwnd:
                time.sleep(0.05)
        if self._snip is not s or not hwnd:
            self._snip_finish(s, {"action": "cancel"})
            return
        try:
            stl = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, stl & ~(WS_CAPTION | WS_THICKFRAME))
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
            ox, oy = s["origin"]
            s["hwnd"] = hwnd
            user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), ox, oy, s["img"].width, s["img"].height, SWP_FRAMECHANGED)
        except Exception as e:
            self.trace(f"Область: окно не растянулось ({e})")
        s["ready"].wait(8)                                  # страница сообщит, когда нарисовала снимок
        try:
            s["window"].show()                              # через WinForms, иначе WebView2 не рисует страницу
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        end = time.time() + SNIP_TIMEOUT
        while self._snip is s and time.time() < end:
            time.sleep(0.5)
        if self._snip is s:
            self._snip_finish(s, {"action": "cancel"})

    def _snip_close(self):
        s, self._snip = self._snip, None
        if s is not None and s.get("window") is not None:
            try:
                s["window"].destroy()
            except Exception:
                pass
        return s

    def _snip_finish(self, s: dict, payload: dict):
        """Закрывает окно выбора, возвращает остров и выполняет выбранное действие."""
        if self._snip is not s:
            return
        self._snip_close()
        self._unhide(s["hidden"])
        act = str((payload or {}).get("action") or "cancel")
        if act == "cancel":
            return
        try:
            if act == "color":
                v = str(payload.get("value") or "")[:40]
                st.clip_set_text(v)
                self._toast(f"Цвет {v} скопирован")
                return
            crop = st.crop_fraction(s["img"], payload.get("rect"))
            dims = f"{crop.width} × {crop.height}"
            if act == "copy":
                self._toast(f"Снимок {dims} скопирован" if st.clip_set_image(crop) else "Не удалось скопировать картинку")
            elif act == "save":
                self._toast("Сохранено: " + Path(st.save_image(crop)).name)
            elif act == "text":
                self._toast("Распознаю текст…")
                text = st.ocr_image(crop)
                if text.strip():
                    self.push({"type": "tool", "kind": "text", "text": text})
                else:
                    self._toast("Текст не найден")
        except st.ToolError as e:
            self._toast(str(e))
        except Exception as e:
            self.trace(f"Область: ошибка ({e.__class__.__name__}: {e})")
            self._toast("Не получилось: " + (str(e) or e.__class__.__name__))

    def _pump(self, w, q):
        while True:
            ev = q.get()
            if ev is None or self.window is not w:
                return
            try:
                w.evaluate_js(f"window.karina && window.karina.event({json.dumps(ev, ensure_ascii=False)})")
            except Exception:
                pass

    def _on_loaded(self):
        if not self.ready:
            self.ready = True
            self.trace("Остров: страница загружена")
        self._hook_drop()
        self._setup_window()

    # ---- положение и форма ----
    def _hwnd_of(self, refresh: bool = False) -> int:
        if self._hwnd and not refresh:
            return self._hwnd
        h = 0
        nat = getattr(self.window, "native", None) if self.window else None
        if nat is not None:
            try:
                h = int(nat.Handle.ToInt64())
            except Exception:
                try:
                    h = int(nat.Handle.ToInt32())
                except Exception:
                    h = 0
        if not h and IS_WIN:
            try:
                h = int(user32.FindWindowW(None, TITLE) or 0)         # запасной путь: найти окно по заголовку
            except Exception:
                h = 0
        if h or not refresh:                                         # при обновлении не затираем прежний handle, если новый получить не удалось
            self._hwnd = h
        return self._hwnd

    def _setup_window(self) -> bool:
        """Без рамки и заголовка, без значка на панели задач и без кражи фокуса, поверх всех окон, по центру у верхнего края.
        Можно вызывать сколько угодно раз (и из события «показано», и из потока проверки)."""
        if not IS_WIN or self.window is None:
            return False
        h = self._hwnd_of()
        if not h:
            return False
        try:
            st = user32.GetWindowLongPtrW(h, GWL_STYLE)
            user32.SetWindowLongPtrW(h, GWL_STYLE, st & ~(WS_CAPTION | WS_THICKFRAME))
            ex = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
            user32.SetWindowLongPtrW(h, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)     # не значок на панели задач
            user32.SetWindowPos(h, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_ASYNCWINDOWPOS)
            if not self._painting:
                self._show_native(h)
        except Exception as e:
            self.trace(f"Остров: стили окна не применены ({e})")
        try:
            self._apply(force=True)
        except Exception as e:
            self.trace(f"Остров: не удалось поставить окно ({e})")
            return False
        return True

    def _init_loop(self, w):
        """Ждёт окно и страницу и ставит остров на место. Положение повторяем несколько раз: при первом показе WinForms
        может переставить окно в центр экрана уже после нашей подгонки."""
        t0 = time.time()
        ok = False
        while self.window is w and time.time() - t0 < 30:
            if self._setup_window():
                ok = True
                break
            time.sleep(0.1)
        if self.window is not w:
            return
        self.trace(f"Остров: окно найдено ({'hwnd ' + str(self._hwnd) if ok else 'НЕ найдено'}) за {time.time() - t0:.1f} с")
        self._give_back_focus()
        ev = getattr(getattr(w, "events", None), "loaded", None)
        while self.window is w and not self.ready and time.time() - t0 < 45:
            try:
                if ev is not None and ev.is_set():
                    break
            except Exception:
                break
            time.sleep(0.15)
        if self.window is not w:
            return
        if not self.ready:
            self.ready = True
            self.trace("Остров: страница загружена (проверка потока)")
        self._hook_drop()
        for pause in (0.3, 0.7, 1.5, 3.0):
            time.sleep(pause)
            if self.window is not w:
                return
            self._setup_window()

    def _give_back_focus(self):
        """Окно создано показанным: если оно успело забрать фокус, возвращаем его окну, где человек работал."""
        try:
            h, prev = self._hwnd_of(), int(getattr(self, "_prev_fg", 0) or 0)
            if h and prev and prev != h and int(user32.GetForegroundWindow() or 0) == h:
                user32.SetForegroundWindow(prev)
        except Exception:
            pass

    def _on_shown(self):
        self._setup_window()
        self._give_back_focus()

    def _scale(self, h) -> float:
        try:
            return max(1.0, user32.GetDpiForWindow(h) / 96.0)
        except Exception:
            return 1.0

    def _monitor(self):
        """((левый, верхний) угол монитора, рабочая область (left, top, right, bottom)): основной монитор или тот, где остров закреплён."""
        pt, flag = (wintypes.POINT(self._mon[0] + 1, self._mon[1] + 1), 2) if self._mon else (wintypes.POINT(0, 0), 1)
        return self._monitor_at(pt, flag)

    def _monitor_at(self, pt, flag=2):
        mon = user32.MonitorFromPoint(pt, flag)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r, w = mi.rcMonitor, mi.rcWork
            return (r.left, r.top), (w.left, w.top, w.right, w.bottom)
        return (0, 0), (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))

    # ---- геометрия: всё в физических пикселях экрана ----
    def _rect_for(self, mode: str, ext, sc: float, work, dock=None, pos=None):
        """Где и какой величины плашка в режиме mode: (x, y, w, h, радиус)."""
        dock, pos = dock or self._dock, self._pos if pos is None else pos
        w, h, r = mode_size(dock, mode, ext)
        pw, ph = max(20, int(round(w * sc))), max(16, int(round(h * sc)))
        pr = max(4.0, min(r * sc, ph / 2, pw / 2))
        x, y = pill_xy(dock, pos, pw, ph, work, int(round(TOP_MARGIN * sc)))
        return x, y, pw, ph, pr

    def _win_rect(self, sc: float, work, dock=None, pos=None):
        """Окно с запасом: накрывает плашку во всех режимах (в том числе самую большую панель). Видна только область региона."""
        dock = dock or self._dock
        rects = []
        for m in SIZES:
            ext = (VEXT_MAX if is_vert(dock, m) else WIDTH_MAX) if m in ("info", "music") else None
            rects.append(self._rect_for(m, ext, sc, work, dock, pos))
        return union_rect(rects)

    def _target(self, mode: str, ext):
        """(куда должна встать плашка, раскладка для страницы). Раскладка — размеры в CSS-пикселях относительно окна: страница
        рисует содержимое сразу на конечном месте, а регион плавно до него «дорастает»."""
        if not IS_WIN or self.window is None:
            return None
        h = self._hwnd_of()
        if not h:
            return None
        sc = self._scale(h)
        _, work = self._monitor()
        W = self._win_rect(sc, work)
        b = self._rect_for(mode, ext, sc, work)
        fx, fy = self._rect_for("full", None, sc, work)[:2]
        lay = {"mode": mode, "dock": self._dock, "pinned": self._pinned, "r": b[4] / sc,
               "s": [(b[0] - W[0]) / sc, (b[1] - W[1]) / sc, b[2] / sc, b[3] / sc], "f": [(fx - W[0]) / sc, (fy - W[1]) / sc]}
        return b, lay

    def layout_info(self):
        """Текущая раскладка для страницы (при загрузке и после смены края)."""
        t = self._target(self._mode, self._ext)
        return t[1] if t else None

    def _apply(self, force: bool = False):
        """Ставит окно на место (с запасом по размеру) и возвращает форму (регион) плашке. Можно вызывать сколько угодно раз."""
        h = self._hwnd_of()
        if not h:
            return
        sc = self._scale(h)
        _, work = self._monitor()
        W = self._win_rect(sc, work)
        r = _RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if force or (r.left, r.top, r.right - r.left, r.bottom - r.top) != W:
            user32.SetWindowPos(h, wintypes.HWND(HWND_TOPMOST), W[0], W[1], W[2], W[3], SWP_NOACTIVATE)      # без ASYNC: регион ниже должен лечь на уже готовое окно
        self._win = W
        if self._rect_cur is None:
            self._rect_cur = self._rect_for(self._mode, self._ext, sc, work)
        if not self._animating:
            self._draw(self._rect_cur)

    def _draw(self, rect, halo_ok: bool = True):
        """Форма плашки: регион по прямоугольнику rect (экранные координаты) + сглаженная кромка под ней."""
        h, W = self._hwnd_of(), self._win
        if not h or W is None:
            return
        x, y, w, hh, r = rect
        x, y, pw, ph = int(round(x)), int(round(y)), max(20, int(round(w))), max(16, int(round(hh)))
        pr = max(4.0, min(float(r), ph / 2, pw / 2))
        smooth = False
        halo = self._halo
        if halo is not None and halo_ok:
            if not halo.ok and not halo.tried:
                halo.tried = True
                halo.start()
            smooth = halo.update(h, x, y, pw, ph, pr)
        ins = 1 if smooth else 0                         # со сглаженной кромкой регион чуть меньше окна: по краю видна она
        rx, ry = x - W[0], y - W[1]
        rgn = gdi32.CreateRoundRectRgn(rx + ins, ry + ins, rx + pw - ins + 1, ry + ph - ins + 1, int(2 * (pr - ins)), int(2 * (pr - ins)))
        user32.SetWindowRgn(h, rgn, True)                # система сама освободит регион
        self._rect_cur = (x, y, pw, ph, pr)

    # ---- режимы и анимация ----
    def set_mode(self, mode: str, width=None):
        """Страница просит другой режим (свёрнутая плашка, «думаю», панель…). Возвращает раскладку: где внутри окна рисовать содержимое."""
        if mode not in SIZES or not IS_WIN or self.window is None:
            return None
        self._mode, self._ext = mode, (float(width) if _num(width) else None)
        return self._go()

    def _go(self):
        t = self._target(self._mode, self._ext)
        if t is None:
            return None
        b, lay = t
        with self._lock:
            if self._busy or self._win is None:
                return lay                                       # тащат плашку или окно ещё не поставлено: применится потом
            a = self._rect_cur or b
            if all(abs(a[i] - b[i]) < 0.5 for i in range(5)):
                return lay
            self._gen += 1
            gen = self._gen
        grow = b[2] * b[3] >= a[2] * a[3]
        threading.Thread(target=self._animate, args=(gen, a, b, grow), daemon=True, name="karina-island-anim").start()
        return lay

    @staticmethod
    def _hires(on: bool):
        """Таймер Windows с шагом 1 мс: иначе sleep спит по 15,6 мс, и анимация идёт рывками."""
        try:
            m = ctypes.WinDLL("winmm")
            (m.timeBeginPeriod if on else m.timeEndPeriod)(1)
        except Exception:
            pass

    def _animate(self, gen, a, b, grow):
        """Окно не трогаем, меняем только регион: от прежней плашки к новой. Время считаем по часам, а не по числу кадров."""
        dur = ANIM_GROW if grow else ANIM_SHRINK
        heavy = max(a[2] * a[3], b[2] * b[3]) > HALO_ANIM_AREA * self._scale(self._hwnd_of()) ** 2
        halo = self._halo
        if heavy and halo is not None:
            halo.warm(b[2], b[3], b[4])                          # кромку большой панели готовим заранее, в другом потоке
            halo.show(False)
        self._animating = True
        self._hires(True)
        try:
            t0 = time.perf_counter()
            while gen == self._gen and self.window is not None:
                p = (time.perf_counter() - t0) / dur
                e = _ease(p, grow)
                self._draw(tuple(a[i] + (b[i] - a[i]) * e for i in range(5)), halo_ok=not heavy)
                if p >= 1:
                    break
                time.sleep(0.008)
        except Exception as ex:
            self.trace(f"Остров: анимация прервана ({ex})")
        finally:
            self._hires(False)
            self._animating = False
        if gen == self._gen and self.window is not None:
            try:
                self._draw(b)                                    # итог: точный размер и сглаженная кромка
            except Exception:
                pass

    # ---- перетаскивание и прилипание к краю ----
    def drag(self) -> dict:
        """Страница заметила, что плашку потащили. Пока кнопка мыши нажата, окно ходит за курсором (мышь читаем сами, окно под курсором
        уезжает, поэтому события страницы тут не помощники); отпустили — плашка прилипает к ближайшему краю экрана."""
        if not IS_WIN or self.window is None or not self.ready or self._pinned or self._snip is not None:
            return {"ok": False}
        h = self._hwnd_of()
        with self._lock:
            if not h or self._busy or self._mode in ("full", "drop") or self._rect_cur is None or self._win is None:
                return {"ok": False}
            self._busy = True
            self._gen += 1                                       # прерываем анимацию режима
        try:
            self._hires(True)
            try:
                return self._drag_loop(h)
            finally:
                self._hires(False)
        except Exception as e:
            self.trace(f"Остров: перетаскивание не удалось ({e.__class__.__name__}: {e})")
            return {"ok": False}
        finally:
            self._busy = False
            self._go()                                           # режим мог поменяться, пока тащили

    def _drag_loop(self, h) -> dict:
        p0, r0 = wintypes.POINT(), _RECT()
        user32.GetCursorPos(ctypes.byref(p0))
        user32.GetWindowRect(h, ctypes.byref(r0))
        x0, y0, W = r0.left, r0.top, self._win
        rect = self._rect_cur
        ox, oy = rect[0] - x0, rect[1] - y0                      # где плашка внутри окна
        vk = 0x02 if user32.GetSystemMetrics(23) else 0x01       # основная кнопка мыши (SM_SWAPBUTTON)
        pt, moved, end = wintypes.POINT(), False, time.time() + 120
        nx, ny = x0, y0
        while user32.GetAsyncKeyState(vk) & 0x8000 and time.time() < end and self.window is not None:
            user32.GetCursorPos(ctypes.byref(pt))
            nx, ny = x0 + pt.x - p0.x, y0 + pt.y - p0.y
            if not moved and abs(pt.x - p0.x) + abs(pt.y - p0.y) < 4:
                time.sleep(0.008)
                continue
            moved = True
            user32.SetWindowPos(h, wintypes.HWND(HWND_TOPMOST), nx, ny, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
            self._win = (nx, ny, W[2], W[3])
            halo = self._halo
            if halo is not None and halo.ok:
                halo.update(h, nx + ox, ny + oy, int(rect[2]), int(rect[3]), rect[4])
            time.sleep(0.008)
        if not moved:
            return {"ok": True, "moved": False}
        cx, cy = nx + ox + rect[2] / 2, ny + oy + rect[3] / 2
        mon, work = self._monitor_at(wintypes.POINT(int(cx), int(cy)), 2)
        dock, pos = snap_dock(cx, cy, work)
        self._fly_to(dock, pos, mon)
        return {"ok": True, "moved": True, "dock": dock, "pos": pos}

    def _fly_to(self, dock: str, pos: float, mon):
        """Плавно перелетает на новое место (край, доля вдоль края, монитор). Содержимое на время прячем: оно перерисуется уже на месте."""
        h = self._hwnd_of()
        a = self._rect_cur
        old_vert = is_side(self._dock)
        self._dock, self._pos, self._mon = dock, pos, (tuple(mon) if mon else None)
        if is_side(dock) != old_vert:
            self._ext = None                                     # длинная сторона была шириной, стала высотой (или наоборот): страница пришлёт заново
        self._js("window.karina && window.karina.fly(true)")
        time.sleep(0.14)
        sc = self._scale(h)
        _, work = self._monitor()
        b = self._rect_for(self._mode, self._ext, sc, work)
        # на время полёта окно = сама плашка: двигать маленькое окно дёшево, а содержимое спрятано
        self._animating = True
        try:
            ax, ay, aw, ah, ar = a
            user32.SetWindowPos(h, wintypes.HWND(HWND_TOPMOST), int(ax), int(ay), int(aw), int(ah), SWP_NOACTIVATE)
            self._win = (int(ax), int(ay), int(aw), int(ah))
            t0 = time.perf_counter()
            while self.window is not None:
                p = (time.perf_counter() - t0) / ANIM_FLY
                e = _ease(p, True)
                cur = tuple(a[i] + (b[i] - a[i]) * e for i in range(5))
                user32.SetWindowPos(h, wintypes.HWND(HWND_TOPMOST), int(round(cur[0])), int(round(cur[1])), int(round(cur[2])), int(round(cur[3])), SWP_NOACTIVATE)
                self._win = (int(round(cur[0])), int(round(cur[1])), int(round(cur[2])), int(round(cur[3])))
                self._draw((cur[0], cur[1], cur[2], cur[3], cur[4]))
                if p >= 1:
                    break
                time.sleep(0.008)
        finally:
            self._animating = False
        self._rect_cur = b
        self._apply(force=True)                                   # окно снова с запасом под самую большую панель
        lay = self.layout_info()
        self._js("window.karina && window.karina.layout(" + json.dumps(lay) + ")")
        self._js("window.karina && window.karina.fly(false)")

    def set_pinned(self, on: bool):
        """Закрепить на этом месте: после перезапуска остров встанет сюда же, и перетащить его нельзя, пока не открепят."""
        self._pinned = bool(on)
        patch = {"pinned": self._pinned}
        if self._pinned:
            patch.update(dock=self._dock, pos=self._pos, mon=list(self._mon) if self._mon else None)
        self.settings_set(patch)
        return self.layout_info()

    def dock_reset(self):
        """Вернуть наверх по центру (и открепить)."""
        if not IS_WIN or self.window is None:
            return None
        self._pinned = False
        self.settings_set({"pinned": False})
        with self._lock:
            if self._busy:
                return self.layout_info()
            self._busy = True
            self._gen += 1
        try:
            self._fly_to("top", 0.5, None)
        finally:
            self._busy = False
            self._go()
        return self.layout_info()

    def _js(self, code: str):
        w = self.window
        if w is not None:
            try:
                w.evaluate_js(code)
            except Exception:
                pass

    # ---- сторож: форма окна не должна слетать ----
    def reassert(self):
        """Вернуть остров в порядок после смены темы Windows и подобных событий (несколько проверок подряд: система реагирует не сразу)."""
        def run():
            for d in (0.05, 0.2, 0.4, 0.8, 1.5, 2.0):
                time.sleep(d)
                self._guard_once(True)
        threading.Thread(target=run, daemon=True, name="karina-island-reassert").start()

    def _guard(self, w, stop: threading.Event):
        while not stop.wait(0.15):
            if self.window is not w:
                return
            try:
                self._guard_once()
            except Exception as e:
                self.trace(f"Остров: сторож: {e}")

    def _guard_once(self, force: bool = False):
        """Проверяем окно: цел ли регион (форма «таблетки»), нет ли рамки и заголовка, там ли стоит окно, тот же ли handle.
        После смены темы Windows регион слетал, и остров становился прямоугольником с уголками."""
        if not IS_WIN or self.window is None or not self.ready or self._busy or self._animating or self._hidden_by_us or self._snip is not None:
            return
        old = self._hwnd
        h = self._hwnd_of(refresh=True)
        if not h or not user32.IsWindow(h):
            return
        bad = force or h != old
        if not bad:
            tmp = gdi32.CreateRectRgn(0, 0, 0, 0)
            try:
                bad = user32.GetWindowRgn(h, tmp) == 0           # ERROR: региона нет
            finally:
                gdi32.DeleteObject(tmp)
        if not bad:
            bad = bool(user32.GetWindowLongPtrW(h, GWL_STYLE) & (WS_CAPTION | WS_THICKFRAME))
        if not bad and self._win is not None:
            r = _RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            bad = (r.left, r.top, r.right - r.left, r.bottom - r.top) != tuple(self._win)
        if bad:
            if not force and time.time() - self._last_fix > 2.0:
                self.trace("Остров: форма окна слетела, возвращаю")
            self._last_fix = time.time()
            self._setup_window()
