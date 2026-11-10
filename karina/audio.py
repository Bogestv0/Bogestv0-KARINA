"""Захват микрофона и выделение фраз (простой адаптивный детектор речи, без внешних моделей)."""
from __future__ import annotations
import collections, threading
import numpy as np

SR = 16000
FRAME = 480  # 30 мс


class Listener:
    def __init__(self, cfg, on_utterance, on_level):
        self.cfg, self.on_utterance, self.on_level = cfg, on_utterance, on_level
        self.muted = False       # пока говорит Карина или слушание выключено
        self.stream = None
        self._reset()
        self.nf = 0.004          # уровень фонового шума
        self._lvl_ts = 0.0

    def _reset(self):
        self.pre = collections.deque(maxlen=10)
        self.buf: list = []
        self.speaking = False
        self.run = 0
        self.silence = 0

    @staticmethod
    def devices() -> list[dict]:
        import sounddevice as sd
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                out.append({"id": i, "name": d["name"]})
        return out

    def start(self):
        import sounddevice as sd
        self.stop()
        dev = self.cfg["stt"].get("mic_device")
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME,
                                     device=dev if dev not in (None, "") else None, callback=self._cb)
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self._reset()

    def _cb(self, indata, frames, t, status):
        import time
        x = indata[:, 0].copy()
        if self.muted:
            self._reset()
            return
        rms = float(np.sqrt(np.mean(x * x)))
        now = time.time()
        if now - self._lvl_ts > 0.06:
            self._lvl_ts = now
            self.on_level(min(1.0, rms * 10))
        base = float(self.cfg["stt"].get("vad_threshold", 0.012))
        voiced = rms > max(self.nf * 3.0, base)
        if not voiced:
            self.nf = min(0.05, 0.97 * self.nf + 0.03 * rms)
        if not self.speaking:
            self.pre.append(x)
            self.run = self.run + 1 if voiced else 0
            if self.run >= 3:
                self.speaking, self.silence = True, 0
                self.buf = list(self.pre)
            return
        self.buf.append(x)
        self.silence = 0 if voiced else self.silence + 1
        n = len(self.buf)
        if self.silence >= 27 or n >= 500:   # 0.8 c тишины или 15 c речи
            audio = np.concatenate(self.buf)
            self._reset()
            if len(audio) / SR >= 0.45:
                self.on_utterance(audio)
