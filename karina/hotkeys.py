"""Глобальные горячие клавиши (Windows): «говорить без имени» и «замолчи». Работают, даже когда окно Карины свёрнуто.

По умолчанию: Ctrl+Alt+A — слушать следующую фразу без обращения по имени; Ctrl+Alt+S — прервать речь Карины;
Ctrl+Alt+I — раскрыть/свернуть остров; Ctrl+Alt+X — выбрать область экрана (остров).
Меняются в острове (вкладка «Вид») или в config.json: "hotkeys": {"enabled": true, "talk": "ctrl+alt+a", ...}. Пустое значение выключает клавишу.
Занятое сочетание пропускается, причина идёт в журнал.
"""
from __future__ import annotations
import re, sys, threading

MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
KEYS = {"space": 0x20, "esc": 0x1B, "escape": 0x1B, "pause": 0x13, "tab": 0x09, "enter": 0x0D, "backspace": 0x08,
        "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000
DEFAULTS = {"talk": "ctrl+alt+a", "stop": "ctrl+alt+s", "island": "ctrl+alt+i", "snip": "ctrl+alt+x"}


def parse(combo: str) -> tuple[int, int] | None:
    """«ctrl+alt+a» → (модификаторы, код клавиши). Нужна хотя бы одна модификаторная клавиша (иначе перехватим обычную клавишу у всех программ)."""
    mods, vk = 0, None
    for part in re.split(r"\s*\+\s*", str(combo or "").strip().lower()):
        if not part:
            continue
        if part in MOD:
            mods |= MOD[part]
        elif part in KEYS:
            vk = KEYS[part]
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = 0x70 + int(part[1:]) - 1
        elif re.fullmatch(r"[a-z0-9]", part):
            vk = ord(part.upper())
        else:
            return None
    return (mods, vk) if mods and vk else None


class Hotkeys:
    """actions — {имя: функция}; имена те же, что в config["hotkeys"]. Каждая клавиша вызывает свою функцию в потоке цикла сообщений."""

    def __init__(self, cfg, actions: dict, log=lambda m: None):
        self.cfg, self.actions, self.log = cfg, actions, log
        self.errors: list[str] = []
        self._tid = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def start(self):
        hk = self.cfg.get("hotkeys") or {}
        if not self.supported or not hk.get("enabled", True) or self._thread:
            return
        self.errors = []
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="karina-hotkeys")
        self._thread.start()
        self._ready.wait(3)

    def stop(self):
        t = self._thread
        if self._tid and self.supported:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if t is not None and t is not threading.current_thread():
            t.join(1.5)                                  # старый поток должен снять сочетания, иначе новый не сможет их занять
        self._thread, self._tid = None, 0

    def reload(self):
        """Перечитать сочетания из настроек (поменяли в острове): снять старые и занять новые."""
        self.stop()
        self.start()

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        hk = self.cfg.get("hotkeys") or {}
        ids: dict[int, str] = {}
        for i, name in enumerate(self.actions, 1):
            combo = hk.get(name, DEFAULTS.get(name))
            parsed = parse(combo) if combo else None
            if not parsed:
                if combo:
                    self.errors.append(f"сочетание «{combo}» ({name}) не разобрано")
                continue
            if user32.RegisterHotKey(None, i, parsed[0] | MOD_NOREPEAT, parsed[1]):
                ids[i] = name
            else:
                self.errors.append(f"сочетание «{combo}» ({name}) занято другой программой")
        for e in self.errors:
            self.log("Горячие клавиши: " + e)
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    try:
                        self.actions[ids[msg.wParam]]()
                    except Exception as e:
                        self.log(f"Горячая клавиша {ids[msg.wParam]}: {e}")
        finally:
            for i in ids:
                user32.UnregisterHotKey(None, i)
