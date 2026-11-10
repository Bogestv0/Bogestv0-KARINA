"""Голос Карины. Есть интернет — нейросетевой голос Microsoft (edge-tts), нет — системный голос Windows (SAPI)."""
from __future__ import annotations
import asyncio, os, re, sys, tempfile, threading, time
from . import net

IS_WIN = sys.platform == "win32"


def _mci_play(path: str, stop: threading.Event):
    import ctypes
    mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(64)
    mci(f'close karina_tts', None, 0, 0)
    if mci(f'open "{path}" type mpegvideo alias karina_tts', None, 0, 0):
        raise OSError("не удалось открыть звук")
    try:
        mci('play karina_tts', None, 0, 0)
        while not stop.is_set():
            mci('status karina_tts mode', buf, 63, 0)
            if buf.value != "playing":
                break
            time.sleep(0.05)
    finally:
        mci('close karina_tts', None, 0, 0)


class Speaker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine_used = ""
        self.warning = ""
        self._stop = threading.Event()
        self._edge_fail_until = 0.0

    def stop(self):
        self._stop.set()

    def speak(self, text: str):
        t = self.cfg["tts"]
        text = re.sub(r"[«»]", "", text).strip()
        if not t.get("enabled", True) or not text:
            return
        self._stop.clear()
        eng = t.get("engine", "auto")
        want_online = eng in ("auto", "online") and net.is_online() and time.time() > self._edge_fail_until
        if want_online:
            try:
                self._edge(text)
                self.engine_used = "online"
                return
            except Exception:
                self._edge_fail_until = time.time() + 60   # минуту не пытаемся, пока сеть не наладится
        self._sapi(text)
        self.engine_used = "offline"

    def _edge(self, text: str):
        import edge_tts
        rate = int(self.cfg["tts"].get("rate", 0)) * 10
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="karina_")
        os.close(fd)
        try:
            async def gen():
                await asyncio.wait_for(edge_tts.Communicate(text, self.cfg["tts"]["voice_online"], rate=f"{rate:+d}%").save(path), 8)
            asyncio.run(gen())
            if self._stop.is_set():
                return
            _mci_play(path, self._stop)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _sapi(self, text: str):
        if not IS_WIN:
            print("[Карина]", text)
            return
        import comtypes, comtypes.client
        comtypes.CoInitialize()
        try:
            v = comtypes.client.CreateObject("SAPI.SpVoice")
            voices = v.GetVoices()
            ru, pick = [], None
            for i in range(voices.Count):
                it = voices.Item(i)
                lang = ""
                try:
                    lang = it.GetAttribute("Language")
                except Exception:
                    pass
                if lang.split(";")[0].strip().lower() in ("419",) or "russian" in it.GetDescription().lower():
                    ru.append(it)
            for it in ru:
                if re.search(r"irina|ekaterina|svetlana|ирина|екатерина", it.GetDescription(), re.I):
                    pick = it
                    break
            pick = pick or (ru[0] if ru else None)
            if pick is not None:
                v.Voice = pick
                self.warning = ""
            else:
                self.warning = "В Windows нет русского голоса: Параметры → Время и язык → Речь → Добавить голоса."
            v.Rate = max(-10, min(10, int(self.cfg["tts"].get("rate", 0))))
            v.Volume = 100
            v.Speak(text, 0)
        finally:
            comtypes.CoUninitialize()
