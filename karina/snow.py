"""Снег, который идёт из-под острова, а не внутри него.

Окно острова обрезано регионом по форме «таблетки», поэтому внутри окна снег нельзя пустить за её край. Здесь снег живёт в своём окне:
прозрачное, невидимое для мыши окно с попиксельной прозрачностью (UpdateLayeredWindow) лежит сразу под островом, хлопья появляются
на его нижней кромке и падают вниз, постепенно тая. Окно следует за островом (положение берётся у острова каждый кадр).

Не на Windows (или если что-то пошло не так) модуль ничего не делает, а страница острова рисует снег внутри плашки, как раньше."""
from __future__ import annotations
import random
import sys
import threading
import time

IS_WIN = sys.platform == "win32"

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x8, 0x20, 0x80, 0x80000, 0x08000000
SW_HIDE, SW_SHOWNA = 0, 8
WM_QUIT = 0x0012
ULW_ALPHA = 0x2
FPS = 20
FALL = 150          # на сколько логических пикселей вниз идёт снег
SIDE = 36           # запас по бокам от плашки: хлопья сносит ветром


def flake_sprites():
    """Мягкие круглые «хлопья» трёх размеров (покрытие 0..1, numpy float32)."""
    import numpy as np
    out = []
    for r in (1.2, 1.8, 2.5):
        n = int(r * 2 + 3)
        ys, xs = np.mgrid[0:n, 0:n]
        d = np.hypot(xs - (n - 1) / 2, ys - (n - 1) / 2)
        out.append(np.clip(r + 0.6 - d, 0.0, 1.0).astype(np.float32))
    return out


class Flakes:
    """Движение хлопьев без привязки к Windows (можно проверить тестами): координаты внутри окна снега."""

    def __init__(self, w: int, h: int, x0: float, x1: float, scale: float = 1.0, rnd=None):
        self.rnd = rnd or random.Random()
        self.w, self.h, self.x0, self.x1, self.sc = w, h, float(x0), float(x1), scale
        self.items: list[list[float]] = []
        n = max(8, int((x1 - x0) / (14 * scale)))
        for _ in range(n):
            self.items.append(self._new(True))

    def _new(self, anywhere: bool = False) -> list[float]:
        r, sc = self.rnd, self.sc
        y = r.uniform(0, self.h) if anywhere else r.uniform(-6, 0) * sc
        return [r.uniform(self.x0, self.x1), y, r.uniform(18, 46) * sc, r.uniform(0, 6.3), r.choice((0, 0, 1, 1, 2))]    # x, y, скорость (px/с), фаза, размер

    def resize(self, w: int, h: int, x0: float, x1: float):
        if (w, h) != (self.w, self.h) or abs(x0 - self.x0) > 1 or abs(x1 - self.x1) > 1:
            self.w, self.h = w, h
            for f in self.items:
                if not self.x0 <= f[0] <= self.x1 or abs(x0 - self.x0) > 1 or abs(x1 - self.x1) > 1:
                    f[0] = self.rnd.uniform(x0, x1)
            self.x0, self.x1 = float(x0), float(x1)

    def step(self, dt: float):
        for i, f in enumerate(self.items):
            f[1] += f[2] * dt
            f[3] += 1.6 * dt
            f[0] += (self._sway(f[3]) * 7 * self.sc + 3 * self.sc) * dt       # лёгкий снос вправо и покачивание
            if f[1] > self.h + 3:
                self.items[i] = self._new()

    @staticmethod
    def _sway(p: float) -> float:
        import math
        return math.sin(p)

    def alpha(self, f) -> float:
        """Прозрачность хлопья: появляется из-под острова за первые ~8 px и тает к низу окна."""
        y = f[1] / max(1.0, self.h)
        if y <= 0:
            return 0.0
        fade_in = min(1.0, f[1] / (8 * self.sc))
        return max(0.0, min(1.0, fade_in * (1.0 - y) ** 1.1)) * 0.9


def render(fl: Flakes, sprites):
    """BGRA с заранее умноженной прозрачностью (цвет белый, значит каждый канал равен альфе): байты h×w×4."""
    import numpy as np
    a = np.zeros((fl.h, fl.w), dtype=np.float32)
    for f in fl.items:
        k = fl.alpha(f)
        if k <= 0.01:
            continue
        sp = sprites[int(f[4])]
        n = sp.shape[0]
        cx, cy = int(round(f[0])) - n // 2, int(round(f[1])) - n // 2
        x0, y0, x1, y1 = max(0, cx), max(0, cy), min(fl.w, cx + n), min(fl.h, cy + n)
        if x1 <= x0 or y1 <= y0:
            continue
        part = sp[y0 - cy:y1 - cy, x0 - cx:x1 - cx] * k
        a[y0:y1, x0:x1] = np.maximum(a[y0:y1, x0:x1], part)
    b = (a * 255 + 0.5).astype(np.uint8)
    out = np.empty((fl.h, fl.w, 4), dtype=np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = out[..., 3] = b
    return out.tobytes()


if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _BLEND(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class _BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    _u32.CreateWindowExW.restype = wintypes.HWND
    _u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    _u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _u32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    _u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.DestroyWindow.argtypes = [wintypes.HWND]
    _u32.GetDC.restype = wintypes.HDC
    _u32.GetDC.argtypes = [wintypes.HWND]
    _u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _u32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE), wintypes.HDC,
                                         ctypes.POINTER(_POINT), wintypes.COLORREF, ctypes.POINTER(_BLEND), wintypes.DWORD]
    _g32.CreateCompatibleDC.restype = wintypes.HDC
    _g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _g32.CreateDIBSection.restype = wintypes.HBITMAP
    _g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    _g32.SelectObject.restype = wintypes.HGDIOBJ
    _g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _g32.DeleteDC.argtypes = [wintypes.HDC]


class Snow:
    """geom() → None (снег не нужен: остров спрятан, у нижнего края и т. п.) или (x, y, w, h, scale): плашка острова в физических пикселях
    экрана и масштаб экрана; floor — нижняя граница видимой области экрана (снег не рисуем ниже)."""

    def __init__(self, geom, floor, trace=lambda s: None):
        self.geom, self.floor, self._trace = geom, floor, trace
        self.ok = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hwnd = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> bool:
        if not IS_WIN:
            return False
        if self.running:
            return self.ok
        try:
            import numpy  # noqa: F401
        except Exception:
            return False
        self._stop.clear()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True, name="karina-island-snow")
        self._thread.start()
        ready.wait(3)
        return self.ok

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(1.5)
        self._thread, self.ok = None, False

    # ---- поток снега: окно, цикл кадров ----
    def _run(self, ready: threading.Event):
        h = 0
        try:
            ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
            h = _u32.CreateWindowExW(ex, "STATIC", "", WS_POPUP, 0, 0, 1, 1, None, None, None, None)
            if not h:
                raise OSError(f"CreateWindowEx: {ctypes.get_last_error()}")
            self._hwnd, self.ok = int(h), True
        except Exception as e:
            self._trace(f"Остров: снег снаружи недоступен ({e.__class__.__name__}: {e})")
            self.ok = False
        finally:
            ready.set()
        if not self.ok:
            return
        sprites = flake_sprites()
        fl: Flakes | None = None
        shown, last = False, time.time()
        dib = None                                   # (w, h, mem, bmp, bits, old): буфер кадра переживает кадры, пересоздаётся при смене размера
        screen = _u32.GetDC(None)
        msg = wintypes.MSG()
        try:
            while not self._stop.wait(1.0 / FPS):
                while _u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):             # PM_REMOVE: окну нужен цикл сообщений
                    _u32.TranslateMessage(ctypes.byref(msg))
                    _u32.DispatchMessageW(ctypes.byref(msg))
                now = time.time()
                dt, last = min(0.2, now - last), now
                try:
                    g = self.geom()
                except Exception:
                    g = None
                if not g:
                    if shown:
                        _u32.ShowWindow(h, SW_HIDE)
                        shown = False
                    continue
                px, py, pw, ph, sc = g
                side, fall = int(SIDE * sc), int(FALL * sc)
                top = int(py + ph) - 1
                fall = min(fall, int(self.floor()) - top)
                if fall < int(24 * sc) or pw < 8:
                    if shown:
                        _u32.ShowWindow(h, SW_HIDE)
                        shown = False
                    continue
                w = int(pw) + 2 * side
                x0, x1 = side + 0.18 * min(ph, pw), side + pw - 0.18 * min(ph, pw)       # хлопья падают из-под ровной части плашки, не из-под скруглений
                if fl is None:
                    fl = Flakes(w, fall, x0, x1, sc)
                else:
                    fl.resize(w, fall, x0, x1)
                fl.step(dt)
                pix = render(fl, sprites)
                if dib is None or dib[0] != w or dib[1] != fall:
                    self._free(dib)
                    dib = self._make(screen, w, fall)
                if dib is None:
                    continue
                ctypes.memmove(dib[4], pix, w * fall * 4)
                blend = _BLEND(0, 0, 255, 1)                                        # AC_SRC_OVER, 255, AC_SRC_ALPHA
                if not _u32.UpdateLayeredWindow(h, screen, ctypes.byref(_POINT(int(px) - side, top)), ctypes.byref(_SIZE(w, fall)), dib[2],
                                                ctypes.byref(_POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA):
                    raise OSError(f"UpdateLayeredWindow: {ctypes.get_last_error()}")
                if not shown:
                    _u32.ShowWindow(h, SW_SHOWNA)
                    shown = True
        except Exception as e:
            self._trace(f"Остров: снег остановился ({e.__class__.__name__}: {e})")
        finally:
            self.ok = False
            self._free(dib)
            _u32.ReleaseDC(None, screen)
            try:
                _u32.DestroyWindow(h)
            except Exception:
                pass
            self._hwnd = 0

    @staticmethod
    def _make(screen, w, h):
        mem = _g32.CreateCompatibleDC(screen)
        bmi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)           # отрицательная высота: строки сверху вниз
        bits = ctypes.c_void_p()
        bmp = _g32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not bmp or not bits.value:
            _g32.DeleteDC(mem)
            return None
        old = _g32.SelectObject(mem, bmp)
        return (w, h, mem, bmp, bits.value, old)

    @staticmethod
    def _free(dib):
        if not dib:
            return
        _g32.SelectObject(dib[2], dib[5])
        _g32.DeleteObject(dib[3])
        _g32.DeleteDC(dib[2])
