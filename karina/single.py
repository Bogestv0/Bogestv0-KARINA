"""Один экземпляр Карины. Пока она в трее, повторный запуск не должен поднимать вторую копию (два микрофона, два голоса):
он только просит первую показать окно и выходит. Только Windows; при любой ошибке запуску не мешаем."""
from __future__ import annotations
import sys, threading

MUTEX = "Local\\Bogestv0KARINA.single"
EVENT = "Local\\Bogestv0KARINA.show"
_ALREADY_EXISTS = 183
_ACCESS_DENIED = 5               # мьютекс создан процессом с другими правами (например, «от администратора») — значит, Карина уже запущена
WINDOW_TITLE = "Bogestv0 KARINA"
_EVENT_MODIFY_STATE = 0x0002
_keep: list = []


def _k32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def _file_lock() -> bool:
    """Вторая, независимая защита: блокировка файла в папке данных (видна и между разными правами/сеансами).
    True — блокировку взяли (или проверить нельзя); False — её держит другой экземпляр."""
    try:
        import msvcrt
        from .config import DATA
        DATA.mkdir(parents=True, exist_ok=True)
        f = open(DATA / "karina.lock", "a+b")
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.close()
            return False
        _keep.append(f)                     # держим до конца процесса: Windows снимет блокировку сама, даже если Карина упадёт
        return True
    except Exception:
        return True


def acquire(wait: float = 0.0) -> bool:
    """True — мы первый экземпляр (или проверить не удалось). False — Карина уже запущена.
    wait — сколько секунд ждать, пока прежний экземпляр освободится (после обновления он ещё доживает).
    Пока ждём, просим прежний экземпляр показать окно (он мог ещё только запускаться и не успеть создать сигнал)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes, time
        k = _k32()
        deadline = time.time() + wait
        signalled = False
        while True:
            ctypes.set_last_error(0)
            h = k.CreateMutexW(None, False, MUTEX)
            err = ctypes.get_last_error()
            if not h and err != _ACCESS_DENIED:
                return True                 # проверить не получилось — запуску не мешаем
            exists = (not h) or err == _ALREADY_EXISTS
            if not exists:
                if _file_lock():
                    _keep.append(h)         # держим до конца процесса
                    return True
                k.CloseHandle(h)            # мьютекс есть у нас, но файл держит другой процесс — это он
                exists = True
            elif h:
                k.CloseHandle(h)            # чужой мьютекс не удерживаем: пусть исчезнет вместе с прежним процессом
            if not signalled:
                signalled = ask_existing_to_show()
            if time.time() >= deadline:
                return False
            time.sleep(0.25)
    except Exception:
        return True


def _raise_window_by_title() -> bool:
    """Запасной путь, если сигнал недоступен: найти окно Карины по заголовку и поднять его."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        hwnd = u.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return False
        u.ShowWindow(hwnd, 9)               # SW_RESTORE: показать (в том числе из трея) и вернуть из свёрнутого
        u.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def ask_existing_to_show() -> bool:
    """Из второго экземпляра: попросить первый показать окно. True — просьбу передали."""
    if sys.platform != "win32":
        return False
    try:
        k = _k32()
        h = k.OpenEventW(_EVENT_MODIFY_STATE, False, EVENT)
        if h:
            k.SetEvent(h)
            k.CloseHandle(h)
            return True
    except Exception:
        pass
    return _raise_window_by_title()


def prepare() -> None:
    """Первый экземпляр: создать сигнал «покажи окно» сразу при запуске, чтобы просьбы, пришедшие во время загрузки, не терялись."""
    if sys.platform != "win32":
        return
    try:
        ev = _k32().CreateEventW(None, False, False, EVENT)
        if ev:
            _keep.append(ev)
    except Exception:
        pass


def listen_for_show(callback, stop: threading.Event | None = None) -> None:
    """В первом экземпляре: фоновый поток, вызывающий callback, когда запустили вторую копию."""
    if sys.platform != "win32":
        return
    try:
        k = _k32()
        ev = k.CreateEventW(None, False, False, EVENT)       # автосброс
        if not ev:
            return
        _keep.append(ev)
    except Exception:
        return
    stop = stop or threading.Event()

    def loop():
        while not stop.is_set():
            try:
                if k.WaitForSingleObject(ev, 1000) == 0:     # WAIT_OBJECT_0
                    callback()
            except Exception:
                return
    threading.Thread(target=loop, daemon=True, name="karina-single").start()
