"""Мощность рисования картинок: что за железо, сколько видеопамяти, как рисовать, чтобы было быстро.

Почему это нужно. Модели SDXL (Pony, NoobAI, Animagine, Illustrious) весят ~7 ГБ и рассчитаны на видеокарту от 8 ГБ. На 3–4 ГБ они
не помещаются: движок несколько раз подряд падает по памяти, а в конце всё считает на процессоре в полный размер — это десятки минут.
Поэтому Карина:
  * определяет видеокарту и объём видеопамяти (и даёт указать его вручную);
  * в режиме «Авто» сама подбирает режим памяти, размер картинки и число шагов под это железо (как «Рекомендуется» у текстовых моделей);
  * позволяет выбрать мощность вручную: режим, качество/скорость, потоки процессора, выгрузку текстовой модели из видеопамяти;
  * подсказывает, какие модели подходят вашему ПК и сколько примерно ждать.

Модуль только считает и ничего не рисует: рисует localimg, порядок и события ведёт chat.py.
"""
from __future__ import annotations
import math, os, re, subprocess, sys, threading

IS_WIN = sys.platform == "win32"

POWER_MODES = ("auto", "max", "balanced", "low", "cpu")
QUALITIES = ("auto", "fast", "normal", "high")
UNLOAD_MODES = ("auto", "yes", "no")
POWER_LABEL = {"auto": "Авто", "max": "Максимум", "balanced": "Сбалансированно", "low": "Экономно", "cpu": "Процессор"}
MODE_OF_POWER = {"max": "fast", "balanced": "balanced", "low": "low", "cpu": "cpu"}      # режим «мощности» → режим памяти localimg
QUALITY_LABEL = {"fast": "быстро", "normal": "обычно", "high": "качественно"}

# целевая площадь картинки в мегапикселях и потолок числа шагов для SDXL (у FLUX.2 Klein шаги всегда свои — 4)
AREA = {"fast": 0.55, "normal": 0.80, "high": 1.05}
AREA_CPU = {"fast": 0.39, "normal": 0.55, "high": 0.80}
STEPS_CAP = {"fast": 20, "normal": 24, "high": 99}
STEPS_CAP_CPU = {"fast": 16, "normal": 20, "high": 28}
# что выбирает «Авто» для каждого уровня видеокарты: (режим памяти, качество)
AUTO = {"high": ("fast", "high"), "mid": ("balanced", "normal"), "low": ("low", "fast"),
        "tiny": ("cpu", "fast"), "none": ("cpu", "fast"), "unknown": ("balanced", "normal")}
QUALITY_OF_MODE = {"fast": "high", "balanced": "normal", "low": "fast", "cpu": "fast"}
MIN_SIDE = 448


# ---------------------------------------------------------------- железо
_HW: dict = {}
_LOCK = threading.Lock()
_INTEGRATED = re.compile(r"intel|uhd|iris|vega \d+ graphics|radeon\(tm\) graphics|radeon graphics|microsoft basic|remote display|virtual|parsec", re.I)
_SKIP = re.compile(r"microsoft basic|remote display|virtual display|parsec|meta virtual|indirect", re.I)


def _flags() -> int:
    return 0x08000000 if IS_WIN else 0          # CREATE_NO_WINDOW


def parse_nvidia(text: str) -> list[dict]:
    """Вывод nvidia-smi --query-gpu=name,memory.total (МБ) → [{"name","vram_gb"}]."""
    out = []
    for line in (text or "").splitlines():
        name, _, mb = line.rpartition(",")
        try:
            gb = round(int(float(mb.strip())) / 1024, 1)
        except ValueError:
            continue
        if name.strip():
            out.append({"name": name.strip(), "vram_gb": gb})
    return out


def _nvidia() -> list[dict]:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=6, creationflags=_flags())
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return parse_nvidia(r.stdout) if r.returncode == 0 else []


def reg_bytes_to_gb(v) -> float:
    """Значение HardwareInformation.qwMemorySize из реестра (число или байты, little-endian) → ГБ."""
    try:
        n = int.from_bytes(bytes(v)[:8], "little") if isinstance(v, (bytes, bytearray)) else int(v)
    except (TypeError, ValueError):
        return 0.0
    return round(n / 2 ** 30, 1)


def _registry() -> list[dict]:
    """Видеокарты Windows из реестра. Там память 64-битная (WMI AdapterRAM упирается в 4 ГБ и врёт про любую большую карту)."""
    if not IS_WIN:
        return []
    try:
        import winreg
    except ImportError:
        return []
    out = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    sub = winreg.EnumKey(k, i)
                    if not sub.isdigit():
                        continue
                    with winreg.OpenKey(k, sub) as s:
                        name = str(winreg.QueryValueEx(s, "DriverDesc")[0])
                        gb = 0.0
                        for val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                gb = reg_bytes_to_gb(winreg.QueryValueEx(s, val)[0])
                            except OSError:
                                continue
                            if gb:
                                break
                        out.append({"name": name, "vram_gb": gb})
                except OSError:
                    continue
    except OSError:
        return []
    return out


def _sysfs() -> list[dict]:
    """Linux (AMD): объём видеопамяти из /sys. На Windows не нужен, но не мешает."""
    out = []
    try:
        for p in sorted(os.scandir("/sys/class/drm"), key=lambda e: e.name):
            f = os.path.join(p.path, "device", "mem_info_vram_total")
            if os.path.isfile(f):
                out.append({"name": "AMD Radeon", "vram_gb": round(int(open(f).read().strip()) / 2 ** 30, 1)})
    except (OSError, ValueError):
        pass
    return out


def pick_gpu(found: list[dict]) -> dict | None:
    """Из всех найденных видеокарт — самая мощная отдельная; встроенная графика считается «без видеопамяти»."""
    cards = [g for g in found if g.get("name") and not _SKIP.search(g["name"])]
    if not cards:
        return None
    for g in cards:
        g["integrated"] = bool(_INTEGRATED.search(g["name"])) and g["vram_gb"] < 3 or g["vram_gb"] < 1.5
    cards.sort(key=lambda g: (g["integrated"], -g["vram_gb"]))
    return cards[0]


def _detect() -> dict:
    found = _nvidia()
    reg = _registry() if IS_WIN else []
    names = {g["name"] for g in found}
    found += [g for g in reg if g["name"] not in names]          # NVIDIA из реестра дублирует nvidia-smi
    if not found:
        found = _sysfs()
    g = pick_gpu(found)
    if not g:
        return {"detected": False, "gpu": "", "vram_gb": 0.0, "integrated": False, "gpus": []}
    return {"detected": g["vram_gb"] > 0 or g["integrated"], "gpu": g["name"], "vram_gb": g["vram_gb"], "integrated": g["integrated"],
            "gpus": [f"{x['name']} · {x['vram_gb']} ГБ" for x in found]}


def hardware(cfg=None, refresh: bool = False) -> dict:
    """{"gpu","vram_gb","integrated","detected","ram_gb","cpu","source"}. images.vram_gb в настройках перекрывает определённое."""
    global _HW
    with _LOCK:
        if refresh or not _HW:
            try:
                _HW = _detect()
            except Exception:                                    # определение железа не должно ломать рисование
                _HW = {"detected": False, "gpu": "", "vram_gb": 0.0, "integrated": False, "gpus": []}
    hw = dict(_HW, source="auto")
    try:
        from . import models
        hw["ram_gb"] = models.system_ram_gb()
    except Exception:
        hw["ram_gb"] = 0.0
    hw["cpu"] = os.cpu_count() or 4
    try:
        manual = float(((cfg.get("images", {}) or {}) if cfg is not None else {}).get("vram_gb") or 0)
    except (TypeError, ValueError):
        manual = 0.0
    if manual > 0:
        hw.update(vram_gb=manual, detected=True, integrated=False, source="manual")
    return hw


def tier(hw: dict) -> str:
    """high ≥10 ГБ видеопамяти · mid 6–10 · low 3–6 · tiny <3 · none (нет отдельной видеокарты) · unknown (не удалось определить)."""
    if not hw.get("detected"):
        return "unknown"
    v = float(hw.get("vram_gb") or 0)
    if hw.get("integrated") or v <= 0:
        return "none"
    return "high" if v >= 10 else "mid" if v >= 6 else "low" if v >= 3 else "tiny"


# ---------------------------------------------------------------- размеры и шаги
def _r64(x: float) -> int:
    return max(MIN_SIDE, int(round(x / 64.0)) * 64)


def fit_size(size: tuple[int, int], area_mp: float) -> tuple[int, int]:
    """Тот же формат кадра, но площадью не больше area_mp (мегапикселей); стороны кратны 64. Больше исходного не делает."""
    w, h = size
    if not area_mp or w * h <= area_mp * 1e6 * 1.02:
        return w, h
    k = math.sqrt(area_mp * 1e6 / (w * h))
    return min(w, _r64(w * k)), min(h, _r64(h * k))


def shape(m: dict, native: tuple[int, int], plan: dict, cpu: bool = False) -> tuple[int, int, int]:
    """Размер и число шагов для этой модели при этом плане. cpu=True — запасной проход на процессоре (меньше и короче)."""
    q = plan.get("quality") or "high"                             # без плана (старый вызов) ничего не урезаем
    area = plan.get("area") or AREA["high"]
    steps = int(m.get("steps") or 20)
    if cpu:
        area = min(area, AREA_CPU.get(q, AREA_CPU["fast"]))
    w, h = fit_size(native, area)
    if m.get("kind") != "flux2":                                  # дистиллированная FLUX.2 Klein рисует за 4 шага — их не трогаем
        steps = min(steps, (STEPS_CAP_CPU if cpu else STEPS_CAP).get(q, 99))
    return w, h, steps


# ---------------------------------------------------------------- план рисования
def plan(cfg, m: dict | None = None) -> dict:
    """Что делать с этой моделью при текущих настройках и железе.
    → {"mode": fast|balanced|low|cpu, "quality", "area", "threads", "unload_llm", "auto", "power", "tier", "label", "hw"}"""
    c = ((cfg.get("images", {}) or {}) if cfg is not None else {})
    hw = hardware(cfg)
    t = tier(hw)
    power = c.get("power") if c.get("power") in POWER_MODES else "auto"
    auto = power == "auto"
    if auto:
        mode, q_auto = AUTO[t]
    else:
        mode = MODE_OF_POWER[power]
        q_auto = QUALITY_OF_MODE[mode]
    quality = c.get("quality") if c.get("quality") in ("fast", "normal", "high") else q_auto
    area = (AREA_CPU if mode == "cpu" else AREA)[quality]
    try:
        threads = max(0, min(int(c.get("threads") or 0), hw["cpu"] * 2))
    except (TypeError, ValueError):
        threads = 0
    u = c.get("unload_llm") if c.get("unload_llm") in UNLOAD_MODES else "auto"
    unload = u == "yes" or (u == "auto" and mode != "cpu" and not (t == "high" and float(hw.get("vram_gb") or 0) >= 16))
    p = {"mode": mode, "quality": quality, "area": area, "threads": threads, "unload_llm": unload, "auto": auto, "power": power, "tier": t, "hw": hw}
    p["label"] = describe(p, m)
    return p


MODE_LABEL = {"fast": "всё на видеокарте", "balanced": "сбалансированно", "low": "экономно", "cpu": "на процессоре"}


def describe(p: dict, m: dict | None = None, native: tuple[int, int] = (832, 1216)) -> str:
    """Короткое описание плана для окна: «Экономно · 640×896 · 20 шагов»."""
    if m:
        w, h, steps = shape(m, native, p, cpu=p["mode"] == "cpu")
        tail = f" · {w}×{h} · {steps} {'шаг' if steps == 1 else 'шага' if steps % 10 in (2, 3, 4) and steps not in (12, 13, 14) else 'шагов'}"
    else:
        tail = f" · качество: {QUALITY_LABEL[p['quality']]}"
    return f"{MODE_LABEL[p['mode']].capitalize()}{tail}"


# ---------------------------------------------------------------- подходит ли модель этому ПК
ETA = {("sdxl", "good"): "примерно 30–60 с на картинку", ("sdxl", "ok"): "примерно 1–3 мин на картинку",
       ("sdxl", "slow"): "примерно 3–8 мин на картинку", ("sdxl", "heavy"): "10 минут и больше на картинку",
       ("flux", "good"): "примерно 20–60 с на картинку", ("flux", "ok"): "примерно 1–2 мин на картинку",
       ("flux", "slow"): "примерно 3–8 мин на картинку", ("flux", "heavy"): "примерно 3–8 мин на картинку"}
FIT_LABEL = {"good": "Подходит вашему ПК", "ok": "Рисует, но не быстро", "slow": "Медленно на вашем ПК", "heavy": "Очень тяжело для вашего ПК"}


def fit(m: dict, hw: dict) -> dict:
    """Как модель пойдёт на этом железе: {"level": good|ok|slow|heavy, "label", "eta", "recommended"}. Оценка грубая: время зависит ещё от процессора и диска."""
    t = tier(hw)
    flux = m.get("kind") == "flux2"
    if flux:
        level = {"high": "good", "mid": "good", "low": "good", "unknown": "good", "tiny": "slow", "none": "slow"}[t]
    else:
        level = {"high": "good", "mid": "ok", "low": "slow", "unknown": "ok", "tiny": "heavy", "none": "heavy"}[t]
    kind = "flux" if flux else "sdxl"
    rec = flux or (level == "good" and m.get("id") == "noobai")
    return {"level": level, "label": FIT_LABEL[level], "eta": ETA[(kind, level)], "recommended": bool(rec)}


def is_fast(m: dict | None) -> bool:
    """Быстрая (дистиллированная) модель: до 8 шагов вместо 25–30."""
    return bool(m) and int(m.get("steps") or 99) <= 8


def prefer_fast(order: list[str], p: dict, by_id) -> tuple[list[str], str]:
    """На слабом ПК в «Авто» быстрая модель рисует первой: первая картинка приходит за минуту, а не за десять.
    Тяжёлые модели идут следом (варианты появляются по мере готовности, их можно остановить). → (порядок, пояснение)."""
    if not p.get("auto") or p.get("mode") not in ("low", "cpu") or len(order) < 2:
        return order, ""
    fast = [x for x in order if is_fast(by_id(x))]
    if not fast or order[0] in fast:
        return order, ""
    rest = [x for x in order if x not in fast]
    names = ", ".join((by_id(x) or {}).get("title", x) for x in fast)
    return fast + rest, f"Видеопамяти мало ({p['hw'].get('vram_gb') or '?'} ГБ): сначала рисует быстрая модель ({names}), тяжёлые — после неё и заметно дольше."


def eta_text(p: dict, m: dict | None) -> str:
    """Что показать в строке состояния до первого шага: честная прикидка вместо «обычно до минуты»."""
    f = fit(m or {}, p["hw"]) if m else {"eta": "время зависит от видеокарты"}
    if p["mode"] == "cpu":
        return "на процессоре это медленно, " + ("примерно 3–8 мин" if (m or {}).get("kind") == "flux2" else "10 минут и больше")
    return f["eta"]


def fmt_sec(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60} мин {sec % 60:02d} с" if sec >= 60 else f"{sec} с"
