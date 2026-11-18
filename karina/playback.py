"""Воспроизведение звука Карины на выбранное устройство вывода и с заданной громкостью.

Системный проигрыватель (MCI) умеет только устройство Windows «по умолчанию», поэтому для другого устройства файл
декодируется (PyAV — он уже есть вместе с faster-whisper) и играется через sounddevice.
"""
from __future__ import annotations
import threading
import numpy as np

BLOCK = 2048


def resolve_output(cfg):
    """Настройки вывода → (устройство для sounddevice или None, громкость 0..1, найдено ли выбранное устройство)."""
    from .audio import list_devices, resolve_device
    o = cfg.get("audio_out") or {}
    try:
        vol = max(0, min(100, int(o.get("volume", 100)))) / 100.0
    except (TypeError, ValueError):
        vol = 1.0
    if o.get("device") is None and not o.get("name"):
        return None, vol, True
    try:
        dev, found = resolve_device(o.get("device"), str(o.get("name") or ""), list_devices("output"))
    except Exception:
        return None, vol, False
    return dev, vol, found


def decode(path: str, rate: int, channels: int) -> np.ndarray:
    """Файл (mp3/wav…) → float32 (отсчёты, каналы) с нужной частотой."""
    import av
    layout = "stereo" if channels == 2 else "mono"
    chunks = []
    with av.open(path) as c:
        res = av.AudioResampler(format="flt", layout=layout, rate=rate)
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1, channels))
        for f in res.resample(None):
            chunks.append(f.to_ndarray().reshape(-1, channels))
    return np.concatenate(chunks) if chunks else np.zeros((0, channels), dtype=np.float32)


def _open_info(device):
    import sounddevice as sd
    info = sd.query_devices(device) if device is not None else sd.query_devices(kind="output")
    return int(info["default_samplerate"]), (2 if info["max_output_channels"] >= 2 else 1)


def play_array(data: np.ndarray, rate: int, device, volume: float, stop: threading.Event):
    """Играет готовый массив (отсчёты, каналы). Прерывается по stop."""
    import sounddevice as sd
    ch = data.shape[1]
    data = np.clip(data * float(volume), -1.0, 1.0).astype(np.float32)
    st = sd.OutputStream(samplerate=rate, channels=ch, dtype="float32", device=device)
    st.start()
    try:
        pos = 0
        while pos < len(data) and not stop.is_set():
            st.write(data[pos:pos + BLOCK])
            pos += BLOCK
        if stop.is_set():
            st.abort()
        else:
            st.stop()          # дожидается, пока доиграют последние отсчёты
    finally:
        st.close()


def play_file(path: str, device, volume: float, stop: threading.Event):
    rate, ch = _open_info(device)
    play_array(decode(path, rate, ch), rate, device, volume, stop)


def chime(rate: int, channels: int) -> np.ndarray:
    """Короткий двухнотный сигнал для проверки устройства вывода."""
    parts = []
    for f in (660.0, 880.0):
        t = np.arange(int(rate * 0.28)) / rate
        env = np.minimum(1.0, np.minimum(t / 0.02, (t[-1] - t) / 0.08))
        parts.append(0.35 * np.sin(2 * np.pi * f * t) * env)
    mono = np.concatenate(parts).astype(np.float32)
    return np.repeat(mono[:, None], channels, axis=1)


def play_chime(device, volume: float, stop: threading.Event):
    rate, ch = _open_info(device)
    play_array(chime(rate, ch), rate, device, volume, stop)
