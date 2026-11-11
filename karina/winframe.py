"""Безрамочное окно Карины: свой заголовок внутри интерфейса, перетаскивание, растягивание за края,
разворачивание в рабочую область монитора (панель задач не перекрывается) и подгонка размера под экран.

Всё делается через Win32 (ctypes) и работает в физических пикселях, поэтому масштаб Windows (125 %, 150 %…)
и несколько мониторов учитываются сами. Не на Windows модуль ничего не делает."""
from __future__ import annotations
import sys, threading, time

IS_WIN = sys.platform == "win32"

# «базовый» размер окна в логических пикселях и пределы
BASE_W, BASE_H = 1080, 760
MIN_W, MIN_H = 640, 460
FILL = 0.92            # окно занимает не больше этой доли рабочей области

SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
SW_MINIMIZE = 6
WM_CLOSE = 0x0010
GWL_STYLE = -16
WS_MINIMIZEBOX = 0x00020000
VK_LBUTTON = 0x01

if IS_WIN:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]

    def _fix_types():
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    try:
        _fix_types()
    except Exception:
        pass


def enable_dpi_awareness():
    """Чтобы размеры экрана читались в настоящих пикселях (pywebview делает то же самое, повтор безвреден)."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def _monitor_work(hwnd=None):
    """Рабочая область монитора (без панели задач): (left, top, right, bottom) в физических пикселях."""
    if hwnd:
        mon = user32.MonitorFromWindow(hwnd, 2)             # MONITOR_DEFAULTTONEAREST
    else:
        mon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)   # основной монитор
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _scale(hwnd=None) -> float:
    try:
        if hwnd:
            return max(1.0, user32.GetDpiForWindow(hwnd) / 96.0)
    except Exception:
        pass
    try:
        return max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        pass
    try:
        return max(1.0, ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0)
    except Exception:
        return 1.0


def _size_for(work, scale):
    """Размер окна под рабочую область: не больше 1080×760 (в логических px) и не больше 92 % экрана."""
    ww, wh = work[2] - work[0], work[3] - work[1]
    w = int(min(BASE_W * scale, ww * FILL))
    h = int(min(BASE_H * scale, wh * FILL))
    w = max(min(int(MIN_W * scale), ww), w)
    h = max(min(int(MIN_H * scale), wh), h)
    return w, h


def initial_logical_size():
    """Размер для create_window в логических пикселях (точная подгонка делается позже, когда есть окно)."""
    if not IS_WIN:
        return BASE_W, BASE_H
    try:
        enable_dpi_awareness()
        work = _monitor_work()
        s = _scale()
        w, h = _size_for(work, s)
        return max(MIN_W, int(w / s)), max(MIN_H, int(h / s))
    except Exception:
        return BASE_W, BASE_H


def min_logical_size():
    return (MIN_W, MIN_H)


class FrameControl:
    """Управляет окном по его дескриптору. Методы можно вызывать из любого потока."""

    def __init__(self, get_window, on_state=None, trace=lambda s: None):
        self._get_window = get_window
        self._on_state = on_state or (lambda maximized: None)
        self._trace = trace
        self.maximized = False
        self._normal = None            # прежний размер до разворачивания: (x, y, w, h)
        self._dragging = False
        self._lock = threading.Lock()

    # ---- дескриптор ----
    def hwnd(self):
        w = self._get_window()
        nat = getattr(w, "native", None) if w else None
        if nat is None:
            return 0
        try:
            return int(nat.Handle.ToInt64())
        except Exception:
            try:
                return int(nat.Handle.ToInt32())
            except Exception:
                return 0

    def _rect(self, h):
        r = _RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def _place(self, h, x, y, w, ht):
        user32.SetWindowPos(h, None, int(x), int(y), int(w), int(ht), SWP_NOZORDER | SWP_NOACTIVATE)

    # ---- настройка при показе ----
    def setup(self):
        """Вызывать, когда окно уже показано: подгоняет размер под монитор, включает скругления и кнопку «свернуть»."""
        if not IS_WIN:
            return
        h = self.hwnd()
        if not h:
            return
        try:
            style = user32.GetWindowLongW(h, GWL_STYLE)
            user32.SetWindowLongW(h, GWL_STYLE, style | WS_MINIMIZEBOX)     # клик по значку в панели задач сворачивает окно
        except Exception as e:
            self._trace(f"Окно: стиль не изменён ({e})")
        try:
            pref = ctypes.c_int(2)                                            # DWMWCP_ROUND: скруглённые углы Windows 11
            ctypes.windll.dwmapi.DwmSetWindowAttribute(h, 33, ctypes.byref(pref), 4)
        except Exception:
            pass
        self.fit()

    def fit(self):
        """Размер и положение под рабочую область монитора, на котором окно, по центру."""
        if not IS_WIN or self.maximized:
            return
        h = self.hwnd()
        if not h:
            return
        try:
            work = _monitor_work(h)
            s = _scale(h)
            w, ht = _size_for(work, s)
            x = work[0] + ((work[2] - work[0]) - w) // 2
            y = work[1] + ((work[3] - work[1]) - ht) // 2
            self._place(h, x, y, w, ht)
            self._trace(f"Окно: рабочая область {work[2]-work[0]}×{work[3]-work[1]}, масштаб {s:.2f}, окно {w}×{ht}")
        except Exception as e:
            self._trace(f"Окно: подгонка размера не удалась ({e})")

    # ---- кнопки ----
    def minimize(self):
        h = self.hwnd()
        if h:
            user32.ShowWindow(h, SW_MINIMIZE)
        return True

    def bring_to_front(self):
        """Вернуть из свёрнутого и поднять поверх остальных окон (повторный запуск Карины)."""
        h = self.hwnd()
        if not h:
            return False
        try:
            if user32.IsIconic(h):
                user32.ShowWindow(h, 9)                     # SW_RESTORE
            user32.SetForegroundWindow(h)
            return True
        except Exception:
            return False

    def close(self):
        """То же, что крестик в обычном окне: сработает «закрыть в трей» или выход."""
        h = self.hwnd()
        if h:
            user32.PostMessageW(h, WM_CLOSE, 0, 0)
        return True

    def toggle_maximize(self):
        h = self.hwnd()
        if not h:
            return self.maximized
        with self._lock:
            if self.maximized:
                self._restore(h)
            else:
                self._maximize(h)
        self._on_state(self.maximized)
        return self.maximized

    def _maximize(self, h):
        self._normal = self._rect(h)
        l, t, r, b = _monitor_work(h)
        self._place(h, l, t, r - l, b - t)
        self.maximized = True

    def _restore(self, h, at=None):
        if self._normal:
            x, y, w, ht = self._normal
        else:
            work = _monitor_work(h)
            w, ht = _size_for(work, _scale(h))
            x, y = work[0] + 40, work[1] + 40
        if at:                                  # вернуть окно так, чтобы курсор остался на заголовке
            x, y = at(w, ht)
        self._place(h, x, y, w, ht)
        self.maximized = False

    # ---- перетаскивание и растягивание ----
    def begin_drag(self, edge: str):
        """Ведёт окно за курсором, пока зажата левая кнопка мыши. edge: move, n, s, e, w, ne, nw, se, sw."""
        if not IS_WIN or self._dragging:
            return False
        if edge != "move" and self.maximized:
            return False
        h = self.hwnd()
        if not h:
            return False
        self._dragging = True
        threading.Thread(target=self._drag_loop, args=(h, edge), daemon=True, name="karina-drag").start()
        return True

    def _drag_loop(self, h, edge):
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = pt.x, pt.y
            x0, y0, w0, h0 = self._rect(h)
            if edge == "move" and self.maximized:
                # потянули за заголовок развёрнутого окна — оно возвращается к прежнему размеру под курсором
                ratio = (sx - x0) / max(1, w0)
                with self._lock:
                    self._restore(h, at=lambda w, ht: (int(sx - ratio * w), int(sy - 18 * _scale(h))))
                self._on_state(False)
                x0, y0, w0, h0 = self._rect(h)
            work = _monitor_work(h)
            s = _scale(h)
            min_w = min(int(MIN_W * s), work[2] - work[0])
            min_h = min(int(MIN_H * s), work[3] - work[1])
            last = None
            while user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
                user32.GetCursorPos(ctypes.byref(pt))
                dx, dy = pt.x - sx, pt.y - sy
                x, y, w, ht = x0, y0, w0, h0
                if edge == "move":
                    x, y = x0 + dx, y0 + dy
                else:
                    if "e" in edge:
                        w = max(min_w, w0 + dx)
                    if "s" in edge:
                        ht = max(min_h, h0 + dy)
                    if "w" in edge:
                        w = max(min_w, w0 - dx); x = x0 + w0 - w
                    if "n" in edge:
                        ht = max(min_h, h0 - dy); y = y0 + h0 - ht
                cur = (x, y, w, ht)
                if cur != last:
                    self._place(h, *cur)
                    last = cur
                time.sleep(0.008)
            if edge == "move":
                user32.GetCursorPos(ctypes.byref(pt))
                if pt.y <= work[1] + 2 and not self.maximized:      # отпустили у верхнего края — развернуть
                    with self._lock:
                        self._maximize(h)
                    self._on_state(True)
        except Exception as e:
            self._trace(f"Окно: перетаскивание прервано ({e})")
        finally:
            self._dragging = False
