"""Полка и ярлыки динамического острова.

Полка — временное хранилище «в руках»: файлы, папки и кусочки текста. Файлы хранятся ссылками (путь), ничего не копируется.
Ярлыки — программа, папка, файл или сайт одним нажатием на главной вкладке. Здесь нет окон: всё проверяется тестами."""
from __future__ import annotations
import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import DATA

IS_WIN = sys.platform == "win32"
MAX_SHELF = 40
MAX_LINKS = 12
THUMB_MAX_BYTES = 40 * 1024 * 1024

EXT = {
    "app": {".exe", ".lnk", ".bat", ".cmd", ".msi", ".appref-ms"},
    "img": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".ico"},
    "audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv"},
    "arch": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"},
    "doc": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".rtf", ".odt", ".csv", ".json"},
}


def is_url(s: str) -> bool:
    import re
    return bool(re.match(r"^(https?://|www\.)\S+$", str(s or "").strip(), re.I))


_TLDS = {"com", "ru", "org", "net", "io", "app", "dev", "me", "tv", "su", "рф", "info", "ai", "gg", "co", "by", "kz", "ua", "de", "fm", "ly", "to", "xyz", "online", "site", "pro", "tech", "cloud", "wiki", "link"}


def as_url(s: str) -> str:
    """Ссылка из того, что вставили: «https://…», «www.…» и просто «ya.ru/путь» (без схемы). Пустая строка, если это не ссылка
    (имя файла вроде «readme.md» ссылкой не считается: у домена должен быть известный верхний уровень)."""
    import re
    t = str(s or "").strip().strip('"').strip("<>")
    if not t or any(c in t for c in " \t\n\\"):
        return ""
    if re.match(r"^https?://\S+$", t, re.I):
        return t
    if re.match(r"^www\.\S+$", t, re.I):
        return "https://" + t
    m = re.match(r"^([\w-]+(?:\.[\w-]+)*\.([\w-]+))(?::\d+)?(?:[/?#]\S*)?$", t, re.I | re.U)
    if m and m.group(2).lower() in _TLDS:
        return "https://" + t
    return ""


def url_host(u: str) -> str:
    h = str(u or "").split("//", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].split("@")[-1].split(":", 1)[0]
    return h[4:] if h.lower().startswith("www.") else h


def path_kind(p: str) -> str:
    """folder | app | img | audio | video | arch | doc | url | file — для значка и цвета."""
    p = str(p or "").strip().strip('"')
    if is_url(p):
        return "url"
    try:
        if os.path.isdir(os.path.expanduser(p)):
            return "folder"
    except OSError:
        pass
    ext = os.path.splitext(p)[1].lower()
    for k, s in EXT.items():
        if ext in s:
            return k
    return "file"


def label_of(p: str) -> str:
    """Подпись по умолчанию: имя файла без расширения для программ и ярлыков, иначе полное имя."""
    p = str(p or "").strip().strip('"')
    if is_url(p):
        host = p.split("//", 1)[-1].split("/", 1)[0]
        return host[4:] if host.startswith("www.") else host
    import re
    name = re.split(r"[\\/]", p.rstrip("\\/"))[-1] or p            # и «C:\\папка\\файл», и «/папка/файл»
    if path_kind(p) == "app":
        name = os.path.splitext(name)[0]
    return name


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} Б"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} КБ"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} МБ".replace(".", ",")
    return f"{n / 1024 ** 3:.1f} ГБ".replace(".", ",")


# ---------------------------------------------------------------- ярлыки (хранятся в настройках острова)
def clean_links(items) -> list[dict]:
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        path = str(it.get("path") or "").strip().strip('"')[:500]
        if not path or path.lower() in seen:
            continue
        name = " ".join(str(it.get("name") or "").split())[:20] or label_of(path)[:20]
        seen.add(path.lower())
        out.append({"name": name, "path": path})
    return out[:MAX_LINKS]


def open_target(path: str) -> bool:
    """Открыть ярлык или файл с полки: сайт в браузере, остальное — как двойным щелчком."""
    p = str(path or "").strip().strip('"')
    if not p:
        return False
    if is_url(p):
        import webbrowser
        return bool(webbrowser.open(p if p.lower().startswith("http") else "https://" + p))
    p = os.path.expanduser(p)
    if not os.path.exists(p):
        return False
    os.startfile(p)                    # только Windows
    return True


def reveal(path: str) -> bool:
    p = os.path.expanduser(str(path or ""))
    if not IS_WIN or not os.path.exists(p):
        return False
    subprocess.Popen(["explorer", "/select,", os.path.normpath(p)], creationflags=0x08000000)
    return True


# ---------------------------------------------------------------- картинки и буфер обмена
def thumb(path: str, size: int = 124) -> str:
    """Миниатюра картинки как data-URL (JPEG). Пустая строка, если это не картинка или её не удалось открыть."""
    try:
        p = Path(os.path.expanduser(path))
        if path_kind(str(p)) != "img" or not p.is_file() or p.stat().st_size > THUMB_MAX_BYTES:
            return ""
        from PIL import Image
        with Image.open(p) as im:
            im.draft("RGB", (size * 2, size * 2))
            im = im.convert("RGB")
            w, h = im.size
            s = max(size / w, size / h)                      # обрезаем по центру до квадрата
            im = im.resize((max(1, round(w * s)), max(1, round(h * s))))
            l, t = (im.width - size) // 2, (im.height - size) // 2
            im = im.crop((l, t, l + size, t + size))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=78)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def clipboard_paths() -> list[str]:
    """Файлы, скопированные в проводнике (Ctrl+C). Пусто, если в буфере не файлы."""
    try:
        from PIL import ImageGrab
        r = ImageGrab.grabclipboard()
        if isinstance(r, (list, tuple)):
            return [str(x) for x in r if x]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------- полка
class Shelf:
    def __init__(self, path: Path | None = None):
        self.path = path or (DATA / "shelf.json")
        self._lock = threading.Lock()
        self.data = {"items": [], "seq": 0}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), list):
                self.data = {"items": [i for i in d["items"] if isinstance(i, dict) and i.get("id")], "seq": int(d.get("seq") or 0)}
        except Exception:
            pass

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    def _next(self) -> int:
        self.data["seq"] += 1
        return self.data["seq"]

    def add_paths(self, paths) -> int:
        """Кладёт файлы и папки на полку. Повторы (тот же путь) не добавляются. Возвращает, сколько добавлено."""
        n = 0
        with self._lock:
            have = {i.get("path", "").lower() for i in self.data["items"] if i.get("path")}
            for p in paths or []:
                p = str(p or "").strip().strip('"')
                if not p or p.lower() in have or not os.path.exists(p):
                    continue
                self.data["items"].append({"id": self._next(), "type": "path", "path": p, "name": label_of(p), "ts": time.time()})
                have.add(p.lower())
                n += 1
            self.data["items"] = self.data["items"][-MAX_SHELF:]
            if n:
                self._save()
        return n

    def add_text(self, text: str) -> bool:
        text = str(text or "").strip()[:20000]
        if not text:
            return False
        with self._lock:
            self.data["items"].append({"id": self._next(), "type": "text", "text": text, "name": " ".join(text.split())[:40], "ts": time.time()})
            self.data["items"] = self.data["items"][-MAX_SHELF:]
            self._save()
        return True

    def get(self, item_id) -> dict | None:
        for i in self.data["items"]:
            if i["id"] == item_id:
                return dict(i)
        return None

    def remove(self, item_id) -> bool:
        with self._lock:
            n = len(self.data["items"])
            self.data["items"] = [i for i in self.data["items"] if i["id"] != item_id]
            self._save()
            return len(self.data["items"]) != n

    def clear(self):
        with self._lock:
            self.data["items"] = []
            self._save()

    def view(self) -> list[dict]:
        """Что показывать: новые сверху; у файлов — вид значка, размер и признак «файл исчез»."""
        out = []
        for i in reversed(self.data["items"]):
            if i.get("type") == "text":
                out.append({"id": i["id"], "type": "text", "k": "text", "name": i.get("name", ""), "sub": "текст", "exists": True})
                continue
            p = i.get("path", "")
            ok = os.path.exists(p)
            k = path_kind(p) if ok else "file"
            sub = ""
            if ok and k != "folder":
                try:
                    sub = fmt_size(os.path.getsize(p))
                except OSError:
                    pass
            out.append({"id": i["id"], "type": "path", "k": k, "name": i.get("name") or label_of(p), "sub": "папка" if k == "folder" else sub, "path": p, "exists": ok})
        return out


# ---------------------------------------------------------------- значки ярлыков: настоящие иконки программ и сайтов
ICON_SIZE = 64
ICON_KINDS = {"app", "file", "doc", "audio", "video", "arch"}        # у папок и картинок свои значки/миниатюры


def _png_data_url(im) -> str:
    from PIL import Image
    im = im.convert("RGBA")
    im.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _cache_get(cache: Path, key: str) -> str | None:
    f = cache / (key + ".png")
    try:
        if f.is_file():
            return "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode() if f.stat().st_size > 0 else ""
    except OSError:
        pass
    return None


def _cache_put(cache: Path, key: str, data_url: str):
    try:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / (key + ".png")).write_bytes(base64.b64decode(data_url.split(",", 1)[1]) if data_url else b"")
    except Exception:
        pass


def favicon(url: str, cache: Path) -> str:
    """Значок сайта: favicon (сначала через сервис значков, потом сам файл с сайта). Результат, в том числе «нет значка», кэшируется на диске."""
    import hashlib
    host = url_host(url).lower()
    if not host:
        return ""
    key = "u-" + hashlib.sha1(host.encode()).hexdigest()[:16]
    hit = _cache_get(cache, key)
    if hit is not None and (hit or time.time() - (cache / (key + ".png")).stat().st_mtime < 86400):
        return hit
    import urllib.request
    from PIL import Image
    out = ""
    for u in (f"https://www.google.com/s2/favicons?domain={host}&sz=64", f"https://{host}/favicon.ico", f"http://{host}/favicon.ico"):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 Bogestv0-KARINA"})
            data = urllib.request.urlopen(req, timeout=5).read(400000)
            im = Image.open(io.BytesIO(data))
            if im.width >= 16 and im.height >= 16:
                out = _png_data_url(im)
                break
        except Exception:
            continue
    _cache_put(cache, key, out)
    return out


def file_icon(path: str, cache: Path) -> str:
    """Значок программы или файла из Windows (тот же, что в проводнике). Только Windows: берём через .NET (ExtractAssociatedIcon) в PowerShell."""
    if not IS_WIN:
        return ""
    import hashlib
    p = os.path.expanduser(str(path or "").strip().strip('"'))
    if not os.path.exists(p):
        return ""
    try:
        mt = int(os.path.getmtime(p))
    except OSError:
        mt = 0
    key = "f-" + hashlib.sha1(f"{p.lower()}|{mt}".encode("utf-8", "ignore")).hexdigest()[:16]
    hit = _cache_get(cache, key)
    if hit is not None:
        return hit
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / (key + ".tmp.png")
    script = ("Add-Type -AssemblyName System.Drawing; $i=[System.Drawing.Icon]::ExtractAssociatedIcon($env:KARINA_ICON_SRC); "
              "if($i){$b=$i.ToBitmap(); $b.Save($env:KARINA_ICON_OUT,[System.Drawing.Imaging.ImageFormat]::Png)}")
    out = ""
    try:
        env = dict(os.environ, KARINA_ICON_SRC=p, KARINA_ICON_OUT=str(tmp))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script], env=env,
                       capture_output=True, timeout=12, creationflags=0x08000000)
        if tmp.is_file() and tmp.stat().st_size > 0:
            from PIL import Image
            with Image.open(tmp) as im:
                out = _png_data_url(im)
    except Exception:
        out = ""
    try:
        tmp.unlink()
    except OSError:
        pass
    _cache_put(cache, key, out)
    return out


def icon_for(path: str, cache: Path) -> str:
    """Значок ярлыка как data-URL; пустая строка — оставить нарисованный по виду (папка, документ…)."""
    p = str(path or "").strip().strip('"')
    if is_url(p) or as_url(p):
        return favicon(as_url(p) or p, cache)
    if path_kind(p) in ICON_KINDS:
        return file_icon(p, cache)
    return ""
