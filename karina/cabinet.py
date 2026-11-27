"""Связь Карины с кабинетом (cabinet.bogestv0.ru): привязка компьютера по коду и отправка событий.

Привязка: в кабинете, раздел «Телефон», создаётся одноразовый код (10 минут). Карина отправляет его вместе с именем компьютера
и получает ключ устройства. Пароль и вход в аккаунт Карине не нужны. Ключ лежит в config.json (раздел "cabinet").

События (запуск, выключение, телефон ушёл из сети и т.д.) отправляются пачками на /api/watch/event. Если связи нет, они ждут в очереди на диске
(не дольше суток, не больше 50 штук) и уходят позже со своим временем. Этим же кодом пользуется служба питания (karina/powersvc.py),
которая работает отдельно от окна Карины, поэтому всё без Config и без окон: на входе путь к файлу и словарь с ключом.
Если кабинет отвечает «ключ не действует» (компьютер отвязали в кабинете), Карина забывает ключ и больше ничего не шлёт."""
from __future__ import annotations
import json
import os
import platform
import re
import socket
import threading
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://cabinet.bogestv0.ru"
QUEUE_MAX = 50
QUEUE_TTL = 24 * 3600 * 1000          # мс: старше суток события не отправляем, они уже не важны
BATCH = 20
BEAT_EVERY = 60.0

PAIR_ERRORS = {
    "bad_code": "Код не подходит или устарел. Создайте новый в кабинете (раздел «Телефон»).",
    "rate": "Слишком много попыток. Подождите 10 минут.",
    "too_many": "К кабинету уже подключено 5 компьютеров. Отключите ненужный в кабинете.",
    "not_configured": "Кабинет сейчас не может принять подключение. Попробуйте позже.",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def tz_minutes() -> int:
    """Смещение часового пояса компьютера от UTC в минутах (с учётом летнего времени)."""
    lt = time.localtime()
    return int(-(time.altzone if lt.tm_isdst > 0 and time.daylight else time.timezone) // 60)


def computer_name() -> str:
    try:
        return socket.gethostname()[:40] or "Компьютер"
    except OSError:
        return "Компьютер"


def os_label() -> str:
    try:
        r = platform.release()
        return f"Windows {r}" if platform.system() == "Windows" else platform.system()
    except Exception:
        return ""


def valid_creds(c) -> bool:
    return (isinstance(c, dict) and bool(re.fullmatch(r"[0-9a-f]{64}", str(c.get("token") or "")))
            and bool(re.fullmatch(r"https://[A-Za-z0-9.\-]+(:\d{1,5})?/?", str(c.get("url") or DEFAULT_URL))))


def read_creds(path) -> dict | None:
    """Ключ устройства из файла: либо config.json (раздел cabinet), либо общий cabinet.json службы."""
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return None
    c = data.get("cabinet") if isinstance(data, dict) and isinstance(data.get("cabinet"), dict) else data
    if not valid_creds(c):
        return None
    return {"url": str(c.get("url") or DEFAULT_URL).rstrip("/"), "token": c["token"], "id": str(c.get("id") or ""), "name": str(c.get("name") or "")}


def _http(url: str, payload: dict, token: str | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    """POST JSON. (код ответа, тело). Код 0 — нет связи."""
    headers = {"content-type": "application/json", "user-agent": "Bogestv0-Karina"}
    if token:
        headers["authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _body(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _body(e.read())
    except Exception:
        return 0, {}


def _body(raw: bytes) -> dict:
    try:
        v = json.loads(raw.decode("utf-8"))
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def pair(code: str, url: str = DEFAULT_URL, timeout: float = 15.0) -> tuple[bool, dict | str]:
    """Привязка по коду. (True, creds) или (False, текст ошибки для человека)."""
    code = str(code or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9\- ]{8,14}", code):
        return False, PAIR_ERRORS["bad_code"]
    st, body = _http(url.rstrip("/") + "/api/watch/pair",
                     {"code": code, "name": computer_name(), "os": os_label(), "tz": tz_minutes()}, timeout=timeout)
    if st == 0:
        return False, "Нет связи с кабинетом. Проверьте интернет."
    if st == 200 and body.get("ok") and valid_creds({"token": body.get("token"), "url": url}):
        return True, {"url": url.rstrip("/"), "token": body["token"], "id": str(body.get("id") or ""), "name": str(body.get("name") or computer_name())}
    return False, PAIR_ERRORS.get(str(body.get("error")), "Кабинет не принял код.")


def post_events(creds: dict, events: list[dict], timeout: float = 10.0) -> str:
    """"ok" — приняты; "revoked" — ключ больше не действует; "fail" — нет связи или сбой (попробуем позже)."""
    st, body = _http(creds["url"] + "/api/watch/event", {"events": events}, token=creds["token"], timeout=timeout)
    if st == 200 and body.get("ok"):
        return "ok"
    if st == 401:
        return "revoked"
    return "fail"


class Outbox:
    """Очередь событий на диске: переживает перезапуск и отсутствие сети."""

    def __init__(self, path):
        self.path = path
        self._mu = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            v = json.loads(open(self.path, encoding="utf-8").read())
            return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, ev: dict):
        with self._mu:
            items = self._fresh(self._load())
            items.append(ev)
            self._save(items[-QUEUE_MAX:])

    @staticmethod
    def _fresh(items: list[dict], now: int | None = None) -> list[dict]:
        now = now or now_ms()
        return [e for e in items if isinstance(e.get("t"), (int, float)) and now - e["t"] < QUEUE_TTL]

    def pending(self) -> int:
        with self._mu:
            return len(self._load())

    def flush(self, creds: dict | None, timeout: float = 10.0) -> str:
        """Отправляет накопленное пачками. "ok" (всё ушло или пусто), "fail", "revoked", "nocreds"."""
        with self._mu:
            items = self._fresh(self._load())
            if not items:
                self._save([])
                return "ok"
            if not creds:
                self._save(items[-QUEUE_MAX:])
                return "nocreds"
            while items:
                res = post_events(creds, items[:BATCH], timeout)
                if res == "revoked":
                    self._save([])
                    return "revoked"
                if res != "ok":
                    self._save(items[-QUEUE_MAX:])
                    return "fail"
                items = items[BATCH:]
            self._save([])
            return "ok"


def make_event(type_: str, src: str, **fields) -> dict:
    ev = {"type": type_, "src": src, "t": now_ms(), "tz": tz_minutes()}
    ev.update({k: v for k, v in fields.items() if v is not None})
    return ev


class CabinetClient:
    """Для окна Карины: привязка, отправка событий телефона и пульс приложения. Ключ хранится в config.json (раздел cabinet)."""

    def __init__(self, cfg, data_dir, log=lambda m: None, watch_info=lambda: None, shared_dir=None):
        self.cfg, self.log, self.watch_info = cfg, log, watch_info
        self.outbox = Outbox(os.path.join(str(data_dir), "cabinet_queue.json"))
        self.shared_dir = shared_dir
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    # ---- состояние
    def creds(self) -> dict | None:
        c = self.cfg.get("cabinet") if hasattr(self.cfg, "get") else None
        return {"url": str(c.get("url") or DEFAULT_URL).rstrip("/"), "token": c["token"], "id": str(c.get("id") or ""), "name": str(c.get("name") or "")} if valid_creds(c) else None

    @property
    def linked(self) -> bool:
        return self.creds() is not None

    def status(self) -> dict:
        c = self.creds()
        return {"linked": bool(c), "name": c["name"] if c else "", "pending": self.outbox.pending(), "error": self.last_error,
                "url": c["url"] if c else DEFAULT_URL}

    def pair(self, code: str) -> dict:
        ok, res = pair(code, DEFAULT_URL)
        if not ok:
            return {"ok": False, "error": res}
        self.cfg.update({"cabinet": res})
        self.last_error = ""
        self.sync_shared()
        self.log(f"Кабинет: компьютер подключён ({res['name']})")
        threading.Thread(target=self.beat, daemon=True, name="karina-cabinet-beat").start()
        return {"ok": True, **self.status()}

    def unlink(self):
        self.cfg.update({"cabinet": {"url": DEFAULT_URL, "token": "", "id": "", "name": ""}})
        self.sync_shared()
        self.log("Кабинет: компьютер отключён в Карине")

    def sync_shared(self):
        """Копия ключа для службы питания (она работает отдельно, у неё свой каталог в ProgramData)."""
        d = self.shared_dir
        if not d or not os.path.isdir(d):
            return
        path = os.path.join(d, "cabinet.json")
        c = self.creds()
        try:
            if c:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(c, f)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as e:
            self.log(f"Кабинет: не удалось обновить ключ для службы ({e})")

    # ---- отправка
    def send(self, type_: str, force: bool = False, **fields):
        """Событие в очередь и сразу попытка отправить (в фоне, окно не ждёт сети)."""
        if not self.linked:
            return
        self.outbox.add(make_event(type_, "karina", **fields))
        self._wake.set()
        if force:
            threading.Thread(target=self.flush, daemon=True, name="karina-cabinet-flush").start()

    def _revoked(self):
        self.log("Кабинет: компьютер отвязан в кабинете, ключ удалён")
        self.cfg.update({"cabinet": {"url": DEFAULT_URL, "token": "", "id": "", "name": ""}})
        self.sync_shared()
        self.last_error = "Компьютер отвязан в кабинете. Подключите заново."

    def flush(self):
        res = self.outbox.flush(self.creds())
        if res == "revoked":
            self._revoked()
        elif res == "fail":
            self.last_error = "Нет связи с кабинетом, события отправятся позже."
        elif res == "ok":
            self.last_error = ""
        return res

    def beat(self):
        """Пульс приложения (раз в минуту): кабинет видит, что Карина запущена, и состояние телефона."""
        c = self.creds()
        if not c:
            return
        ev = make_event("beat", "karina")
        w = self.watch_info()
        if w:
            ev["watch"] = w
        if self.flush() == "ok":                    # пульс в очередь не кладём: старый пульс бесполезен, шлём только свежий
            if post_events(c, [ev]) == "revoked":
                self._revoked()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="karina-cabinet")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _run(self):
        self.sync_shared()
        last_beat = 0.0
        while not self._stop.is_set():
            try:
                if self.linked:
                    if time.monotonic() - last_beat >= BEAT_EVERY:
                        last_beat = time.monotonic()
                        self.beat()
                    elif self.outbox.pending():
                        self.flush()
            except Exception as e:
                self.log(f"Кабинет: ошибка отправки: {e.__class__.__name__}: {e}")
            self._wake.wait(5.0)
            self._wake.clear()
