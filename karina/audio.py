"""Захват микрофонов (до 4 одновременно) и выделение фраз (простой адаптивный детектор речи, без внешних моделей).

Каждый включённый микрофон слушает сам по себе. Одну и ту же фразу часто слышат сразу несколько микрофонов:
такие записи собирает Fuser в одну «группу», а ассистент выбирает из неё лучший вариант (см. Assistant._recognize).
"""
from __future__ import annotations
import collections, threading, time
from dataclasses import dataclass, field
import numpy as np

SR = 16000
FRAME = 480  # 30 мс
MAX_MICS = 4


# ---------------- устройства ----------------
def list_devices(kind: str = "input") -> list[dict]:
    """Устройства ввода/вывода: {"id", "name", "api", "label"}. label отличается от name, только если имя повторяется
    (в Windows одно и то же устройство видно через несколько звуковых интерфейсов — MME, WASAPI…)."""
    import sounddevice as sd
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d[key] > 0:
            try:
                api = apis[d["hostapi"]]["name"]
            except Exception:
                api = ""
            out.append({"id": i, "name": d["name"], "api": api})
    count = collections.Counter(x["name"] for x in out)
    for x in out:
        x["label"] = x["name"] if count[x["name"]] == 1 or not x["api"] else f'{x["name"]} · {x["api"]}'
    return out


def resolve_device(dev_id, name: str, devices: list[dict]):
    """Сохранённое устройство → (номер для sounddevice, найдено ли).
    Номера устройств меняются при подключении/отключении, поэтому сверяем ещё и имя.
    Ничего не сохранено → (None, True): «устройство по умолчанию»."""
    if dev_id is None and not name:
        return None, True
    by_id = {d["id"]: d for d in devices}
    if dev_id in by_id and (not name or by_id[dev_id]["name"] == name):
        return dev_id, True
    if name:
        for d in devices:
            if d["name"] == name:
                return d["id"], True
    return None, False


# ---------------- настройки микрофонов ----------------
def _slot(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    dev = raw.get("device")
    try:
        dev = None if dev in (None, "") else int(dev)
    except (TypeError, ValueError):
        dev = None
    return {"enabled": bool(raw.get("enabled")), "device": dev, "name": str(raw.get("name") or "")}


def mic_slots(cfg) -> list[dict]:
    """Ровно MAX_MICS слотов {"enabled","device","name"}. Старые настройки (один микрофон в stt.mic_device)
    превращаются в слот 1. Слот 1 с device=None — системный микрофон по умолчанию."""
    st = cfg["stt"]
    raw = st.get("mics")
    if not raw:
        raw = [{"enabled": True, "device": st.get("mic_device")}]
    slots = [_slot(r) for r in list(raw)[:MAX_MICS]]
    while len(slots) < MAX_MICS:
        slots.append(_slot(None))
    return slots


def sanitize_mics(raw, devices: list[dict]) -> list[dict]:
    """Проверка слотов, пришедших из окна. Имя устройства берём из нашего списка, а не от окна."""
    names = {d["id"]: d["name"] for d in devices}
    slots = [_slot(r) for r in (list(raw) if isinstance(raw, (list, tuple)) else [])[:MAX_MICS]]
    while len(slots) < MAX_MICS:
        slots.append(_slot(None))
    for s in slots:
        s["name"] = names.get(s["device"], "") if s["device"] is not None else ""
    return slots


# ---------------- фраза и её качество ----------------
@dataclass
class Utterance:
    slot: int
    audio: np.ndarray
    t0: float            # когда началась речь (time.monotonic)
    t1: float            # когда закончилась
    snr: float = 1.0     # громкость речи / уровень шума на этом микрофоне
    peak: float = 0.0    # самый громкий отсчёт (≈1.0 — микрофон перегружен)

    @property
    def quality(self) -> float:
        """Чем выше, тем чище запись: отношение речи к шуму, с наказанием за перегрузку."""
        q = min(self.snr, 60.0)
        return q * 0.5 if self.peak >= 0.98 else q


class Fuser:
    """Склеивает записи одной и той же фразы с разных микрофонов в одну группу и отбрасывает повторы,
    чтобы команда не выполнилась дважды. Логика чистая (время передаётся снаружи) — проверяется тестами."""
    WAIT = 0.5          # ждём остальные микрофоны столько секунд после последней записи группы
    MAX_WAIT = 2.0      # но не дольше этого от первой
    PAD = 0.25          # запас на разную задержку устройств
    SAME = 0.3          # доля пересечения, начиная с которой две записи — одна фраза
    KEEP = 20.0         # сколько помним уже выданные группы (для отсева запоздалых повторов)

    def __init__(self, emit, clock=time.monotonic):
        self.emit, self.clock = emit, clock
        self._pending: list[dict] = []
        self._done: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        self._closed = False

    @staticmethod
    def _same(a0, a1, b0, b1, pad) -> bool:
        ov = min(a1 + pad, b1) - max(a0 - pad, b0)
        return ov > 0 and ov >= Fuser.SAME * min(a1 - a0, b1 - b0)

    def add(self, u: Utterance, now: float | None = None) -> bool:
        """True — запись принята в группу, False — это повтор уже обработанной фразы (отброшена)."""
        now = self.clock() if now is None else now
        with self._lock:
            while self._done and now - self._done[0][2] > self.KEEP:
                self._done.popleft()
            for d0, d1, _ in self._done:
                if self._same(u.t0, u.t1, d0, d1, self.PAD):
                    return False
            for g in self._pending:
                if self._same(u.t0, u.t1, g["t0"], g["t1"], self.PAD):
                    du, dg = u.t1 - u.t0, g["t1"] - g["t0"]
                    if max(du, dg) > 3 * min(du, dg) + 1.5:
                        return False      # «склеенный» длинный кусок с шумного микрофона: фраза уже есть в группе
                    g["members"].append(u)
                    g["t0"], g["t1"], g["last"] = min(g["t0"], u.t0), max(g["t1"], u.t1), now
                    return True
            self._pending.append({"members": [u], "t0": u.t0, "t1": u.t1, "first": now, "last": now})
        self._wake.set()
        return True

    def ready(self, now: float | None = None, force: bool = False) -> list[list[Utterance]]:
        """Группы, дождавшиеся остальных микрофонов (лучший вариант первым)."""
        now = self.clock() if now is None else now
        out = []
        with self._lock:
            keep = []
            for g in self._pending:
                if force or now - g["last"] >= self.WAIT or now - g["first"] >= self.MAX_WAIT:
                    self._done.append((g["t0"], g["t1"], now))
                    out.append(sorted(g["members"], key=lambda m: m.quality, reverse=True))
                else:
                    keep.append(g)
            self._pending = keep
        return out

    # --- фоновый поток ---
    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True, name="karina-mic-fuser")
            self._thread.start()

    def _loop(self):
        while True:
            self._wake.wait(0.1)
            self._wake.clear()
            if self._closed:
                return
            for grp in self.ready():
                try:
                    self.emit(grp)
                except Exception:
                    pass

    def reset(self):
        """Забыть всё накопленное (микрофоны переоткрываются)."""
        with self._lock:
            self._pending.clear()
            self._done.clear()

    def close(self):
        self._closed = True
        self._wake.set()


# ---------------- один микрофон ----------------
class _Channel:
    """Один открытый микрофон со своим детектором речи и своим уровнем шума."""

    def __init__(self, owner: "Listener", slot: int, device):
        self.owner, self.slot, self.device = owner, slot, device
        self.stream = None
        self.nf = 0.004                 # уровень фонового шума именно этого микрофона
        self.last_cb = time.monotonic()
        self._lvl_ts = 0.0
        self._reset()

    def _reset(self):
        self.pre = collections.deque(maxlen=10)
        self.buf: list = []
        self.speaking = False
        self.run = 0
        self.silence = 0
        self.sp_sq = 0.0
        self.sp_n = 0
        self.peak = 0.0
        self.t0 = 0.0

    def start(self):
        import sounddevice as sd
        self.stop()
        self.last_cb = time.monotonic()
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME,
                                     device=self.device, callback=self._cb)
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self._reset()

    def _cb(self, indata, frames, t, status):
        now = time.monotonic()
        self.last_cb = now
        x = indata[:, 0].copy()
        if self.owner.muted:
            self._reset()
            return
        rms = float(np.sqrt(np.mean(x * x)))
        if now - self._lvl_ts > 0.06:
            self._lvl_ts = now
            self.owner.on_level(self.slot, min(1.0, rms * 10))
        base = float(self.owner.cfg["stt"].get("vad_threshold", 0.012))
        voiced = rms > max(self.nf * 3.0, base)
        if not voiced:
            self.nf = min(0.05, 0.97 * self.nf + 0.03 * rms)
        if not self.speaking:
            self.pre.append(x)
            self.run = self.run + 1 if voiced else 0
            if self.run >= 3:
                self.speaking, self.silence = True, 0
                self.buf = list(self.pre)
                self.t0 = now - len(self.buf) * FRAME / SR
                self.sp_sq = self.sp_n = 0
                self.peak = 0.0
                for f in self.buf:
                    self.peak = max(self.peak, float(np.max(np.abs(f))))
            return
        self.buf.append(x)
        self.peak = max(self.peak, float(np.max(np.abs(x))))
        if voiced:
            self.sp_sq += rms * rms
            self.sp_n += 1
        self.silence = 0 if voiced else self.silence + 1
        n = len(self.buf)
        if self.silence >= 27 or n >= 500:   # 0.8 c тишины или 15 c речи
            audio = np.concatenate(self.buf)
            speech = (self.sp_sq / self.sp_n) ** 0.5 if self.sp_n else 0.0
            u = Utterance(self.slot, audio, self.t0, now, snr=speech / max(self.nf, 1e-4), peak=self.peak)
            self._reset()
            if len(audio) / SR >= 0.45:
                self.owner._utterance(u)


# ---------------- все микрофоны ----------------
class Listener:
    """До MAX_MICS микрофонов одновременно. on_utterance(список записей одной фразы, лучшая первой);
    on_level(номер_микрофона, уровень); on_status(список состояний микрофонов)."""

    def __init__(self, cfg, on_utterance, on_level, on_status=None):
        self.cfg, self.on_utterance, self.on_level = cfg, on_utterance, on_level
        self.on_status = on_status or (lambda st: None)
        self.muted = False       # пока говорит Карина или слушание выключено
        self.channels: dict[int, _Channel] = {}
        self.statuses: list[dict] = []
        self._fuser = Fuser(self._emit_group)
        self._lock = threading.RLock()
        self._wd_stop = threading.Event()

    @staticmethod
    def devices() -> list[dict]:
        return list_devices("input")

    # --- запуск / остановка ---
    def start(self) -> list[dict]:
        """(Пере)открывает все включённые микрофоны. Если один не открылся — остальные всё равно работают.
        Ошибка — только когда не открылся ни один. Возвращает состояния слотов."""
        self.stop()
        with self._lock:
            devices = []
            try:
                devices = self.devices()
            except Exception:
                pass
            slots = mic_slots(self.cfg)
            statuses, used = [], set()
            for i, s in enumerate(slots):
                statuses.append(self._open_slot(i, s, devices, used))
            self._publish(statuses)
            enabled = [st for st in statuses if st["state"] != "off"]
            if enabled:      # следим и тогда, когда ничего не открылось: подключат микрофон — Карина подхватит
                self._wd_stop = threading.Event()
                threading.Thread(target=self._watch, args=(self._wd_stop,), daemon=True, name="karina-mic-watch").start()
            if enabled and not self.channels:
                raise RuntimeError(enabled[0]["text"] or "микрофон не открылся")
            return statuses

    def _open_slot(self, i: int, s: dict, devices: list[dict], used: set) -> dict:
        st = {"slot": i, "state": "off", "text": "", "device": None}
        if not s["enabled"]:
            return st
        dev, found = resolve_device(s["device"], s["name"], devices)
        if i > 0 and dev is None and found:
            st.update(state="unset", text="Не выбран микрофон")
            return st
        if not found:
            st.update(state="missing", text=f"Не найден: {s['name'] or 'устройство'}. Подключите его — Карина подхватит сама")
            return st
        key = dev if dev is not None else "default"
        if dev is None:        # «по умолчанию» — узнаем, какое это устройство, чтобы не открыть один микрофон дважды
            try:
                import sounddevice as sd
                idx = sd.query_devices(kind="input").get("index")
                key = idx if idx is not None else "default"
            except Exception:
                pass
        if key in used:
            st.update(state="dup", text="Этот микрофон уже включён в другой строке")
            return st
        ch = _Channel(self, i, dev)
        try:
            ch.start()
        except Exception as e:
            st.update(state="error", text=f"Не открылся: {e}")
            return st
        used.add(key)
        self.channels[i] = ch
        st.update(state="ok", device=dev)
        return st

    def stop(self):
        self._wd_stop.set()
        with self._lock:
            for ch in list(self.channels.values()):
                ch.stop()
            self.channels.clear()
        self._fuser.reset()

    # --- события ---
    def _publish(self, statuses: list[dict]):
        changed = [(s["state"], s["text"]) for s in statuses] != [(s["state"], s["text"]) for s in self.statuses]
        self.statuses = statuses
        if changed:
            try:
                self.on_status(statuses)
            except Exception:
                pass

    def _utterance(self, u: Utterance):
        if len(self.channels) <= 1:          # один микрофон — как раньше, без ожидания
            self.on_utterance([u])
            return
        self._fuser.start()
        self._fuser.add(u)

    def _emit_group(self, group: list[Utterance]):
        if not self.muted:
            self.on_utterance(group)

    # --- слежение: микрофон отключили или включили заново ---
    def _watch(self, stop: threading.Event):
        last_retry = time.monotonic()
        while not stop.wait(3.0):
            try:
                self._check(stop, retry=time.monotonic() - last_retry >= 9.0)
                if time.monotonic() - last_retry >= 9.0:
                    last_retry = time.monotonic()
            except Exception:
                pass

    def _check(self, stop: threading.Event, retry: bool):
        with self._lock:
            if stop.is_set():
                return
            now = time.monotonic()
            statuses = [dict(s) for s in self.statuses]
            for i, ch in list(self.channels.items()):
                if now - ch.last_cb > 3.0:           # звук перестал идти: устройство отключили
                    ch.stop()
                    del self.channels[i]
                    statuses[i].update(state="error", text="Микрофон перестал отвечать (отключён?). Пробую подключить снова")
            if retry:
                devices = []
                try:
                    devices = self.devices()
                except Exception:
                    pass
                slots = mic_slots(self.cfg)
                used = {c.device if c.device is not None else "default" for c in self.channels.values()}
                for i, st in enumerate(statuses):
                    if st["state"] in ("error", "missing") and slots[i]["enabled"] and i not in self.channels:
                        statuses[i] = self._open_slot(i, slots[i], devices, used)
            self._publish(statuses)


def format_status(statuses: list[dict]) -> str:
    """Коротко для главной: «2 из 3 микрофонов»."""
    on = [s for s in statuses if s["state"] != "off"]
    ok = [s for s in on if s["state"] == "ok"]
    if not on:
        return "выключены"
    return f"{len(ok)} из {len(on)} работают" if len(on) > 1 else ("работает" if ok else "не работает")
