"""После ответа Карина ещё слушает следующую фразу без имени (как после «Карина, привет»)."""
import sys, time, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina.assistant import Assistant


def make(cfg):
    a = Assistant.__new__(Assistant)
    a.cfg = cfg
    a.ptt_until = 0.0
    a.sleeping = False
    a.listening = True
    a._last_talk = time.time()
    return a


def test_window_opens_after_reply():
    a = make({"wake_required": True, "followup_window": 12})
    a._arm_followup()
    assert a.ptt_until > time.time() + 10


def test_no_window_for_unprompted_speech_or_when_disabled():
    a = make({"wake_required": True, "followup_after_reply": False})
    a._arm_followup()
    assert a.ptt_until == 0.0
    b = make({"wake_required": True})
    b._last_talk = time.time() - 600
    b._arm_followup()
    assert b.ptt_until == 0.0
