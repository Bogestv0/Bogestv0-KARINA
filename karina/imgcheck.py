"""Проверка нарисованной картинки: «а нарисовано ли то, что просили?».

Текстовая модель картинок не видит, а картиночная не умеет сверяться с просьбой. Поэтому после каждого круга рисования маленькая модель-«тегер»
(WD ViT Tagger v3, Apache-2.0, ~0,4 ГБ, работает на процессоре за 1–2 секунды) перечисляет, что она видит на картинке (lying, on_couch, white_hair, blue_eyes …),
а Карина сравнивает это с тегами из просьбы. Чего не хватает — то усиливается в следующем круге.

Тегер необязателен: без него (нет пакета onnxruntime или модель не скачана) Карина рисует один проход и честно говорит, что автопроверки не было.
Модуль ничего не рисует; чат и «поток рисования» (imgflow.py) только вызывают его.
"""
from __future__ import annotations
import csv, io, re, threading
from pathlib import Path
from . import localimg, prompts
from .config import DATA

DIR = DATA / "models" / "tagger"
REPO = "SmilingWolf/wd-vit-tagger-v3"
FILES = ("model.onnx", "selected_tags.csv")
THRESHOLD = 0.35          # с какой уверенностью тег считается «виден на картинке»
MAX_REQUIRED = 12         # сколько тегов из просьбы проверяем (самые важные)
PASS_COVERAGE = 0.85      # доля найденных тегов, при которой круг считается удачным

# теги, которые не имеет смысла проверять: стиль, качество, общие слова
_SKIP = set(t.lower().replace(" ", "_") for t in prompts.STYLE_CORE + prompts.STYLE_TAIL_TAGS) | {
    "masterpiece", "best_quality", "amazing_quality", "very_aesthetic", "absurdres", "highres", "newest", "solo", "anime_screencap", "official_art",
    "detailed_background", "simple_background", "portrait", "upper_body"}
# что проверять в первую очередь: поза, ракурс, цвета волос и глаз, одежда, место
_PRIORITY = re.compile(r"^(?:lying|on_(?:back|side|stomach|couch|bed|floor)|sitting|kneeling|all_fours|seiza|squatting|standing|from_(?:behind|above|below|side)|profile|looking_back|arms?_up|"
                       r"arms_behind_(?:head|back)|knees_up|crossed_legs|wink|one_eye_closed|hugging|carrying|princess_carry|stretching|jumping|running|walking|waving|reclining|curled_up|"
                       r".+_hair|.+_eyes|.+_(?:shirt|dress|skirt|shorts|pants|jacket|hoodie|bikini|uniform|kimono|swimsuit|sweater|thighhighs)|school_uniform|couch|bed|indoors|outdoors|night|sunset)$")

_LOCK = threading.Lock()
_SESSION: dict = {}


def importable() -> bool:
    try:
        import numpy, onnxruntime, PIL  # noqa: F401
        return True
    except Exception:
        return False


def installed() -> bool:
    return all((DIR / f).exists() and (DIR / f).stat().st_size > 1000 for f in FILES)


def ready() -> bool:
    return importable() and installed()


def download(progress=None, cancel: threading.Event | None = None) -> None:
    """Скачивает тегер с Hugging Face (один раз, ~0,4 ГБ). progress(done, total, stage)."""
    cancel = cancel or threading.Event()
    prog = progress or (lambda *a: None)
    DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        final = DIR / name
        if final.exists() and final.stat().st_size > 1000:
            continue
        part = final.with_name(final.name + ".part")
        url = f"{localimg.HF}/{REPO}/resolve/main/{name}"
        total = 0
        try:
            with localimg._open(url, {"Range": "bytes=0-0"}) as r:
                cr = r.headers.get("Content-Range", "")
                total = int(cr.rsplit("/", 1)[-1]) if "/" in cr else 0
        except Exception:
            total = 0
        localimg._stream(url, part, total, prog, cancel, "download")
        part.replace(final)
    prog(1, 1, "done")


def _load():
    with _LOCK:
        if _SESSION:
            return _SESSION
        import onnxruntime as ort
        sess = ort.InferenceSession(str(DIR / "model.onnx"), providers=["CPUExecutionProvider"])
        names, cats = [], []
        with open(DIR / "selected_tags.csv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                names.append(row["name"])
                cats.append(int(row.get("category") or 0))
        shape = sess.get_inputs()[0].shape
        side = int(shape[1]) if isinstance(shape[1], int) else 448
        _SESSION.update(sess=sess, names=names, cats=cats, side=side, input=sess.get_inputs()[0].name)
        return _SESSION


def vocabulary() -> set[str]:
    """Названия общих тегов, которые тегер умеет узнавать."""
    s = _load()
    return {n for n, c in zip(s["names"], s["cats"]) if c == 0}


def tag(data: bytes) -> dict[str, float]:
    """Что тегер видит на картинке: {тег: уверенность}. Только общие теги (поза, волосы, одежда, место …)."""
    import numpy as np
    from PIL import Image
    s = _load()
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    img = bg.convert("RGB")
    side = max(img.size)
    pad = Image.new("RGB", (side, side), (255, 255, 255))
    pad.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    pad = pad.resize((s["side"], s["side"]), Image.BICUBIC)
    arr = np.asarray(pad, dtype=np.float32)[:, :, ::-1]            # BGR, 0..255
    probs = s["sess"].run(None, {s["input"]: np.expand_dims(arr, 0)})[0][0]
    return {n: float(p) for n, p, c in zip(s["names"], probs, s["cats"]) if c == 0 and p >= 0.05}


def norm_tag(t: str) -> str:
    t = re.sub(r"^\(+|\)+$|:\d+(?:\.\d+)?\)*$", "", t.strip())
    return re.sub(r"\s+", "_", t.strip().lower())


def required_tags(tags: str, vocab: set[str] | None = None) -> list[str]:
    """Теги из описания, которые стоит проверять: те, что тегер знает, без стилевых; поза, волосы, глаза, одежда — первыми."""
    out, seen = [], set()
    for raw in tags.split(","):
        t = norm_tag(raw)
        if not t or t in seen or t in _SKIP or re.fullmatch(r"\d+(?:girls?|boys?|others?)", t):
            continue
        if vocab is not None and t not in vocab:
            continue
        seen.add(t)
        out.append(t)
    out.sort(key=lambda t: 0 if _PRIORITY.match(t) else 1)       # sort устойчивый: порядок из описания сохраняется внутри групп
    return out[:MAX_REQUIRED]


def verdict(required: list[str], found: dict[str, float], threshold: float = THRESHOLD) -> dict:
    """Сверка: {"missing": [...], "present": [...], "coverage": 0..1, "ok": bool}. Нет проверяемых тегов — считаем удачным (проверять нечего)."""
    present = [t for t in required if found.get(t, 0.0) >= threshold]
    missing = [t for t in required if t not in present]
    cov = len(present) / len(required) if required else 1.0
    return {"missing": missing, "present": present, "coverage": cov, "ok": not required or cov >= PASS_COVERAGE}


def check(data: bytes, tags: str) -> dict | None:
    """Проверяет картинку по тегам описания. None — проверка недоступна (нет тегера) или упала; рисование из-за этого не останавливается."""
    if not ready():
        return None
    try:
        vocab = vocabulary()
        req = required_tags(tags, vocab)
        return dict(verdict(req, tag(data)), required=req)
    except Exception:
        return None
