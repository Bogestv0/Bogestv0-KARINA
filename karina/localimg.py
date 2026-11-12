"""Встроенный генератор картинок: stable-diffusion.cpp + локальная модель (аниме SDXL или FLUX.2 Klein) прямо на этом компьютере.

Без интернета (после одной загрузки), без водяного знака и без привязки к текстовой модели: текстовая модель только пишет описание,
а рисует отдельная картинка-модель. Поэтому аниме получается одинаково хорошо с любой выбранной текстовой моделью.

Движок: https://github.com/leejet/stable-diffusion.cpp (сборки Windows: Vulkan для видеокарты и CPU).
Модели скачиваются с Hugging Face; точное имя файла берётся из списка файлов репозитория, как и у текстовых моделей.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request, zipfile
from pathlib import Path
from . import imgpower, prompts
from .config import DATA

IS_WIN = sys.platform == "win32"
MODELS_DIR = DATA / "models" / "image"
ENGINE_DIR = DATA / "engine-sd"
HF = "https://huggingface.co"
API = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases?per_page=20"
UA = {"User-Agent": "Bogestv0-KARINA"}
EXE_NAMES = ("sd-cli.exe", "sd.exe") if IS_WIN else ("sd-cli", "sd")
FLAVORS = {"vulkan": (r"bin-win-vulkan-x64\.zip$",),
           "cpu": (r"bin-win-cpu-x64\.zip$", r"bin-win-avx2-x64\.zip$", r"bin-win-avx-x64\.zip$", r"bin-win-noavx-x64\.zip$")}   # новые релизы: win-cpu (все варианты процессора внутри), старые: avx2/avx/noavx

# Хорошие аниме-модели SDXL по умолчанию. quality — теги качества, которые эти модели понимают лучше всего.
CATALOG: list[dict] = [
    {"id": "animagine4", "title": "Animagine XL 4.0", "repo": "cagliostrolab/animagine-xl-4.0", "file": r"^animagine-xl-4\.0\.safetensors$",
     "size_gb": 6.9, "vram": 8, "steps": 28, "cfg": 5.0,
     "quality": prompts.MODEL_QUALITY["animagine4"],
     "negative": prompts.MODEL_NEGATIVE["animagine4"],
     "desc": "Лучший выбор по умолчанию: чистый аниме-стиль как в кадрах сериалов, хорошие лица и руки. Нужна видеокарта от 6–8 ГБ (на процессоре картинка рисуется несколько минут)."},
    {"id": "illustrious1", "title": "Illustrious XL 1.0", "repo": "OnomaAIResearch/Illustrious-XL-v1.0", "file": r"^Illustrious-XL-v1\.0\.safetensors$",
     "size_gb": 6.9, "vram": 8, "steps": 28, "cfg": 5.5,
     "quality": prompts.MODEL_QUALITY["illustrious1"],
     "negative": prompts.MODEL_NEGATIVE["illustrious1"],
     "desc": "Альтернатива: больше разных стилей и персонажей. Если на Hugging Face потребуется принять условия, Карина скажет об этом при скачивании."},
]
# FLUX.2 Klein 4B (Apache-2.0): быстрая универсальная модель (4 шага), рисует и фото, и иллюстрации, и аниме, понимает русские/английские описания.
# Состоит из трёх файлов: сама модель (GGUF), VAE и текстовый энкодер Qwen3-4B (GGUF). Для каждого перечислены запасные репозитории.
KLEIN_REPOS = ("leejet/FLUX.2-klein-4B-GGUF", "unsloth/FLUX.2-klein-4B-GGUF", "MXKA/FLUX.2-klein-4B-GGUF", "w3ss/FLUX.2-klein-4B-GGUF")
CATALOG.insert(0, {
    "id": "flux2klein", "title": "FLUX.2 Klein 4B", "kind": "flux2", "general": True,
    "size_gb": 5.4, "vram": 6, "steps": 4, "cfg": 1.0, "quality": prompts.MODEL_QUALITY["flux2klein"], "negative": prompts.MODEL_NEGATIVE["flux2klein"],
    "parts": [
        {"role": "model", "repos": KLEIN_REPOS, "file": r"(?i)^flux[-_.]?2[-_.]klein[-_.]4b[-_.](Q4_K_M|Q4_0)\.gguf$"},
        {"role": "vae", "repos": ("unsloth/FLUX.2-VAE", "Comfy-Org/flux2-dev"), "dir": "split_files/vae", "file": r"(?i)^flux2[-_.]vae\.safetensors$"},
        {"role": "llm", "repos": ("unsloth/Qwen3-4B-GGUF", "Qwen/Qwen3-4B-GGUF"), "file": r"^Qwen3-4B-Q4_K_M\.gguf$"},
    ],
    "desc": "Новая универсальная модель: быстро (4 шага), хорошо рисует и людей, и пейзажи, и аниме, лучше слушается описания и надписей. "
            "Три файла около 5,4 ГБ (модель, VAE, текстовый энкодер Qwen3). Нужна видеокарта от 6 ГБ или 16 ГБ ОЗУ; лицензия Apache-2.0.",
})
CATALOG.extend([
    {"id": "ponyxl", "title": "Pony Diffusion V6 XL", "general": True, "clip_skip": 2, "qpos": "prefix",
     "repos": ("LyliaEngine/Pony_Diffusion_V6_XL", "gnicartweet/Pony_Diffusion_V6_XL", "misri/ponyDiffusionV6XL"),
     "file": r"(?i)^ponyDiffusionV6XL.*\.safetensors$",
     "size_gb": 6.5, "vram": 8, "steps": 25, "cfg": 7.0,
     "quality": prompts.MODEL_QUALITY["ponyxl"],
     "negative": prompts.MODEL_NEGATIVE["ponyxl"],
     "desc": "Pony XL: очень гибкая модель, хорошо понимает сложные просьбы (позы, сцены, персонажи), качественная рисовка. Теги score_9… Карина добавляет сама. "
             "Лицензия Fair AI Public License: личное использование разрешено, монетизация нет."},
    {"id": "noobai", "title": "NoobAI XL 1.1", "general": True, "repos": ("Laxhar/noobai-XL-1.1",),
     "file": r"(?i)^noobai-?xl[-_]?v?1\.1\.safetensors$",
     "size_gb": 6.9, "vram": 8, "steps": 28, "cfg": 5.0,
     "quality": prompts.MODEL_QUALITY["noobai"],
     "negative": prompts.MODEL_NEGATIVE["noobai"],
     "desc": "NoobAI XL: сильная danbooru-модель (на базе Illustrious), знает тысячи персонажей и стилей, чистые линии и цвета. Версия 1.1 (обычная, не v-pred)."},
])
DEFAULT_ID = "flux2klein"
ENGINE_MIN_FLUX2 = "2026-01-20"   # сборки stable-diffusion.cpp старше этой даты FLUX.2 не знают
VRAM_MODES = ("fast", "balanced", "low", "cpu")
DEFAULT_VRAM = "balanced"    # fast — всё на видеокарте (быстрее всего, ~8 ГБ); balanced — веса в ОЗУ, пик ~5–6 ГБ; low — ещё и 8-бит сжатие, пик ~3–4 ГБ, но заметно дольше; cpu — только процессор
MODE_RANK = {"fast": 0, "balanced": 1, "low": 2}        # чем больше, тем меньше видеопамяти нужно
OOM = re.compile(r"out of memory|outofdevicememory|outofhostmemory|failed to allocate|alloc\w* fail|cannot allocate|not enough memory|vk::|vkallocate", re.I)
SAFE_NEG = prompts.SAFE_NEG   # обычные просьбы не должны случайно давать лишнего (prompts.py)


class Cancelled(Exception):
    pass


class LocalImgError(Exception):
    pass


def by_id(mid: str) -> dict | None:
    return next((m for m in CATALOG if m["id"] == mid), None)


# ---------- установленное ----------
def _index() -> Path:
    return MODELS_DIR / "installed.json"


def _read() -> dict:
    try:
        return json.loads(_index().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write(d: dict):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(_index())


def parts_of(m: dict) -> list[dict]:
    return m.get("parts") or [{"role": "model", "repos": m.get("repos") or (m["repo"],), "file": m["file"]}]


def model_paths(mid: str) -> dict[str, Path] | None:
    """Все файлы модели по ролям (model / vae / llm). None, если чего-то не хватает."""
    m = by_id(mid)
    rec = _read().get(mid)
    if not m or not rec:
        return None
    if isinstance(rec, str):
        rec = {"model": rec}
    out = {}
    for part in parts_of(m):
        name = rec.get(part["role"])
        p = MODELS_DIR / name if name else None
        if not p or not p.exists():
            return None
        out[part["role"]] = p
    return out


def model_path(mid: str) -> Path | None:
    paths = model_paths(mid)
    return paths["model"] if paths else None


def installed_ids() -> list[str]:
    return [m["id"] for m in CATALOG if model_path(m["id"])]


def find_exe(flavor: str) -> Path | None:
    d = ENGINE_DIR / flavor
    if not d.exists():
        return None
    for name in EXE_NAMES:
        hits = list(d.rglob(name))
        if hits:
            return hits[0]
    return None


def engine_ready() -> bool:
    return bool(find_exe("vulkan") or find_exe("cpu"))


def engine_published() -> str:
    try:
        return str(json.loads((ENGINE_DIR / "engine.json").read_text("utf-8")).get("published_at", ""))[:10]
    except (OSError, ValueError):
        return ""


def engine_supports(m: dict) -> bool:
    return m.get("kind") != "flux2" or engine_published() >= ENGINE_MIN_FLUX2


def ready(mid: str = "") -> bool:
    """Можно рисовать прямо сейчас: есть и движок, и хотя бы одна модель."""
    return engine_ready() and bool(model_path(mid) if mid else installed_ids())


def pick_installed(preferred: str = "") -> dict | None:
    ids = installed_ids()
    if preferred in ids:
        return by_id(preferred)
    return by_id(ids[0]) if ids else None


def delete(mid: str) -> bool:
    paths = model_paths(mid)
    d = _read()
    d.pop(mid, None)
    _write(d)
    ok = True
    try:
        for p in (paths or {}).values():
            p.unlink()
        if (MODELS_DIR / mid).is_dir():
            shutil.rmtree(MODELS_DIR / mid, ignore_errors=True)
    except OSError:
        ok = False
    return ok


# ---------- загрузка ----------
def _open(url: str, headers: dict | None = None, timeout: int = 60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout)


def _stream(url: str, part: Path, total: int, prog, cancel: threading.Event, stage: str):
    """Скачивание с докачкой в part."""
    have = part.stat().st_size if part.exists() else 0
    if have and total and have > total:
        part.unlink()
        have = 0
    if total and have == total:
        return
    try:
        r = _open(url, {"Range": f"bytes={have}-"} if have else {})
    except urllib.error.HTTPError as e:
        if e.code == 416:
            return
        if e.code in (401, 403):
            raise LocalImgError("Hugging Face не отдаёт файл без согласия с условиями модели: откройте страницу модели на huggingface.co, "
                                "войдите и примите условия, либо выберите другую модель.")
        raise
    with r:
        if have and getattr(r, "status", 200) != 206:
            have = 0
        total = total or int(r.headers.get("Content-Length", 0)) + have
        with open(part, "ab" if have else "wb") as f:
            done, last = have, 0.0
            while True:
                if cancel.is_set():
                    raise Cancelled()
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if time.time() - last > 0.4:
                    prog(done, total, stage)
                    last = time.time()
    if total and part.stat().st_size != total:
        raise LocalImgError("Загрузка оборвалась. Нажмите «Скачать» ещё раз — продолжу с того же места.")


def _asset(rel: dict, flavor: str):
    for pat in FLAVORS[flavor]:
        for a in rel.get("assets", []):
            if re.search(pat, a["name"]) and "cudart" not in a["name"]:
                return a
    return None


def find_release() -> dict:
    h = {**UA, "Accept": "application/vnd.github+json"}
    rels = json.loads(_open(API, h, 30).read().decode("utf-8"))
    for rel in rels:
        if not rel.get("draft") and _asset(rel, "vulkan") and _asset(rel, "cpu"):
            return rel
    raise LocalImgError("Не нашла сборку stable-diffusion.cpp для Windows в последних релизах GitHub.")


def fetch_engine(prog, cancel: threading.Event, need: dict | None = None):
    if engine_ready() and (not need or engine_supports(need)):
        return
    prog(0, 0, "search")
    rel = find_release()
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    for flavor in ("vulkan", "cpu"):
        a = _asset(rel, flavor)
        out = ENGINE_DIR / flavor
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        z = ENGINE_DIR / a["name"]
        part = z.with_name(z.name + ".part")
        part.unlink(missing_ok=True)
        _stream(a["browser_download_url"], part, int(a.get("size") or 0), prog, cancel, "engine")
        part.replace(z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(out)
        z.unlink(missing_ok=True)
    if not engine_ready():
        raise LocalImgError("Движок скачан, но в нём нет программы sd.")
    try:
        (ENGINE_DIR / "engine.json").write_text(json.dumps({"tag": rel.get("tag_name", ""), "published_at": rel.get("published_at", "")}), "utf-8")
    except OSError:
        pass


def resolve_file(part: dict) -> dict:
    """Ищет файл по шаблону в репозиториях части (по очереди: если в первом нет, пробуем запасной)."""
    last = ""
    for repo in part["repos"]:
        sub = "/" + part["dir"].strip("/") if part.get("dir") else ""
        try:
            with _open(f"{HF}/api/models/{repo}/tree/main{sub}", timeout=30) as r:
                items = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, OSError) as e:
            last = f"{repo}: {e}"
            continue
        for it in items:
            p = it.get("path", "")
            if it.get("type") == "file" and re.search(part["file"], p.rsplit("/", 1)[-1]):
                return {"repo": repo, "path": p, "size": int(it.get("size") or (it.get("lfs") or {}).get("size") or 0)}
    raise LocalImgError(f"Не нашёлся файл модели ({', '.join(part['repos'])}). Возможно, его переименовали или нет доступа к Hugging Face. {last}".strip())


def download(mid: str, progress=None, cancel: threading.Event | None = None) -> Path:
    """Движок (один раз) + файлы модели. progress(done, total, stage): stage = search | engine | download | done."""
    m = by_id(mid)
    if not m:
        raise LocalImgError("Неизвестная модель картинок")
    cancel = cancel or threading.Event()
    prog = progress or (lambda *a: None)
    fetch_engine(prog, cancel, m)
    parts = parts_of(m)
    multi = len(parts) > 1
    prog(0, 0, "search")
    infos = [resolve_file(p) for p in parts]
    grand = sum(i["size"] for i in infos)
    base = 0
    names: dict[str, str] = {}
    for part, info in zip(parts, infos):
        fname = info["path"].rsplit("/", 1)[-1]
        rel = f"{mid}/{fname}" if multi else info["path"]
        final = MODELS_DIR / rel
        final.parent.mkdir(parents=True, exist_ok=True)
        part_file = final.with_name(final.name + ".part")
        if not (final.exists() and info["size"] and final.stat().st_size == info["size"]):
            offset = base
            _stream(f"{HF}/{info['repo']}/resolve/main/{info['path']}", part_file, info["size"],
                    (lambda d, t, s, o=offset: prog(o + d, grand or t, s)) if multi else prog, cancel, "download")
            part_file.replace(final)
        base += info["size"]
        names[part["role"]] = rel
    d = _read()
    d[mid] = names if multi else names["model"]
    _write(d)
    prog(grand, grand, "done")
    return MODELS_DIR / names["model"]


# ---------- рисование ----------
def sdxl_size(size: tuple[int, int]) -> tuple[int, int]:
    """SDXL лучше всего рисует на «родных» размерах: другие дают двойные головы и кривые руки."""
    w, h = size
    if w == h:
        return 1024, 1024
    return (832, 1216) if h > w else (1216, 704)


# ---------- что получилось в прошлый раз (чтобы не повторять провальные попытки) ----------
LEARN_DAYS = 7


def _tuning_path() -> Path:
    return MODELS_DIR / "tuning.json"


def _recall(mid: str) -> str:
    """Режим, который в прошлый раз сработал после неудач на этой модели (на ПК без запаса видеопамяти). Пусто — нет данных или они устарели."""
    try:
        rec = json.loads(_tuning_path().read_text("utf-8")).get(mid) or {}
        return rec["mode"] if time.time() - float(rec.get("t", 0)) < LEARN_DAYS * 86400 and rec.get("mode") in (*MODE_RANK, "cpu") else ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _remember(mid: str, mode: str):
    try:
        d = json.loads(_tuning_path().read_text("utf-8")) if _tuning_path().exists() else {}
    except (OSError, ValueError):
        d = {}
    d[mid] = {"mode": mode, "t": time.time()}
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        _tuning_path().write_text(json.dumps(d), "utf-8")
    except OSError:
        pass


def forget_tuning():
    """Сбросить запомненное (после смены настроек мощности или видеокарты)."""
    try:
        _tuning_path().unlink()
    except OSError:
        pass


LAST_RUN: dict = {}          # как нарисована последняя картинка: режим, размер, шаги, секунды (для сообщения в чате)


def _gpu_extra(mode: str) -> list[str]:
    """Флаги экономии видеопамяти. balanced: веса лежат в ОЗУ и подгружаются на видеокарту по частям (--offload-to-cpu), текстовый энкодер на процессоре
    (--clip-on-cpu), экономное внимание (--diffusion-fa): пик ~5–6 ГБ. low: ещё и сжатие весов до 8 бит при загрузке (--type q8_0): пик ~3–4 ГБ."""
    if mode == "fast":
        return []
    return ["--offload-to-cpu", "--clip-on-cpu", "--diffusion-fa"] + (["--type", "q8_0"] if mode == "low" else [])


def _attempts(m: dict, mode: str) -> list[tuple[str, str]]:
    """Порядок попыток (движок, режим). Выбранный режим → экономнее → «обычный» (если флаги экономии не поддерживаются) → процессор."""
    if mode == "cpu":
        return [("cpu", "cpu")]
    modes = [mode] if m.get("kind") == "flux2" else [mode] + [x for x in MODE_RANK if MODE_RANK[x] > MODE_RANK[mode]] + (["fast"] if mode != "fast" else [])
    return [("vulkan", x) for x in dict.fromkeys(modes)] + [("cpu", "cpu")]


def _command(exe: Path, m: dict, paths: dict, prompt: str, neg: str, w: int, h: int, steps: int, seed: int, out: Path, flavor: str, mode: str, threads: int) -> list[str]:
    if m.get("kind") == "flux2":      # FLUX.2 Klein: три файла, дистиллированная модель (шаги и cfg из каталога), негативного промпта нет
        cmd = [str(exe), "--diffusion-model", str(paths["model"]), "--vae", str(paths["vae"]), "--llm", str(paths["llm"]),
               "-p", prompt, "-W", str(w), "-H", str(h), "--steps", str(steps), "--cfg-scale", str(m["cfg"]),
               "-s", str(seed), "--diffusion-fa", "--offload-to-cpu", "--vae-tiling"]
    else:
        cmd = [str(exe), "-m", str(paths["model"]), "-p", prompt, "-n", neg, "-W", str(w), "-H", str(h), "--steps", str(steps),
               "--cfg-scale", str(m["cfg"]), "-s", str(seed), "--sampling-method", "euler_a", "--vae-tiling"]
        if m.get("clip_skip"):
            cmd[1:1] = ["--clip-skip", str(m["clip_skip"])]
        if flavor == "vulkan":
            cmd += _gpu_extra(mode)
    if threads > 0 and flavor == "cpu":
        cmd += ["-t", str(threads)]
    return cmd + ["-o", str(out)]


def generate(prompt: str, size: tuple[int, int], seed: int, model_id: str = "", negative: str = "", stop: threading.Event | None = None,
             vram: str = DEFAULT_VRAM, plan: dict | None = None, progress=None) -> tuple[bytes, str]:
    """Рисует картинку. plan — результат imgpower.plan (режим памяти, размер, шаги, потоки); без него работает только режим vram.
    progress(шаг, всего, секунд на шаг) вызывается по мере рисования. Не вышло в одном режиме — пробует экономнее, в конце процессор."""
    m = pick_installed(model_id)
    if not m or not engine_ready():
        raise LocalImgError("встроенный генератор не установлен (Настройки → Модели → Картинки → «Скачать»).")
    if not engine_supports(m):
        raise LocalImgError("движок stable-diffusion.cpp устарел и не знает FLUX.2: удалите модель в «Настройки → Модели → Картинки» и скачайте её заново: движок обновится.")
    plan = plan or {"mode": vram if vram in VRAM_MODES else DEFAULT_VRAM}
    mode = plan.get("mode") if plan.get("mode") in VRAM_MODES else DEFAULT_VRAM
    if plan.get("auto") and mode != "cpu":                      # в «Авто» помним, что уже не помещалось
        rec = _recall(m["id"])
        if rec == "cpu" or (rec in MODE_RANK and MODE_RANK[rec] > MODE_RANK[mode]):
            mode = rec
    native = sdxl_size(size)
    base = prompt.rstrip(" ,.")
    if not m.get("quality"):
        full = base
    elif m.get("qpos") == "prefix":          # Pony: теги score_* должны стоять в начале описания
        full = f"{m['quality']}, {base}"
    else:
        full = f"{base}, {m['quality']}"
    neg = prompts.unique_tags(prompts.join_tags(SAFE_NEG, negative or m["negative"], prompts.extra_negative(m["id"])))   # + ваши негативы из prompts.py
    paths = model_paths(m["id"]) or {}
    out = Path(tempfile.mkdtemp(prefix="karina-sd-")) / "out.png"
    errors = []
    queue = _attempts(m, mode)
    first = queue[0]
    t0 = time.time()
    try:
        while queue:
            flavor, md = queue.pop(0)
            exe = find_exe(flavor)
            if not exe:
                continue
            w, h, steps = imgpower.shape(m, native, plan, cpu=flavor == "cpu")
            cmd = _command(exe, m, paths, full, neg, w, h, steps, seed, out, flavor, md, int(plan.get("threads") or 0))
            res = _run(cmd, out, stop, progress)
            if res is not None:
                LAST_RUN.clear()
                LAST_RUN.update(flavor=flavor, mode=md, w=w, h=h, steps=steps, sec=round(time.time() - t0))
                if plan.get("auto") and (flavor, md) != first:
                    _remember(m["id"], "cpu" if flavor == "cpu" else md)
                return res, "png"
            tail = _LAST_TAIL[0]
            errors.append(f"{'видеокарта' if flavor == 'vulkan' else 'процессор'}: {tail}")
            if flavor == "vulkan" and OOM.search(tail) and md in MODE_RANK:
                # не хватило памяти: «обычный» режим (ему нужно больше) пробовать бессмысленно — только экономнее
                queue = [q for q in queue if not (q[0] == "vulkan" and MODE_RANK.get(q[1], 9) < MODE_RANK[md])]
        raise LocalImgError("встроенный генератор не смог нарисовать (" + "; ".join(errors or ["нет движка"]) + ").")
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)


_LAST_TAIL = [""]


_PROG = re.compile(r"(\d+)\s*/\s*(\d+)\s*-\s*([\d.]+)\s*(s/it|it/s)")


def parse_progress(line: str) -> tuple[int, int, float] | None:
    """Строка прогресса sd.cpp («|=====>   | 7/20 - 3.40s/it») → (шаг, всего, секунд на шаг)."""
    m = _PROG.search(line)
    if not m:
        return None
    v = float(m.group(3))
    per = v if m.group(4) == "s/it" else (1.0 / v if v else 0.0)
    return int(m.group(1)), int(m.group(2)), per


def _run(cmd: list[str], out: Path, stop, progress=None) -> bytes | None:
    """Запускает движок. Готовая картинка — байты; ошибка — None (последняя строка вывода в _LAST_TAIL)."""
    flags = 0x08000000 if IS_WIN else 0                       # CREATE_NO_WINDOW
    if out.exists():
        out.unlink()
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    except OSError as e:
        _LAST_TAIL[0] = str(e)
        return None
    lines: list[str] = []

    def pump():
        buf = b""
        try:
            while True:
                chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(1)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for raw in parts:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    pr = parse_progress(line)
                    if pr:
                        if progress:
                            try:
                                progress(*pr)
                            except Exception:
                                pass
                    else:
                        lines.append(line)
                        del lines[:-40]
        except (OSError, ValueError):
            pass
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t0 = time.time()
    while p.poll() is None:
        time.sleep(0.3)
        if stop is not None and stop.is_set():
            p.kill()
            raise LocalImgError("остановлено")
        if time.time() - t0 > 1800:
            p.kill()
            raise LocalImgError("генератор рисует слишком долго (больше 30 минут).")
    t.join(2)
    try:
        p.stdout.close()
    except OSError:
        pass
    if p.returncode == 0 and out.exists() and out.stat().st_size > 1000:
        return out.read_bytes()
    _LAST_TAIL[0] = (lines[-1] if lines else f"код {p.returncode}")[:160]      # видеокарта не потянула — следующая попытка
    return None
