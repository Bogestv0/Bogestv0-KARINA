"""Управление Bogestv0 MIXER голосом. Говорим с «Удалённым пультом» микшера по WebSocket (/ws): тот же протокол, что у страницы на телефоне.

Что нужно один раз: в микшере Настройки → «Удалённый пульт» → включить. Порт и PIN Карина берёт из файла настроек микшера сама
(можно вписать вручную в config.json: "mixer": {"port": 8080, "pin": "1234"}).

Протокол (см. src-tauri/src/remote.rs микшера):
  мы → микшер: {"t":"auth","pin":"…"} · {"t":"gain","i":2,"v":-6.0} (дБ, −48…+12) · {"t":"mute","i":2,"v":1} · {"t":"scene","id":"play"}
  микшер → мы: {"t":"auth","ok":true} · {"t":"meta","strips":[{"name","tag","hidden"}],"scenes":[{"id","name"}]} · {"t":"state","strips":[{"g","m","s","r"}]} · {"t":"err","msg"}
"""
from __future__ import annotations
import json, math, os, threading, time
from pathlib import Path

DB_MIN, DB_MAX = -48.0, 12.0
SETTINGS_FILE = "bogestv0-settings.json"
APP_ID = "com.bogestv0.mixer"


class MixerError(Exception):
    """Сообщение можно сразу произнести голосом."""


def clamp_db(db: float) -> float:
    return max(DB_MIN, min(DB_MAX, float(db)))


def pct_to_db(pct: float) -> float:
    """Громкость «в процентах» → дБ: 100 % = 0 дБ, 50 % = −6 дБ, 10 % = −20 дБ, 0 % = минимум фейдера."""
    if pct <= 0:
        return DB_MIN
    return clamp_db(20.0 * math.log10(pct / 100.0))


def db_to_pct(db: float) -> int:
    return max(0, round(100.0 * 10 ** (db / 20.0)))


def mixer_settings_paths() -> list[Path]:
    """Где микшер хранит настройки (Tauri: app_config_dir = %APPDATA%\\com.bogestv0.mixer)."""
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(env):
            out.append(Path(os.environ[env]) / APP_ID / SETTINGS_FILE)
    home = Path.home()
    out += [home / ".config" / APP_ID / SETTINGS_FILE, home / "Library" / "Application Support" / APP_ID / SETTINGS_FILE]
    return out


def read_mixer_settings(paths: list[Path] | None = None) -> dict:
    """Настройки микшера (или {}, если файла нет). Нужны для порта, PIN и имён дорожек."""
    for p in paths if paths is not None else mixer_settings_paths():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


class MixerRemote:
    def __init__(self, cfg, settings_paths: list[Path] | None = None):
        self.cfg = cfg
        self._paths = settings_paths
        self._lock = threading.RLock()
        self._ws = None
        self._cm = None            # контекстный менеджер соединения (так клиент websockets работает без предупреждений в любой версии)
        self._reader: threading.Thread | None = None
        self.meta: dict | None = None
        self.state: dict | None = None
        self._authed = threading.Event()
        self._ready = threading.Event()
        self._error = ""
        self._pin_sent = False
        self._need_pin = False

    # ---------- адрес и PIN ----------
    def target(self) -> tuple[str, int, str]:
        c = self.cfg.get("mixer") or {}
        app = (read_mixer_settings(self._paths) or {}).get("app") or {}
        port = int(c.get("port") or 0) or int(app.get("remotePort") or 0) or 8080
        pin = str(c.get("pin") or "").strip() or "".join(ch for ch in str(app.get("remotePin") or "") if ch.isdigit())
        return str(c.get("host") or "127.0.0.1"), port, pin

    def remote_enabled_in_mixer(self) -> bool | None:
        """True / False, если файл настроек микшера найден; None — не знаем."""
        s = read_mixer_settings(self._paths)
        if not s:
            return None
        return bool((s.get("app") or {}).get("remoteOn"))

    # ---------- соединение ----------
    def close(self):
        with self._lock:
            ws, self._ws = self._ws, None
            cm, self._cm = self._cm, None
            self.meta = self.state = None
            self._authed.clear()
            self._ready.clear()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass

    def _alive(self) -> bool:
        return self._ws is not None and self._reader is not None and self._reader.is_alive() and self._ready.is_set()

    def _send(self, obj: dict):
        ws = self._ws
        if ws is None:
            raise MixerError("Связь с микшером потеряна.")
        ws.send(json.dumps(obj))

    def _read_loop(self, ws):
        try:
            for raw in ws:
                if not isinstance(raw, str):
                    continue            # двоичные кадры — звук для телефона, нам не нужен
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                self._on_msg(ws, m)
        except Exception:
            pass
        finally:
            with self._lock:
                if self._ws is ws:
                    self._ws = None
                    self.meta = self.state = None
                    self._authed.clear()
                    self._ready.clear()

    def _on_msg(self, ws, m: dict):
        t = m.get("t")
        if t == "auth":
            if m.get("ok"):
                self._authed.set()
            else:
                self._need_pin = True
                _host, _port, pin = self.target()
                if not self._pin_sent and pin:
                    self._pin_sent = True
                    try:
                        ws.send(json.dumps({"t": "auth", "pin": pin}))
                    except Exception:
                        pass
                else:
                    self._error = m.get("msg") or "Неверный PIN"
                    self._ready.set()       # будим ожидающего: он увидит ошибку
        elif t == "meta":
            self.meta = m
        elif t == "state":
            self.state = m
        elif t == "err":
            self._error = str(m.get("msg") or "")
        if self.meta is not None and self.state is not None:
            self._ready.set()

    def connect(self, timeout: float = 4.0):
        from websockets.sync.client import connect as ws_connect
        with self._lock:
            if self._alive():
                return
            self.close()
            self._error, self._pin_sent, self._need_pin = "", False, False
            host, port, _pin = self.target()
            try:
                cm = ws_connect(f"ws://{host}:{port}/ws", open_timeout=timeout, max_size=2 ** 20)
                ws = cm.__enter__()
            except Exception:
                hint = ""
                if self.remote_enabled_in_mixer() is False:
                    hint = " В настройках микшера выключен «Удалённый пульт»: включите его."
                else:
                    hint = " Откройте микшер и включите «Удалённый пульт» в его настройках."
                raise MixerError("Не получается связаться с микшером." + hint)
            self._ws, self._cm = ws, cm
            self._reader = threading.Thread(target=self._read_loop, args=(ws,), daemon=True, name="mixer-remote")
            self._reader.start()
            if not self._ready.wait(timeout):
                self.close()
                raise MixerError("Микшер не ответил. Проверьте, что он запущен.")
            if self._error and not self._authed.is_set():
                err = self._error
                self.close()
                if "много" in err.lower():
                    raise MixerError("Микшер временно закрыл вход: слишком много попыток. Подождите полминуты.")
                if self._need_pin and not self.target()[2]:
                    raise MixerError("Микшеру нужен PIN. Впишите его в настройках Карины, раздел «Микшер».")
                raise MixerError("Микшер не принял PIN. Проверьте его в настройках Карины, раздел «Микшер».")

    # ---------- данные ----------
    def strips(self) -> list[dict]:
        """[{i, name, tag, hidden, gain (дБ), mute}] по данным микшера."""
        self.connect()
        meta, st = self.meta or {}, self.state or {}
        cur_all = st.get("strips") or []
        out = []
        for i, s in enumerate(meta.get("strips") or []):
            cur = cur_all[i] if i < len(cur_all) else {}
            out.append({"i": i, "name": s.get("name") or f"Дорожка {i + 1}", "tag": s.get("tag") or "", "hidden": bool(s.get("hidden")),
                        "gain": float(cur.get("g", 0.0)), "mute": bool(cur.get("m", 0))})
        return out

    def scenes(self) -> list[dict]:
        self.connect()
        return [{"id": s["id"], "name": s["name"]} for s in (self.meta or {}).get("scenes") or []]

    def active_scene(self):
        return (self.meta or {}).get("active")

    # ---------- команды ----------
    def set_gain(self, i: int, db: float) -> float:
        self.connect()
        db = round(clamp_db(db), 1)
        self._send({"t": "gain", "i": int(i), "v": db})
        self._patch(i, g=db)
        return db

    def set_mute(self, i: int, on: bool):
        self.connect()
        self._send({"t": "mute", "i": int(i), "v": 1 if on else 0})
        self._patch(i, m=1 if on else 0)

    def set_scene(self, scene_id: str):
        self.connect()
        self._error = ""
        self._send({"t": "scene", "id": scene_id})
        time.sleep(0.25)
        if self._error:
            raise MixerError(self._error)

    def _patch(self, i: int, **kw):
        try:
            self.state["strips"][i].update(kw)      # не ждём следующего state: «ещё тише» подряд считается от нового значения
        except Exception:
            pass
