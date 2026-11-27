"""Тяжёлые компоненты, которые не входят в установщик (он весит 200–300 МБ), а докачиваются при первом запуске Карины:
библиотека torch (нужна голосу Silero), сам голос «Байя» и модели распознавания речи (small и tiny).

Всё это собирается в один архив Bogestv0-KARINA-Components-<версия>.zip (build/make_pack.py) и лежит в том же релизе GitHub, что и установщик.
Карина скачивает его один раз (с докачкой при обрыве, с проверкой SHA-256) и раскладывает:
  pylibs/  → %APPDATA%\\Bogestv0 KARINA\\components\\pylibs   (подключается к sys.path: activate())
  voices/  → %APPDATA%\\Bogestv0 KARINA\\voices
  models/  → %APPDATA%\\Bogestv0 KARINA\\models
Если архив скачать не удалось, остаются прежние запасные пути: модели распознавания и голос скачиваются по отдельности из интернета.
Из исходников (не exe) ничего не докачивается: там torch ставится через requirements.txt.
"""
from __future__ import annotations
import hashlib, json, os, shutil, sys, threading, time, urllib.error, urllib.request, zipfile
from pathlib import Path

from . import __version__
from .config import DATA, FROZEN
from .diag import trace

COMPONENTS_VERSION = "1"            # увеличить, если изменился состав архива (другой torch и т. п.): тогда он скачается заново
COMP = DATA / "components"
PYLIBS = COMP / "pylibs"
MARK = COMP / "installed.json"
UA = {"User-Agent": "Bogestv0-KARINA"}
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")

READ_CHUNK = 1 << 17                 # 128 КБ: прогресс идёт плавно даже на медленном канале (раньше читали по 1 МБ и окно казалось зависшим)
STALL_TIMEOUT = 30                   # секунд без данных — соединение считается оборванным, докачаем с того же места
MIN_SPEED = 2048                     # байт/с за 30 секунд: медленнее — соединение «висит»
PAUSED = threading.Event()           # кнопка «Пауза» на экране первого запуска

_active = False
_dll_handles: list = []


class ComponentsError(Exception):
    pass


class Cancelled(Exception):
    pass


class _Paused(Exception):
    pass


def _repo() -> str:
    from .updater import REPO
    return REPO


def pack_name(ver: str | None = None) -> str:
    return f"Bogestv0-KARINA-Components-{ver or __version__}.zip"


def release_base(ver: str | None = None) -> str:
    return f"https://github.com/{_repo()}/releases/download/v{ver or __version__}/"


def activate() -> bool:
    """Подключает докачанные библиотеки (torch и его зависимости) к Python. Безопасно вызывать сколько угодно раз."""
    global _active
    if _active or not PYLIBS.is_dir():
        return _active
    p = str(PYLIBS)
    if p not in sys.path:
        sys.path.append(p)               # в конец: то, что вложено в exe (numpy и др.), имеет приоритет
    dirs = [PYLIBS / "torch" / "lib", PYLIBS]
    for d in dirs:
        if d.is_dir():
            if hasattr(os, "add_dll_directory"):
                try:
                    _dll_handles.append(os.add_dll_directory(str(d)))
                except OSError:
                    pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    import importlib
    importlib.invalidate_caches()
    _active = True
    return True


def is_installed() -> bool:
    try:
        info = json.loads(MARK.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return info.get("version") == COMPONENTS_VERSION and (PYLIBS / "torch").is_dir()


def _safe(url: str) -> bool:
    import urllib.parse
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" and (u.hostname or "") in ALLOWED_HOSTS


def _expected_sha(name: str, base: str) -> str:
    req = urllib.request.Request(base + "SHA256SUMS.txt", headers=UA)
    text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            return parts[0].lower()
    raise ComponentsError(f"в SHA256SUMS.txt релиза нет {name}")


def _download(url: str, part: Path, progress, cancel: threading.Event | None) -> None:
    """Скачивает с докачкой: если файл .part уже есть, просит у сервера остаток. Зависшее соединение обрывает само."""
    have = part.stat().st_size if part.exists() else 0
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=STALL_TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:            # файл уже докачан целиком
            return
        raise
    with r:
        if not _safe(r.geturl()):
            raise ComponentsError("сервер перенаправил на неожиданный адрес: " + r.geturl())
        resumed = r.status == 206
        if have and not resumed:
            have = 0
        length = int(r.headers.get("Content-Length") or 0)
        total = have + length if length else 0
        done, last = have, 0.0
        win_t, win_b = time.time(), 0
        read = getattr(r, "read1", r.read)
        with open(part, "ab" if resumed else "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if PAUSED.is_set():
                    raise _Paused()
                b = read(READ_CHUNK)
                if not b:
                    break
                f.write(b)
                done += len(b)
                win_b += len(b)
                now = time.time()
                if progress and now - last > 0.4:
                    progress(done, total, "download")
                    last = now
                if now - win_t >= 30:
                    if win_b < 30 * MIN_SPEED:
                        raise ComponentsError("скорость почти нулевая — соединение зависло")
                    win_t, win_b = now, 0
        if progress:
            progress(done, total, "download")
        if total and done < total:
            raise ComponentsError(f"загрузка оборвалась на {done // 1048576} из {total // 1048576} МБ")


def _sha256(path: Path, progress=None) -> str:
    h = hashlib.sha256()
    size = path.stat().st_size
    done, last = 0, 0.0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            done += len(chunk)
            if progress and time.time() - last > 0.4:
                progress(done, size, "verify")
                last = time.time()
    return h.hexdigest()


def _replace_dir(src: Path, dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def install_zip(zip_path: Path, progress=None) -> None:
    """Распаковывает архив компонентов и раскладывает по местам."""
    COMP.mkdir(parents=True, exist_ok=True)
    tmp = COMP / "unpack"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for i, n in enumerate(names):
            z.extract(n, tmp)
            if progress and i % 200 == 0:
                progress(i, len(names), "extract")
    if not (tmp / "pylibs" / "torch").is_dir():
        shutil.rmtree(tmp, ignore_errors=True)
        raise ComponentsError("в архиве нет torch")
    _replace_dir(tmp / "pylibs", PYLIBS)
    vdir = tmp / "voices"
    if vdir.is_dir():
        (DATA / "voices").mkdir(parents=True, exist_ok=True)
        for f in vdir.glob("*.pt"):
            os.replace(f, DATA / "voices" / f.name)
            trace(f"Компоненты: голос {f.name} положен в {DATA / 'voices'} ({(DATA / 'voices' / f.name).stat().st_size // 1048576} МБ)")
    else:
        trace("Компоненты: в архиве нет папки voices — голос придётся скачать отдельно")
    mdir = tmp / "models"
    if mdir.is_dir():
        (DATA / "models").mkdir(parents=True, exist_ok=True)
        for d in mdir.iterdir():
            _replace_dir(d, DATA / "models" / d.name)
    MARK.write_text(json.dumps({"version": COMPONENTS_VERSION, "app": __version__}), "utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    if progress:
        progress(1, 1, "extract")


def ensure(progress=None, cancel: threading.Event | None = None, log=lambda m: None, attempts: int = 3) -> bool:
    """Гарантирует, что компоненты на месте. progress(сделано, всего, этап: download|verify|extract). True — готово.
    В исходниках (не exe) ничего не делает. Бросает ComponentsError, если скачать не удалось."""
    if not FROZEN:
        return False
    if is_installed():
        activate()
        return True
    name = pack_name()
    base = release_base()
    COMP.mkdir(parents=True, exist_ok=True)
    part = COMP / (name + ".part")
    last: Exception | None = None
    i = 0
    while i < attempts:
        try:
            log(f"Компоненты: скачиваю {name} (попытка {i + 1}" + (f", уже есть {part.stat().st_size // 1048576} МБ" if part.exists() else "") + ")")
            expected = _expected_sha(name, base)
            _download(base + name, part, progress, cancel)
            if progress:
                progress(0, 1, "verify")
            if _sha256(part, progress) != expected:
                part.unlink(missing_ok=True)
                raise ComponentsError("контрольная сумма архива не совпала")
            log("Компоненты: архив скачан и проверен, распаковываю")
            install_zip(part, progress)
            part.unlink(missing_ok=True)
            activate()
            log("Компоненты: установлены")
            return True
        except Cancelled:
            raise
        except _Paused:
            log("Компоненты: пауза")
            if progress:
                progress(part.stat().st_size if part.exists() else 0, 0, "paused")
            while PAUSED.is_set():                     # пауза не тратит попытки: ждём «Продолжить»
                if cancel is not None and cancel.wait(0.3):
                    raise Cancelled()
                if cancel is None:
                    time.sleep(0.3)
            log("Компоненты: продолжаю")
            continue
        except Exception as e:
            last = e
            i += 1
            log(f"Компоненты: попытка {i} не удалась: {e.__class__.__name__}: {e}")
            if progress:
                progress(part.stat().st_size if part.exists() else 0, 0, "retry")
            if i >= attempts:
                break
            if cancel is not None and cancel.wait(3 * i):
                raise Cancelled()
            if cancel is None:
                time.sleep(3 * i)
    raise ComponentsError(f"не удалось скачать компоненты ({last}). Проверьте интернет; если GitHub закрыт — включите VPN на время первого запуска. Скачанное сохранено: при следующем запуске загрузка продолжится с того же места")
