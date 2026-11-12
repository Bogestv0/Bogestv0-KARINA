"""Микшер и VPN голосом: настоящие клиент и сервер Карины против поддельного микшера и поддельного расширения (без Windows)."""
import json, sys, tempfile, threading, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from websockets.sync.server import serve
from websockets.sync.client import connect

from karina import actions, integrations
from karina.actions import Ctx, dispatch
from karina.config import Config
from karina.mixer_remote import MixerRemote, pct_to_db, db_to_pct
from karina.textutil import norm
from karina.vpn_link import VpnLink, EXTENSION_ID
from karina.wake import split_wake

STRIPS = ["Микрофон 1", "Микрофон 2", "Игры", "Музыка", "Discord", "Донаты", "Браузер", "Браузер 2"]
TAGS = ["HW1", "HW2", "V1", "V2", "V3", "V4", "V5", "V6"]


class FakeMixer:
    """Повторяет поведение src-tauri/src/remote.rs: PIN, meta, state, gain / mute / scene."""
    def __init__(self, pin="1234", names=STRIPS):
        self.pin, self.names = pin, names
        self.gain = [-3.0, -5.0, -6.0, -14.0, -4.0, -8.0, -9.0, -12.0]
        self.mute = [0] * 8
        self.scene = None
        self.log = []
        self.srv = serve(self._h, "127.0.0.1", 0)
        self.port = self.srv.socket.getsockname()[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def state(self):
        return json.dumps({"t": "state", "strips": [{"g": g, "m": m, "s": 0, "r": 0} for g, m in zip(self.gain, self.mute)]})

    def meta(self):
        return json.dumps({"t": "meta", "strips": [{"name": n, "tag": t, "hidden": False} for n, t in zip(self.names, TAGS)],
                           "scenes": [{"id": "play", "name": "Играю и стримлю"}, {"id": "call", "name": "Звонок"}, {"id": "spk", "name": "Всё в колонки"}],
                           "active": self.scene})

    def _h(self, ws):
        authed = False
        ws.send(json.dumps({"t": "auth", "ok": False, "need": True}))
        for raw in ws:
            m = json.loads(raw)
            t = m.get("t")
            if t == "auth":
                if m.get("pin") == self.pin:
                    authed = True
                    ws.send(json.dumps({"t": "auth", "ok": True}))
                    ws.send(self.meta())
                    ws.send(self.state())
                else:
                    ws.send(json.dumps({"t": "auth", "ok": False, "need": True, "msg": "Неверный PIN"}))
                continue
            if not authed:
                continue
            i = m.get("i")
            if t == "gain":
                self.gain[i] = m["v"]; self.log.append(("gain", i, m["v"]))
            elif t == "mute":
                self.mute[i] = m["v"]; self.log.append(("mute", i, m["v"]))
            elif t == "scene":
                self.scene = m["id"]; self.log.append(("scene", m["id"]))
            ws.send(self.state())

    def close(self):
        self.srv.shutdown()


class FakeAssistant:
    sleeping = False
    def __init__(self, cfg, settings=None):
        self.mixer = MixerRemote(cfg, settings_paths=[settings] if settings else [])
        self.vpn = VpnLink(cfg)
    def ask_confirm(self, a): pass


class FakeExtension:
    """Ведёт себя как background.js: подключается с Origin расширения, отвечает на cmd."""
    def __init__(self, port, origin=f"chrome-extension://{EXTENSION_ID}", connected=False, fail=""):
        self.connected, self.fail, self.cmds = connected, fail, []
        self._cm = connect(f"ws://127.0.0.1:{port}/", origin=origin, open_timeout=3)
        self.ws = self._cm.__enter__()
        self.ws.send(json.dumps({"t": "hello", "connected": connected, "hasProfile": True, "v": "1.4.0"}))
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            for raw in self.ws:
                m = json.loads(raw)
                if m.get("t") != "cmd":
                    continue
                self.cmds.append(m["cmd"])
                if self.fail:
                    self.ws.send(json.dumps({"t": "result", "id": m["id"], "ok": False, "error": self.fail, "connected": self.connected}))
                    continue
                already = (m["cmd"] == "on" and self.connected) or (m["cmd"] == "off" and not self.connected)
                if m["cmd"] == "on": self.connected = True
                elif m["cmd"] == "off": self.connected = False
                elif m["cmd"] == "toggle": self.connected = not self.connected
                self.ws.send(json.dumps({"t": "result", "id": m["id"], "ok": True, "already": already, "connected": self.connected}))
        except Exception:
            pass


def free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(Path(self.tmp) / "c.json")
        self.mixer = FakeMixer()
        self.cfg.data["mixer"].update({"port": self.mixer.port, "pin": "1234"})
        self.cfg.data["vpn"]["port"] = free_port()
        self.settings = Path(self.tmp) / "bogestv0-settings.json"
        self.a = FakeAssistant(self.cfg, self.settings)
        self.a.vpn.start()
        self.names = self.cfg.wake_names()

    def tearDown(self):
        self.a.mixer.close(); self.a.vpn.stop(); self.mixer.close()

    def say(self, phrase):
        addressed, rest = split_wake(phrase, self.names)
        handled, reply = dispatch(Ctx(self.cfg, unittest.mock.MagicMock(), self.a, raw=phrase), rest)
        # команды уходят микшеру без подтверждения: ждём, пока поддельный микшер их обработает
        last, quiet = -1, 0
        for _ in range(60):
            n = len(self.mixer.log)
            quiet = quiet + 1 if n == last else 0
            if quiet >= 3:
                break
            last = n
            time.sleep(0.02)
        return handled, reply


class MixerTests(Base):
    def test_quieter_game(self):
        _, r = self.say("Карина, сделай тише в игре")
        self.assertEqual(r, "Игры тише.")
        self.assertEqual(self.mixer.log[-1], ("gain", 2, -9.0))       # было −6, шаг 3 дБ

    def test_many_phrasings(self):
        cases = [
            ("сделай музыку тише", 3, -17.0), ("музыку потише", 3, -17.0), ("убавь звук в игре", 2, -9.0),
            ("прибавь донаты", 5, -5.0), ("сделай игру громче", 2, -3.0), ("добавь громкость браузеру", 6, -6.0),
            ("убавь дискорд", 4, -7.0), ("чуть тише музыку", 3, -15.5), ("намного громче игру", 2, 0.0),
        ]
        for phrase, idx, want in cases:
            with self.subTest(phrase):
                self.mixer.gain = [-3.0, -5.0, -6.0, -14.0, -4.0, -8.0, -9.0, -12.0]
                self.a.mixer.close()
                handled, _ = self.say("Карина, " + phrase)
                self.assertTrue(handled)
                self.assertEqual(self.mixer.log[-1], ("gain", idx, want))

    def test_plain_quieter_is_not_mixer(self):
        n = len(self.mixer.log)
        with unittest.mock.patch.object(actions, "_get_volume", lambda: 50), unittest.mock.patch.object(actions, "_set_volume", lambda v: True):
            _, r = self.say("Карина, тише")
        self.assertEqual(r, "Громкость 40.")                          # общая громкость компьютера, микшер не тронут
        self.assertEqual(len(self.mixer.log), n)

    def test_set_percent(self):
        _, r = self.say("Карина, громкость музыки пятьдесят процентов")
        self.assertEqual(r, "Музыка: 50 процентов.")
        self.assertEqual(self.mixer.log[-1], ("gain", 3, round(pct_to_db(50), 1)))
        _, r = self.say("музыку на 100")
        self.assertEqual(self.mixer.log[-1], ("gain", 3, 0.0))

    def test_mute_unmute(self):
        self.assertEqual(self.say("заглуши донаты")[1], "Выключила звук: Донаты.")
        self.assertEqual(self.mixer.mute[5], 1)
        self.assertEqual(self.say("верни звук донатов")[1], "Включила звук: Донаты.")
        self.assertEqual(self.mixer.mute[5], 0)

    def test_browser_vs_second_browser(self):
        self.say("убавь браузер")
        self.assertEqual(self.mixer.log[-1][1], 6)
        self.say("убавь второй браузер")
        self.assertEqual(self.mixer.log[-1][1], 7)
        self.say("убавь firefox")
        self.assertEqual(self.mixer.log[-1][1], 7)

    def test_both_mics(self):
        self.say("заглуши микрофон")
        self.assertEqual({e[1] for e in self.mixer.log if e[0] == "mute"}, {0, 1})

    def test_limits(self):
        self.mixer.gain[3] = -48.0
        self.a.mixer.close()
        self.assertEqual(self.say("потише музыку")[1], "Музыка уже на минимуме.")
        self.mixer.gain[2] = 12.0
        self.a.mixer.close()
        self.assertEqual(self.say("громче игру")[1], "Игры уже на максимуме.")

    def test_renamed_strip_and_alias(self):
        self.a.mixer.close()
        self.mixer.names = ["Микрофон 1", "Микрофон 2", "Warzone", "Музыка", "Discord", "Донаты", "Браузер", "Браузер 2"]
        self.settings.write_text(json.dumps({"strips": [{"name": n, "tag": t} for n, t in zip(self.mixer.names, TAGS)]}), "utf-8")   # так микшер хранит имена
        self.say("убавь warzone")
        self.assertEqual(self.mixer.log[-1][1], 2)
        self.cfg.data["mixer"]["aliases"] = {"Warzone": ["шутер"]}
        self.say("сделай шутер громче")
        self.assertEqual(self.mixer.log[-1][1], 2)

    def test_scenes(self):
        self.assertEqual(self.say("включи сцену звонок")[1], "Сцена «Звонок».")
        self.assertEqual(self.mixer.scene, "call")
        self.say("сцена играю")
        self.assertEqual(self.mixer.scene, "play")
        self.say("переключи на сцену колонки")
        self.assertEqual(self.mixer.scene, "spk")

    def test_wrong_pin(self):
        self.cfg.data["mixer"]["pin"] = "0000"
        self.assertIn("не принял PIN", self.say("убавь музыку")[1])

    def test_mixer_off(self):
        self.mixer.close()
        r = self.say("убавь музыку")[1]
        self.assertIn("Удалённый пульт", r)

    def test_unrelated_phrases_untouched(self):
        n = len(self.mixer.log)
        for p in ["включи музыку", "останови музыку", "открой игры", "который час"]:
            self.say(p)
        self.assertEqual(len(self.mixer.log), n)

    def test_db_math(self):
        self.assertEqual(round(pct_to_db(50), 1), -6.0)
        self.assertEqual(db_to_pct(-6.0), 50)


class VpnTests(Base):
    def test_on_off_status(self):
        ext = FakeExtension(self.cfg.data["vpn"]["port"])
        time.sleep(0.2)
        self.assertEqual(self.say("Карина, включи впн")[1], "Включила впн.")
        self.assertTrue(ext.connected)
        self.assertEqual(self.say("включи VPN")[1], "Впн уже включён.")
        self.assertEqual(self.say("включён ли впн")[1], "Впн включён.")
        self.assertEqual(self.say("выключи vpn")[1], "Выключила впн.")
        self.assertEqual(self.say("статус впн")[1], "Впн выключен.")
        self.assertEqual(self.say("отключи ви пи эн")[1], "Впн уже выключен.")
        self.assertEqual(self.say("переключи впн")[1], "Включила впн.")
        self.assertEqual(self.say("выключи расширение впн")[1], "Выключила впн.")

    def test_not_opening_app(self):
        FakeExtension(self.cfg.data["vpn"]["port"]); time.sleep(0.2)
        started = []
        with unittest.mock.patch("karina.winutil.start", lambda *a, **k: started.append(a)):
            self.say("включи в п н")
        self.assertEqual(started, [])

    def test_no_extension(self):
        self.assertIn("не подключено", self.say("включи впн")[1])

    def test_extension_error(self):
        FakeExtension(self.cfg.data["vpn"]["port"], fail="Сначала добавьте ссылку на вкладке «Серверы»"); time.sleep(0.2)
        self.assertEqual(self.say("включи впн")[1], "Не получилось включить впн: Сначала добавьте ссылку на вкладке «Серверы».")

    def test_foreign_origin_rejected(self):
        for origin in ("https://evil.example", None):
            with self.assertRaises(Exception):
                kw = {"origin": origin} if origin else {"origin": None}
                with connect(f"ws://127.0.0.1:{self.cfg.data['vpn']['port']}/", open_timeout=3, **kw):
                    pass
        self.assertEqual(self.a.vpn.browsers(), 0)

    def test_two_browsers(self):
        e1 = FakeExtension(self.cfg.data["vpn"]["port"]); e2 = FakeExtension(self.cfg.data["vpn"]["port"]); time.sleep(0.2)
        self.assertEqual(self.say("включи впн")[1], "Включила впн.")
        self.assertTrue(e1.connected and e2.connected)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
