"""Распознавание речи: faster-whisper, полностью офлайн (модель скачивается один раз)."""
from __future__ import annotations
import re
import numpy as np
from .config import DATA
from .textutil import norm

# типичные «галлюцинации» Whisper на тишине и шуме
_JUNK = ("субтитр", "продолжение следует", "спасибо за просмотр", "подписывайтесь", "редактор субтитров",
         "dimatorzok", "amara.org")


class STT:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.loaded_name = None

    def load(self):
        from faster_whisper import WhisperModel
        name = self.cfg["stt"].get("model", "small")
        if self.model is not None and self.loaded_name == name:
            return
        self.model = WhisperModel(name, device="auto", compute_type="int8", download_root=str(DATA / "models"))
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
