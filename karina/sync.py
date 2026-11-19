"""Синхронизация между компьютерами через Google.

Как это устроено. При входе через Google Карина просит доступ только к скрытой папке СВОЕГО приложения в вашем Google Drive
(область drive.appdata: чужих файлов и обычных документов она не видит и не показывает). В ней лежит один файл karina-sync.json.
Содержимое (профиль, настройки облачных моделей с ключами API, ключ Claude, список компьютеров) зашифровано ПЕРСОНАЛЬНЫМ КОДОМ,
который Карина выдаёт при первом входе. Код знаете только вы: без него файл — просто набор байтов, даже Google его не прочитает.
На втором компьютере: войти через тот же аккаунт Google → ввести персональный код → данные подтянутся, а в списке появится 2 устройства.
Дальше изменения уходят и приходят сами (побеждает более свежая правка).

Шифрование: PBKDF2-HMAC-SHA256 (300 000 итераций) из кода → два ключа; поток ключей HMAC-SHA256 в счётчиковом режиме + имитовставка HMAC-SHA256
(encrypt-then-MAC). Только стандартная библиотека Python, без дополнительных пакетов."""
from __future__ import annotations
import base64, copy, hashlib, hmac, json, platform, secrets, socket, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from . import google_auth, profile as prof

FILE_NAME = "karina-sync.json"
PBKDF2_ROUNDS = 300_000
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # без похожих знаков: 0/O, 1/I
POLL_SECONDS = 300
PUSH_DELAY = 4.0                                       # ждём после последней правки, чтобы не слать на каждое нажатие
MAX_DEVICES = 10
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class SyncError(Exception):
    """Текст можно показать пользователю."""


# ---------------------------------------------------------------- персональный код и шифрование
def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "KRN-" + "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def norm_code(code: str) -> str:
    c = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    return c[3:] if c.startswith("KRN") and len(c) == 19 else c


def code_ok(code: str) -> bool:
    c = norm_code(code)
    return len(c) == 16 and all(ch in ALPHABET for ch in c)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


_key_cache: dict[tuple[str, bytes], bytes] = {}


def _derive(code: str, salt: bytes) -> bytes:
    k = (norm_code(code), salt)
    if k not in _key_cache:
        if len(_key_cache) > 8:
            _key_cache.clear()
        _key_cache[k] = hashlib.pbkdf2_hmac("sha256", k[0].encode(), salt, PBKDF2_ROUNDS, 64)
    return _key_cache[k]


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def encrypt(code: str, obj: dict, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    dk = _derive(code, salt)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _stream(dk[:32], nonce, len(data))))
    mac = hmac.new(dk[32:], b"KARINA1" + salt + nonce + ct, hashlib.sha256).digest()
    return {"app": "karina", "v": 1, "salt": _b64(salt), "nonce": _b64(nonce), "ct": _b64(ct), "mac": _b64(mac)}


def decrypt(code: str, blob: dict) -> dict:
    try:
        salt, nonce, ct, mac = _unb64(blob["salt"]), _unb64(blob["nonce"]), _unb64(blob["ct"]), _unb64(blob["mac"])
    except Exception:
        raise SyncError("Файл синхронизации повреждён.") from None
    dk = _derive(code, salt)
    if not hmac.compare_digest(mac, hmac.new(dk[32:], b"KARINA1" + salt + nonce + ct, hashlib.sha256).digest()):
        raise SyncError("Неверный персональный код.")
    data = bytes(a ^ b for a, b in zip(ct, _stream(dk[:32], nonce, len(ct))))
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise SyncError("Файл синхронизации повреждён.") from None


# ---------------------------------------------------------------- Google Drive (скрытая папка приложения)
class Drive:
    def __init__(self, token: str):
        self.token = token

    def _req(self, url: str, data: bytes | None = None, method: str = "GET", ctype: str = "") -> bytes:
        h = {"Authorization": "Bearer " + self.token}
        if ctype:
            h["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (401,):
                raise SyncError("Google не принял доступ. Выйдите и войдите через Google заново.") from None
            if e.code == 403 and ("accessNotConfigured" in body or "has not been used" in body or "disabled" in body.lower()):
                raise SyncError("В вашем проекте Google Cloud выключен Google Drive API. Включите его: «API и сервисы» → «Библиотека» → Google Drive API → «Включить».") from None
            if e.code == 403:
                raise SyncError("Google не дал доступ к скрытой папке Drive. Выйдите и войдите заново, разрешив доступ к данным приложения.") from None
            raise SyncError(f"Google Drive вернул ошибку {e.code}.") from None
        except (OSError, ValueError):
            raise SyncError("Нет связи с Google Drive. Проверьте интернет.") from None

    def find(self) -> str:
        q = urllib.parse.urlencode({"spaces": "appDataFolder", "q": f"name='{FILE_NAME}' and trashed=false", "fields": "files(id,modifiedTime)", "pageSize": 10,
                                    "orderBy": "modifiedTime desc"})
        files = json.loads(self._req(f"{API}?{q}").decode("utf-8")).get("files") or []
        return files[0]["id"] if files else ""

    def read(self, fid: str) -> dict:
        try:
            return json.loads(self._req(f"{API}/{fid}?alt=media").decode("utf-8"))
        except ValueError:
            raise SyncError("Файл синхронизации повреждён.") from None

    def write(self, fid: str, blob: dict) -> str:
        data = json.dumps(blob).encode("utf-8")
        if fid:
            self._req(f"{UPLOAD}/{fid}?uploadType=media", data, "PATCH", "application/json")
            return fid
        boundary = "karina" + secrets.token_hex(8)
        meta = json.dumps({"name": FILE_NAME, "parents": ["appDataFolder"], "mimeType": "application/json"})
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{boundary}\r\nContent-Type: application/json\r\n\r\n").encode() \
            + data + f"\r\n--{boundary}--".encode()
        res = self._req(f"{UPLOAD}?uploadType=multipart&fields=id", body, "POST", f"multipart/related; boundary={boundary}")
        return json.loads(res.decode("utf-8")).get("id", "")

    def delete(self, fid: str):
        self._req(f"{API}/{fid}", method="DELETE")


# ---------------------------------------------------------------- сама синхронизация
class Sync:
    def __init__(self, cfg, emit=None, on_applied=None, drive_factory=None):
        self.cfg = cfg
        self.emit = emit or (lambda ev: None)
        self.on_applied = on_applied or (lambda: None)
        self._drive_factory = drive_factory or (lambda token: Drive(token))
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._token = ("", 0.0)
        self.busy = False
        self._started = False

    # ---- настройки
    def c(self) -> dict:
        return self.cfg.get("sync") or {}

    def _set(self, **kw):
        self.cfg.update({"sync": kw})

    def device_id(self) -> str:
        d = self.c().get("device_id")
        if not d:
            d = uuid.uuid4().hex[:12]
            self._set(device_id=d)
        return d

    def device_name(self) -> str:
        return self.c().get("device_name") or (socket.gethostname() or "Компьютер")[:40]

    def _me(self, old: dict | None = None) -> dict:
        now = time.time()
        return {"name": self.device_name(), "os": platform.system() or "?", "added": (old or {}).get("added", now), "seen": now}

    # ---- что синхронизируем
    def snapshot(self) -> dict:
        p = prof.get(self.cfg)
        profile = {k: v for k, v in p.items() if k not in ("google", "last_greeted")}
        llm = self.cfg.get("llm") or {}
        return {"profile": profile, "cloud": copy.deepcopy(self.cfg.get("cloud") or {}),
                "claude": {"api_key": llm.get("api_key", ""), "enabled": bool(llm.get("enabled")), "model": (self.cfg.get("brain") or {}).get("claude_model", "")}}

    def apply(self, payload: dict):
        from . import cloud as cloudmod
        cur = prof.get(self.cfg)
        if isinstance(payload.get("profile"), dict):
            new = prof.clean({**payload["profile"], "google": cur.get("google") or {}, "last_greeted": cur.get("last_greeted", "")})
            self.cfg.update({"profile": new})
        cl = payload.get("cloud")
        if isinstance(cl, dict):
            local = copy.deepcopy(self.cfg.get("cloud") or {})
            provs = {**(local.get("providers") or {}), **{k: v for k, v in (cl.get("providers") or {}).items() if k in cloudmod.PRESETS and isinstance(v, dict)}}
            merged = {"strategy": cl.get("strategy") if cl.get("strategy") in cloudmod.STRATEGIES else local.get("strategy", "failover"),
                      "order": [x for x in (cl.get("order") or []) if x in cloudmod.PRESETS], "providers": provs}
            self.cfg.data["cloud"] = merged
            self.cfg.save()
        ai = payload.get("claude")
        if isinstance(ai, dict):
            patch: dict = {}
            if ai.get("api_key"):
                patch["llm"] = {"api_key": str(ai["api_key"])[:400], "enabled": bool(ai.get("enabled"))}
            if ai.get("model"):
                patch["brain"] = {"claude_model": str(ai["model"])[:80]}
            if patch:
                self.cfg.update(patch)
        try:
            self.on_applied()
        except Exception:
            pass

    # ---- Google
    def creds(self) -> tuple[str, str]:
        return google_auth.client_credentials(self.cfg)

    def has_access(self) -> bool:
        return bool(self.c().get("refresh_token"))

    def _drive(self) -> Drive:
        tok, until = self._token
        if not tok or time.time() > until:
            cid, sec = self.creds()
            try:
                tok = google_auth.refresh_access(cid, sec, self.c().get("refresh_token", ""))
            except google_auth.GoogleError as e:
                raise SyncError(str(e)) from None
            self._token = (tok, time.time() + 3000)
        return self._drive_factory(tok)

    # ---- состояние для окна
    def state(self) -> dict:
        c = self.c()
        g = (prof.get(self.cfg).get("google") or {})
        me = self.device_id()
        devs = [{"id": d.get("id"), "name": d.get("name", "?"), "os": d.get("os", ""), "seen": d.get("seen", 0), "this": d.get("id") == me} for d in (c.get("devices") or [])]
        return {"signed": bool(g.get("email")), "access": self.has_access(), "enabled": bool(c.get("enabled")), "pending": bool(c.get("pending")),
                "code": c.get("code", "") if c.get("enabled") else "", "devices": devs, "last_sync": c.get("last_sync", 0), "error": c.get("error", ""),
                "busy": self.busy, "device_name": self.device_name()}

    # ---- шаги
    def _push(self, drive: Drive, fid: str, devices: dict, code: str, salt: bytes | None):
        payload = self.snapshot()
        payload["devices"] = devices
        payload["updated"] = time.time()
        fid = drive.write(fid, encrypt(code, payload, salt))
        self._set(remote_updated=payload["updated"], changed=0, last_sync=time.time(), error="",
                  devices=[{"id": k, **v} for k, v in devices.items()])
        return fid

    def _load(self, drive: Drive, fid: str, code: str):
        blob = drive.read(fid)
        return decrypt(code, blob), _unb64(blob["salt"])

    def on_login(self, info: dict):
        """После входа через Google: запоминаем доступ и смотрим, есть ли уже файл синхронизации."""
        with self._lock:
            if not info.get("refresh_token") or not info.get("drive"):
                self._set(refresh_token="", enabled=False, pending=False,
                          error="Google не дал доступ к скрытой папке Drive: вход выполнен, но синхронизация недоступна. Войдите ещё раз и разрешите доступ к данным приложения.")
                return {"kind": "no_access"}
            self._set(refresh_token=info["refresh_token"], error="")
            self._token = ("", 0.0)
            self.device_id()
            drive = self._drive()
            fid = drive.find()
            code = self.c().get("code", "")
            if not fid:                                     # первый компьютер: выдаём персональный код
                code = new_code()
                self._set(code=code, enabled=True, pending=False)
                self._push(drive, "", {self.device_id(): self._me()}, code, None)
                return {"kind": "new_code", "code": code}
            if code_ok(code):                               # этот же компьютер входит повторно: код уже известен
                try:
                    self._load(drive, fid, code)
                    self._set(enabled=True, pending=False)
                    self._start_sync_locked(drive, fid)
                    return {"kind": "resumed"}
                except SyncError:
                    pass
            self._set(enabled=False, pending=True, code="")
            return {"kind": "need_code"}

    def enter_code(self, code: str) -> dict:
        with self._lock:
            if not code_ok(code):
                raise SyncError("Код выглядит так: KRN-XXXX-XXXX-XXXX-XXXX (буквы и цифры).")
            if not self.has_access():
                raise SyncError("Сначала войдите через Google.")
            code = "KRN-" + "-".join(norm_code(code)[i:i + 4] for i in range(0, 16, 4))
            drive = self._drive()
            fid = drive.find()
            if not fid:
                raise SyncError("Файла синхронизации в вашем Google Drive нет. Нажмите «Начать заново».")
            payload, salt = self._load(drive, fid, code)         # неверный код → SyncError
            devices = dict(payload.get("devices") or {})
            if len(devices) >= MAX_DEVICES and self.device_id() not in devices:
                raise SyncError(f"Уже подключено {MAX_DEVICES} компьютеров. Отключите ненужные на подключённом компьютере.")
            self._set(code=code, enabled=True, pending=False)
            self.apply(payload)
            devices[self.device_id()] = self._me(devices.get(self.device_id()))
            self._push(drive, fid, devices, code, salt)
            return {"devices": len(devices)}

    def reset(self) -> str:
        """«Забыл код»: стираем старый файл и создаём новый (с данными этого компьютера и новым кодом). Другие компьютеры придётся подключить заново."""
        with self._lock:
            if not self.has_access():
                raise SyncError("Сначала войдите через Google.")
            drive = self._drive()
            fid = drive.find()
            code = new_code()
            self._set(code=code, enabled=True, pending=False)
            self._push(drive, fid, {self.device_id(): self._me()}, code, None)
            return code

    def _start_sync_locked(self, drive: Drive, fid: str):
        code = self.c().get("code", "")
        payload, salt = self._load(drive, fid, code)
        devices = dict(payload.get("devices") or {})
        if self.device_id() not in devices:
            devices[self.device_id()] = self._me()
        self.apply(payload)
        self._push(drive, fid, devices, code, salt)

    def sync_now(self) -> dict:
        """Подтянуть чужие изменения или отправить свои — смотря что новее."""
        with self._lock:
            c = self.c()
            if not c.get("enabled") or not code_ok(c.get("code", "")):
                return {"did": "off"}
            drive = self._drive()
            fid = drive.find()
            code = c["code"]
            if not fid:                                              # файл удалили: восстанавливаем из этого компьютера
                self._push(drive, "", {self.device_id(): self._me()}, code, None)
                return {"did": "push"}
            payload, salt = self._load(drive, fid, code)
            devices = dict(payload.get("devices") or {})
            me = self.device_id()
            if me not in devices:
                self._set(enabled=False, pending=False, code="", error="Этот компьютер отключили на другом устройстве. Подключите его снова, введя код.", devices=[])
                return {"did": "removed"}
            remote_updated = float(payload.get("updated") or 0)
            changed = float(c.get("changed") or 0)
            if changed and changed >= remote_updated:
                devices[me] = self._me(devices.get(me))
                self._push(drive, fid, devices, code, salt)
                return {"did": "push"}
            if remote_updated > float(c.get("remote_updated") or 0):
                self.apply(payload)
                self._set(remote_updated=remote_updated, last_sync=time.time(), error="", devices=[{"id": k, **v} for k, v in devices.items()])
                return {"did": "pull"}
            self._set(last_sync=time.time(), error="", devices=[{"id": k, **v} for k, v in devices.items()])
            return {"did": "same"}

    def remove_device(self, did: str):
        with self._lock:
            c = self.c()
            if not c.get("enabled") or did == self.device_id():
                return
            drive = self._drive()
            fid = drive.find()
            if not fid:
                return
            payload, salt = self._load(drive, fid, c["code"])
            devices = dict(payload.get("devices") or {})
            devices.pop(did, None)
            self._push(drive, fid, devices, c["code"], salt)

    def disconnect(self):
        """Этот компьютер выходит из синхронизации (данные на остальных и в Drive остаются)."""
        with self._lock:
            c = self.c()
            try:
                if c.get("enabled") and c.get("refresh_token") and code_ok(c.get("code", "")):
                    drive = self._drive()
                    fid = drive.find()
                    if fid:
                        payload, salt = self._load(drive, fid, c["code"])
                        devices = dict(payload.get("devices") or {})
                        if devices.pop(self.device_id(), None) is not None:
                            self._push(drive, fid, devices, c["code"], salt)
            except Exception:
                pass                                          # нет сети — не страшно: выходим локально
            self._set(enabled=False, pending=False, code="", refresh_token="", devices=[], error="", changed=0)
            self._token = ("", 0.0)

    # ---- фоновая работа
    def touch(self):
        """Что-то изменилось локально (профиль, ключи, очередь облаков): отправим чуть позже одним пакетом."""
        if not self.c().get("enabled"):
            return
        self._set(changed=time.time())
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(PUSH_DELAY, self.run_async, kwargs={"quiet": True})
        self._timer.daemon = True
        self._timer.start()

    def run_async(self, quiet: bool = False):
        def work():
            if self.busy:
                return
            self.busy = True
            err = ""
            try:
                self.sync_now()
            except SyncError as e:
                err = str(e)
            except Exception as e:
                err = f"Не получилось синхронизировать: {e.__class__.__name__}"
            finally:
                self.busy = False
                if err:
                    self._set(error=err)
                self.emit({"type": "sync_done", "error": err, "quiet": quiet})
        threading.Thread(target=work, daemon=True, name="karina-sync").start()

    def start_background(self):
        if self._started:
            return
        self._started = True

        def loop():
            time.sleep(8)
            while True:
                if self.c().get("enabled") and not self.busy:
                    self.run_async(quiet=True)
                time.sleep(POLL_SECONDS)
        threading.Thread(target=loop, daemon=True, name="karina-sync-poll").start()
