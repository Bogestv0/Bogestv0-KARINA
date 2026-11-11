"""Офлайн-голос: нейросетевой Piper «Ирина» (русский, ~60 МБ). Скачивается один раз кнопкой в настройках,
дальше говорит без интернета вместо роботизированного системного голоса Windows."""
from __future__ import annotations
import hashlib, json, os, threading, time, urllib.error, urllib.request, wave
from pathlib import Path

from .models import Cancelled

HF = "https://huggingface.co"
REPO = "rhasspy/piper-voices"
SUB = "ru/ru_RU/irina/medium"
NAME = "ru_RU-irina-medium"
UA = {"User-Agent": "Bogestv0-KARINA"}


def voice_dir(base: Path) -> Path:
    return Path(base) / "voices"


def paths(base: Path) -> tuple[Path, Path]:
    d = voice_dir(base)
    return d / f"{NAME}.onnx", d / f"{NAME}.onnx.json"


def is_ready(base: Path) -> bool:
    """Голос скачан целиком: .json кладётся последним, поэтому его наличие — отметка «готово»."""
    onnx, js = paths(base)
    try:
        return onnx.stat().st_size > 0 and js.stat().st_size > 0
    except OSError:
        return False


def delete(base: Path) -> None:
    for f in paths(base):
        f.unlink(missing_ok=True)
    unload()


def _open(url: str, headers: dict | None = None, timeout: int = 30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout)


def _listing() -> dict[str, dict]:
    with _open(f"{HF}/api/models/{REPO}/tree/main/{SUB}") as r:
        items = json.loads(r.read().decode("utf-8"))
    out = {}
    for it in items:
        if it.get("type") == "file":
            out[it["path"].rsplit("/", 1)[-1]] = {
                "path": it["path"], "size": int(it.get("size") or (it.get("lfs") or {}).get("size") or 0),
                "sha256": ((it.get("lfs") or {}).get("oid") or "").lower()}
    return out


def _fetch(info: dict, dest: Path, progress, cancel: threading.Event):
    """Скачивает файл с докачкой и проверкой размера и SHA-256 (если известна). dest появляется только целиком."""
    part = dest.with_name(dest.name + ".part")
    total = info["size"]
    have = part.stat().st_size if part.exists() else 0
    if have and total and have > total:
        part.unlink()
        have = 0
    if not (total and have == total):
        try:
            r = _open(f"{HF}/{REPO}/resolve/main/{info['path']}", {"Range": f"bytes={have}-"} if have else {}, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 416:           # 416 — уже скачано целиком
                raise
            r = None
        if r is not None:
            with r:
                if have and getattr(r, "status", 200) != 206:
                    have = 0            # докачки нет — заново
                total = total or (int(r.headers.get("Content-Length", 0)) + have)
                done, last = have, 0.0
                with open(part, "ab" if have else "wb") as f:
                    while True:
                        if cancel.is_set():
                            raise Cancelled()
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 0.4:
                            progress(done, total, "download")
                            last = time.time()
    if total and part.stat().st_size != total:
        raise RuntimeError("Загрузка оборвалась. Нажмите «Скачать» ещё раз — продолжу с того же места.")
    if info["sha256"]:
        progress(total, total, "verify")
        h = hashlib.sha256()
        with open(part, "rb") as f:
            while True:
                b = f.read(1 << 22)
                if not b:
                    break
                h.update(b)
        if h.hexdigest() != info["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError("Контрольная сумма не совпала: файл повреждён. Нажмите «Скачать» ещё раз.")
    part.replace(dest)


def download(base: Path, progress=None, cancel: threading.Event | None = None) -> Path:
    """Скачивает голос в <base>/voices. progress(done, total, stage). Бросает Cancelled при отмене."""
    prog = progress or (lambda *a: None)
    cancel = cancel or threading.Event()
    prog(0, 0, "search")
    files = _listing()
    try:
        m, j = files[f"{NAME}.onnx"], files[f"{NAME}.onnx.json"]
    except KeyError:
        raise RuntimeError("В репозитории голосов не нашёлся файл «Ирина». Возможно, его переименовали.") from None
    onnx, js = paths(base)
    onnx.parent.mkdir(parents=True, exist_ok=True)
    _fetch(m, onnx, prog, cancel)
    _fetch(j, js, lambda *a: None, cancel)
    try:
        json.loads(js.read_text("utf-8"))["audio"]
    except Exception:
        js.unlink(missing_ok=True)
        raise RuntimeError("Файл настроек голоса повреждён. Нажмите «Скачать» ещё раз.") from None
    prog(m["size"], m["size"], "done")
    return onnx


# ---------- синтез ----------
_lock = threading.Lock()
_loaded: dict = {"key": None, "voice": None}


def unload():
    with _lock:
        _loaded.update(key=None, voice=None)


def synth_wav(base: Path, text: str, out_path: str, speed: int = 0):
    """Озвучить text в wav. speed — ползунок «Скорость речи» (-5…5). Бросает исключение, если piper не установлен или голос не скачан."""
    onnx, js = paths(base)
    if not is_ready(base):
        raise FileNotFoundError("офлайн-голос не скачан")
    from piper import PiperVoice
    length_scale = round(max(0.6, min(1.6, 1.0 - int(speed) * 0.06)), 2)    # меньше — быстрее
    with _lock:
        key = str(onnx)
        if _loaded["key"] != key:
            _loaded.update(voice=PiperVoice.load(str(onnx), config_path=str(js)), key=key)
        voice = _loaded["voice"]
        with wave.open(out_path, "wb") as wf:
            if hasattr(voice, "synthesize_wav"):            # piper-tts 1.3+
                try:
                    from piper import SynthesisConfig
                    voice.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=length_scale))
                except ImportError:
                    voice.synthesize_wav(text, wf)
            else:                                           # старый интерфейс
                voice.synthesize(text, wf, length_scale=length_scale)
