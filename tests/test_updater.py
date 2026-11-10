"""Обновление: сравнение версий, выбор файла релиза, загрузка с проверкой SHA-256."""
import hashlib, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import updater

BODY = b"MZ" + b"x" * 100000


def rel(tag, **kw):
    base = "https://github.com/Bogestv0/Bogestv0-KARINA/releases/download/" + tag + "/"
    r = {"tag_name": tag, "body": "## Что нового\n* штука", "html_url": "https://github.com/Bogestv0/Bogestv0-KARINA/releases/tag/" + tag,
         "assets": [{"name": "Bogestv0-KARINA-Setup-%s.exe" % tag.lstrip("v"), "browser_download_url": base + "Setup.exe", "size": len(BODY)},
                    {"name": "SHA256SUMS.txt", "browser_download_url": base + "SHA256SUMS.txt"}]}
    r.update(kw)
    return r


class T(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.is_newer("v1.2.0", "1.1.0"))
        self.assertTrue(updater.is_newer("v1.10.0", "1.9.9"))
        self.assertFalse(updater.is_newer("v1.1.0", "1.1.0"))
        self.assertFalse(updater.is_newer("v1.0.5", "1.1.0"))
        self.assertFalse(updater.is_newer("мусор", "1.1.0"))

    def test_parse_release(self):
        r = updater.parse_release(rel("v1.2.0"), "1.1.0")
        self.assertTrue(r["available"]); self.assertEqual(r["latest"], "1.2.0"); self.assertTrue(r["url"].endswith("Setup.exe"))
        self.assertFalse(updater.parse_release(rel("v1.1.0"), "1.1.0")["available"])

    def test_foreign_host_rejected(self):
        evil = rel("v1.2.0")
        evil["assets"][0]["browser_download_url"] = "https://evil.example.com/Setup.exe"
        r = updater.parse_release(evil, "1.1.0")
        self.assertTrue(r["available"]); self.assertEqual(r["url"], "")          # установить нельзя, только открыть страницу

    def _serve(self, exe, sums):
        class H(BaseHTTPRequestHandler):
            def log_message(s, *a): pass
            def do_GET(s):
                b = sums.encode() if s.path.endswith("txt") else exe
                s.send_response(200); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def _info(self, sha, exe=BODY):
        base = self._serve(exe, f"{sha}  Bogestv0-KARINA-Setup-1.2.0.exe\n")
        old = updater._safe_url
        updater._safe_url = lambda u: True
        self.addCleanup(setattr, updater, "_safe_url", old)
        updater.UPD_DIR = Path(tempfile.mkdtemp())
        return {"url": base + "/Setup.exe", "sha_url": base + "/SHA256SUMS.txt", "name": "Bogestv0-KARINA-Setup-1.2.0.exe"}

    def test_download_ok(self):
        p = updater.download(self._info(hashlib.sha256(BODY).hexdigest()))
        self.assertEqual(p.read_bytes(), BODY)

    def test_download_bad_hash(self):
        info = self._info("0" * 64)
        with self.assertRaises(RuntimeError):
            updater.download(info)
        self.assertEqual(list(updater.UPD_DIR.glob("*.exe")), [])

    def test_name_path_traversal(self):
        info = self._info(hashlib.sha256(BODY).hexdigest())
        info["name"] = "../../evil.exe"
        with self.assertRaises(RuntimeError):                       # имени нет в SHA256SUMS
            updater.download(info)


if __name__ == "__main__":
    unittest.main()
