"""Динамический остров: маленькая плашка у верхнего края экрана, показывает, что делает Карина
(слушает, думает, говорит), и по нажатию раскрывается в панель с кнопками.

Остров — отдельное безрамочное окно pywebview (ui/island.html). Форму (скруглённую «таблетку») и плавное
изменение размера делаем через Win32: меняем размер окна и его регион, поэтому прозрачность окна не нужна.
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
WIDTH_MIN, WIDTH_MAX = 150, 420        # страница сама сообщает нужную ширину для режимов «info» и «music» (по содержимому)
RADIUS_MAX = 30         # то же число в ui/island.html: --r = min(30px, 50vh)
TOP_MARGIN = 6          # отступ от верхнего края экрана
ANIM_SECONDS = 0.32
FORWARD_TYPES = ("state", "level", "log")      # какие события главного окна нужны острову

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
    SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_ASYNCWINDOWPOS = 0x0010, 0x0020, 0x4000
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
    try:
        _fix_types()
    except Exception:
        pass


def _ease(p: float, grow: bool) -> float:
    """Пружинка при раскрытии (небольшой «перелёт»), мягкое торможение при сворачивании."""
    p = max(0.0, min(1.0, p))
    if grow:
        c1 = 1.25
        c3 = c1 + 1
        return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2
    return 1 - (1 - p) ** 3


class IslandApi:
    """Методы, которые остров вызывает из JavaScript (pywebview.api.<метод>). Ссылки хранятся с «_», чтобы pywebview их не показывал странице."""

    def __init__(self, island: "Island", api, show_main):
        self._island, self._api, self._show_main = island, api, show_main

    # ---- состояние и режимы ----
    def get_state(self):
        a = self._api.assistant
        return {"state": a.state, "sleeping": bool(a.sleeping), "listening": bool(a.listening), "name": a.cfg["name"],
                "settings": self._island.settings(), "volume": self.volume_get(), "admin": self._island.is_admin()}

    def set_mode(self, mode: str, width=None):
        self._island.set_mode(str(mode), width)
        return True

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
            return tools.dark_set(not bool(tools.dark_get()))
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
        self._cur = SIZES["mini"]            # текущий (ширина, высота, радиус) во время анимации
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

    def _start_xy(self):
        """Где создать окно: сверху по центру основного экрана (точное положение потом ставит _place)."""
        try:
            sw = int(user32.GetSystemMetrics(0))
            return max(0, (sw - SIZES["mini"][0]) // 2), TOP_MARGIN
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
    def _hwnd_of(self) -> int:
        if self._hwnd:
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
        self._hwnd = h
        return h

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
            self._place(*self._cur)
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
        mon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)       # основной монитор
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r = mi.rcMonitor
            return r.left, r.top, r.right, r.bottom
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    def _place(self, w: float, h: float, r: float):
        hwnd = self._hwnd_of()
        if not hwnd:
            return
        sc = self._scale(hwnd)
        pw, ph = max(20, int(w * sc)), max(16, int(h * sc))
        pr = max(4.0, min(RADIUS_MAX * sc, ph / 2))      # та же формула, что в CSS страницы: min(30px, 50vh)
        left, top, right, _ = self._monitor()
        x = left + (right - left - pw) // 2
        y = top + int(TOP_MARGIN * sc)
        user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), x, y, pw, ph, SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
        smooth = False
        halo = self._halo
        if halo is not None:
            if not halo.ok and not halo.tried:
                halo.tried = True
                halo.start()
            smooth = halo.update(hwnd, x, y, pw, ph, pr)
        ins = 1 if smooth else 0                         # со сглаженной кромкой регион чуть меньше окна: по краю видна она
        rgn = gdi32.CreateRoundRectRgn(ins, ins, pw - ins + 1, ph - ins + 1, int(2 * (pr - ins)), int(2 * (pr - ins)))
        user32.SetWindowRgn(hwnd, rgn, True)         # система сама освободит регион

    def set_mode(self, mode: str, width=None):
        if mode not in SIZES or not IS_WIN or self.window is None:
            return
        b = SIZES[mode]
        if mode in ("info", "music", "mini") and isinstance(width, (int, float)) and not isinstance(width, bool):
            b = (max(WIDTH_MIN, min(WIDTH_MAX, float(width))), b[1], b[2])        # ширину задаёт содержимое плашки
        with self._lock:
            if tuple(round(v) for v in b) == tuple(round(v) for v in self._cur):
                return
            self._gen += 1
            gen = self._gen
            a = self._cur
        grow = b[0] * b[1] >= a[0] * a[1]
        threading.Thread(target=self._animate, args=(gen, a, b, grow), daemon=True, name="karina-island-anim").start()

    def _animate(self, gen, a, b, grow):
        t0 = time.time()
        while gen == self._gen and self.window is not None:
            p = (time.time() - t0) / ANIM_SECONDS
            e = _ease(p, grow)
            cur = tuple(a[i] + (b[i] - a[i]) * e for i in range(3))
            try:
                self._place(*cur)
            except Exception as ex:
                self.trace(f"Остров: анимация прервана ({ex})")
                return
            self._cur = cur
            if p >= 1:
                break
            time.sleep(0.012)
        if gen == self._gen and self.window is not None:
            self._cur = b
            try:
                self._place(*b)
            except Exception:
                pass
