"""Файлы, перетащенные в окно чата (или вставленные из буфера): окно присылает их кусками, а здесь они складываются во временную папку.

Зачем так: из окна браузера (pywebview) надёжно получить можно только содержимое файла, а не его путь на диске. Поэтому окно читает файл
и передаёт байты кусками (upload_begin → upload_chunk … → upload_end), а чат потом работает с этой копией, как с обычным приложенным файлом.
Оригиналы не трогаются. Копии лежат в data/uploads/<пачка>/ и удаляются через несколько дней.

Защита: путь файла очищается (без «..», без дисков, без программ и служебных папок), есть лимиты на размер файла, пачки и число файлов.
"""
from __future__ import annotations
import base64, binascii, os, shutil, threading, time, uuid
from pathlib import Path
from . import archives
from .config import DATA

UPLOADS = DATA / "uploads"
MAX_FILE = 80 * 1024 * 1024
MAX_BATCH = 200 * 1024 * 1024
MAX_FILES = 1500
KEEP_DAYS = 3


class UploadError(Exception):
    pass


class Uploads:
    def __init__(self, root: Path | None = None):
        self.root = root or UPLOADS
        self._lock = threading.Lock()
        self._open: dict[str, dict] = {}
        self._batches: dict[str, dict] = {}

    # ---- пачка и файлы ----
    def _batch(self, batch: str) -> dict:
        b = self._batches.get(batch)
        if b is None:
            batch = "".join(c for c in (batch or "") if c.isalnum())[:16] or uuid.uuid4().hex[:12]
            b = self._batches.setdefault(batch, {"id": batch, "dir": self.root / batch, "bytes": 0, "files": 0})
        return b

    def begin(self, rel: str, size: int = 0, batch: str = "") -> dict:
        """Начать приём файла. → {'ok', 'token', 'batch', 'top'} (top — что прикладывать к сообщению: файл или папка верхнего уровня)."""
        with self._lock:
            self.cleanup()
            clean = archives.clean_member(rel or "")
            if not clean:
                return {"ok": False, "skipped": True, "error": "файл пропущен (служебный, программа или недопустимое имя)"}
            if size and size > MAX_FILE:
                return {"ok": False, "error": f"«{clean.split('/')[-1]}» больше {MAX_FILE // 2**20} МБ — приложите архив поменьше или нужные файлы отдельно."}
            b = self._batch(batch)
            if b["files"] >= MAX_FILES:
                return {"ok": False, "error": f"За один раз принимается не больше {MAX_FILES} файлов."}
            dest = (b["dir"] / clean).resolve()
            if b["dir"].resolve() not in dest.parents:
                return {"ok": False, "error": "недопустимый путь"}
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            token = uuid.uuid4().hex[:16]
            self._open[token] = {"f": open(tmp, "wb"), "tmp": tmp, "dest": dest, "batch": b, "n": 0}
            b["files"] += 1
            top = (b["dir"] / clean.split("/")[0])
            return {"ok": True, "token": token, "batch": b["id"], "top": str(top)}

    def chunk(self, token: str, data_b64: str) -> dict:
        with self._lock:
            st = self._open.get(token)
            if not st:
                return {"ok": False, "error": "приём файла не начат"}
            try:
                raw = base64.b64decode(data_b64 or "", validate=False)
            except (binascii.Error, ValueError):
                self._abort(token)
                return {"ok": False, "error": "повреждённые данные"}
            st["n"] += len(raw)
            if st["n"] > MAX_FILE or st["batch"]["bytes"] + st["n"] > MAX_BATCH:
                self._abort(token)
                return {"ok": False, "error": "слишком большой размер — приложите меньше файлов"}
            st["f"].write(raw)
            return {"ok": True}

    def end(self, token: str) -> dict:
        with self._lock:
            st = self._open.pop(token, None)
            if not st:
                return {"ok": False, "error": "приём файла не начат"}
            st["f"].close()
            os.replace(st["tmp"], st["dest"])
            st["batch"]["bytes"] += st["n"]
            return {"ok": True, "size": st["n"]}

    def _abort(self, token: str):
        st = self._open.pop(token, None)
        if st:
            try:
                st["f"].close()
                st["tmp"].unlink(missing_ok=True)
            except OSError:
                pass

    def discard(self, path: str) -> bool:
        """Убрать принятую копию (когда файл убрали из сообщения). Только внутри папки загрузок."""
        try:
            p = Path(path).resolve()
            if self.root.resolve() not in p.parents:
                return False
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def cleanup(self, days: int = KEEP_DAYS):
        if not self.root.exists():
            return
        limit = time.time() - days * 86400
        for d in self.root.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < limit:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
