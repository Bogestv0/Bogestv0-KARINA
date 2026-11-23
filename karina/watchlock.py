"""«Часы как ключ»: пока часы (или телефон) рядом, компьютер работает как обычно. Связь пропала дольше N секунд —
Карина блокирует Windows и шлёт уведомление, которое через iPhone доходит до часов. Заряд часов показывается, если часы его отдают.

Как это устроено
  * Что считаем «рядом» — список целей (targets), хватает любой одной:
      ble    — Bluetooth-реклама устройства (по адресу или куску имени), с уровнем сигнала RSSI. Нужен пакет bleak.
      paired — устройство, сопряжённое с Windows (Параметры → Bluetooth): смотрим, есть ли с ним связь. Нужен winrt-Windows.Devices.Bluetooth.
      ping   — ответ на ping по IP в домашней сети (телефон в Wi-Fi). Грубо: телефон засыпает и может не отвечать.
  * Блокировка — только если ВСЕ цели точно «не рядом» дольше lock_after секунд. Ошибка опроса (нет bleak, Bluetooth выключен)
    — это «не знаю», а не «ушёл»: по ошибке компьютер не блокируется. Пока цель хотя бы раз не была увидена после включения, тоже не блокируем.
  * Уведомление на часы: часы Honor получают уведомления от iPhone (через приложение Honor/Huawei Health). Поэтому шлём push на iPhone
    через ntfy (приложение ntfy из App Store) или Telegram-бота; в Health нужно разрешить показ уведомлений этого приложения на часах.
  * Заряд: стандартная BLE-служба Battery (0x180F). Многие часы её не отдают, и пока они соединены с телефоном, второго соединения
    не принимают. Тогда в окне будет «нет данных» — это нормально.

Модуль не создаёт окон. Логика (Tracker, clean_settings, разбор целей) не зависит от Windows и проверяется тестами;
Bluetooth и блокировка работают только на Windows."""
from __future__ import annotations
import asyncio
import base64
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0

KINDS = ("ble", "paired", "ping")
NOTIFY_KINDS = ("none", "ntfy", "telegram")
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
APPLE_ID = 0x004C                      # производитель в рекламе: iPhone, iPad, Mac, AirPods…
MAX_TARGETS = 4
POLL_EVERY = 5.0                       # пауза между опросами, секунд
SCAN_SECONDS = 4.0                     # сколько слушаем эфир за один опрос
BATTERY_EVERY = 900.0                  # заряд читаем не чаще раза в 15 минут: лишние подключения мешают связи часов с телефоном
BATTERY_FAILS_MAX = 3                  # после трёх неудач подряд перестаём пробовать (часы заряд не отдают)
STARTUP_GRACE = 20.0                   # после запуска или включения первые секунды ничего не блокируем

DEFAULTS = {
    "enabled": False,
    "targets": [],                     # [{"kind": "ble"|"paired"|"ping", "id": "адрес / id устройства / IP", "name": "как назвать"}]
    "lock": True,                      # блокировать компьютер (False — только уведомлять)
    "lock_after": 30,                  # секунд без связи до блокировки (10–600)
    "rssi_min": 0,                     # слабее этого сигнала (дБм) считаем «ушёл»; 0 — не учитывать (иначе от −100 до −30)
    "battery": True,                   # пробовать читать заряд часов
    "low_battery": 15,                 # предупреждать при таком заряде и ниже, % (0 — не предупреждать)
    "notify": {"kind": "none", "server": "https://ntfy.sh", "topic": "", "token": "", "chat_id": "",
               "on_lost": True, "on_back": True},
}


# ---------------------------------------------------------------- настройки
def _norm_addr(s: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", str(s or "")).upper() if re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:\-]?){5}[0-9A-Fa-f]{2}", str(s or "").strip()) else ""


def _fmt_addr(s: str) -> str:
    h = _norm_addr(s)
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if h else ""


def clean_target(t) -> dict | None:
    """Проверка одной цели. None — мусор, выбрасываем."""
    if not isinstance(t, dict) or t.get("kind") not in KINDS:
        return None
    kind = t["kind"]
    raw = str(t.get("id") or "").strip()
    name = " ".join(str(t.get("name") or "").split())[:60]
    if kind == "ble":
        ident = _fmt_addr(raw) or " ".join(raw.split())[:60]          # адрес или кусок имени («honor», «watch es»)
    elif kind == "ping":
        ident = raw if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,62}", raw) else ""          # без пробелов и «-» в начале: это не должно стать ключом команды
    else:
        ident = raw[:300] if raw and "\n" not in raw else ""
    return {"kind": kind, "id": ident, "name": name} if ident else None


def new_topic() -> str:
    """Длинная случайная тема ntfy: на ntfy.sh темы публичные, и кто угадает название, увидит чужие уведомления."""
    return "karina-" + secrets.token_urlsafe(14).replace("_", "x").replace("-", "y")


def clean_notify(patch: dict | None, old: dict | None = None) -> dict:
    out = dict(DEFAULTS["notify"])
    out.update({k: v for k, v in (old or {}).items() if k in out})
    p = patch or {}
    if p.get("kind") in NOTIFY_KINDS:
        out["kind"] = p["kind"]
    if "server" in p:
        s = str(p["server"] or "").strip().rstrip("/")
        out["server"] = s if re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-/]*)?", s) else DEFAULTS["notify"]["server"]
    if "topic" in p:
        t = str(p["topic"] or "").strip()
        out["topic"] = t if re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", t) else ""
    if p.get("token"):                                  # пустое поле = «оставить как было» (как ключ API в настройках)
        t = str(p["token"]).strip()
        out["token"] = t if re.fullmatch(r"\d{3,15}:[A-Za-z0-9_\-]{20,80}", t) else out.get("token", "")
    if "chat_id" in p:
        c = str(p["chat_id"] or "").strip()
        out["chat_id"] = c if re.fullmatch(r"-?\d{3,20}", c) else ""
    for k in ("on_lost", "on_back"):
        if k in p:
            out[k] = bool(p[k])
    return out


def clean_settings(patch: dict | None, old: dict | None = None) -> dict:
    """Проверяем значения: в config.json не должно попасть мусора."""
    out = {**DEFAULTS, **{k: v for k, v in (old or {}).items() if k in DEFAULTS}}
    out["notify"] = clean_notify(None, (old or {}).get("notify"))
    p = patch or {}
    for k in ("enabled", "lock", "battery"):
        if k in p:
            out[k] = bool(p[k])
    for k, lo, hi in (("lock_after", 10, 600), ("low_battery", 0, 50)):
        if k in p:
            try:
                out[k] = max(lo, min(hi, int(p[k])))
            except (TypeError, ValueError):
                pass
    if "rssi_min" in p:
        try:
            v = int(p["rssi_min"])
            out["rssi_min"] = 0 if v == 0 else max(-100, min(-30, v))
        except (TypeError, ValueError):
            pass
    if "targets" in p and isinstance(p["targets"], list):
        seen, ts = set(), []
        for t in p["targets"]:
            c = clean_target(t)
            if c and (c["kind"], c["id"].lower()) not in seen:
                seen.add((c["kind"], c["id"].lower()))
                ts.append(c)
        out["targets"] = ts[:MAX_TARGETS]
    if isinstance(p.get("notify"), dict):
        out["notify"] = clean_notify(p["notify"], out["notify"])
    out["targets"] = [c for c in (clean_target(t) for t in out.get("targets") or []) if c][:MAX_TARGETS]
    return out


def public_view(conf: dict) -> dict:
    """Настройки для окна: токен Telegram наружу не отдаём, только «задан ли он»."""
    c = clean_settings({}, conf)
    n = dict(c["notify"])
    n["has_token"] = bool(n.pop("token", ""))
    c["notify"] = n
    return c


# ---------------------------------------------------------------- логика «рядом / ушёл» (без Bluetooth)
class Tracker:
    """Решает, когда пора блокировать. feed(present, now) → список событий: "lost" (блокируем) и "back" (вернулись).
    present: True — рядом, False — точно нет, None — не знаю (ошибка опроса: ничего не меняем)."""

    def __init__(self, lock_after: float = 30, grace: float = 0, gap: float = 60):
        self.lock_after, self.grace, self.gap = float(lock_after), float(grace), float(gap)
        self.started: float | None = None
        self.last_feed: float | None = None
        self.absent_since: float | None = None
        self.armed = False           # цель хотя бы раз была рядом: без этого блокировать нельзя (иначе «часов нет» заперло бы компьютер сразу)
        self.fired = False           # «потеряна» уже сработало, ждём возвращения
        self.seen = False            # состояние «рядом» установлено

    def feed(self, present, now: float) -> list[str]:
        ev: list[str] = []
        if self.started is None:
            self.started = now
        if self.last_feed is not None and now - self.last_feed > self.gap and self.absent_since is not None and not self.fired:
            self.absent_since = now          # компьютер спал или опрос завис: время «отсутствия» за это окно не засчитываем
        self.last_feed = now
        if present is None:
            return ev
        if present:
            self.armed = True
            self.absent_since = None
            if self.fired:
                ev.append("back")
            self.fired = False
            self.seen = True
            return ev
        if self.absent_since is None:
            self.absent_since = now
        if self.armed and not self.fired and now - self.started >= self.grace and now - self.absent_since >= self.lock_after:
            self.fired = True
            self.seen = False
            ev.append("lost")
        return ev

    def state(self, now: float) -> tuple[str, float]:
        """("unknown" | "present" | "waiting" | "away", секунд до блокировки для waiting)."""
        if self.fired:
            return "away", 0.0
        if self.absent_since is not None:
            if not self.armed:
                return "unknown", 0.0
            return "waiting", max(0.0, self.lock_after - (now - self.absent_since))
        return ("present", 0.0) if self.seen else ("unknown", 0.0)


class Smoother:
    """Среднее по последним 3 замерам сигнала: один случайный провал не должен считаться «ушёл»."""

    def __init__(self, n: int = 3):
        self.n, self.v = n, []

    def add(self, x):
        if x is None:
            return None
        self.v = (self.v + [x])[-self.n:]
        return sum(self.v) / len(self.v)

    def reset(self):
        self.v = []


def match_ble(target: dict, devices: list[dict]) -> dict | None:
    """Находит в результатах сканирования устройство цели: по адресу, а если там имя — по куску имени."""
    ident = target.get("id") or ""
    addr = _norm_addr(ident)
    for d in devices:
        if addr:
            if _norm_addr(d.get("address")) == addr:
                return d
        elif ident and ident.lower() in str(d.get("name") or "").lower():
            return d
    return None


# ---------------------------------------------------------------- уведомление на телефон → часы
def send_push(conf: dict, title: str, text: str, timeout: float = 10.0) -> tuple[bool, str]:
    """ntfy или Telegram. (успех, текст ошибки). Сетевые сбои не бросают исключений."""
    n = clean_notify(conf)
    try:
        if n["kind"] == "ntfy":
            if not n["topic"]:
                return False, "Не указана тема ntfy"
            # заголовки HTTP — latin-1, поэтому кириллицу в заголовке отправляем в виде RFC 2047 (ntfy его понимает)
            subj = "=?UTF-8?B?" + base64.b64encode(title.encode("utf-8")).decode() + "?="
            req = urllib.request.Request(f"{n['server']}/{n['topic']}", data=text.encode("utf-8"), method="POST",
                                         headers={"Title": subj, "Priority": "high", "Tags": "lock"})
        elif n["kind"] == "telegram":
            if not n["token"] or not n["chat_id"]:
                return False, "Не указан токен бота или chat id"
            body = urllib.parse.urlencode({"chat_id": n["chat_id"], "text": f"{title}\n{text}"}).encode("utf-8")
            req = urllib.request.Request(f"https://api.telegram.org/bot{n['token']}/sendMessage", data=body, method="POST")
        else:
            return False, "Уведомления выключены"
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 300), "" if 200 <= r.status < 300 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


# ---------------------------------------------------------------- опрос устройств (только Windows + bleak/winrt)
class ProbeError(Exception):
    pass


async def scan_ble(seconds: float = SCAN_SECONDS) -> list[dict]:
    try:
        from bleak import BleakScanner
    except ImportError:
        raise ProbeError("Не установлен пакет bleak (pip install bleak)")
    try:
        found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    except Exception as e:
        raise ProbeError(f"Bluetooth недоступен: {e}")
    out = []
    for addr, (dev, adv) in found.items():
        out.append({"address": str(dev.address).upper(), "name": adv.local_name or dev.name or "", "rssi": adv.rssi,
                    "apple": APPLE_ID in (adv.manufacturer_data or {})})
    out.sort(key=lambda d: -(d["rssi"] if isinstance(d["rssi"], (int, float)) else -999))
    return out


async def list_paired() -> list[dict]:
    """Сопряжённые с Windows устройства: [{"id","name","kind":"classic"|"le","connected": bool|None}]."""
    try:
        from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice
        from winrt.windows.devices.enumeration import DeviceInformation
    except ImportError:
        raise ProbeError("Не установлен пакет winrt-Windows.Devices.Bluetooth")
    out = []
    for cls, kind in ((BluetoothDevice, "classic"), (BluetoothLEDevice, "le")):
        try:
            infos = await DeviceInformation.find_all_async_aqs_filter(cls.get_device_selector_from_pairing_state(True))
        except Exception as e:
            raise ProbeError(f"Список сопряжённых устройств недоступен: {e}")
        for i in infos:
            conn = None
            try:
                d = await cls.from_id_async(i.id)
                if d is not None:
                    conn = int(d.connection_status) == 1          # BluetoothConnectionStatus.Connected = 1
                    d.close()
            except Exception:
                pass
            out.append({"id": i.id, "name": i.name or "", "kind": kind, "connected": conn})
    return out


async def paired_connected(dev_id: str):
    """True/False — связь есть/нет; None — не удалось узнать."""
    try:
        from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice
    except ImportError:
        raise ProbeError("Не установлен пакет winrt-Windows.Devices.Bluetooth")
    for cls in (BluetoothDevice, BluetoothLEDevice):
        try:
            d = await cls.from_id_async(dev_id)
        except Exception:
            continue
        if d is not None:
            try:
                return int(d.connection_status) == 1
            finally:
                try:
                    d.close()
                except Exception:
                    pass
    return None


def ping_ok(host: str) -> bool | None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,62}", host or ""):
        return None
    cmd = ["ping", "-n", "1", "-w", "1500", host] if IS_WIN else ["ping", "-c", "1", "-W", "2", host]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=6, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


async def read_battery(address: str, timeout: float = 8.0) -> int | None:
    try:
        from bleak import BleakClient
    except ImportError:
        return None
    async with BleakClient(address, timeout=timeout) as c:
        v = await c.read_gatt_char(BATTERY_CHAR)
        pct = int(v[0]) if v else None
        return pct if pct is not None and 0 <= pct <= 100 else None


# ---------------------------------------------------------------- сам сервис
class WatchLock:
    """Фоновый поток: раз в POLL_EVERY секунд опрашивает цели, ведёт Tracker, блокирует и уведомляет.
    emit(ev) получает события {"type": "watch", ...} для окна и острова. lock_fn и push_fn подменяются в тестах."""

    def __init__(self, cfg, emit=lambda ev: None, log=lambda m: None, lock_fn=None, push_fn=None, clock=time.monotonic):
        self.cfg, self.emit, self.log = cfg, emit, log
        self.cabinet = None                 # CabinetClient: если Карина подключена к кабинету, события идут туда, а ntfy/Telegram молчат
        self.clock = clock
        if lock_fn is None:
            from . import winutil
            lock_fn = winutil.lock
        self.lock_fn, self.push_fn = lock_fn, push_fn or send_push
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mu = threading.RLock()
        self._reset()

    def _reset(self):
        c = self.conf()
        self.tracker = Tracker(c["lock_after"], STARTUP_GRACE)
        self.rssi = Smoother()
        self.battery: int | None = None
        self.battery_at = 0.0
        self.battery_fails = 0
        self.low_sent = False
        self.error = ""
        self.readings: list[dict] = []
        self.last_event = ""
        self._last_pub = None

    # ---- настройки
    def conf(self) -> dict:
        return clean_settings({}, self.cfg.get("watch") if hasattr(self.cfg, "get") else {})

    def set_conf(self, patch: dict) -> dict:
        new = clean_settings(patch, self.conf())
        self.cfg.update({"watch": new})
        self.apply()
        return public_view(new)

    # ---- запуск
    def start(self):
        self.apply()

    def apply(self):
        """Включает или выключает слежение по настройкам; при изменении настроек начинает заново."""
        with self._mu:
            self._halt()
            self._reset()
            c = self.conf()
            if not c["enabled"] or not c["targets"]:
                self._publish(force=True)
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True, name="karina-watchlock")
            self._thread.start()
            self.log(f"Часы: слежение включено ({len(c['targets'])} цел., блокировка через {c['lock_after']} с)")

    def _halt(self):
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=SCAN_SECONDS + 3)

    def stop(self):
        with self._mu:
            self._halt()

    # ---- опрос
    def _run(self, stop: threading.Event):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:            # слежение не должно падать целиком
                self.error = f"{e.__class__.__name__}: {e}"
                self.log(f"Часы: ошибка опроса: {self.error}")
            if stop.wait(POLL_EVERY):
                break

    def tick(self, now: float | None = None):
        """Один опрос: замеры → Tracker → действия. now можно подать в тесте."""
        c = self.conf()
        now = self.clock() if now is None else now
        self.error = ""
        present, self.readings = self._poll(c)
        for ev in self.tracker.feed(present, now):
            self._on_event(ev, c)
        self._maybe_battery(c, now)
        self._publish()

    def _poll(self, c: dict) -> tuple[bool | None, list[dict]]:
        """(рядом?, замеры по целям). Рядом — если хоть одна цель рядом; точно нет — если все цели точно нет; иначе None."""
        ble_t = [t for t in c["targets"] if t["kind"] == "ble"]
        devices: list[dict] | None = None
        err = ""
        if ble_t:
            try:
                devices = asyncio.run(scan_ble())
            except ProbeError as e:
                err = str(e)
            except Exception as e:
                err = f"{e.__class__.__name__}: {e}"
        out = []
        for t in c["targets"]:
            r = {"kind": t["kind"], "id": t["id"], "name": t["name"], "present": None, "rssi": None}
            if t["kind"] == "ble":
                if devices is None:
                    r["error"] = err
                else:
                    d = match_ble(t, devices)
                    avg = self.rssi.add(d["rssi"]) if d else None
                    if not d:
                        r["present"] = False
                    else:
                        r["rssi"] = round(avg) if avg is not None else None
                        r["present"] = not (c["rssi_min"] and avg is not None and avg < c["rssi_min"])
                        r["address"] = d["address"]
                        if not r["name"]:
                            r["name"] = d.get("name") or ""
            elif t["kind"] == "paired":
                try:
                    r["present"] = asyncio.run(paired_connected(t["id"]))
                except ProbeError as e:
                    r["error"] = str(e)
                except Exception as e:
                    r["error"] = f"{e.__class__.__name__}: {e}"
            else:
                r["present"] = ping_ok(t["id"])
            out.append(r)
        states = [r["present"] for r in out]
        if any(s is True for s in states):
            return True, out
        if states and all(s is False for s in states):
            return False, out
        self.error = next((r.get("error") for r in out if r.get("error")), "") or err
        return None, out

    # ---- события
    def _on_event(self, ev: str, c: dict):
        n = c["notify"]
        if ev == "lost":
            self.last_event = "Связь потеряна — компьютер заблокирован" if c["lock"] else "Связь потеряна"
            self.log(f"Часы: {self.last_event}")
            if c["lock"]:
                try:
                    self.lock_fn()
                except Exception as e:
                    self.log(f"Часы: не удалось заблокировать: {e}")
            self._toast(("🔒 " if c["lock"] else "⌚ ") + self.last_event)
            if self._cab():
                self.cabinet.send("lost", force=True, locked=bool(c["lock"]))
            elif n["kind"] != "none" and n["on_lost"]:
                self._push("Компьютер заблокирован" if c["lock"] else "Связь с компьютером потеряна",
                           "Часы вышли из зоны связи: " + time.strftime("%H:%M:%S"))
        elif ev == "back":
            self.last_event = "Часы снова рядом"
            self.log(f"Часы: {self.last_event}")
            self._toast("⌚ " + self.last_event)
            if self._cab():
                self.cabinet.send("back", force=True)
            elif n["kind"] != "none" and n["on_back"]:
                self._push("Часы снова рядом", "Компьютер ждёт: разблокируйте его.")

    def _cab(self) -> bool:
        return bool(self.cabinet is not None and self.cabinet.linked)

    def cabinet_info(self):
        """Состояние часов для пульса в кабинет (None, если слежение выключено)."""
        c = self.conf()
        if not c["enabled"] or not c["targets"]:
            return None
        st = self.status()
        state = {"present": "near", "waiting": "waiting", "away": "away"}.get(st["state"], "unknown")
        return {"state": state, "rssi": st["rssi"], "battery": st["battery"], "name": c["targets"][0].get("name") or ""}

    def _push(self, title: str, text: str):
        c = self.conf()
        def work():
            ok, err = self.push_fn(c["notify"], title, text)
            if not ok:
                self.log(f"Часы: уведомление не ушло: {err}")
        threading.Thread(target=work, daemon=True, name="karina-watch-push").start()

    def _toast(self, text: str):
        self.emit({"type": "watch", "toast": text, **self.status()})

    def test_push(self) -> dict:
        if self._cab():
            return {"ok": False, "error": "Карина подключена к кабинету: проверка уведомлений там, в разделе «Часы» (кнопка «Проверить»)."}
        ok, err = self.push_fn(self.conf()["notify"], "Карина", "Проверка: это уведомление должно появиться на часах.")
        return {"ok": ok, "error": err}

    # ---- заряд
    def _maybe_battery(self, c: dict, now: float):
        if not c["battery"] or self.battery_fails >= BATTERY_FAILS_MAX:
            return
        ble = [r for r in self.readings if r["kind"] == "ble" and r["present"]]
        if not ble or (self.battery_at and now - self.battery_at < BATTERY_EVERY):
            return
        addr = next((t["id"] for t in c["targets"] if t["kind"] == "ble" and _norm_addr(t["id"])), "")
        if not addr:                                    # цель задана именем: адрес берём из последнего замера сканера
            addr = next((r.get("address") for r in ble if r.get("address")), "")
        if not addr:
            return
        self.battery_at = now
        try:
            pct = asyncio.run(read_battery(addr))
        except Exception:
            pct = None
        if pct is None:
            self.battery_fails += 1
            return
        self.battery_fails = 0
        self.battery = pct
        low = c["low_battery"]
        if low and pct <= low and not self.low_sent:
            self.low_sent = True
            self._toast(f"⌚ Заряд часов {pct}%")
            if self._cab():
                self.cabinet.send("low", force=True, pct=pct)
            elif c["notify"]["kind"] != "none":
                self._push("Часы разряжаются", f"Заряд {pct}%")
        elif low and pct > low + 5:
            self.low_sent = False

    def refresh_battery(self) -> dict:
        """Кнопка «Прочитать заряд»: пробуем ещё раз сразу."""
        self.battery_fails, self.battery_at = 0, 0.0
        self._maybe_battery(self.conf(), self.clock())
        self._publish(force=True)
        return {"battery": self.battery}

    # ---- состояние для окна
    def status(self) -> dict:
        c = self.conf()
        state, left = self.tracker.state(self.clock())
        if not c["enabled"] or not c["targets"]:
            state = "off"
        rssi = next((r["rssi"] for r in self.readings if r.get("rssi") is not None), None)
        return {"enabled": c["enabled"], "state": state, "left": round(left), "rssi": rssi, "battery": self.battery,
                "readings": self.readings, "error": self.error, "last_event": self.last_event,
                "lock_after": c["lock_after"], "locking": c["lock"]}

    def _publish(self, force: bool = False):
        s = self.status()
        key = (s["state"], s["rssi"], s["battery"], s["error"], tuple((r["id"], r["present"]) for r in s["readings"]), s["last_event"])
        if force or key != self._last_pub:
            self._last_pub = key
            self.emit({"type": "watch", **s})

    # ---- поиск устройств для вкладки (долгая операция: вызывать не из потока интерфейса)
    def scan(self) -> dict:
        out = {"ble": [], "paired": [], "errors": []}
        for key, fn in (("ble", scan_ble), ("paired", list_paired)):
            try:
                out[key] = asyncio.run(fn())
            except ProbeError as e:
                out["errors"].append(str(e))
            except Exception as e:
                out["errors"].append(f"{e.__class__.__name__}: {e}")
        return out
