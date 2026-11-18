"""Естественный русский голос Silero v5 — работает у вас на компьютере, без интернета и без ключей.

Почему он: нейросеть заточена под русский язык, сама ставит ударения и ё, на обычном процессоре говорит в десятки раз быстрее, чем длится речь
(Silero заявляет RTF около 0,04). Используется один голос — Байя («baya», женский); в интерфейсе он называется «Карина».

Лицензия моделей v5_*_ru — CC BY-NC 4.0: бесплатно для личного некоммерческого использования. Для коммерческого есть MIT-вариант v5_cis_base
(другие голоса, нужны ударения от silero-stress). Модель скачивается один раз с models.silero.ai (адрес взят из официального models.yml).

Нужен пакет torch (CPU-версия подходит). Он входит в установщик, в requirements.txt и в обновления: ничего ставить вручную не нужно.
Модель входит в установщик (папка voices рядом с программой, её кладёт build/fetch_voice.py при сборке), поэтому голос работает сразу после установки,
ничего скачивать и обновлять не нужно. Докачка из интернета (ensure_model) остаётся только запасным путём для запуска из исходников,
где встроенной модели нет; пока её нет, Карина говорит обычным голосом.
"""
from __future__ import annotations
import http.client, importlib.util, os, re, ssl, sys, threading, time, urllib.error, urllib.request, wave
from pathlib import Path
from xml.sax.saxutils import escape

from .models import Cancelled
from . import components
from .diag import trace

components.activate()

MODEL_ID = "v5_6_ru"
URL = f"https://models.silero.ai/models/tts/ru/{MODEL_ID}.pt"
UA = {"User-Agent": "Bogestv0-KARINA"}
SPEAKERS = {"baya": "Байя"}        # голос Карины; остальные голоса модели намеренно не используются
SAMPLE_RATE = 48000
MAX_CHUNK = 450          # модель не любит очень длинные куски: режем по предложениям


FROZEN = bool(getattr(sys, "frozen", False))

READ_CHUNK = 1 << 16          # читаем по 64 КБ: на медленном канале прогресс виден сразу, а не раз в мегабайт
STALL_TIMEOUT = 20            # столько секунд без единого байта — считаем обрыв и переподключаемся
MAX_RECONNECTS = 8            # сколько раз за одну загрузку переподключаться, прежде чем сдаться
LAST_ERROR = ""               # последняя причина, почему голос не заработал: показывается в настройках, а не пропадает молча
_torch_state: dict = {"checked": False, "ok": False, "error": ""}

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) if FROZEN else Path(__file__).resolve().parent.parent


def bundled_path() -> Path:
    """Модель, которая лежит в самой программе (её кладёт установщик): voices/silero_v5_6_ru.pt рядом с приложением."""
    return APP_ROOT / "voices" / f"silero_{MODEL_ID}.pt"


def data_model_path(base: Path) -> Path:
    """Скачанная копия в папке данных (запасной путь, если встроенной модели нет)."""
    return Path(base) / "voices" / f"silero_{MODEL_ID}.pt"


def has_bundled() -> bool:
    try:
        return bundled_path().stat().st_size > 1_000_000
    except OSError:
        return False


def model_path(base: Path) -> Path:
    """Какой файл модели использовать: встроенный в программу, а если его нет — скачанный в папку данных."""
    return bundled_path() if has_bundled() else data_model_path(base)


def torch_available() -> bool:
    """torch входит в установщик и в requirements.txt, поэтому Silero работает и в exe, и из исходников."""
    return importlib.util.find_spec("torch") is not None


def torch_check(force: bool = False) -> tuple[bool, str]:
    """Реально пробует загрузить torch (find_spec видит только наличие папки, а не рабочие библиотеки: не хватает VC++ или DLL —
    раньше это молча оставляло системный голос). Возвращает (ок, текст ошибки). Результат запоминается."""
    if _torch_state["checked"] and not force:
        return _torch_state["ok"], _torch_state["error"]
    ok, err = False, ""
    if not torch_available():
        err = "torch не найден"
    else:
        try:
            import torch  # noqa: F401
            ok = True
        except BaseException as e:           # OSError (DLL), ImportError, RuntimeError
            err = f"{e.__class__.__name__}: {e}"
    _torch_state.update(checked=True, ok=ok, error=err)
    if not ok:
        trace(f"Silero: torch не загружается — {err}")
    return ok, err


def last_error() -> str:
    """Что мешает голосу прямо сейчас (пусто — всё хорошо)."""
    if LAST_ERROR:
        return LAST_ERROR
    if _torch_state["checked"] and not _torch_state["ok"]:
        return "Не загружается torch: " + _torch_state["error"]
    return ""


def _set_error(msg: str) -> None:
    global LAST_ERROR
    LAST_ERROR = msg


def torch_hint() -> str:
    if FROZEN:
        return "Библиотека torch (нужна голосу Silero) ещё не скачана: Карина докачивает её при первом запуске вместе с голосом. Проверьте интернет (или включите VPN) и перезапустите Карину."
    return "Для голоса Silero нужен пакет torch: закройте Карину и запустите run.bat ещё раз (он доустановит torch сам) или выполните «pip install torch»."


def is_ready(base: Path) -> bool:
    try:
        return model_path(base).stat().st_size > 1_000_000
    except OSError:
        return False


def delete(base: Path) -> None:
    """Удаляет только скачанную копию; встроенный голос остаётся."""
    p = data_model_path(base)
    for f in (p, p.with_name(p.name + ".part"), p.with_name(p.name + ".new"), p.with_name(p.name + ".new.part")):
        f.unlink(missing_ok=True)
    unload()


def _cert_message(dest: Path) -> str:
    return ("Не удалось проверить сертификат сервера Silero (просрочен или не принят системой). Проверьте дату и время на компьютере и обновите Windows. "
            f"Если не помогло — скачайте файл {URL} в браузере и положите его сюда: {dest} (папка «Открыть папку с данными» → voices).")


def _curl_exe() -> str | None:
    """curl.exe есть в Windows 10/11. Он проверяет сертификат средствами самой Windows (как браузер, сам докачивает недостающие промежуточные сертификаты),
    поэтому справляется там, где встроенная проверка Python не может построить цепочку. Проверка сертификата остаётся включённой."""
    import shutil
    return shutil.which("curl.exe" if sys.platform == "win32" else "curl")


def _swap_in(new: Path, dest: Path) -> None:
    """Заменяет модель свежей копией. Загруженную модель Windows держит открытой («Отказано в доступе»), поэтому сначала выгружаем её;
    если файл всё равно занят — свежая копия остаётся рядом и подставится при следующем запуске Карины."""
    import gc
    unload()
    gc.collect()
    for i in range(6):
        try:
            new.replace(dest)
            return
        except PermissionError:
            time.sleep(0.4 * (i + 1))
            gc.collect()
    raise RuntimeError("Свежая копия голоса скачана и применится при следующем запуске Карины (текущий файл занят).")


def apply_pending(base: Path) -> bool:
    """Подставляет скачанное, но не применённое обновление голоса (вызывается до загрузки модели). True — подставили."""
    dest = data_model_path(base)
    new = dest.with_name(dest.name + ".new")
    try:
        if new.exists() and new.stat().st_size > 1_000_000:
            new.replace(dest)
            return True
    except OSError:
        pass
    return False


def _download_curl(part: Path, prog, cancel: threading.Event) -> None:
    import subprocess
    exe = _curl_exe()
    if not exe:
        raise FileNotFoundError("curl не найден")
    flags = 0x08000000 if sys.platform == "win32" else 0         # без чёрного окна
    total = 0
    try:
        head = subprocess.run([exe, "-sIL", "--max-time", "30", URL], capture_output=True, text=True, creationflags=flags, timeout=40).stdout
        sizes = re.findall(r"(?im)^content-length:\s*(\d+)", head)
        total = int(sizes[-1]) if sizes else 0
    except Exception:
        pass
    part.unlink(missing_ok=True)            # сервер не поддерживает докачку (curl: (33)) — всегда качаем с начала
    proc = subprocess.Popen([exe, "-fL", "--retry", "3", "--connect-timeout", "30", "-o", str(part), URL],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
    try:
        while proc.poll() is None:
            if cancel.is_set():
                proc.kill()
                raise Cancelled()
            prog(part.stat().st_size if part.exists() else 0, total, "download")
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode:
        err = (proc.stderr.read().decode("utf-8", "replace").strip().splitlines() or ["ошибка"])[-1]
        raise RuntimeError(f"Не удалось скачать голос Silero через curl: {err}")


MIN_MODEL_SIZE = 50 * 1024 * 1024        # настоящая модель около 141 МБ; меньше 50 МБ — обрыв или страница с ошибкой вместо файла
MIN_SPEED = 2048                         # байт/с: медленнее за 30 секунд — считаем, что соединение «висит»


def _stream(part: Path, prog, cancel: threading.Event) -> tuple[int, int]:
    """Одно подключение: докачивает .part с того места, где остановились. Читает небольшими кусками, чтобы прогресс шёл плавно,
    и сам обрывает «зависшее» соединение (нет данных STALL_TIMEOUT секунд или скорость почти нулевая). Возвращает (скачано, всего)."""
    have = part.stat().st_size if part.exists() else 0
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    r = urllib.request.urlopen(urllib.request.Request(URL, headers=headers), timeout=STALL_TIMEOUT)
    with r:
        if have and getattr(r, "status", 200) != 206:
            have = 0                                    # сервер не умеет докачку — начинаем сначала
        length = int(r.headers.get("Content-Length") or 0)
        total = have + length if length else 0
        done, last = have, 0.0
        win_t, win_b = time.time(), 0
        read = getattr(r, "read1", r.read)
        with open(part, "ab" if have else "wb") as f:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = read(READ_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                win_b += len(chunk)
                now = time.time()
                if now - last > 0.4:
                    prog(done, total, "download")
                    last = now
                if now - win_t >= 30:
                    if win_b < 30 * MIN_SPEED:
                        raise TimeoutError("скорость почти нулевая — соединение зависло")
                    win_t, win_b = now, 0
    prog(done, total, "download")
    if total and done < total:
        raise ConnectionError(f"соединение оборвалось на {done // 1048576} из {total // 1048576} МБ")
    return done, total


def download(base: Path, progress=None, cancel: threading.Event | None = None, fresh: bool = False) -> Path:
    """Скачивает модель. progress(done, total, stage): stage = search | download | reconnect | done. Бросает Cancelled при отмене.
    Сама переподключается (до MAX_RECONNECTS раз) и докачивает с места обрыва, если сервер это умеет; зависшее соединение обрывает.
    fresh=True — «обновить»: качает свежую копию рядом и заменяет старую только после успеха (старый голос не пропадёт, если что-то пошло не так)."""
    prog = progress or (lambda *a: None)
    cancel = cancel or threading.Event()
    dest = data_model_path(base)
    part = dest.with_name(dest.name + (".new.part" if fresh else ".part"))     # готовая свежая копия получает имя .new только после полной загрузки
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fresh:
        part.unlink(missing_ok=True)
    prog(part.stat().st_size if part.exists() else 0, 0, "search")
    fails = 0
    total = 0
    while True:
        if cancel.is_set():
            raise Cancelled()
        try:
            _, total = _stream(part, prog, cancel)
            break
        except Cancelled:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416:                 # .part уже весит больше файла: либо докачан, либо мусор
                if part.exists() and part.stat().st_size >= MIN_MODEL_SIZE:
                    break
                part.unlink(missing_ok=True)
                continue
            raise RuntimeError(f"Сервер Silero ответил ошибкой {e.code}. Попробуйте позже.") from None
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                try:                           # Python не смог построить цепочку сертификатов — пробуем проверку средствами системы (curl.exe)
                    _download_curl(part, prog, cancel)
                except Cancelled:
                    raise
                except Exception as e2:
                    raise RuntimeError(_cert_message(dest) + f" ({e2})") from None
                break
            err = e
        except (OSError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            err = e
        fails += 1
        trace(f"Silero: загрузка прервалась ({err.__class__.__name__}: {err}), переподключение {fails} из {MAX_RECONNECTS}")
        if fails > MAX_RECONNECTS:
            raise RuntimeError(f"Не удаётся скачать голос: {err}. Проверьте интернет или включите VPN и нажмите «Повторить» — продолжу с того же места.") from None
        prog(part.stat().st_size if part.exists() else 0, total, "reconnect")
        if cancel.wait(min(3 * fails, 15)):
            raise Cancelled()
    if not part.exists() or part.stat().st_size < MIN_MODEL_SIZE:
        size = part.stat().st_size if part.exists() else 0
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Скачанный файл слишком мал ({size // 1024} КБ): сервер отдал не модель. Попробуйте позже или включите VPN.")
    if fresh:
        done_new = dest.with_name(dest.name + ".new")
        part.replace(done_new)
        _swap_in(done_new, dest)
    else:
        part.replace(dest)
    size = dest.stat().st_size
    prog(size, size, "done")
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
        apply_pending(base)
        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 2)))
        imp = torch.package.PackageImporter(path)
        model = imp.load_pickle("tts_models", "model")
        model.to("cpu")
        _loaded.update(key=path, model=model)
    return _loaded["model"]


def preload(base: Path) -> bool:
    """Прогрев в фоне: первая загрузка модели занимает секунду-две, дальше фразы идут сразу. Пишет результат в журнал; True — модель загружена."""
    if not (is_ready(base) and torch_available()):
        return False
    t0 = time.time()
    with _lock:
        try:
            _load(base)
        except BaseException as e:
            msg = f"{e.__class__.__name__}: {e}"
            _set_error("Модель голоса есть, но не загрузилась: " + msg)
            trace("Silero: модель не загрузилась — " + msg)
            return False
    _set_error("")
    trace(f"Silero: модель голоса загружена за {time.time() - t0:.1f} с")
    return True


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


def synth_wav(base: Path, text: str, out_path: str, speaker: str = "baya", speed: int = 0):
    """Озвучить text в wav (48 кГц, моно). speed — ползунок «Скорость речи» (−5…5). Бросает исключение, если нет torch или модели."""
    if not is_ready(base):
        raise FileNotFoundError("голос Silero не скачан")
    if not torch_available():
        raise ImportError(torch_hint())
    if speaker not in SPEAKERS:
        speaker = "baya"
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


def ensure_model(base: Path, log=lambda m: None, emit=None, attempts: int = 3, cancel: threading.Event | None = None) -> bool:
    """Сама докачивает модель голоса, если torch есть, а модели ещё нет. True — голос готов. Не бросает исключений."""
    if is_ready(base):
        return True
    if not torch_available():
        log("Silero: torch не найден, модель не качаю")
        return False
    cancel = cancel or threading.Event()
    for i in range(attempts):
        if cancel.is_set():
            return False
        try:
            log("Silero: скачиваю модель голоса" + (f" (попытка {i + 1})" if i else ""))
            download(base, (lambda d, t, st: emit({"type": "silero_dl", "done": d, "total": t, "stage": st})) if emit else None, cancel)
            break
        except Cancelled:
            return False
        except Exception as e:
            log(f"Silero: не скачалось ({e.__class__.__name__}: {e})")
            _set_error(str(e) or e.__class__.__name__)
            if cancel.wait(8 * (i + 1)):
                return False
    ok = is_ready(base)
    if emit:
        emit({"type": "silero_dl_done", "error": "" if ok else (LAST_ERROR or "не удалось скачать, попробую при следующем запуске"), "ready": ok})
    if ok:
        _set_error("")
        log("Silero: файл голоса скачан")
        if preload(base):
            log("Silero: голос готов")
        else:
            ok = False
    return ok
