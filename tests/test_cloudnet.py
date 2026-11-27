"""Облачные модели через VPN-сервер: когда идут напрямую, когда через отдельный прокси; без Windows, сети и sing-box."""
import sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import tunnel as T, cloudnet, claude
from karina.config import Config
from test_tunnel import REALITY  # noqa: E402


def make(servers=True, cloud=True):
    base = Path(tempfile.mkdtemp())
    cfg = Config(base / "c.json")
    tn = T.Tunnel(cfg, base)
    if servers:
        tn.add_text(REALITY)
    tn.save(cloud=cloud)
    return tn


class Route(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(T, "IS_WIN", True)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_off_in_settings_goes_direct(self):
        tn = make(cloud=False)
        with mock.patch.object(T.Tunnel, "cloud_start") as st:
            self.assertIsNone(tn.cloud_route())
            st.assert_not_called()

    def test_no_server_goes_direct_with_hint(self):
        tn = make(servers=False)
        self.assertIsNone(tn.cloud_route())
        self.assertIn("ссылку", tn.cloud_problem)

    def test_no_engine_goes_direct_with_hint(self):
        tn = make()
        self.assertIsNone(tn.cloud_route())
        self.assertIn("движок", tn.cloud_problem)

    def test_starts_own_proxy_without_touching_computer_vpn(self):
        tn = make()
        with mock.patch.object(T.Tunnel, "engine_ready", return_value=True), mock.patch.object(T.Tunnel, "cloud_start", return_value="127.0.0.1:10908") as st, \
                mock.patch.object(T.Tunnel, "start") as main, mock.patch.object(T, "set_system_proxy") as sysp:
            self.assertEqual(tn.cloud_route(), "127.0.0.1:10908")
            st.assert_called_once()
            main.assert_not_called()
            sysp.assert_not_called()
            self.assertEqual(tn.cloud_problem, "")

    def test_whole_pc_vpn_already_on_needs_no_second_proxy(self):
        tn = make()
        for info in ({"scope": "all", "mode": "vpn"}, {"scope": "all", "mode": "proxy"}):
            with mock.patch.object(T.Tunnel, "status", return_value={"running": True, **info}), mock.patch.object(T.Tunnel, "cloud_start") as st:
                self.assertIsNone(tn.cloud_route())
                st.assert_not_called()

    def test_vpn_for_apps_only_does_not_cover_cloud(self):
        tn = make()
        with mock.patch.object(T.Tunnel, "engine_ready", return_value=True), mock.patch.object(T.Tunnel, "status", return_value={"running": True, "scope": "apps"}), \
                mock.patch.object(T.Tunnel, "cloud_start", return_value="127.0.0.1:10908"):
            self.assertEqual(tn.cloud_route(), "127.0.0.1:10908")

    def test_start_failure_is_explained_not_raised(self):
        tn = make()
        with mock.patch.object(T.Tunnel, "engine_ready", return_value=True), mock.patch.object(T.Tunnel, "cloud_start", side_effect=T.TunnelError("порт занят")):
            self.assertIsNone(tn.cloud_route())
            self.assertIn("порт занят", tn.cloud_problem)

    def test_proxy_config_is_local_only_and_proxy_only(self):
        _, ob = T.parse_link(REALITY)
        c = T.build_config(ob, "proxy", "all", [], 10908, False)
        self.assertEqual(c["inbounds"][0]["type"], "mixed")
        self.assertEqual(c["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(c["route"]["final"], "proxy")


class Opener(unittest.TestCase):
    def tearDown(self):
        cloudnet.bind(None)

    def proxies(self, url):
        """Прокси, которые применит запрос к url ({} — напрямую). ProxyHandler без адресов в список обработчиков не попадает."""
        out = {}
        for h in cloudnet.opener_for(url).handlers:
            if h.__class__.__name__ == "ProxyHandler":
                out.update(h.proxies)
        return out

    def test_local_addresses_are_always_direct(self):
        cloudnet.bind(mock.Mock(cloud_route=mock.Mock(return_value="127.0.0.1:10908")))
        for u in ("http://localhost:11434/v1", "http://192.168.1.5:1234/v1", "http://10.0.0.2/v1", "http://172.20.1.1/v1", "http://127.0.0.1:8080", "http://nas/v1"):
            self.assertEqual(self.proxies(u), {}, u)

    def test_cloud_goes_through_proxy_when_routed(self):
        cloudnet.bind(mock.Mock(cloud_route=mock.Mock(return_value="127.0.0.1:10908")))
        px = self.proxies("https://generativelanguage.googleapis.com/v1beta/openai/models")
        self.assertEqual(px["https"], "http://127.0.0.1:10908")

    def test_cloud_direct_when_not_routed_or_unbound(self):
        for tn in (None, mock.Mock(cloud_route=mock.Mock(return_value=None))):
            cloudnet.bind(tn)
            self.assertNotIn("10908", str(self.proxies("https://api.anthropic.com/v1/messages")))

    def test_router_failure_never_breaks_request(self):
        cloudnet.bind(mock.Mock(cloud_route=mock.Mock(side_effect=RuntimeError("x"))))
        self.assertTrue(cloudnet.opener_for("https://api.anthropic.com"))

    def test_hint_reaches_error_text(self):
        cloudnet.bind(mock.Mock(cloud_problem="Добавьте ссылку."))
        self.assertIn("Добавьте ссылку.", cloudnet.note())
        self.assertIn("Добавьте ссылку.", claude.ClaudeClient(Config(Path(tempfile.mkdtemp()) / "c.json"))._explain(403, ""))
        cloudnet.bind(None)
        self.assertEqual(cloudnet.note(), "")


class Lifecycle(unittest.TestCase):
    def test_stop_cloud_is_safe_when_not_running_and_kills_when_running(self):
        tn = make()
        self.assertFalse(tn.stop_cloud())
        proc = mock.Mock(); proc.poll.return_value = None
        tn._cproc = proc
        self.assertTrue(tn.stop_cloud())
        proc.terminate.assert_called_once()
        self.assertIsNone(tn._cproc)

    def test_settings_default_and_ui_switch(self):
        self.assertTrue(T.DEFAULTS["cloud"])
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.assertTrue(cfg["tunnel"]["cloud"])


if __name__ == "__main__":
    unittest.main()
