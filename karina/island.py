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

from .config import ROOT
from . import island_tools as tools
from .halo import Halo

IS_WIN = sys.platform == "win32"
TITLE = "Остров Карины"

# режим -> (ширина, высота, радиус скругления) в логических пикселях
SIZES = {"mini": (150, 36, 18), "wide": (290, 40, 20), "full": (480, 330, 30)}
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
                "settings": self._island.settings(), "volume": self.volume_get()}

    def set_mode(self, mode: str):
        self._island.set_mode(str(mode))
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

    def settings_set(self, patch: dict):
        return self._island.settings_set(patch if isinstance(patch, dict) else {})

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
            kw = dict(url=str(ROOT / "ui" / "island.html"), js_api=IslandApi(self, self.api, self.show_main),
                      width=SIZES["mini"][0], height=SIZES["mini"][1], min_size=(40, 20),
                      frameless=True, easy_drag=False, shadow=False, on_top=True, background_color="#000000",
                      hidden=True, focus=False)      # создаём спрятанным и без фокуса: стили «не приложение» ставим до первого показа
            w = None
            for drop in ((), ("hidden", "focus"), ("shadow", "hidden", "focus"), ("shadow", "easy_drag", "hidden", "focus"),
                         ("shadow", "easy_drag", "min_size", "hidden", "focus"), ("shadow", "easy_drag", "min_size", "on_top", "hidden", "focus")):
                try:
                    w = webview.create_window(TITLE, **{k: v for k, v in kw.items() if k not in drop})
                    break
                except TypeError as e:
                    self.trace(f"Остров: create_window без {', '.join(drop) or '-'} ({e})")
            if w is None:
                return False
            self.window, self.ready, self._hwnd = w, False, 0
            self._halo = Halo(self.trace)
            self._q = queue.Queue(maxsize=200)
            threading.Thread(target=self._pump, args=(w, self._q), daemon=True, name="karina-island").start()
            self._stop_tick = threading.Event()
            threading.Thread(target=self._tick, args=(self._stop_tick,), daemon=True, name="karina-island-tick").start()
            self.clip.start()
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
        self.clip.stop()
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
        return new

    def _say(self, text: str):
        try:
            threading.Thread(target=self.api.assistant.say, args=(text,), daemon=True).start()
        except Exception:
            pass

    def _tick(self, stop: threading.Event):
        """Раз в секунду: сработал ли таймер или напоминание. Говорит вслух и показывает подсказку на острове."""
        while not stop.wait(1.0):
            try:
                if self.store.timer_due():
                    self._say("Таймер закончился.")
                    self.push({"type": "toast", "text": "Таймер закончился", "kind": "timer"})
                for r in self.store.due_reminders():
                    self._say("Напоминаю: " + r["what"])
                    self.push({"type": "toast", "text": r["what"], "kind": "reminder"})
            except Exception as e:
                self.trace(f"Остров: ошибка таймера ({e})")

    def screenshot(self) -> dict:
        """Снимок экрана без самого острова: на мгновение прячем окно."""
        h = self._hwnd_of() if IS_WIN else 0
        try:
            if h:
                user32.ShowWindow(h, 0)
                if self._halo is not None:
                    self._halo.show(False)
                time.sleep(0.3)
            path = tools.save_screenshot()
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__}
        finally:
            if h:
                user32.ShowWindow(h, 8)
                if self._halo is not None:
                    self._halo.show(True)

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
            if not user32.IsWindowVisible(h):
                user32.ShowWindow(h, 8)                                                      # SW_SHOWNA
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
        for pause in (0.3, 0.7, 1.5, 3.0):
            time.sleep(pause)
            if self.window is not w:
                return
            self._setup_window()

    def _on_shown(self):
        self._setup_window()

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

    def set_mode(self, mode: str):
        if mode not in SIZES or not IS_WIN or self.window is None:
            return
        with self._lock:
            self._gen += 1
            gen = self._gen
            a, b = self._cur, SIZES[mode]
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
