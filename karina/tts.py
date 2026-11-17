"""Голос Карины: один голос — Silero «Байя» (локально, без интернета). Пока он не скачан или нет torch, временно говорит системный голос Windows (SAPI)."""
from __future__ import annotations
import os, queue, re, sys, tempfile, threading, time
from . import silero_voice

IS_WIN = sys.platform == "win32"

# Голос один: Silero «Байя». В интерфейсе он называется «Карина». «voice» — только ключ внутри настроек.
VOICES = {"karina": {"label": "Карина", "engine": "silero", "speaker": "baya"}}
DEFAULT_VOICE = "karina"


def prep_text(text: str, tcfg: dict, stress: bool = False) -> str:
    """Что читать: без кавычек-ёлочек, слова из словаря произношений («discord» → «дискорд») заменяются на «как слышится».
    Знак ударения (U+0301) нужен только Silero; системный голос его не понимает и коверкает слово («Кареена»), поэтому без stress=True он убирается."""
    text = re.sub(r"[«»]", "", text).strip()
    for word, say in (tcfg.get("pronounce") or {}).items():
        if word:
            text = re.sub(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lambda m, say=say: say, text, flags=re.I)
    return text if stress else text.replace("\u0301", "")


def split_speech(text: str, limit: int = 220) -> list[str]:
    """Режет ответ для потоковой озвучки: первое предложение — отдельно (чтобы заговорить как можно раньше), остальное — кусками до limit знаков."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return [text] if text else []
    sents = re.split(r"(?<=[.!?…])\s+", text)
    first, rest = sents[0], " ".join(sents[1:])
    return silero_voice.split_text(first, limit) + (silero_voice.split_text(rest, limit) if rest else [])


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
        self.out_warning = ""      # проблема с выбранным устройством вывода (вкладка «Звук»)
        self._stop = threading.Event()
        self._lock = threading.Lock()      # один голос за раз: вторая реплика ждёт, пока договорит первая
        self.speaking = False

    def stop(self):
        self._stop.set()

    def speak(self, text: str):
        t = self.cfg["tts"]
        stressed = prep_text(text, t, stress=True)
        text = prep_text(text, t)
        if not t.get("enabled", True) or not text:
            return
        with self._lock:
            self._stop.clear()
            self.speaking = True
            try:
                self._speak(text, stressed, t)
            finally:
                self.speaking = False

    def _speak(self, text: str, stressed: str, t: dict):
        if self._silero(stressed, VOICES[DEFAULT_VOICE]):
            self.engine_used = "silero"
            return
        self._sapi(text)                 # Silero ещё не скачан или нет torch: временно говорит системный голос, причина — в self.warning
        self.engine_used = "offline"

    def _pipeline(self, parts: list[str], synth) -> tuple[int, Exception | None]:
        """Озвучивает куски по порядку; пока играет один, готовится следующий (конвейер) — длинный ответ начинается сразу, а не после синтеза целиком.
        synth(часть, путь_для_записи) → (путь_готового_файла, удалить_после). Возвращает (сколько кусков прозвучало, ошибка или None)."""
        q: queue.Queue = queue.Queue(maxsize=2)
        err: list = []

        def rm(path):
            try:
                os.remove(path)
            except OSError:
                pass

        def producer():
            try:
                for part in parts:
                    if self._stop.is_set() or err:
                        break
                    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="karina_")
                    os.close(fd)
                    try:
                        path, temp = synth(part, tmp)
                    except Exception as e:
                        err.append(e)
                        rm(tmp)
                        break
                    if path != tmp:
                        rm(tmp)                      # запись легла в другое место (кэш, mp3) — пустой временный файл не нужен
                    q.put((path, temp))
            except BaseException as e:               # на всякий случай: потребитель ждёт сигнала конца
                err.append(e)
            finally:
                q.put(None)

        threading.Thread(target=producer, daemon=True, name="karina-tts-synth").start()
        played = 0
        while True:
            item = q.get()
            if item is None:
                break
            path, temp = item
            try:
                if not self._stop.is_set() and not err:
                    self._play(path)
                    played += 1
            except Exception as e:
                err.append(e)                        # дальше не играем, но очередь дочитываем до конца, чтобы не оставить мусор
            finally:
                if temp:
                    rm(path)
        return played, (err[0] if err else None)

    def _silero(self, text: str, v: dict) -> bool:
        """Говорит локальным голосом Silero по предложениям. False — не вышло (нет torch или модели): тогда говорит обычный голос, а причина видна в предупреждении."""
        base = self.cfg.path.parent
        if not silero_voice.is_ready(base):
            self.warning = "Голос Silero ещё скачивается (или не скачан): Настройки → «Голос» → «Скачать голос Silero». Пока говорю обычным голосом."
            return False
        if not silero_voice.torch_available():
            self.warning = silero_voice.torch_hint()
            return False
        speaker, rate = v.get("speaker", "baya"), int(self.cfg["tts"].get("rate", 0))

        def synth(part, tmp):
            silero_voice.synth_wav(base, part, tmp, speaker, rate)
            return tmp, True
        try:
            played, err = self._pipeline(split_speech(text), synth)
        except Exception as e:
            played, err = 0, e
        if self._stop.is_set() or (err is None):
            self.warning = ""
            return True
        if played:                                   # начали говорить и споткнулись на середине — договаривать другим голосом поздно
            self.warning = f"Голос Silero оборвался ({err.__class__.__name__}: {err})."
            return True
        self.warning = f"Голос Silero не запустился ({err.__class__.__name__}: {err}), говорю обычным голосом."
        return False

    def _play(self, path: str):
        """Играет файл на устройство из вкладки «Звук». Устройство Windows по умолчанию на полной громкости — системным
        проигрывателем, как раньше; иначе — через sounddevice. Если не вышло, говорим через устройство по умолчанию."""
        from . import playback
        dev, vol, found = playback.resolve_output(self.cfg)
        if not found:
            self.out_warning = "Выбранное устройство вывода не найдено — говорю через устройство Windows по умолчанию."
        elif self.out_warning.startswith("Выбранное"):
            self.out_warning = ""
        if dev is None and vol >= 1.0:
            _mci_play(path, self._stop)
            return
        try:
            playback.play_file(path, dev, vol, self._stop)
            if found:
                self.out_warning = ""
        except Exception as e:
            if self._stop.is_set():
                return
            self.out_warning = f"Не удалось играть на выбранное устройство ({e.__class__.__name__}: {e}) — говорю через устройство по умолчанию."
            _mci_play(path, self._stop)

    def _sapi_volume(self) -> int:
        try:
            return max(0, min(100, int((self.cfg.get("audio_out") or {}).get("volume", 100))))
        except (TypeError, ValueError):
            return 100

    def _sapi_output(self, v):
        """Системный голос тоже направляем на выбранное устройство (по имени; если не нашли — остаётся по умолчанию)."""
        want = str((self.cfg.get("audio_out") or {}).get("name") or "").strip().lower()
        if not want:
            return
        try:
            outs = v.GetAudioOutputs()
            for i in range(outs.Count):
                d = outs.Item(i).GetDescription().lower()
                if want in d or d.startswith(want):
                    v.AudioOutput = outs.Item(i)
                    return
        except Exception:
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
            v.Volume = self._sapi_volume()
            self._sapi_output(v)
            v.Speak(text, 0)
        finally:
            comtypes.CoUninitialize()
