"""Распознавание речи: faster-whisper, полностью офлайн (модель скачивается один раз)."""
from __future__ import annotations
import re, socket, threading, time
import numpy as np
from .config import DATA
from .textutil import norm

# типичные «галлюцинации» Whisper на тишине и шуме
_JUNK = ("субтитр", "продолжение следует", "спасибо за просмотр", "подписывайтесь", "редактор субтитров",
         "dimatorzok", "amara.org")


def hf_reachable(timeout: float = 6.0) -> tuple[bool, str]:
    try:
        socket.create_connection(("huggingface.co", 443), timeout=timeout).close()
        return True, ""
    except OSError as e:
        return False, str(e) or e.__class__.__name__


def _poll_size(root, progress, done: threading.Event):
    """Раз в пару секунд сообщает, сколько мегабайт модели уже скачано."""
    while not done.wait(2.0):
        try:
            total = sum(f.stat().st_size for d in root.glob("models--*") for f in d.rglob("*") if f.is_file())
            progress(int(total / 1048576))
        except OSError:
            pass


class STT:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.loaded_name = None

    def load(self, progress=None):
        """Загружает модель. Если её ещё нет на диске — проверяет доступность huggingface.co и скачивает,
        сообщая прогресс через progress(МБ). Если скачать нельзя — понятная ошибка, а не вечное «загружаю»."""
        from faster_whisper import WhisperModel
        name = self.cfg["stt"].get("model", "small")
        if self.model is not None and self.loaded_name == name:
            return
        root = DATA / "models"
        try:
            self.model = WhisperModel(name, device="auto", compute_type="int8", download_root=str(root), local_files_only=True)
        except Exception as first:
            self.model = None
            ok, why = hf_reachable()
            if not ok:
                raise RuntimeError(f"модель распознавания ещё не скачана, а сайт huggingface.co недоступен ({why}). "
                                   "Проверьте интернет; если сайт у вас закрыт — включите VPN на время первого запуска "
                                   f"(скачивается один раз, ~0,5 ГБ). Исходная ошибка: {first}") from first
            done = threading.Event()
            if progress:
                threading.Thread(target=_poll_size, args=(root, progress, done), daemon=True, name="stt-download-progress").start()
            try:
                self.model = WhisperModel(name, device="auto", compute_type="int8", download_root=str(root))
            finally:
                done.set()
        self.loaded_name = name

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        hint = f"{self.cfg['name']}, открой, закрой, запусти, громкость, найди в интернете, сделай скриншот."
        segs, _ = self.model.transcribe(audio, language="ru", beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False, initial_prompt=hint,
                                        no_speech_threshold=0.6, temperature=0.0)
        parts = [s.text.strip() for s in segs if s.no_speech_prob < 0.7]
        text = " ".join(parts).strip()
        low = text.lower()
        if len(norm(text)) < 2 or any(j in low for j in _JUNK):
            return ""
        return text
