"""Проверка логики без Windows: разбор фраз и вызов нужных команд (системные вызовы подменены)."""
import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import actions, winutil
from karina.actions import Ctx, dispatch
from karina.apps import AppIndex
from karina.config import Config
from karina.textutil import norm
from karina.wake import split_wake


class FakeAssistant:
    sleeping = False
    pending = None
    def ask_confirm(self, a): self.pending = a


def make(tmp):
    cfg = Config(Path(tmp) / "c.json")
    cfg.data["commands"] = [{"phrases": ["включи запись"], "do": {"type": "keys", "value": "ctrl+shift+r"}, "reply": "Запись включена"}]
    cfg.data["apps"] = [{"name": "Моя программа", "aliases": ["моя прога", "редактор"], "path": "C:\\x\\my.exe", "proc": ["my"]}]
    with mock.patch.object(AppIndex, "refresh_menu", lambda s: None):
        apps = AppIndex(cfg)
    apps._menu = [("Discord", "C:\\lnk\\Discord.lnk"), ("Bogestv0 MIXER", "C:\\lnk\\Bogestv0 MIXER.lnk"), ("Google Chrome", "C:\\lnk\\Google Chrome.lnk")]
    apps._menu_ts = 9e18
    return cfg, apps


class T(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg, self.apps = make(self.tmp)
        self.fa = FakeAssistant()
        self.started, self.closed, self.keys, self.pressed = [], [], [], []
        patches = [mock.patch.object(winutil, "start", lambda t, a="": self.started.append(t)),
                   mock.patch.object(winutil, "close_by_name", lambda p: (self.closed.append(p), 1)[1]),
                   mock.patch.object(winutil, "hotkey", lambda *k: self.keys.append(k)),
                   mock.patch.object(winutil, "press", lambda k, n=1: self.pressed.append((k, n))),
                   mock.patch.object(actions, "_endpoint", lambda: None),
                   mock.patch("webbrowser.open", lambda u: self.started.append(u))]
        for p in patches: p.start(); self.addCleanup(p.stop)

    def say(self, phrase):
        ctx = Ctx(self.cfg, self.apps, self.fa, raw=phrase)
        addressed, rest = split_wake(phrase, self.cfg.wake_names())
        return dispatch(ctx, rest if addressed else norm(phrase))

    def test_name_variants(self):
        for p in ["Карина, открой блокнот", "карена открой блокнот", "открой блокнот Карина"]:
            self.assertTrue(split_wake(p, self.cfg.wake_names())[0], p)
        self.assertFalse(split_wake("Марина открой блокнот", self.cfg.wake_names())[0])

    def test_open_apps(self):
        for phrase, expect in [("Карина, открой миксер", "Bogestv0 MIXER.lnk"), ("Карина открой дискорд", "Discord.lnk"),
                               ("Карина, запусти хром", "Google Chrome.lnk"), ("Карина, открой блокнот", "notepad.exe"),
                               ("Карина, открой мою прогу", "my.exe"), ("Карина, открой редактор", "my.exe")]:
            self.started.clear()
            ok, reply = self.say(phrase)
            self.assertTrue(ok, phrase)
            self.assertTrue(self.started and self.started[0].endswith(expect), (phrase, self.started, reply))

    def test_site(self):
        self.say("Карина, открой ютуб")
        self.assertEqual(self.started[-1], "https://www.youtube.com")
        self.say("Карина, открой example.com")
        self.assertEqual(self.started[-1], "https://example.com")

    def test_unknown_app(self):
        ok, reply = self.say("Карина, открой фывапролдж")
        self.assertTrue(ok); self.assertIn("Не нашла", reply); self.assertFalse(self.started)

    def test_close_focus(self):
        ok, r = self.say("Карина, закрой миксер")
        self.assertIn("Bogestv0 MIXER", r); self.assertTrue(self.closed)
        ok, r = self.say("Карина, закрой окно"); self.assertEqual(self.keys[-1], ("alt", "f4"))

    def test_volume(self):
        ok, r = self.say("Карина, громкость пятьдесят процентов")
        self.assertEqual(r, "Громкость 50 процентов."); self.assertIn(("vol_up", 25), self.pressed)
        self.pressed.clear(); self.say("сделай громче"); self.assertEqual(self.pressed[-1], ("vol_up", 5))
        self.say("тише"); self.assertEqual(self.pressed[-1], ("vol_down", 5))
        self.assertFalse(self.say("громкость тихо")[0] and "Громкость" in self.say("громкость тихо")[1])

    def test_media_before_open(self):
        self.say("включи музыку"); self.assertEqual(self.pressed[-1][0], "play"); self.assertFalse(self.started)

    def test_time_date(self):
        self.assertIn("Сейчас", self.say("который час")[1]); self.assertIn("Сегодня", self.say("какое сегодня число")[1])

    def test_custom(self):
        ok, r = self.say("Карина, включи запись")
        self.assertEqual((ok, r), (True, "Запись включена")); self.assertEqual(self.keys[-1], ("ctrl", "shift", "r"))

    def test_shutdown_needs_confirm(self):
        ok, r = self.say("Карина, выключи компьютер")
        self.assertIn("«да»", r); self.assertIsNotNone(self.fa.pending)

    def test_search(self):
        with mock.patch("karina.net.is_online", lambda *a: True):
            self.say("Карина, найди в интернете погода в красноярске")
        self.assertIn("google.com/search?q=", self.started[-1])
        with mock.patch("karina.net.is_online", lambda *a: False):
            self.assertIn("Нет интернета", self.say("найди кошек")[1])

    def test_unknown_phrase(self):
        self.assertFalse(self.say("расскажи анекдот про носки")[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
