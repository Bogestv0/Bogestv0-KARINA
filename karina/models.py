"""Каталог встроенных моделей и их загрузка (один раз, дальше всё офлайн).

Точное имя файла в репозитории Hugging Face узнаётся при скачивании по списку файлов репозитория
(так не зависим от регистра букв и схемы именования), а целостность проверяется по SHA-256 из того же списка.
"""
from __future__ import annotations
import hashlib, json, os, re, sys, threading, urllib.error, urllib.request
from pathlib import Path
from .config import DATA

MODELS_DIR = DATA / "models" / "llm"
HF = "https://huggingface.co"
UA = {"User-Agent": "Bogestv0-KARINA"}

# tier: light | medium | strong | max.  ram — сколько оперативной памяти желательно (ГБ).
# ctx — размер контекста по умолчанию. cpu_moe — держать эксперты MoE в оперативной памяти (видеокарта берёт остальное).
CATALOG: list[dict] = [
    {"id": "qwen35-4b", "title": "Qwen 3.5 · 4B", "tier": "light", "repo": "unsloth/Qwen3.5-4B-GGUF", "quant": "Q4_K_M",
     "size_gb": 2.7, "ram": 8, "ctx": 8192, "ru": "хороший",
     "desc": "Для слабых компьютеров и ноутбуков (от 8 ГБ памяти). Быстрая, подходит и для процессора."},
    {"id": "gemma4-e4b", "title": "Gemma 4 · E4B", "tier": "medium", "repo": "unsloth/gemma-4-e4b-it-GGUF", "quant": "Q4_K_M",
     "size_gb": 5.0, "ram": 12, "ctx": 8192, "ru": "очень хороший",
     "desc": "Золотая середина: от 12 ГБ памяти. Хорошо пишет по-русски, заметно умнее лёгкой."},
    {"id": "qwen35-9b", "title": "Qwen 3.5 · 9B", "tier": "strong", "repo": "unsloth/Qwen3.5-9B-GGUF", "quant": "Q4_K_M",
     "size_gb": 5.7, "ram": 16, "ctx": 12288, "ru": "отличный",
     "desc": "Для мощного компьютера: от 16 ГБ памяти, лучше с видеокартой на 8 ГБ. Хорошо справляется с поиском и отчётами."},
    {"id": "gemma4-26b", "title": "Gemma 4 · 26B-A4B (MoE)", "tier": "max", "repo": "unsloth/gemma-4-26B-A4B-it-GGUF", "quant": "UD-Q4_K_M",
     "size_gb": 16.9, "ram": 32, "ctx": 16384, "ru": "отличный", "cpu_moe": True,
     "desc": "Самая умная из встроенных: нужно от 32 ГБ оперативной памяти. Большая, но при ответе работает лишь часть модели, поэтому скорость приемлемая."},
]
TIER_LABEL = {"light": "Слабый ПК", "medium": "Средний ПК", "strong": "Мощный ПК", "max": "Максимум"}


def by_id(mid: str) -> dict | None:
    return next((m for m in CATALOG if m["id"] == mid), None)


def system_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            s = MS()
            s.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            return round(s.total / 2 ** 30, 1)
        for line in open("/proc/meminfo", encoding="utf-8"):
            if line.startswith("MemTotal"):
                return round(int(line.split()[1]) / 2 ** 20, 1)
    except Exception:
        pass
    return 0.0


def recommended_id(ram_gb: float) -> str:
    """Самая сильная модель, которая комфортно помещается в память."""
    best = CATALOG[0]["id"]
    for m in CATALOG:
        if ram_gb >= m["ram"]:
            best = m["id"]
    return best


# ---------- установленные модели ----------
def _index_path() -> Path:
    return MODELS_DIR / "installed.json"


def _read_index() -> dict:
    try:
        return json.loads(_index_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_index(d: dict):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(_index_path())


def local_path(mid: str) -> Path | None:
    name = _read_index().get(mid)
    p = MODELS_DIR / name if name else None
    return p if p and p.exists() else None


def installed_ids() -> list[str]:
    return [m["id"] for m in CATALOG if local_path(m["id"])]


def delete(mid: str) -> bool:
    p = local_path(mid)
    idx = _read_index()
    idx.pop(mid, None)
    _write_index(idx)
    try:
        if p:
            p.unlink()
    except OSError:
        return False
    return True


# ---------- загрузка ----------
class Cancelled(Exception):
    pass


def _open(url: str, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def resolve_file(model: dict) -> dict:
    """Находит в репозитории нужный .gguf: {'path','size','sha256'}."""
    with _open(f"{HF}/api/models/{model['repo']}/tree/main?recursive=false") as r:
        items = json.loads(r.read().decode("utf-8"))
    return pick_file(items, model["quant"])


def pick_file(items: list[dict], quant: str) -> dict:
    q = quant.lower()
    cands = []
    for it in items:
        p = it.get("path", "")
        pl = p.lower()
        if it.get("type") != "file" or not pl.endswith(".gguf") or "/" in p:
            continue
        if "mmproj" in pl or "mtp" in pl or re.search(r"-0000\d-of-", pl):
            continue
        if not re.search(r"(^|[-_.])" + re.escape(q) + r"\.gguf$", pl):
            continue
        cands.append(it)
    if not cands:
        raise RuntimeError(f"В репозитории не нашёлся файл «{quant}». Возможно, модель переименовали.")
    cands.sort(key=lambda i: len(i["path"]))
    it = cands[0]
    return {"path": it["path"], "size": int(it.get("size") or (it.get("lfs") or {}).get("size") or 0),
            "sha256": ((it.get("lfs") or {}).get("oid") or "").lower()}


def download(mid: str, progress=None, cancel: threading.Event | None = None) -> Path:
    """Скачивает модель с докачкой. progress(done, total, stage). Бросает Cancelled при отмене."""
    m = by_id(mid)
    if not m:
        raise RuntimeError("Неизвестная модель")
    cancel = cancel or threading.Event()
    prog = progress or (lambda *a: None)
    prog(0, 0, "search")
    info = resolve_file(m)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    final = MODELS_DIR / info["path"]
    part = final.with_name(final.name + ".part")
    total = info["size"]
    have = part.stat().st_size if part.exists() else 0
    if have and total and have > total:
        part.unlink()
        have = 0
    if not (total and have == total):
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            r = _open(f"{HF}/{m['repo']}/resolve/main/{info['path']}", headers, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code == 416:           # уже скачано целиком
                r = None
            else:
                raise
        if r is not None:
            with r:
                if have and getattr(r, "status", 200) != 206:
                    have = 0            # сервер не умеет докачку: начинаем заново
                total = total or (int(r.headers.get("Content-Length", 0)) + have)
                with open(part, "ab" if have else "wb") as f:
                    done = have
                    last = 0.0
                    import time
                    while True:
                        if cancel.is_set():
                            raise Cancelled()
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last > 0.4:
                            prog(done, total, "download")
                            last = now
    if total and part.stat().st_size != total:
        raise RuntimeError("Загрузка оборвалась: размер файла не совпал. Нажмите «Скачать» ещё раз — продолжу с того же места.")
    if info["sha256"]:
        prog(total, total, "verify")
        h = hashlib.sha256()
        with open(part, "rb") as f:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                b = f.read(1 << 22)
                if not b:
                    break
                h.update(b)
        if h.hexdigest() != info["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError("Контрольная сумма модели не совпала: файл повреждён. Нажмите «Скачать» ещё раз.")
    part.replace(final)
    idx = _read_index()
    idx[mid] = final.name
    _write_index(idx)
    prog(total, total, "done")
    return final
