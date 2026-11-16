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

    def test_bad_address(self):
        with self.assertRaises(tunnel.TunnelError):
            tunnel.fetch_subscription("ftp://x")


if __name__ == "__main__":
    unittest.main()
