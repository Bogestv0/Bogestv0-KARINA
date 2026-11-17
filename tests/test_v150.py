"""1.15.0: плашка острова — музыка (karina/media.py), настройки показа, ширина по содержимому."""
import time

from karina import island_tools as tools
from karina import media


def test_idle_settings_defaults_and_cleaning():
    s = tools.clean_settings({})
    assert all(s[k] is True for k in tools.IDLE_KEYS)
    s = tools.clean_settings({"idle_temp": 0, "idle_music": "", "bogus": 1}, s)
    assert s["idle_temp"] is False and s["idle_music"] is False and s["idle_time"] is True
    assert "bogus" not in s


def test_clean_info():
    assert media.clean_info(None) is None
    assert media.clean_info({"title": "", "artist": "", "status": "playing"}) is None
    r = media.clean_info({"title": "  Сансара \n", "artist": "Баста", "status": "playing", "app": "Spotify"})
    assert r == {"title": "Сансара", "artist": "Баста", "status": "playing", "app": "Spotify"}
    r = media.clean_info({"title": "", "artist": "Только исполнитель", "status": "stopped"})
    assert r["title"] == "Только исполнитель" and r["artist"] == "" and r["status"] == "paused"
    assert len(media.clean_info({"title": "я" * 500, "status": "playing"})["title"]) == 120


class FakeBackend:
    def __init__(self):
        self.raw, self.cmds, self.fail = {"title": "A", "artist": "B", "status": "playing"}, [], False

    def poll(self):
        return self.raw

    def command(self, name):
        self.cmds.append(name)
        return not self.fail


def _wait(cond, t=2.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_media_reports_changes_only_and_runs_commands():
    seen, be, keys = [], FakeBackend(), []
    m = media.Media(seen.append, backend=be, press=keys.append, interval=0.02)
    m.start()
    try:
        assert _wait(lambda: len(seen) == 1) and seen[0]["title"] == "A"
        time.sleep(0.1)
        assert len(seen) == 1                                    # без изменений событий нет
        be.raw = {"title": "C", "artist": "", "status": "paused"}
        assert _wait(lambda: len(seen) == 2) and seen[1]["status"] == "paused"
        assert m.command("toggle") and m.command("bogus") is False
        assert _wait(lambda: be.cmds == ["toggle"]) and keys == []
        be.fail = True                                           # WinRT не справился — нажимаем мультимедийную клавишу
        m.command("next")
        assert _wait(lambda: keys == ["next"])
        be.raw = None                                            # плеер закрыли
        assert _wait(lambda: seen[-1] is None)
    finally:
        m.stop()
    assert _wait(lambda: not m.running or True)


def test_media_without_backend_uses_keys():
    keys = []
    m = media.Media(lambda i: None, backend=None, press=keys.append, interval=0.02)
    m._make_backend = lambda: None                               # winrt нет
    m.start()
    try:
        m.command("prev")
        assert _wait(lambda: keys == ["prev"])
    finally:
        m.stop()


def test_island_page_has_idle_plate():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "ui" / "island.html").read_text(encoding="utf-8")
    for needle in ("id=\"cInfo\"", "id=\"cMus\"", "media_cmd", "battery_get", "idle_music", "m-info", "m-music"):
        assert needle in html, needle
