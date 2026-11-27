import json, os
import pytest
from karina import cabinet as cab

TOKEN = "a" * 64


class FakeCfg(dict):
    def update(self, d):
        super().update(d)


@pytest.fixture
def net(monkeypatch):
    """Подставной кабинет: запоминает отправленное, ответ задаётся тестом."""
    class Net:
        sent = []
        result = "ok"
    n = Net(); n.sent = []

    def fake_post(creds, events, timeout=10.0):
        if n.result == "ok":
            n.sent.extend(events)
        return n.result
    monkeypatch.setattr(cab, "post_events", fake_post)
    return n


def test_valid_creds():
    assert cab.valid_creds({"token": TOKEN, "url": "https://cabinet.bogestv0.ru"})
    assert cab.valid_creds({"token": TOKEN})
    assert not cab.valid_creds({"token": "short"})
    assert not cab.valid_creds({"token": TOKEN, "url": "http://evil.example"})
    assert not cab.valid_creds({"token": TOKEN, "url": "https://a.b/path"})
    assert not cab.valid_creds(None)


def test_read_creds_both_layouts(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"cabinet": {"token": TOKEN, "name": "ПК"}}), encoding="utf-8")
    assert cab.read_creds(p)["name"] == "ПК"
    q = tmp_path / "cabinet.json"
    q.write_text(json.dumps({"token": TOKEN, "url": "https://cabinet.bogestv0.ru/"}), encoding="utf-8")
    assert cab.read_creds(q)["url"] == "https://cabinet.bogestv0.ru"
    assert cab.read_creds(tmp_path / "none.json") is None
    q.write_text("{bad", encoding="utf-8")
    assert cab.read_creds(q) is None


def test_outbox_cap_and_ttl(tmp_path):
    ob = cab.Outbox(tmp_path / "q.json")
    for i in range(60):
        ob.add(cab.make_event("wake", "x"))
    assert ob.pending() == cab.QUEUE_MAX
    old = tmp_path / "old.json"
    old.write_text(json.dumps([{"type": "boot", "t": cab.now_ms() - cab.QUEUE_TTL - 1000}]))
    assert cab.Outbox(old).flush({"url": "https://x.y", "token": TOKEN}) == "ok"  # устаревшее выбрасывается


def test_outbox_flush_results(tmp_path, net):
    ob = cab.Outbox(tmp_path / "q.json")
    creds = {"url": "https://cabinet.bogestv0.ru", "token": TOKEN}
    ob.add(cab.make_event("boot", "service"))
    assert ob.flush(None) == "nocreds" and ob.pending() == 1
    net.result = "fail"
    assert ob.flush(creds) == "fail" and ob.pending() == 1
    net.result = "ok"
    assert ob.flush(creds) == "ok" and ob.pending() == 0 and net.sent[0]["type"] == "boot"
    ob.add(cab.make_event("boot", "service"))
    net.result = "revoked"
    assert ob.flush(creds) == "revoked" and ob.pending() == 0


def test_outbox_batches(tmp_path, net):
    ob = cab.Outbox(tmp_path / "q.json")
    for _ in range(45):
        ob.add(cab.make_event("wake", "x"))
    assert ob.flush({"url": "https://x.y", "token": TOKEN}) == "ok"
    assert len(net.sent) == 45


def test_pair_validates_and_maps_errors(monkeypatch):
    assert cab.pair("")[0] is False
    monkeypatch.setattr(cab, "_http", lambda *a, **k: (0, {}))
    assert "интернет" in cab.pair("KR-AAAA-AAAA")[1]
    monkeypatch.setattr(cab, "_http", lambda *a, **k: (400, {"ok": False, "error": "bad_code"}))
    assert cab.pair("KR-AAAA-AAAA")[1] == cab.PAIR_ERRORS["bad_code"]
    monkeypatch.setattr(cab, "_http", lambda *a, **k: (200, {"ok": True, "token": TOKEN, "id": "d1", "name": "ПК"}))
    ok, c = cab.pair("KR-AAAA-AAAA")
    assert ok and c["token"] == TOKEN and c["id"] == "d1"
    monkeypatch.setattr(cab, "_http", lambda *a, **k: (200, {"ok": True, "token": "bad"}))
    assert cab.pair("KR-AAAA-AAAA")[0] is False


def test_client_pair_send_unlink(tmp_path, net, monkeypatch):
    shared = tmp_path / "shared"; shared.mkdir()
    cfg = FakeCfg(cabinet={"url": cab.DEFAULT_URL, "token": "", "id": "", "name": ""})
    cl = cab.CabinetClient(cfg, tmp_path, shared_dir=str(shared))
    cl.send("lost")
    assert cl.outbox.pending() == 0                      # без привязки ничего не копится
    monkeypatch.setattr(cab, "pair", lambda code, url: (True, {"url": cab.DEFAULT_URL, "token": TOKEN, "id": "d", "name": "ПК"}))
    monkeypatch.setattr(cl, "beat", lambda: None)
    r = cl.pair("KR-AAAA-AAAA")
    assert r["ok"] and cl.linked
    assert json.loads((shared / "cabinet.json").read_text())["token"] == TOKEN
    cl.send("lost", force=True, locked=True)
    cl.flush()
    assert any(e["type"] == "lost" and e["locked"] is True for e in net.sent)
    cl.unlink()
    assert not cl.linked and not (shared / "cabinet.json").exists()


def test_client_revoked_forgets_key(tmp_path, net):
    cfg = FakeCfg(cabinet={"url": cab.DEFAULT_URL, "token": TOKEN, "id": "d", "name": "ПК"})
    cl = cab.CabinetClient(cfg, tmp_path)
    cl.send("back")
    net.result = "revoked"
    assert cl.flush() == "revoked"
    assert not cl.linked and "отвязан" in cl.last_error


def test_beat_includes_watch_and_skips_queue(tmp_path, net):
    cfg = FakeCfg(cabinet={"url": cab.DEFAULT_URL, "token": TOKEN, "id": "d", "name": "ПК"})
    cl = cab.CabinetClient(cfg, tmp_path, watch_info=lambda: {"state": "near", "battery": 50})
    cl.beat()
    b = [e for e in net.sent if e["type"] == "beat"][0]
    assert b["watch"]["battery"] == 50 and b["src"] == "karina"
