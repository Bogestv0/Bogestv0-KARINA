"""Обновление из GitHub Releases: проверка новой версии, загрузка установщика с проверкой SHA-256, запуск."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request
from pathlib import Path
from . import __version__
from .config import DATA, FROZEN

REPO = "Bogestv0/Bogestv0-KARINA"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases"
UPD_DIR = DATA / "updates"
UA = {"User-Agent": "Bogestv0-KARINA"}
# скачивать разрешаем только из релизов этого репозитория (GitHub отдаёт файлы через свой CDN)
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")


class Cancelled(Exception):
    pass


def parse_version(s: str) -> tuple:
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", s or "")
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def is_newer(tag: str, current: str = __version__) -> bool:
    a, b = parse_version(tag), parse_version(current)
    return bool(a) and a > b


def _get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/vnd.github+json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def _safe_url(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" and (u.hostname or "") in ALLOWED_HOSTS


def installed_via_setup() -> bool:
    """Установлено установщиком (рядом с exe лежит деинсталлятор Inno Setup)?"""
    return FROZEN and any(Path(sys.executable).parent.glob("unins*.exe"))


def parse_release(rel: dict, current: str = __version__) -> dict:
    tag = rel.get("tag_name", "")
    out = {"available": False, "current": current, "latest": tag.lstrip("vV"), "tag": tag, "notes": (rel.get("body") or "").strip()[:1500],
           "page": rel.get("html_url") or PAGE, "url": "", "sha_url": "", "name": "", "can_install": False}
    if not is_newer(tag, current):
        return out
    out["available"] = True
    assets = rel.get("assets", [])
    setup = next((a for a in assets if re.match(r"^Bogestv0-KARINA-Setup-.*\.exe$", a["name"])), None)
    sums = next((a for a in assets if a["name"] == "SHA256SUMS.txt"), None)
    if setup and sums and _safe_url(setup["browser_download_url"]) and _safe_url(sums["browser_download_url"]):
        out.update(url=setup["browser_download_url"], sha_url=sums["browser_download_url"], name=setup["name"],
                   size=setup.get("size", 0), can_install=installed_via_setup())
    return out


def check(current: str = __version__) -> dict:
    """Возвращает описание последней версии. Исключения — сеть/GitHub недоступны."""
    return parse_release(_get_json(API), current)


def _expected_sha(sha_url: str, name: str) -> str:
    req = urllib.request.Request(sha_url, headers=UA)
    for line in urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            return parts[0].lower()
    raise RuntimeError("В SHA256SUMS.txt нет контрольной суммы установщика — не устанавливаю.")


def download(info: dict, progress=None, cancel: threading.Event | None = None) -> Path:
    cancel = cancel or threading.Event()
    if not (info.get("url") and _safe_url(info["url"])):
        raise RuntimeError("У релиза нет установщика.")
    name = Path(info["name"]).name           # только имя файла, без путей
    expected = _expected_sha(info["sha_url"], name)
    UPD_DIR.mkdir(parents=True, exist_ok=True)
    for old in UPD_DIR.glob("*"):            # прошлые загрузки больше не нужны
        try:
            old.unlink()
        except OSError:
            pass
    dest = UPD_DIR / name
    tmp = dest.with_suffix(".part")
    h = hashlib.sha256()
    req = urllib.request.Request(info["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total, done, last = int(r.headers.get("Content-Length", 0)), 0, 0.0
        while True:
            if cancel.is_set():
                f.close(); tmp.unlink(missing_ok=True)
                raise Cancelled()
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b); h.update(b); done += len(b)
            if progress and time.time() - last > 0.3:
                progress(done, total); last = time.time()
    if h.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Контрольная сумма не совпала: файл повреждён или подменён. Установка отменена.")
    tmp.replace(dest)
    if progress:
        progress(done, done)
    return dest


def launch_installer(path: Path):
    """Запускает установщик (он сам закроет Карину и перезапустит её)."""
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"], creationflags=flags, close_fds=True)
