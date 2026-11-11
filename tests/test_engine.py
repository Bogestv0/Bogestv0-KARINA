"""Движок и загрузка моделей без настоящей модели: подставной сервер и подставной «Hugging Face»."""
import hashlib, json, subprocess, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import engine, models
from karina.config import Config

FAKE = str(Path(__file__).resolve().parent / "fake_llama_server.py")
DATA = bytes(range(256)) * 4096            # 1 МБ


class FakeHF(BaseHTTPRequestHandler):
    files = {}
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/api/models/"):
            items = [{"type": "file", "path": n, "size": len(d), "lfs": {"oid": hashlib.sha256(d).hexdigest(), "size": len(d)}} for n, d in self.files.items()]
            b = json.dumps(items).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        name = self.path.split("/resolve/main/")[1]
        d = self.files[name]
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(d) - start)); self.end_headers(); self.wfile.write(d[start:])


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._md = models.MODELS_DIR
        models.MODELS_DIR = self.tmp / "llm"
        engine.DATA = self.tmp
        self.cfg = Config(self.tmp / "c.json")

    def tearDown(self):
        models.MODELS_DIR = self._md

    def test_pick_file(self):
        items = [{"type": "file", "path": p, "size": 1} for p in (
            "gemma-4-E4B-it-Q4_K_M.gguf", "gemma-4-E4B-it-Q4_K_M-MTP.gguf", "mmproj-F16.gguf", "gemma-4-E4B-it-UD-Q4_K_XL.gguf", "README.md")]
        self.assertEqual(models.pick_file(items, "Q4_K_M")["path"], "gemma-4-E4B-it-Q4_K_M.gguf")
        items2 = [{"type": "file", "path": p, "size": 1} for p in ("gemma-UD-Q4_K_M.gguf", "gemma-Q4_K_M.gguf")]
        self.assertEqual(models.pick_file(items2, "UD-Q4_K_M")["path"], "gemma-UD-Q4_K_M.gguf")
        with self.assertRaises(RuntimeError):
            models.pick_file(items, "Q9_K")

    def test_recommended(self):
        self.assertEqual(models.recommended_id(8), "qwen35-4b")
        self.assertEqual(models.recommended_id(32), "gemma4-26b")
        self.assertEqual(models.recommended_id(16), "qwen35-9b")
        self.assertEqual(models.recommended_id(64), "gemma4-26b")        # модели для кода в общую рекомендацию не попадают
        self.assertEqual([m["id"] for m in models.CATALOG if m.get("coder")], ["qwen25-coder-7b", "qwen3-coder-30b", "qwen38-27b"])

    def _hf(self, files):
        FakeHF.files = files
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeHF)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        models.HF = f"http://127.0.0.1:{srv.server_address[1]}"
        self.addCleanup(setattr, models, "HF", "https://huggingface.co")

    def test_download_resume_verify(self):
        self._hf({"Qwen3.5-4B-Q4_K_M.gguf": DATA})
        part = models.MODELS_DIR / "Qwen3.5-4B-Q4_K_M.gguf.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(DATA[:300000])                       # прошлая загрузка оборвалась
        seen = []
        p = models.download("qwen35-4b", lambda d, t, s: seen.append(s))
        self.assertEqual(p.read_bytes(), DATA)
        self.assertEqual(models.local_path("qwen35-4b"), p)
        self.assertIn("verify", seen); self.assertEqual(seen[-1], "done")
        self.assertIn("qwen35-4b", models.installed_ids())
        self.assertTrue(models.delete("qwen35-4b")); self.assertIsNone(models.local_path("qwen35-4b"))

    def test_download_bad_checksum_and_cancel(self):
        self._hf({"Qwen3.5-4B-Q4_K_M.gguf": DATA})
        FakeHF.files = {"Qwen3.5-4B-Q4_K_M.gguf": DATA}
        ev = threading.Event(); ev.set()
        with self.assertRaises(models.Cancelled):
            models.download("qwen35-4b", cancel=ev)
        # повреждённый файл в .part того же размера
        part = models.MODELS_DIR / "Qwen3.5-4B-Q4_K_M.gguf.part"
        part.write_bytes(b"x" * len(DATA))
        with self.assertRaises(RuntimeError):
            models.download("qwen35-4b")
        self.assertFalse(part.exists())

    def _fake_engine(self):
        mp = models.MODELS_DIR / "m.gguf"
        mp.parent.mkdir(parents=True, exist_ok=True); mp.write_bytes(b"x")
        models._write_index({"qwen35-4b": "m.gguf"})
        self.cfg.update({"models": {"active": "qwen35-4b"}})
        ev = []
        e = engine.Engine(self.cfg, ev.append)
        e.launcher = lambda args: subprocess.Popen([sys.executable, FAKE] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(e.stop)
        return e, ev

    def test_engine_chat_and_think_stripped(self):
        e, ev = self._fake_engine()
        deltas = []
        out = e.chat([{"role": "user", "content": "привет мир"}], on_delta=deltas.append)
        self.assertEqual(out, "Ответ: привет мир")
        self.assertNotIn("секрет", out)
        self.assertTrue(all("<think>" not in d for d in deltas))
        self.assertEqual(e.state, "ready")
        self.assertEqual([x["state"] for x in ev if x["type"] == "engine"][:2], ["starting", "ready"])
        port = e.port
        e.chat([{"role": "user", "content": "ещё"}])
        self.assertEqual(e.port, port)                        # второй вопрос — тот же процесс
        e.stop(); self.assertEqual(e.state, "stopped")

    def test_engine_requires_key_and_model(self):
        e, _ = self._fake_engine()
        e.ensure()
        import urllib.request, urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{e.port}/health")
        e2 = engine.Engine(Config(self.tmp / "c2.json"))
        with self.assertRaises(engine.EngineError):
            e2.chat([{"role": "user", "content": "x"}])       # модель не выбрана

    def test_engine_start_failure_reports(self):
        mp = models.MODELS_DIR / "m.gguf"; mp.parent.mkdir(parents=True, exist_ok=True); mp.write_bytes(b"x")
        models._write_index({"qwen35-4b": "m.gguf"})
        self.cfg.update({"models": {"active": "qwen35-4b", "gpu": "cpu"}})
        e = engine.Engine(self.cfg)
        e.launcher = lambda args: subprocess.Popen([sys.executable, "-c", "print('boom'); raise SystemExit(3)"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with self.assertRaises(engine.EngineError) as c:
            e.start("qwen35-4b")
        self.assertIn("boom", str(c.exception)); self.assertEqual(e.state, "error")

    def test_strip_think(self):
        self.assertEqual(engine.strip_think("a<think>x</think>b"), "ab")
        self.assertEqual(engine.strip_think("a<think>не закрыто"), "a")

    def test_args(self):
        e = engine.Engine(self.cfg)
        e.port, e.key = 1234, "k"
        a = e._args(Path("m.gguf"), models.by_id("gemma4-26b"), "vulkan")
        self.assertIn("--cpu-moe", a); self.assertEqual(a[a.index("-ngl") + 1], "99")
        self.assertEqual(e._args(Path("m.gguf"), models.by_id("qwen35-4b"), "cpu")[a.index("-ngl") + 1], "0")


if __name__ == "__main__":
    unittest.main()
