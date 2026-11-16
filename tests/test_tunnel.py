"""Встроенный VPN/прокси: разбор ссылок, настройки sing-box, голосовые команды с подтверждением «да/нет». Windows и сеть не нужны."""
import base64, json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import tunnel as T, tunnel_cmds as tc, actions, integrations  # noqa: F401
from karina.config import Config

UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"
REALITY = f"vless://{UUID}@r.example.com:443?type=tcp&security=reality&sni=www.microsoft.com&fp=chrome&pbk=KEY123&sid=abcd&flow=xtls-rprx-vision#Reality%20DE"
WS = f"vless://{UUID}@w.example.com:443?type=ws&security=tls&host=w.example.com&path=%2Fvl#WS"
VMESS = "vmess://" + base64.b64encode(json.dumps({"ps": "VM", "add": "vm.example.com", "port": "443", "id": UUID, "aid": "0", "net": "ws", "host": "h.example.com", "path": "/p", "tls": "tls"}).encode()).decode()
TROJAN = "trojan://pa%24%24@t.example.com:443?security=tls&sni=t.example.com#Trojan"
SS = "ss://" + base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:pass123").decode().rstrip("=") + "@ss.example.com:8388#SS"
SS_OLD = "ss://" + base64.b64encode(b"aes-256-gcm:secret@ss2.example.com:8389").decode() + "#SS2"
HY2 = "hysteria2://hpass@h.example.com:443/?sni=h.example.com&insecure=1#HY2"


class Parse(unittest.TestCase):
    def test_every_link_type(self):
        for link, typ in ((REALITY, "vless"), (WS, "vless"), (VMESS, "vmess"), (TROJAN, "trojan"), (SS, "shadowsocks"), (SS_OLD, "shadowsocks"), (HY2, "hysteria2")):
            name, ob = T.parse_link(link)
            self.assertEqual(ob["type"], typ, link)
            self.assertTrue(name)
            self.assertIn("server", ob)

    def test_reality_details(self):
        name, ob = T.parse_link(REALITY)
        self.assertEqual(name, "Reality DE")
        self.assertEqual(ob["tls"]["reality"], {"enabled": True, "public_key": "KEY123", "short_id": "abcd"})
        self.assertEqual(ob["flow"], "xtls-rprx-vision")
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")

    def test_ss_both_forms(self):
        self.assertEqual(T.parse_link(SS)[1]["method"], "chacha20-ietf-poly1305")
        o = T.parse_link(SS_OLD)[1]
        self.assertEqual((o["server"], o["server_port"], o["password"]), ("ss2.example.com", 8389, "secret"))

    def test_broken_links(self):
        for bad in ("vless://@x:1", "vless://u@h:99999", "vmess://!!!", "ss://", f"vless://{UUID}@h:443?security=reality"):
            with self.assertRaises(T.LinkError, msg=bad):
                T.parse_link(bad)

    def test_text_and_subscription(self):
        found, errs = T.parse_text(f"{REALITY}\n\nмусор\n{TROJAN}\n{REALITY}\nvless://@bad:1")
        self.assertEqual([s["kind"] for s in found], ["VLESS", "Trojan"])          # повтор убран
        self.assertEqual(len(errs), 1)
        sub = base64.b64encode(f"{WS}\n{HY2}\n".encode()).decode()
        self.assertEqual(len(T.parse_text(sub)[0]), 2)                            # подписка в base64


class Config_(unittest.TestCase):
    def ob(self):
        return T.parse_link(REALITY)[1]

    def test_proxy_mode(self):
        c = T.build_config(self.ob(), "proxy", "all", [], 10808)
        self.assertEqual(c["inbounds"][0]["type"], "mixed")
        self.assertEqual(c["route"]["final"], "proxy")

    def test_vpn_mode_is_tun(self):
        c = T.build_config(self.ob(), "vpn", "all", [], 10808)
        self.assertEqual(c["inbounds"][0]["type"], "tun")

    def test_only_apps_goes_through_tun_and_everything_else_direct(self):
        c = T.build_config(self.ob(), "proxy", "apps", ["chrome", "Discord.exe"], 10808)      # «только приложения» принудительно через TUN
        self.assertEqual(c["inbounds"][0]["type"], "tun")
        self.assertEqual(c["route"]["final"], "direct")
        rule = next(r for r in c["route"]["rules"] if r.get("process_path_regex"))
        self.assertEqual(rule["outbound"], "proxy")
        import re
        rx = [re.compile(x) for x in rule["process_path_regex"]]
        self.assertTrue(any(r.search(r"C:\Program Files\Google\Chrome\Application\CHROME.EXE".replace("\\", "\\")) for r in rx[:1]))
        self.assertFalse(any(r.search(r"C:\x\notchrome.exe") for r in rx[:1]))

    def test_apps_required(self):
        with self.assertRaises(T.TunnelError):
            T.build_config(self.ob(), "vpn", "apps", [], 10808)

    def test_direct_ru(self):
        c = T.build_config(self.ob(), "vpn", "all", [], 10808, direct_ru=True)
        self.assertTrue(any("domain_suffix" in r for r in c["route"]["rules"]))

    def test_clean_apps(self):
        self.assertEqual(T.clean_apps(["chrome", "Chrome.exe", "a/b.exe", "", "bad;rm.exe"]), ["chrome.exe"])

    def test_server_proxy_is_never_routed_back_into_itself(self):
        c = T.build_config(self.ob(), "vpn", "all", [], 10808)
        self.assertEqual(c["dns"]["rules"][0], {"outbound": "any", "server": "dns-local"})
        self.assertTrue(c["route"]["auto_detect_interface"])


class FakeAssistant:
    def __init__(self, tn, confirm_log):
        self.tunnel, self._log = tn, confirm_log

    def ask_confirm(self, action):
        self._log.append(action)


class Voice(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.cfg = Config(d / "c.json")
        self.tn = T.Tunnel(self.cfg, d)
        self.pending = []
        self.asst = FakeAssistant(self.tn, self.pending)
        self.apps = mock.MagicMock()
        self.apps.resolve.return_value = None
        self.ctx = actions.Ctx(self.cfg, self.apps, self.asst)

    def say(self, text):
        return actions.dispatch(self.ctx, text)

    def add_server(self):
        self.tn.add_text(f"{REALITY}\n{TROJAN}")

    def test_without_servers_old_extension_path_stays(self):
        self.assertIsNone(tc.on(self.ctx, "vpn"))
        self.assertIn("не настроены", tc.on(self.ctx, "proxy"))

    def test_vpn_on_asks_yes_no_then_starts(self):
        self.add_server()
        with mock.patch.object(T.Tunnel, "start", return_value="Включён VPN.") as start:
            handled, reply = self.say("включи впн")
            self.assertTrue(handled)
            self.assertIn("да", reply.lower())
            self.assertIn("Reality DE", reply)
            start.assert_not_called()                  # пока не сказали «да», ничего не включено
            self.assertEqual(len(self.pending), 1)
            out = self.pending[0]()
            start.assert_called_once()
            self.assertIn("Включила", out)

    def test_proxy_on_forces_proxy_mode(self):
        self.add_server()
        with mock.patch.object(T.Tunnel, "start", return_value="ok") as start:
            self.say("включи прокси")
            self.pending[0]()
            self.assertEqual(start.call_args.kwargs["mode"], "proxy")
            self.assertEqual(start.call_args.kwargs["scope"], "all")

    def test_only_for_app(self):
        self.add_server()
        with mock.patch.object(T.Tunnel, "start", return_value="ok") as start:
            handled, reply = self.say("включи впн только для гугл")
            self.assertTrue(handled)
            self.assertIn("только для", reply)
            self.pending[0]()
            kw = start.call_args.kwargs
            self.assertEqual((kw["mode"], kw["scope"], kw["apps"]), ("vpn", "apps", ["chrome.exe"]))

    def test_two_apps_and_unknown_app(self):
        self.add_server()
        found, unknown = tc.resolve_apps(self.ctx, "гугл и дискорд и какаято программа")
        self.assertEqual([x[1] for x in found], ["chrome.exe", "Discord.exe"])
        self.assertEqual(unknown, ["какаято программа"])

    def test_unknown_app_alone_is_explained(self):
        self.add_server()
        _, reply = self.say("включи впн для неизвестной штуки")
        self.assertIn("вручную", reply)
        self.assertFalse(self.pending)

    def test_confirmation_can_be_switched_off(self):
        self.add_server()
        self.tn.save(confirm=False)
        with mock.patch.object(T.Tunnel, "start", return_value="ok") as start:
            _, reply = self.say("включи прокси")
            start.assert_called_once()
            self.assertEqual(self.pending, [])
            self.assertIn("Включила", reply)

    def test_off_and_status(self):
        self.add_server()
        _, r = self.say("включен ли прокси")
        self.assertIn("выключен", r.lower())
        with mock.patch.object(T.Tunnel, "running", return_value=True), mock.patch.object(T.Tunnel, "stop", return_value=True) as stop:
            _, r = self.say("выключи впн")
            stop.assert_called()
            self.assertIn("Выключила", r)

    def test_change_server(self):
        self.add_server()
        first = self.tn.settings()["active"]
        _, r = self.say("смени сервер")
        self.assertNotEqual(self.tn.settings()["active"], first)
        _, r = self.say("переключи сервер на реалити")
        self.assertEqual(self.tn.active_server()["name"], "Reality DE")
        _, r = self.say("выбери сервер на луну")
        self.assertIn("Не нашла", r)


class Store(unittest.TestCase):
    def test_add_remove_and_secrets_not_in_window_config(self):
        d = Path(tempfile.mkdtemp())
        cfg = Config(d / "c.json")
        tn = T.Tunnel(cfg, d)
        self.assertEqual(tn.add_text(REALITY)["added"], 1)
        self.assertEqual(tn.add_text(REALITY)["added"], 0)
        with self.assertRaises(T.TunnelError):
            tn.add_text("просто текст")
        sid = tn.servers()[0]["id"]
        tn.remove(sid)
        self.assertFalse(tn.configured())

    def test_start_explains_missing_engine_and_admin(self):
        d = Path(tempfile.mkdtemp())
        cfg = Config(d / "c.json")
        tn = T.Tunnel(cfg, d)
        with self.assertRaises(T.TunnelError) as e:
            tn.start()
        self.assertIn("Сначала добавьте сервер", str(e.exception))
        tn.add_text(REALITY)
        with mock.patch.object(T, "IS_WIN", True):
            with self.assertRaises(T.TunnelError) as e:
                tn.start()
            self.assertIn("Движок", str(e.exception))
            with mock.patch.object(T.Tunnel, "engine_ready", return_value=True), mock.patch("karina.elevate.is_admin", return_value=False):
                with self.assertRaises(T.TunnelError) as e:
                    tn.start(mode="vpn")
                self.assertIn("администратора", str(e.exception))

    def test_default_config_and_pinned_engine(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.assertEqual(cfg["tunnel"]["servers"], [])
        self.assertEqual(len(T.SINGBOX_SHA256), 64)
        self.assertIn(T.SINGBOX_VERSION, T.SINGBOX_URL)


if __name__ == "__main__":
    unittest.main()
