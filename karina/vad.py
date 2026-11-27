"""Нейросетевое определение речи (Silero VAD) без новых зависимостей: модель лежит в пакете faster-whisper, запускается через onnxruntime.

Зачем: простой детектор по громкости принимает за речь музыку, клавиатуру и вентилятор и пропускает тихий голос. Нейросеть различает именно голос.
Если модель или onnxruntime недоступны, Карина молча возвращается к прежнему детектору по громкости (см. audio._Channel._process).
"""
from __future__ import annotations
import glob, importlib.util, os, threading
import numpy as np

WINDOW = 512        # столько отсчётов (32 мс при 16 кГц) принимает модель за раз
CONTEXT = 64        # плюс хвост предыдущего окна

_lock = threading.Lock()
_state: dict = {"session": None, "failed": False, "kind": ""}


def _model_path() -> str | None:
    spec = importlib.util.find_spec("faster_whisper")
    if not spec or not spec.origin:
        return None
    found = sorted(glob.glob(os.path.join(os.path.dirname(spec.origin), "assets", "silero_vad*.onnx")))
    return found[-1] if found else None


def _session():
    with _lock:
        if _state["session"] is not None or _state["failed"]:
            return _state["session"]
        try:
            import onnxruntime as ort
            path = _model_path()
            if not path:
                raise FileNotFoundError("silero_vad*.onnx не найден в faster_whisper/assets")
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = opts.intra_op_num_threads = 1
            opts.enable_cpu_mem_arena = False
            opts.log_severity_level = 4
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
            names = {i.name for i in sess.get_inputs()}
            if {"input", "h", "c"} <= names:
                _state["kind"] = "hc"                 # экспорт из faster-whisper: состояния h и c
            elif {"input", "state", "sr"} <= names:
                _state["kind"] = "state"              # официальный Silero VAD v5
            else:
                raise RuntimeError("неизвестные входы модели: " + ", ".join(sorted(names)))
            _state["session"] = sess
        except Exception:
            _state["failed"] = True
        return _state["session"]


def available() -> bool:
    return _session() is not None


class NeuralVAD:
    """Один поток звука (один микрофон): копит отсчёты, на каждое готовое окно в 512 отсчётов обновляет вероятность «это голос»."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._buf = np.zeros(0, dtype=np.float32)
        self._ctx = np.zeros(CONTEXT, dtype=np.float32)
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._st = np.zeros((2, 1, 128), dtype=np.float32)
        self.prob = 0.0

    def feed(self, x: np.ndarray) -> float:
        """Принимает кадр 16 кГц, возвращает последнюю известную вероятность речи (0…1)."""
        sess = _session()
        if sess is None:
            return self.prob
        self._buf = np.concatenate([self._buf, np.asarray(x, dtype=np.float32).reshape(-1)])
        kind = _state["kind"]
        while len(self._buf) >= WINDOW:
            win, self._buf = self._buf[:WINDOW], self._buf[WINDOW:]
            inp = np.concatenate([self._ctx, win]).reshape(1, -1).astype(np.float32)
            self._ctx = win[-CONTEXT:]
            if kind == "hc":
                out, self._h, self._c = sess.run(None, {"input": inp, "h": self._h, "c": self._c})
            else:
                out, self._st = sess.run(None, {"input": inp, "state": self._st, "sr": np.array(16000, dtype=np.int64)})
            self.prob = float(np.asarray(out).reshape(-1)[-1])
        return self.prob
