"""Значок в трее: Карина продолжает работать в фоне, когда окно закрыто. Без pystray (или не на Windows) трей не запускается,
и тогда закрытие окна работает как раньше — программа просто закрывается, никто не остаётся «невидимым»."""
from __future__ import annotations
from pathlib import Path


class Tray:
    def __init__(self, title: str, icon_path: Path, on_show, on_quit, log=lambda m: None):
        self.title, self.icon_path, self.on_show, self.on_quit, self.log = title, Path(icon_path), on_show, on_quit, log
        self._icon = None

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image
            image = Image.open(self.icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("Открыть Карину", lambda *_: self.on_show(), default=True),   # default — двойной щелчок по значку
                pystray.MenuItem("Выход", lambda *_: self.on_quit()))
            self._icon = pystray.Icon("bogestv0-karina", image, self.title, menu)
            self._icon.run_detached()
            return True
        except Exception as e:
            self.log(f"Трей не запущен: {e.__class__.__name__}: {e}")
            self._icon = None
            return False

    @property
    def active(self) -> bool:
        return self._icon is not None

    def notify(self, message: str):
        try:
            if self._icon is not None and self._icon.HAS_NOTIFICATION:
                self._icon.notify(message, self.title)
        except Exception:
            pass

    def stop(self):
        icon, self._icon = self._icon, None
        try:
            if icon is not None:
                icon.stop()
        except Exception:
            pass
