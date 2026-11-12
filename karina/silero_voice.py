"""Естественный русский голос Silero v5 — работает у вас на компьютере, без интернета и без ключей.

Почему он: нейросеть заточена под русский язык, сама ставит ударения и ё, на обычном процессоре говорит в десятки раз быстрее, чем длится речь
(Silero заявляет RTF около 0,04). Голоса Ксения и Ксения-2 («xenia», «kseniya»), Байя («baya») — женские, Айдар («aidar») и Евгений («eugene») — мужские.

Лицензия моделей v5_*_ru — CC BY-NC 4.0: бесплатно для личного некоммерческого использования. Для коммерческого есть MIT-вариант v5_cis_base
(другие голоса, нужны ударения от silero-stress). Модель скачивается один раз с models.silero.ai (адрес взят из официального models.yml).

Нужен пакет torch (CPU-версия подходит): pip install torch. Без него Карина говорит обычным голосом и подсказывает, что поставить.
"""
from __future__ import annotations
import importlib.util, os, re, sys, threading, time, urllib.error, urllib.request, wave
from pathlib import Path
from xml.sax.saxutils import escape

from .models import Cancelled

MODEL_ID = "v5_6_ru"
URL = f"https://models.silero.ai/models/tts/ru/{MODEL_ID}.pt"
UA = {"User-Agent": "Bogestv0-KARINA"}
SPEAKERS = {"xenia": "Ксения", "kseniya": "Ксения-2", "baya": "Байя", "aidar": "Айдар", "eugene": "Евгений"}
SAMPLE_RATE = 48000
MAX_CHUNK = 450          # модель не любит очень длинные куски: режем по предложениям


def model_path(base: Path) -> Path:
    return Path(base) / "voices" / f"silero_{MODEL_ID}.pt"


FROZEN = bool(getattr(sys, "frozen", False))


def torch_available() -> bool:
    """В собранной (exe) версии torch не входит — её собирают без него, чтобы установщик не весил гигабайт."""
    return not FROZEN and importlib.util.find_spec("torch") is not None


def torch_hint() -> str:
    if FROZEN:
        return "В установленной версии Карины (exe) Silero недоступен: нет пакета torch. Запустите Карину из исходников (run.bat) с torch или выберите голос Светлана / офлайн «Ирина»."
    return "Для голоса Silero нужен пакет torch: в окне команд выполните «pip install torch» и перезапустите Карину."


def is_ready(base: Path) -> bool:
    try:
        return model_path(base).stat().st_size > 1_000_000
    except OSError:
        return False


def delete(base: Path) -> None:
    for f in (model_path(base), model_path(base).with_name(model_path(base).name + ".part")):
        f.unlink(missing_ok=True)
    unload()


def download(base: Path, progress=None, cancel: threading.Event | None = None) -> Path:
    """Скачивает модель с докачкой. progress(done, total, stage). Бросает Cancelled при отмене."""
    prog = progress or (lambda *a: None)
    cancel = cancel or threading.Event()
    dest = model_path(base)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    prog(have, 0, "search")
    req = urllib.request.Request(URL, headers={**UA, **({"Range": f"bytes={have}-"} if have else {})})
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:                    # уже скачано целиком
            part.replace(dest)
            return dest
        raise RuntimeError(f"Сервер Silero ответил ошибкой {e.code}. Попробуйте позже.") from None
    with r:
        if have and getattr(r, "status", 200) != 206:
            have = 0                         # докачки нет — заново
        total = int(r.headers.get("Content-Length", 0)) + have
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
                    prog(done, total, "download")
                    last = time.time()
    if total and part.stat().st_size != total:
        raise RuntimeError("Загрузка оборвалась. Нажмите «Скачать» ещё раз — продолжу с того же места.")
    part.replace(dest)
    prog(total, total, "done")
    return dest


# ---------- синтез ----------
_lock = threading.Lock()
_loaded: dict = {"key": None, "model": None}


def unload():
    with _lock:
        _loaded.update(key=None, model=None)


def _load(base: Path):
    import torch
    path = str(model_path(base))
    if _loaded["key"] != path:
        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 2)))
        imp = torch.package.PackageImporter(path)
        model = imp.load_pickle("tts_models", "model")
        model.to("cpu")
        _loaded.update(key=path, model=model)
    return _loaded["model"]


def preload(base: Path) -> None:
    """Прогрев в фоне: первая загрузка модели занимает секунду-две, дальше фразы идут сразу."""
    if is_ready(base) and torch_available():
        with _lock:
            try:
                _load(base)
            except Exception:
                pass


def to_plus_stress(text: str) -> str:
    """Ударение из словаря произношений («Кари́на», знак U+0301 после гласной) → формат Silero («Кар+ина»)."""
    return re.sub(r"([а-яёА-ЯЁ])́", lambda m: "+" + m.group(1), text)


def split_text(text: str, limit: int = MAX_CHUNK) -> list[str]:
    """Режет длинный ответ на части по предложениям (потом по запятым и словам), чтобы не превысить лимит модели."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return [text] if text else []
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?…])\s+", text):
        while len(sent) > limit:
            cut = max(sent.rfind(", ", 0, limit), sent.rfind(" ", 0, limit))
            cut = cut if cut > 0 else limit
            piece, sent = sent[:cut + 1].strip(), sent[cut + 1:].strip()
            if cur:
                out.append(cur); cur = ""
            out.append(piece)
        if cur and len(cur) + 1 + len(sent) > limit:
            out.append(cur); cur = ""
        cur = (cur + " " + sent).strip()
    if cur:
        out.append(cur)
    return [x for x in out if x]


def _rate_name(speed: int) -> str:
    return "x-slow" if speed <= -4 else "slow" if speed < 0 else "medium" if speed == 0 else "fast" if speed < 4 else "x-fast"


def synth_wav(base: Path, text: str, out_path: str, speaker: str = "xenia", speed: int = 0):
    """Озвучить text в wav (48 кГц, моно). speed — ползунок «Скорость речи» (−5…5). Бросает исключение, если нет torch или модели."""
    if not is_ready(base):
        raise FileNotFoundError("голос Silero не скачан")
    if not torch_available():
        raise ImportError(torch_hint())
    if speaker not in SPEAKERS:
        speaker = "xenia"
    import numpy as np
    parts = split_text(to_plus_stress(text))
    if not parts:
        raise ValueError("нет текста")
    with _lock:
        model = _load(base)
        audio = []
        for part in parts:
            if int(speed) != 0:
                try:
                    wav = model.apply_tts(ssml_text=f'<speak><prosody rate="{_rate_name(int(speed))}">{escape(part)}</prosody></speak>',
                                          speaker=speaker, sample_rate=SAMPLE_RATE)
                except Exception:
                    wav = model.apply_tts(text=part, speaker=speaker, sample_rate=SAMPLE_RATE)
            else:
                wav = model.apply_tts(text=part, speaker=speaker, sample_rate=SAMPLE_RATE)
            audio.append(np.asarray(wav.detach().cpu().numpy() if hasattr(wav, "detach") else wav, dtype=np.float32).reshape(-1))
            audio.append(np.zeros(int(SAMPLE_RATE * 0.12), dtype=np.float32))       # короткая пауза между частями
    pcm = (np.clip(np.concatenate(audio), -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
