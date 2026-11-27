"""«Телефон как ключ»: проверка настроек, решения «блокировать или нет», поиска устройства и всего цикла на подменённом Bluetooth/Wi-Fi."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import watchlock as w


# ---------------------------------------------------------------- настройки
def test_clean_settings_limits_and_garbage():
    c = w.clean_settings({"enabled": 1, "lock_after": 5, "rssi_min": -5, "low_battery": 99,
                          "targets": [{"kind": "ble", "id": "aa-bb-cc-dd-ee-ff", "name": "  Мои   часы "},
                                      {"kind": "ble", "id": "AA:BB:CC:DD:EE:FF"},               # дубль
                                      {"kind": "ping", "id": "-oProxyCommand=x"},               # не должно стать ключом команды
                                      {"kind": "ping", "id": "192.168.1.20"},
                                      {"kind": "evil", "id": "x"}, "мусор", None]})
    assert c["enabled"] is True and c["lock_after"] == 10 and c["rssi_min"] == -30 and c["low_battery"] == 50
    assert [t["id"] for t in c["targets"]] == ["AA:BB:CC:DD:EE:FF", "192.168.1.20"]
    assert c["targets"][0]["name"] == "Мои часы"
    assert w.clean_settings({"lock_after": "abc", "rssi_min": "x"})["lock_after"] == 30


def test_targets_capped_and_old_values_kept():
    many = [{"kind": "ping", "id": f"10.0.0.{i}"} for i in range(1, 9)]
    c = w.clean_settings({"targets": many})
    assert len(c["targets"]) == w.MAX_TARGETS
    c2 = w.clean_settings({"lock_after": 60}, c)
    assert len(c2["targets"]) == w.MAX_TARGETS and c2["lock_after"] == 60


def test_notify_validation_and_token_hidden():
    n = w.clean_notify({"kind": "ntfy", "topic": "my topic!", "server": "javascript:alert(1)", "chat_id": "abc"})
    assert n["topic"] == "" and n["server"] == "https://ntfy.sh" and n["chat_id"] == ""
    n = w.clean_notify({"kind": "telegram", "token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "chat_id": "-100123"})
    assert n["token"].startswith("123456:") and n["chat_id"] == "-100123"
    n2 = w.clean_notify({"token": ""}, n)                      # пустое поле — оставить прежний токен
    assert n2["token"] == n["token"]
    assert w.clean_notify({"token": "плохой"}, n)["token"] == n["token"]
    pub = w.public_view({"notify": n})
    assert "token" not in pub["notify"] and pub["notify"]["has_token"] is True


def test_new_topic_is_long_and_valid():
    a, b = w.new_topic(), w.new_topic()
    assert a != b and len(a) >= 20 and w.clean_notify({"topic": a})["topic"] == a


def test_phone_chosen_by_bluetooth_is_saved():
    c = w.clean_settings({"phone": {"id": "BT-pair-id", "name": "  Мой   iPhone  "}})
    assert c["phone"] == {"id": "BT-pair-id", "name": "Мой iPhone"}
    c2 = w.clean_settings({"phone": {"name": "Другой"}}, c)
    assert c2["phone"] == {"id": "BT-pair-id", "name": "Другой"}
    assert w.clean_settings({"phone": {"id": "bad\nid"}}, c)["phone"]["id"] == "BT-pair-id"


# ---------------------------------------------------------------- когда блокировать
def feed(tr, seq):
    """seq — список (время, present); возвращает [(время, событие)]."""
    out = []
    for t, p in seq:
        out += [(t, e) for e in tr.feed(p, t)]
    return out


def test_locks_after_timeout_once():
    tr = w.Tracker(lock_after=30)
    seq = [(0, True), (5, True)] + [(t, False) for t in range(10, 100, 5)]
    assert feed(tr, seq) == [(40, "lost")]                       # absent_since = 10, 10 + 30 = 40; повторно не блокирует
    assert tr.state(95) == ("away", 0.0)


def test_short_dropout_does_not_lock():
    tr = w.Tracker(lock_after=30)
    seq = [(0, True), (10, False), (20, False), (30, True), (40, False), (50, False), (60, True)]
    assert feed(tr, seq) == []


def test_unknown_is_not_absent():
    tr = w.Tracker(lock_after=30)
    seq = [(0, True)] + [(t, None) for t in range(5, 200, 5)]    # ошибка Bluetooth: «не знаю», блокировать нельзя
    assert feed(tr, seq) == []


def test_never_seen_never_locks():
    tr = w.Tracker(lock_after=10)
    assert feed(tr, [(t, False) for t in range(0, 200, 5)]) == []
    assert tr.state(200)[0] == "unknown"


def test_startup_grace():
    tr = w.Tracker(lock_after=10, grace=60)
    seq = [(0, True)] + [(t, False) for t in range(5, 120, 5)]
    assert feed(tr, seq) == [(60, "lost")]                       # раньше 60 с после запуска не блокируем


def test_back_after_lost_and_relock():
    tr = w.Tracker(lock_after=10)
    seq = [(0, True), (5, False), (20, False), (25, True), (30, False), (45, False)]
    assert feed(tr, seq) == [(20, "lost"), (25, "back"), (45, "lost")]


def test_gap_after_sleep_does_not_count_as_absence():
    tr = w.Tracker(lock_after=30, gap=60)
    seq = [(0, True), (5, False), (3600, False)]                 # компьютер спал час: отсутствие считаем заново
    assert feed(tr, seq) == []
    assert feed(tr, [(3625, False)]) == []
    assert feed(tr, [(3631, False)]) == [(3631, "lost")]


def test_waiting_state_counts_down():
    tr = w.Tracker(lock_after=30)
    feed(tr, [(0, True), (10, False)])
    st, left = tr.state(25)
    assert st == "waiting" and left == 15


# ---------------------------------------------------------------- поиск устройства и сглаживание
def test_match_ble_by_address_and_name():
    devs = [{"address": "11:22:33:44:55:66", "name": "HONOR Watch ES-123", "rssi": -60},
            {"address": "AA:BB:CC:DD:EE:FF", "name": "", "rssi": -70}]
    assert w.match_ble({"id": "aa:bb:cc:dd:ee:ff"}, devs)["rssi"] == -70
    assert w.match_ble({"id": "honor watch"}, devs)["address"] == "11:22:33:44:55:66"
    assert w.match_ble({"id": "xiaomi"}, devs) is None
    assert w.match_ble({"id": "11:22:33:44:55:67"}, devs) is None       # похожий адрес — другое устройство


def test_smoother():
    s = w.Smoother(3)
    assert s.add(-60) == -60 and s.add(-90) == -75 and s.add(-60) == -70
    assert s.add(None) is None


# ---------------------------------------------------------------- весь цикл на подменённом Bluetooth
class FakeCfg(dict):
    def update(self, patch):
        self.update_calls = getattr(self, "update_calls", 0) + 1
        super().update(patch)


def make(monkeypatch_scan, cfg_watch, now_box):
    cfg = FakeCfg({"watch": cfg_watch})
    locks, pushes, events = [], [], []
    svc = w.WatchLock(cfg, events.append, log=lambda m: None, lock_fn=lambda: locks.append(1),
                      push_fn=lambda n, t, x: (pushes.append((t, x)) or (True, "")), clock=lambda: now_box[0])
    return svc, locks, pushes, events


def run_scenario(monkeypatch, visible_by_time):
    """visible_by_time(t) → список устройств, которые «слышно» в момент t. Возвращает (locks, pushes, events, svc)."""
    box = [0.0]
    async def fake_scan(seconds=0):
        return visible_by_time(box[0])
    monkeypatch.setattr(w, "scan_ble", fake_scan)
    async def no_battery(addr, timeout=0):
        return 77
    monkeypatch.setattr(w, "read_battery", no_battery)
    conf = {"enabled": True, "lock_after": 30, "battery": True, "low_battery": 15,
            "targets": [{"kind": "ble", "id": "11:22:33:44:55:66", "name": "Часы"}],
            "notify": {"kind": "ntfy", "topic": "karina-test-topic", "on_lost": True, "on_back": True}}
    svc, locks, pushes, events = make(monkeypatch, conf, box)
    svc.tracker.grace = 0
    watch = {"address": "11:22:33:44:55:66", "name": "HONOR", "rssi": -55}
    for t in range(0, 200, 5):
        box[0] = float(t)
        svc.tick(now=float(t))
    return locks, pushes, events, svc


def _join_pushes():
    import threading
    for th in threading.enumerate():
        if th.name == "karina-watch-push":
            th.join(2)


def test_full_cycle_lock_and_push(monkeypatch):
    watch = {"address": "11:22:33:44:55:66", "name": "HONOR", "rssi": -55}
    locks, pushes, events, svc = run_scenario(monkeypatch, lambda t: [watch] if t < 50 else [])     # ушли на 50-й секунде
    _join_pushes()
    assert locks == [1]                                      # заблокировали ровно один раз
    assert pushes and pushes[0][0] == "Компьютер заблокирован"
    assert svc.battery == 77
    toasts = [e["toast"] for e in events if e.get("toast")]
    assert any("заблокирован" in t for t in toasts)
    last = [e for e in events if e["type"] == "watch"][-1]
    assert last["state"] == "away" and last["battery"] == 77


def test_full_cycle_return_notifies(monkeypatch):
    watch = {"address": "11:22:33:44:55:66", "name": "HONOR", "rssi": -55}
    locks, pushes, events, svc = run_scenario(monkeypatch, lambda t: [] if 50 <= t < 120 else [watch])
    _join_pushes()
    assert locks == [1]
    assert [p[0] for p in pushes] == ["Компьютер заблокирован", "Телефон снова в сети"]
    assert [e for e in events if e["type"] == "watch"][-1]["state"] == "present"


def test_weak_signal_counts_as_away(monkeypatch):
    box = [0.0]
    async def fake_scan(seconds=0):
        return [{"address": "11:22:33:44:55:66", "name": "x", "rssi": -95 if box[0] >= 40 else -50}]
    monkeypatch.setattr(w, "scan_ble", fake_scan)
    conf = {"enabled": True, "lock_after": 20, "rssi_min": -80, "battery": False,
            "targets": [{"kind": "ble", "id": "11:22:33:44:55:66"}]}
    svc, locks, pushes, events = make(None, conf, box)
    svc.tracker.grace = 0
    for t in range(0, 120, 5):
        box[0] = float(t)
        svc.tick(now=float(t))
    assert locks == [1]


def test_bluetooth_error_never_locks(monkeypatch):
    calls = {"n": 0}
    async def broken(seconds=0):
        calls["n"] += 1
        if calls["n"] > 2:
            raise w.ProbeError("Bluetooth недоступен: выключен")
        return [{"address": "11:22:33:44:55:66", "name": "x", "rssi": -50}]
    monkeypatch.setattr(w, "scan_ble", broken)
    conf = {"enabled": True, "lock_after": 10, "battery": False, "targets": [{"kind": "ble", "id": "11:22:33:44:55:66"}]}
    box = [0.0]
    svc, locks, pushes, events = make(None, conf, box)
    svc.tracker.grace = 0
    for t in range(0, 300, 5):
        box[0] = float(t)
        svc.tick(now=float(t))
    assert locks == [] and "Bluetooth" in svc.status()["error"]


def test_lock_disabled_only_notifies(monkeypatch):
    box = [0.0]
    async def fake_scan(seconds=0):
        return [{"address": "11:22:33:44:55:66", "name": "x", "rssi": -50}] if box[0] < 20 else []
    monkeypatch.setattr(w, "scan_ble", fake_scan)
    conf = {"enabled": True, "lock": False, "lock_after": 10, "battery": False,
            "targets": [{"kind": "ble", "id": "11:22:33:44:55:66"}], "notify": {"kind": "ntfy", "topic": "karina-test-topic"}}
    svc, locks, pushes, events = make(None, conf, box)
    svc.tracker.grace = 0
    for t in range(0, 100, 5):
        box[0] = float(t)
        svc.tick(now=float(t))
    _join_pushes()
    assert locks == [] and pushes and pushes[0][0] == "Связь с компьютером потеряна"


def test_two_targets_any_present_is_enough(monkeypatch):
    box = [0.0]
    async def fake_scan(seconds=0):
        return []                                          # часов в эфире нет
    monkeypatch.setattr(w, "scan_ble", fake_scan)
    monkeypatch.setattr(w, "ping_ok", lambda host: True)   # а телефон в сети отвечает
    conf = {"enabled": True, "lock_after": 10, "battery": False,
            "targets": [{"kind": "ble", "id": "11:22:33:44:55:66"}, {"kind": "ping", "id": "192.168.1.20"}]}
    svc, locks, pushes, events = make(None, conf, box)
    svc.tracker.grace = 0
    for t in range(0, 200, 5):
        box[0] = float(t)
        svc.tick(now=float(t))
    assert locks == [] and svc.status()["state"] == "present"


def test_disabled_or_no_targets_stays_off():
    box = [0.0]
    svc, locks, pushes, events = make(None, {"enabled": True, "targets": []}, box)
    svc.start()
    assert svc.status()["state"] == "off" and svc._thread is None
    svc.stop()


def test_ping_rejects_option_injection():
    assert w.ping_ok("-f") is None and w.ping_ok("a b") is None and w.ping_ok("") is None


def test_send_push_without_settings():
    assert w.send_push({"kind": "none"}, "a", "b") == (False, "Уведомления выключены")
    assert w.send_push({"kind": "ntfy", "topic": ""}, "a", "b")[0] is False
    assert w.send_push({"kind": "telegram"}, "a", "b")[0] is False


def test_send_push_ntfy_request(monkeypatch):
    seen = {}
    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_open(req, timeout=0):
        seen["url"], seen["body"], seen["h"] = req.full_url, req.data, dict(req.header_items())
        return R()
    monkeypatch.setattr(w.urllib.request, "urlopen", fake_open)
    ok, err = w.send_push({"kind": "ntfy", "server": "https://ntfy.sh", "topic": "karina-test-topic"}, "Компьютер заблокирован", "Связь потеряна")
    assert ok and err == ""
    assert seen["url"] == "https://ntfy.sh/karina-test-topic" and seen["body"] == "Связь потеряна".encode("utf-8")
    assert seen["h"]["Title"].startswith("=?UTF-8?B?")             # кириллица в заголовке закодирована


class _FakeCab:
    def __init__(self, linked): self.linked, self.sent = linked, []
    def send(self, t, force=False, **f): self.sent.append((t, f))


def _conf():
    return {"enabled": True, "lock_after": 30, "battery": False, "low_battery": 15, "targets": [],
            "notify": {"kind": "ntfy", "topic": "karina-test-topic", "on_lost": True, "on_back": True}}


def test_events_go_to_cabinet_when_linked():
    svc, locks, pushes, _ = make(None, _conf(), [0.0])
    svc.cabinet = _FakeCab(True)
    c = svc.conf()
    svc._on_event("lost", c); svc._on_event("back", c)
    assert [t for t, _ in svc.cabinet.sent] == ["lost", "back"] and svc.cabinet.sent[0][1]["locked"] is True
    assert pushes == []                     # старый канал ntfy при подключённом кабинете молчит


def test_legacy_push_when_not_linked():
    svc, locks, pushes, _ = make(None, _conf(), [0.0])
    svc.cabinet = _FakeCab(False)
    svc._on_event("lost", svc.conf())
    assert len(pushes) == 1 and svc.cabinet.sent == []
