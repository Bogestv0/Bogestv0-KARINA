"""Музыка для острова: что играет сейчас и кнопки «пауза» / «следующий».

Источник — системный медиа-сеанс Windows (SMTC): его показывают Spotify, Яндекс Музыка, браузер, плеер Windows.
Читаем его через пакеты winrt (необязательные: без них название трека на плашке не появится, но кнопки
по-прежнему работают через мультимедийные клавиши). Всё выполняется в одном своём потоке, поэтому с asyncio
и WinRT не возникает путаницы между потоками."""
from __future__ import annotations
import queue
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
COMMANDS = {"toggle": "play", "next": "next", "prev": "prev"}        # команда -> имя мультимедийной клавиши (winutil.press)


class _Winrt:
    """Чтение и управление через WinRT. Вызывается только из потока Media."""

    def __init__(self):
        import asyncio
        from winrt.windows.media.control import (GlobalSystemMediaTransportControlsSessionManager as Manager,
                                                 GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status)
        self.loop = asyncio.new_event_loop()
        self.Manager, self.Status = Manager, Status
        self.mgr = None

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _session(self):
        if self.mgr is None:
            self.mgr = await self.Manager.request_async()
        return self.mgr.get_current_session()

    def poll(self):
        async def go():
            s = await self._session()
            if s is None:
                return None
            p = await s.try_get_media_properties_async()
            playing = s.get_playback_info().playback_status == self.Status.PLAYING
            return {"title": p.title or "", "artist": p.artist or "", "status": "playing" if playing else "paused",
                    "app": s.source_app_user_model_id or ""}
        return self._run(go())

    def command(self, name: str) -> bool:
        async def go():
            s = await self._session()
            if s is None:
                return False
            fn = {"toggle": s.try_toggle_play_pause_async, "next": s.try_skip_next_async, "prev": s.try_skip_previous_async}[name]
            return bool(await fn())
        return bool(self._run(go()))


def clean_info(raw: dict | None) -> dict | None:
    """Приводит ответ источника к виду для страницы. Нет названия и ничего не играет — значит, показывать нечего."""
    if not raw:
        return None
    title = " ".join(str(raw.get("title") or "").split())[:120]
    artist = " ".join(str(raw.get("artist") or "").split())[:80]
    status = "playing" if raw.get("status") == "playing" else "paused"
    if not title and not artist:
        return None
    return {"title": title or artist, "artist": artist if title else "", "status": status, "app": str(raw.get("app") or "")[:80]}


class Media:
    def __init__(self, on_change=lambda info: None, trace=lambda s: None, backend=None, press=None, interval: float = 1.5):
        self.on_change, self._trace, self._backend, self._press, self.interval = on_change, trace, backend, press, interval
        self.info: dict | None = None
        self._cmds: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="karina-island-media")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._thread = None
        self.info = None

    def command(self, name: str) -> bool:
        """Пауза/продолжить, следующий, предыдущий. Выполняется в потоке Media; страница узнает результат из обновления состояния."""
        if name not in COMMANDS:
            return False
        self._cmds.put(name)
        self._wake.set()
        return True

    # ---- поток ----
    def _make_backend(self):
        if self._backend is not None or not IS_WIN:
            return self._backend
        try:
            self._backend = _Winrt()
        except Exception as e:
            self._trace(f"Музыка: winrt недоступен, название трека показывать не буду ({e.__class__.__name__}: {e})")
        return self._backend

    def _press_key(self, name: str):
        try:
            if self._press is not None:
                self._press(name)
            else:
                from . import winutil
                winutil.press(name)
        except Exception as e:
            self._trace(f"Музыка: клавиша «{name}» не сработала ({e})")

    def _loop(self):
        backend = self._make_backend()
        while not self._stop.is_set():
            try:
                while True:
                    name = self._cmds.get_nowait()
                    ok = False
                    if backend is not None:
                        try:
                            ok = backend.command(name)
                        except Exception as e:
                            self._trace(f"Музыка: команда {name} не прошла ({e.__class__.__name__}: {e})")
                    if not ok:
                        self._press_key(COMMANDS[name])
                    time.sleep(0.25)                        # плееру нужно мгновение, чтобы сменить состояние
            except queue.Empty:
                pass
            if backend is not None:
                try:
                    info = clean_info(backend.poll())
                except Exception as e:
                    info = None
                    self._trace(f"Музыка: не удалось прочитать сеанс ({e.__class__.__name__}: {e})")
                if info != self.info:
                    self.info = info
                    try:
                        self.on_change(info)
                    except Exception:
                        pass
            self._wake.wait(self.interval)           # команда будит поток сразу
            self._wake.clear()
        self._trace("Музыка: поток остановлен")
