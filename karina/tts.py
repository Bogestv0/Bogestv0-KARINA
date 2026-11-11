"""Голос Карины. Есть интернет — нейросетевой голос Microsoft (edge-tts), нет — системный голос Windows (SAPI)."""
from __future__ import annotations
import asyncio, hashlib, os, re, sys, tempfile, threading, time
from pathlib import Path
from . import net, offline_voice

IS_WIN = sys.platform == "win32"

# Голоса на выбор: id -> (подпись, голос Microsoft, темп %, тон Гц). Темп складывается с ползунком «Скорость речи».
VOICES = {
    "svetlana_cute": {"label": "Светлана — милая", "voice": "ru-RU-SvetlanaNeural", "rate": 4, "pitch": 24},
    "ava_calm": {"label": "Ava — спокойная героиня", "voice": "en-US-AvaMultilingualNeural", "rate": -4, "pitch": 10},
}
DEFAULT_VOICE = "svetlana_cute"


def voice_params(tcfg: dict, voice_id: str | None = None) -> tuple[str, str, str]:
    """(голос Microsoft, темп, тон) для edge-tts по настройкам."""
    v = VOICES.get(voice_id or tcfg.get("voice")) or VOICES[DEFAULT_VOICE]
    rate = v["rate"] + int(tcfg.get("rate", 0)) * 10
    return v["voice"], f"{max(-60, min(60, rate)):+d}%", f"{v['pitch']:+d}Hz"


def prep_text(text: str, tcfg: dict) -> str:
    """Что читать: без кавычек-ёлочек, слова из словаря произношений («discord» → «дискорд») заменяются на «как слышится»."""
    text = re.sub(r"[«»]", "", text).strip()
    for word, say in (tcfg.get("pronounce") or {}).items():
        if word:
            text = re.sub(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lambda m, say=say: say, text, flags=re.I)
    return text


CACHE_MAX_LEN = 160     # длиннее не кэшируем: это уже разовые ответы, а не повторяющиеся фразы
CACHE_MAX_FILES = 400


def cache_name(text: str, voice: str, rate: str, pitch: str) -> str:
    return hashlib.sha1(f"{voice}|{rate}|{pitch}|{text}".encode("utf-8")).hexdigest()[:24] + ".mp3"


def _mci_play(path: str, stop: threading.Event):
    import ctypes
    mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(64)
    mci(f'close karina_tts', None, 0, 0)
    kind = "waveaudio" if path.lower().endswith(".wav") else "mpegvideo"
    if mci(f'open "{path}" type {kind} alias karina_tts', None, 0, 0):
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
        self.cache_dir = Path(cfg.path).parent / "voice_cache"   # рядом с config.json
        self._warm_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def speak(self, text: str):
        t = self.cfg["tts"]
        text = prep_text(text, t)
        if not t.get("enabled", True) or not text:
            return
        self._stop.clear()
        eng = t.get("engine", "auto")
        if eng != "offline" and self._play_cached(text):
            self.engine_used = "online"
            return
        want_online = eng in ("auto", "online") and net.is_online() and time.time() > self._edge_fail_until
        if want_online:
            # выбранный голос; если сервер его не принял — родной русский, и только потом системный
            for vid in dict.fromkeys([t.get("voice") or DEFAULT_VOICE, DEFAULT_VOICE]):
                try:
                    self._edge(text, vid)
                    self.engine_used = "online"
                    return
                except Exception:
                    if self._stop.is_set():
                        return
            self._edge_fail_until = time.time() + 60   # минуту не пытаемся, пока сеть не наладится
        self._offline(text)

    def _offline(self, text: str):
        """Без интернета: нейросетевая «Ирина» (если скачана и включена), иначе системный голос Windows."""
        t = self.cfg["tts"]
        if t.get("offline", "auto") == "auto" and offline_voice.is_ready(self.cfg.path.parent):
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="karina_")
            os.close(fd)
            try:
                offline_voice.synth_wav(self.cfg.path.parent, text, path, int(t.get("rate", 0)))
                if not self._stop.is_set():
                    self._play(path)
                self.engine_used = "piper"
                self.warning = ""
                return
            except Exception as e:
                if self._stop.is_set():
                    return
                self.warning = f"Офлайн-голос не запустился ({e.__class__.__name__}), говорю системным."
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        self._sapi(text)
        self.engine_used = "offline"

    def _play(self, path: str):
        _mci_play(path, self._stop)

    def _cache_file(self, text: str, voice_id: str | None = None) -> Path:
        return self.cache_dir / cache_name(text, *voice_params(self.cfg["tts"], voice_id))

    def _play_cached(self, text: str) -> bool:
        """Готовая запись выбранного голоса: быстрее сети и работает без интернета."""
        if len(text) > CACHE_MAX_LEN:
            return False
        f = self._cache_file(text)
        if not f.is_file() or f.stat().st_size == 0:
            return False
        try:
            os.utime(f)           # недавно использованные дольше живут в кэше
            self._play(str(f))
            return True
        except OSError:
            return False

    def _synth(self, text: str, voice_id: str, dest: Path):
        """Озвучить через Microsoft и атомарно положить в dest."""
        import edge_tts
        voice, rate, pitch = voice_params(self.cfg["tts"], voice_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".part", prefix="karina_", dir=dest.parent)
        os.close(fd)
        try:
            async def gen():
                await asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(tmp), 8)
            asyncio.run(gen())
            if os.path.getsize(tmp) == 0:
                raise OSError("пустой звук")
            os.replace(tmp, dest)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _edge(self, text: str, voice_id: str):
        if len(text) <= CACHE_MAX_LEN:
            dest = self._cache_file(text, voice_id)
            self._synth(text, voice_id, dest)
            if not self._stop.is_set():
                self._play(str(dest))
            return
        dest = Path(tempfile.gettempdir()) / f"karina_{os.getpid()}_{time.time_ns()}.mp3"
        try:
            self._synth(text, voice_id, dest)
            if not self._stop.is_set():
                self._play(str(dest))
        finally:
            try:
                dest.unlink()
            except OSError:
                pass

    # ---------- запас записей на случай «нет интернета» ----------
    def prewarm(self, texts):
        """В фоне озвучивает фиксированные фразы выбранным голосом (пока есть интернет), чтобы без сети они звучали так же."""
        t = self.cfg["tts"]
        if not t.get("enabled", True) or t.get("engine") == "offline":
            return
        threading.Thread(target=self._prewarm, args=(list(texts),), daemon=True, name="karina-voice-cache").start()

    def _prewarm(self, texts):
        with self._warm_lock:
            t = self.cfg["tts"]
            vid = t.get("voice") or DEFAULT_VOICE
            for text in dict.fromkeys(prep_text(x, t) for x in texts):
                if not text or len(text) > CACHE_MAX_LEN:
                    continue
                f = self._cache_file(text, vid)
                if f.is_file():
                    continue
                if not net.is_online():
                    break
                try:
                    self._synth(text, vid, f)
                except Exception:
                    break
                time.sleep(0.15)
            self._prune()

    def _prune(self):
        try:
            files = sorted(self.cache_dir.glob("*.mp3"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[CACHE_MAX_FILES:]:
                f.unlink()
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
