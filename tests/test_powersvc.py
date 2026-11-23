import json, os
import pytest
from karina import cabinet as cab, powersvc as ps

TOKEN = "b" * 64


class Clock:
    def __init__(self, t=1_800_000_000.0): self.t = t
    def __call__(self): return self.t


@pytest.fixture
def agent(tmp_path, monkeypatch):
    sent = []

    def post(creds, events, timeout=10.0):
        sent.extend(events)
        return "ok"
    monkeypatch.setattr(cab, "post_events", post)
    (tmp_path / "cabinet.json").write_text(json.dumps({"url": cab.DEFAULT_URL, "token": TOKEN, "id": "d", "name": "ПК"}))
    clock = Clock()
    a = ps.PowerAgent(tmp_path, clock=clock, kind_fn=lambda: "shutdown", post=post)
    a.sent, a.clock_obj = sent, clock
    return a


def types(a):
    return [e["type"] for e in a.sent]


def test_first_start_is_not_boot(agent):
    agent.on_start()
    assert "boot" not in types(agent)


def test_boot_after_clean_shutdown(agent):
    agent.on_start(); agent.on_shutdown()
    assert types(agent) == ["shutdown"] and agent.sent[0]["kind"] == "shutdown"
    agent.clock_obj.t += 30
    b = ps.PowerAgent(agent.dir, clock=agent.clock_obj, kind_fn=lambda: "shutdown", post=agent.post)
    b.on_start()
    assert types(agent)[-1] == "boot" and agent.sent[-1]["prev_clean"] is True


def test_unexpected_power_loss(agent):
    agent.on_start(); agent.tick()
    agent.clock_obj.t += 600                      # служба молчала 10 минут, штатного выключения не было
    b = ps.PowerAgent(agent.dir, clock=agent.clock_obj, post=agent.post)
    b.on_start()
    e = agent.sent[-1]
    assert e["type"] == "boot" and e["prev_clean"] is False and e["last_seen"]


def test_short_gap_is_service_restart(agent):
    agent.on_start(); agent.tick()
    agent.clock_obj.t += 20
    ps.PowerAgent(agent.dir, clock=agent.clock_obj, post=agent.post).on_start()
    assert "boot" not in types(agent)


def test_shutdown_reported_once_and_stops_beats(agent):
    agent.on_start()
    agent.on_shutdown(); agent.on_shutdown(); agent.on_stop()
    assert types(agent).count("shutdown") == 1 and "service_stop" not in types(agent)
    n = len(agent.sent); agent.tick()
    assert len(agent.sent) == n


def test_manual_stop_then_start_is_not_boot(agent):
    agent.on_start(); agent.on_stop()
    assert types(agent) == ["service_stop"]
    ps.PowerAgent(agent.dir, clock=agent.clock_obj, post=agent.post).on_start()
    assert "boot" not in types(agent)


def test_sleep_wake_and_beat(agent):
    agent.on_start(); agent.on_sleep(); agent.on_wake(); agent.tick()
    assert types(agent)[-3:] == ["sleep", "wake", "beat"]
    assert all(e["src"] == "service" for e in agent.sent)


def test_events_wait_offline_and_keep_own_time(agent, monkeypatch):
    monkeypatch.setattr(cab, "post_events", lambda *a, **k: "fail")
    agent.on_start(); agent.on_shutdown()
    assert agent.outbox.pending() == 1
    t = agent.outbox._load()[0]["t"]
    assert t == int(agent.clock_obj.t * 1000)


def test_revoked_removes_creds(agent, monkeypatch):
    monkeypatch.setattr(cab, "post_events", lambda *a, **k: "revoked")
    agent.on_start(); agent.on_sleep()
    assert not os.path.exists(agent.cred_path)


def test_parse_shutdown_kind():
    assert ps.parse_shutdown_kind("The process C:\\Windows\\System32\\shutdown.exe has initiated the restart of computer X") == "restart"
    assert ps.parse_shutdown_kind("initiated the power off of computer X") == "shutdown"
    assert ps.parse_shutdown_kind("инициировал перезагрузку компьютера") == "restart"
    assert ps.parse_shutdown_kind("") == "unknown"
