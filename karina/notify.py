"""Уведомления Windows (Telegram, WhatsApp, Discord и другие) для виджета-острова.

Windows складывает показанные уведомления в базу %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db. Карина раз в пару секунд
смотрит туда (на копию файла, саму базу не трогает), находит новые и отдаёт название программы, заголовок (обычно имя отправителя)
и текст. Никаких разрешений и входа в аккаунты не нужно: читаются только те уведомления, которые Windows и так показывает на экране.
Если в Windows для программы выключены уведомления (Параметры → Система → Уведомления) или включён режим «Не беспокоить», сообщения не появятся и здесь.

Настройки (config.json, раздел notify): enabled — следить за уведомлениями; mode — messengers (только мессенджеры) | all (все программы);
apps — свои слова из имени программы, которые тоже считаются мессенджерами; show_text — показывать текст сообщения (иначе только «Telegram: Имя»).
"""
from __future__ import annotations
import os, re, shutil, sqlite3, sys, tempfile, threading, time
from pathlib import Path
from xml.etree import ElementTree as ET

IS_WIN = sys.platform == "win32"
POLL = 2.0

# часть имени программы в системе → как её назвать вслух и на плашке
MESSENGERS = {
    "telegram": "Telegram", "whatsapp": "WhatsApp", "discord": "Discord", "viber": "Viber", "skype": "Skype", "slack": "Slack",
    "msteams": "Teams", "teams": "Teams", "signal": "Signal", "vk": "VK", "vkmessenger": "VK Мессенджер", "max": "MAX", "element": "Element",
}
_ALIAS_EXACT = {"max"}              # слишком короткие слова ищем только как целое слово, а не внутри имени


def db_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db"


def parse_payload(raw) -> tuple[str, str]:
    """XML уведомления → (заголовок, текст). Заголовок — первая строка, текст — остальные строки через пробел."""
    if raw is None:
        return "", ""
    if isinstance(raw, str):
        txt = raw
    else:
        b = bytes(raw)
        txt = ""
        for enc in ("utf-8-sig", "utf-16"):
            try:
                txt = b.decode(enc)
                if txt.lstrip().startswith("<"):
                    break
            except UnicodeDecodeError:
                continue
    txt = txt.strip().lstrip("﻿").replace("\x00", "")
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        return "", ""
    lines = []
    for el in root.iter():
        if el.tag.split("}")[-1] == "text":
            if el.get("placement") == "attribution":
                continue
            t = " ".join((el.text or "").split())
            if t:
                lines.append(t)
    if not lines:
        return "", ""
    return lines[0], " ".join(lines[1:])


def app_title(app_id: str) -> str:
    """Имя программы из идентификатора: «TelegramMessengerLLP.TelegramDesktop» → «Telegram»; чужие — по последней части без «.exe»."""
    low = (app_id or "").lower()
    for key, name in MESSENGERS.items():
        if key in _ALIAS_EXACT:
            if re.search(r"(?<![a-z])" + key + r"(?![a-z])", low):
                return name
        elif key in low:
            return name
    tail = re.split(r"[\\/!]", app_id or "")[-1]
    tail = re.sub(r"\.exe$", "", tail, flags=re.I).split(".")[-1]
    return tail or "Уведомление"


def wanted(app_id: str, conf: dict) -> bool:
    if not conf.get("enabled", True):
        return False
    if conf.get("mode", "messengers") == "all":
        return True
    low = (app_id or "").lower()
    if app_title(app_id) in set(MESSENGERS.values()):
        return True
    return any(str(w).strip().lower() in low for w in conf.get("apps", []) if str(w).strip())


def read_new(db: Path, after_id: int) -> list[dict]:
    """Уведомления с Id больше after_id: [{"id","app_id","title","text"}] по возрастанию Id. Читает копию базы вместе с журналом (-wal)."""
    out: list[dict] = []
    tmp = Path(tempfile.mkdtemp(prefix="karina_wpn_"))
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / ("w.db" + suffix))
        con = sqlite3.connect(str(tmp / "w.db"))
        try:
            rows = con.execute(
                "SELECT n.Id, h.PrimaryId, n.Payload FROM Notification n LEFT JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
                "WHERE n.Type = 'toast' AND n.Id > ? ORDER BY n.Id", (int(after_id),)).fetchall()
        finally:
            con.close()
        for nid, app_id, payload in rows:
            title, text = parse_payload(payload)
            out.append({"id": int(nid), "app_id": app_id or "", "title": title, "text": text})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def max_id(db: Path) -> int:
    rows = read_new(db, 0)
    return rows[-1]["id"] if rows else 0


class Notifier:
    """Фоновый наблюдатель. on_notify({"app","title","text"}) вызывается из своего потока. Включение берётся из conf() при каждом опросе."""

    def __init__(self, on_notify, conf, trace=lambda m: None):
        self.on_notify, self.conf, self.trace = on_notify, conf, trace
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    def start(self):
        if not IS_WIN or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="karina-notify")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        db, last, mt = db_path(), None, 0.0
        while not self._stop.wait(POLL):
            try:
                if not self.conf().get("enabled", True):
                    last = None               # выключили: когда включат снова, старое не показываем
                    continue
                if not db.exists():
                    continue
                stamp = max(((p.stat().st_mtime if p.exists() else 0) for p in (db, Path(str(db) + "-wal"))))
                if last is not None and stamp == mt:
                    continue
                mt = stamp
                if last is None:
                    last = max_id(db)         # первый просмотр: то, что уже лежало в центре уведомлений, не показываем
                    continue
                for n in read_new(db, last):
                    last = max(last, n["id"])
                    conf = self.conf()
                    if not wanted(n["app_id"], conf) or not (n["title"] or n["text"]):
                        continue
                    self.on_notify({"app": app_title(n["app_id"]), "title": n["title"], "text": n["text"] if conf.get("show_text", True) else ""})
                self.last_error = ""
            except Exception as e:                  # база занята или формат другой версии Windows: пробуем снова, на работе Карины это не сказывается
                msg = f"{e.__class__.__name__}: {e}"
                if msg != self.last_error:
                    self.trace("Уведомления: " + msg)
                self.last_error = msg
