"""Подписка VPN: сервер отдаёт список ссылок не всем клиентам — Карина перебирает «User-Agent» и объясняет, что не так."""
import base64, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import tunnel

LINK = "vless://11111111-2222-3333-4444-555555555555@example.com:443?security=tls&sni=example.com&type=tcp#Test"


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/sub"


class Sub(unittest.TestCase):
    def run_with(self, responder):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                code, body = responder(self.headers.get("User-Agent", ""))
                self.send_response(code); self.end_headers(); self.wfile.write(body.encode())
            def log_message(self, *a): pass
        srv, url = serve(H)
        try:
            return url, srv
        finally:
            pass

    def fetch(self, responder):
        url, srv = self.run_with(responder)
        try:
            return tunnel.fetch_subscription(url)
        finally:
            srv.shutdown(); srv.server_close()

    def test_plain_links(self):
        self.assertIn("vless://", self.fetch(lambda ua: (200, LINK)))

    def test_base64_body(self):
        body = base64.b64encode(LINK.encode()).decode()
        found, _ = tunnel.parse_text(self.fetch(lambda ua: (200, body)))
        self.assertEqual(len(found), 1)

    def test_only_a_known_client_gets_the_list(self):
        text = self.fetch(lambda ua: (200, LINK) if ua.startswith("Hiddify") else (200, "<html>Welcome</html>"))
        self.assertIn("vless://", text)

    def test_forbidden_for_unknown_client_then_ok(self):
        text = self.fetch(lambda ua: (200, LINK) if ua.startswith("v2rayN") else (403, ""))
        self.assertIn("vless://", text)

    def test_html_page_is_explained(self):
        with self.assertRaises(tunnel.TunnelError) as c:
            self.fetch(lambda ua: (200, "<!doctype html><html></html>"))
        self.assertIn("веб-страница", str(c.exception))

    def test_json_is_explained(self):
        with self.assertRaises(tunnel.TunnelError) as c:
            self.fetch(lambda ua: (200, '{"outbounds": []}'))
        self.assertIn("JSON", str(c.exception))

    def test_error_code_is_reported(self):
        with self.assertRaises(tunnel.TunnelError) as c:
            self.fetch(lambda ua: (404, ""))
        self.assertIn("404", str(c.exception))

    def test_curl_fallback_when_python_cannot_connect(self):
        import urllib.error
        from unittest import mock
        with mock.patch.object(tunnel.urllib.request, "urlopen", side_effect=urllib.error.URLError("ssl")), \
             mock.patch.object(tunnel, "_fetch_curl", return_value=(200, LINK)) as c:
            self.assertIn("vless://", tunnel.fetch_subscription("https://example.com/sub"))
        c.assert_called()

    def test_network_error_message_when_curl_fails_too(self):
        import urllib.error
        from unittest import mock
        with mock.patch.object(tunnel.urllib.request, "urlopen", side_effect=urllib.error.URLError("нет сети")), \
             mock.patch.object(tunnel, "_fetch_curl", side_effect=OSError("curl не найден")):
            with self.assertRaises(tunnel.TunnelError) as c:
                tunnel.fetch_subscription("https://example.com/sub")
        self.assertIn("нет сети", str(c.exception))

    def test_device_headers_are_sent_and_server_needing_them_works(self):
        seen = {}
        def responder(ua):
            return (200, LINK)
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.update({k.lower(): v for k, v in self.headers.items()})
                ok = self.headers.get("x-hwid") and self.headers.get("x-device-os") and self.headers.get("x-device-model")
                self.send_response(200 if ok else 403); self.end_headers(); self.wfile.write((LINK if ok else "device required").encode())
            def log_message(self, *a): pass
        srv, url = serve(H)
        try:
            with self.assertRaises(tunnel.TunnelError):
                tunnel.fetch_subscription(url)                          # без заголовков устройства сервер отказывает
            self.assertIn("vless://", tunnel.fetch_subscription(url, "a" * 32))
        finally:
            srv.shutdown(); srv.server_close()
        self.assertEqual(seen["x-hwid"], "a" * 32); self.assertEqual(seen["x-device-model"], "Karina")

    def test_panel_explanation_is_shown(self):
        with self.assertRaises(tunnel.TunnelError) as c:
            self.fetch(lambda ua: (403, "Лимит устройств исчерпан"))
        self.assertIn("Лимит устройств", str(c.exception))

    def test_hwid_is_created_once_and_kept(self):
        import tempfile
        from karina.config import Config
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        t = tunnel.Tunnel(cfg, Path(tempfile.mkdtemp()))
        h = t._hwid()
        self.assertRegex(h, r"^[0-9a-f]{32}$")
        self.assertEqual(tunnel.Tunnel(cfg, Path(tempfile.mkdtemp()))._hwid(), h)

    def test_bad_address(self):
        with self.assertRaises(tunnel.TunnelError):
            tunnel.fetch_subscription("ftp://x")


if __name__ == "__main__":
    unittest.main()
