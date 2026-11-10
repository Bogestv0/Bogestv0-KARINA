"""Мелкие функции Windows через ctypes (без лишних зависимостей). На других ОС — безопасные заглушки."""
from __future__ import annotations
import os, subprocess, sys

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0

VK = {"mute": 0xAD, "vol_down": 0xAE, "vol_up": 0xAF, "next": 0xB0, "prev": 0xB1, "stop": 0xB2, "play": 0xB3}


def press(vk_name: str, times: int = 1):
    if not IS_WIN:
        return
    import ctypes
    code = VK[vk_name]
    for _ in range(times):
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)


def hotkey(*keys: str):
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.hotkey(*keys)


def paste_text(text: str):
    import pyperclip, pyautogui, time
    pyautogui.FAILSAFE = False
    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


def lock():
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.LockWorkStation()


def list_windows() -> list[tuple[int, str]]:
    """Видимые окна с заголовком: [(hwnd, title)]."""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out: list[tuple[int, str]] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(proc(cb), 0)
    return out


def focus_window(title_part: str) -> bool:
    if not IS_WIN:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    part = title_part.lower()
    for hwnd, title in list_windows():
        if part in title.lower():
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.keybd_event(0x12, 0, 0, 0)  # Alt — обходит запрет Windows на перехват фокуса
            ok = user32.SetForegroundWindow(hwnd)
            user32.keybd_event(0x12, 0, 2, 0)
            return bool(ok)
    return False


def close_by_name(part: str) -> int:
    """Вежливо закрывает окна процессов, у которых имя процесса или заголовок содержит part. Возвращает число."""
    if not IS_WIN or not part.strip():
        return 0
    script = ('$n=$env:KARINA_NAME; Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and '
              '($_.MainWindowTitle -like "*$n*" -or $_.ProcessName -like "*$n*") } | '
              'ForEach-Object { [void]$_.CloseMainWindow(); $_.Id }')
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                           timeout=15, creationflags=NO_WINDOW, env={**os.environ, "KARINA_NAME": part})
        return len([x for x in r.stdout.split() if x.strip().isdigit()])
    except Exception:
        return 0


def start(target: str, args: str = ""):
    """Запускает файл/ярлык/URI/exe, как двойной клик (os.startfile) или с аргументами."""
    if not IS_WIN:
        raise OSError("запуск программ поддерживается только в Windows")
    if args:
        subprocess.Popen(f'"{target}" {args}', shell=True, creationflags=NO_WINDOW)
    else:
        os.startfile(target)  # type: ignore[attr-defined]
