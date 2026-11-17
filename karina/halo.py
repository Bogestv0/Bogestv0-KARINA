"""Сглаженный контур для острова.

Форму окна острова задаёт регион Win32 (SetWindowRgn), а у региона края «лесенкой» — без сглаживания. Чтобы контур
выглядел гладко, прямо под островом лежит второе, невидимое для мыши окно с попиксельной прозрачностью (UpdateLayeredWindow):
в нём нарисована такая же «таблетка» чёрного цвета, но с плавными краями. Регион острова на 1 пиксель меньше, поэтому
по периметру видна именно эта сглаженная кромка.

Не на Windows (и если что-то пошло не так) модуль ничего не делает: остров просто остаётся с обычным краем."""
from __future__ import annotations
import sys
import threading

IS_WIN = sys.platform == "win32"

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x8, 0x20, 0x80, 0x80000, 0x08000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
SW_HIDE, SW_SHOWNA = 0, 8
WM_CLOSE, WM_QUIT = 0x0010, 0x0012
ULW_ALPHA = 0x2


def pill_alpha(w: int, h: int, r: float):
    """Маска скруглённого прямоугольника w×h с радиусом r: байты 0..255, сглаженный край (покрытие пикселя). Возвращает numpy-массив h×w."""
    import numpy as np
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0.0, min(float(r), w / 2, h / 2))
    ys, xs = np.mgrid[0:h, 0:w]
    qx = np.abs(xs + 0.5 - w / 2) - (w / 2 - r)
    qy = np.abs(ys + 0.5 - h / 2) - (h / 2 - r)
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r     # расстояние до края (<0 — внутри)
    return (np.clip(0.5 - d, 0.0, 1.0) * 255 + 0.5).astype(np.uint8)


def pill_bgra(w: int, h: int, r: float) -> bytes:
    """Чёрная «таблетка» в формате BGRA с заранее умноженной прозрачностью (для UpdateLayeredWindow)."""
    import numpy as np
    a = pill_alpha(w, h, r)
    out = np.zeros((a.shape[0], a.shape[1], 4), dtype=np.uint8)      # цвет чёрный, значит умножать нечего
    out[..., 3] = a
    return out.tobytes()


if IS_WIN:
    import ctypes
    from ctypes import wintypes

    # свои копии библиотек: общие ctypes.windll.* делит весь процесс (из-за этого раньше ломалось главное окно)
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
    _u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
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
    _k32.GetCurrentThreadId.restype = wintypes.DWORD


class Halo:
    def __init__(self, trace=lambda s: None):
        self._trace = trace
        self._hwnd = 0
        self._tid = 0
        self._lock = threading.Lock()
        self._key = None                # (w, h, r) последней нарисованной маски
        self._pix = b""
        self.ok = False
        self.tried = False              # попытка создать окно уже была (повторять на каждом кадре анимации не нужно)

    def start(self) -> bool:
        if not IS_WIN or self.ok:
            return self.ok
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True, name="karina-island-halo").start()
        ready.wait(3)
        return self.ok

    def _run(self, ready: threading.Event):
        """Окно создаём в собственном потоке с циклом сообщений: без него система могла бы считать окно «зависшим»."""
        try:
            self._tid = _k32.GetCurrentThreadId()
            ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
            h = _u32.CreateWindowExW(ex, "STATIC", "", WS_POPUP, 0, 0, 1, 1, None, None, None, None)
            if not h:
                raise OSError(f"CreateWindowEx: {ctypes.get_last_error()}")
            self._hwnd = int(h)
            self.ok = True
        except Exception as e:
            self._trace(f"Остров: сглаженный контур недоступен ({e.__class__.__name__}: {e})")
            self.ok = False
        finally:
            ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))
        self.ok = False
        self._hwnd = 0

    def update(self, island_hwnd: int, x: int, y: int, w: int, h: int, r: float) -> bool:
        """Поставить сглаженный контур под остров. False — не получилось (тогда остров рисуется с обычным краем)."""
        if not self.ok or not self._hwnd or w < 2 or h < 2:
            return False
        try:
            with self._lock:
                key = (int(w), int(h), round(float(r), 2))
                if key != self._key or not self._pix:
                    self._pix, self._key = pill_bgra(w, h, r), key
                self._blit(int(x), int(y), int(w), int(h), self._pix)
                _u32.SetWindowPos(self._hwnd, wintypes.HWND(island_hwnd), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)   # сразу под островом
                _u32.ShowWindow(self._hwnd, SW_SHOWNA)
            return True
        except Exception as e:
            self._trace(f"Остров: контур не обновился ({e.__class__.__name__}: {e})")
            self.ok = False
            return False

    def _blit(self, x, y, w, h, pix: bytes):
        screen = _u32.GetDC(None)
        mem = _g32.CreateCompatibleDC(screen)
        bmi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)       # отрицательная высота: строки сверху вниз
        bits = ctypes.c_void_p()
        bmp = _g32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            if not bmp or not bits.value:
                raise OSError("CreateDIBSection")
            ctypes.memmove(bits, pix, w * h * 4)
            old = _g32.SelectObject(mem, bmp)
            blend = _BLEND(0, 0, 255, 1)                                         # AC_SRC_OVER, 255, AC_SRC_ALPHA
            if not _u32.UpdateLayeredWindow(self._hwnd, screen, ctypes.byref(_POINT(x, y)), ctypes.byref(_SIZE(w, h)), mem,
                                            ctypes.byref(_POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA):
                raise OSError(f"UpdateLayeredWindow: {ctypes.get_last_error()}")
            _g32.SelectObject(mem, old)
        finally:
            if bmp:
                _g32.DeleteObject(bmp)
            _g32.DeleteDC(mem)
            _u32.ReleaseDC(None, screen)

    def show(self, visible: bool):
        if IS_WIN and self.ok and self._hwnd:
            try:
                _u32.ShowWindow(self._hwnd, SW_SHOWNA if visible else SW_HIDE)
            except Exception:
                pass

    def stop(self):
        h, tid, self._hwnd, self.ok, self._key, self._pix = self._hwnd, self._tid, 0, False, None, b""
        if IS_WIN and h:
            try:
                _u32.PostMessageW(h, WM_CLOSE, 0, 0)
                _u32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
