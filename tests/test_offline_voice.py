"""Офлайн-голос Piper, трей и единственный экземпляр — без сети, звука и Windows (подставной «Hugging Face», подставные piper/pystray)."""
import hashlib, json, sys, tempfile, threading, types, unittest, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import offline_voice as ov, single, tray, tts
from karina.config import Config

SUB = ov.SUB
ONNX = bytes(range(256)) * 2048            # 512 КБ
CONF = json.dumps({"audio": {"sample_rate": 22050}}).encode()


class FakeHF(BaseHTTPRequestHandler):
    onnx_sha = hashlib.sha256(ONNX).hexdigest()
    conf = CONF
    def log_message(self, *a): pass
    def do_GET(self):
        files = {f"{SUB}/{ov.NAME}.onnx": ONNX, f"{SUB}/{ov.NAME}.onnx.json": self.conf}
        if self.path.startswith("/api/models/"):
            items = [{"type": "file", "path": p, "size": len(d), **({"lfs": {"oid": self.onnx_sha, "size": len(d)}} if p.endswith(".onnx") else {})}
                     for p, d in files.items()]
            b = json.dumps(items).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        d = files[self.path.split("/resolve/main/")[1]]
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(d) - start)); self.end_headers(); self.wfile.write(d[start:])


class Server:
    def setUp(self):
        FakeHF.onnx_sha = hashlib.sha256(ONNX).hexdigest(); FakeHF.conf = CONF
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeHF)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        p = mock.patch.object(ov, "HF", f"http://127.0.0.1:{self.srv.server_port}"); p.start(); self.addCleanup(p.stop)
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)


class TestDownload(Server, unittest.TestCase):
    def test_download_ok(self):
        seen = []
        ov.download(self.base, lambda d, t, s: seen.append(s))
        self.assertTrue(ov.is_ready(self.base))
        self.assertEqual(ov.paths(self.base)[0].read_bytes(), ONNX)
        self.assertIn("verify", seen); self.assertEqual(seen[-1], "done")
        self.assertEqual(list(ov.voice_dir(self.base).glob("*.part")), [])

    def test_resume_partial(self):
        onnx, _ = ov.paths(self.base); onnx.parent.mkdir(parents=True)
        onnx.with_name(onnx.name + ".part").write_bytes(ONNX[:100000])
        ov.download(self.base)
        self.assertEqual(onnx.read_bytes(), ONNX)

    def test_bad_checksum_removes_file(self):
        FakeHF.onnx_sha = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "Контрольная сумма"):
            ov.download(self.base)
        self.assertFalse(ov.is_ready(self.base)); self.assertFalse(ov.paths(self.base)[0].exists())

    def test_cancel(self):
        c = threading.Event(); c.set()
        with self.assertRaises(ov.Cancelled):
            ov.download(self.base, cancel=c)
        self.assertFalse(ov.is_ready(self.base))

    def test_broken_config_is_not_ready(self):
        FakeHF.conf = b"{ not json"
        with self.assertRaises(RuntimeError):
            ov.download(self.base)
        self.assertFalse(ov.is_ready(self.base))

    def test_delete(self):
        ov.download(self.base); ov.delete(self.base)
        self.assertFalse(ov.is_ready(self.base))


class FakeVoice:
    last = None
    def __init__(self): FakeVoice.last = self
    @classmethod
    def load(cls, model, config_path=None): v = cls(); v.model = model; return v
    def synthesize_wav(self, text, wf, syn_config=None):
        self.text, self.cfg = text, syn_config
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050); wf.writeframes(b"\0\0" * 100)


class OldVoice:
    """piper-tts 1.2: нет synthesize_wav, есть synthesize(text, wav, length_scale=…)."""
    last = None
    @classmethod
    def load(cls, model, config_path=None): cls.last = cls(); return cls.last
    def synthesize(self, text, wf, length_scale=None):
        self.text, self.ls = text, length_scale
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050); wf.writeframes(b"\0\0")


class SynthCfg:
    def __init__(self, length_scale=1.0): self.length_scale = length_scale


class TestSynth(Server, unittest.TestCase):
    def setUp(self):
        super().setUp()
        ov.download(self.base); ov.unload()
        self.out = str(self.base / "o.wav")

    def piper(self, voice_cls, with_cfg=True):
        mod = types.SimpleNamespace(PiperVoice=voice_cls)
        if with_cfg:
            mod.SynthesisConfig = SynthCfg
        return mock.patch.dict(sys.modules, {"piper": mod})

    def test_new_api_and_speed(self):
        with self.piper(FakeVoice):
            ov.synth_wav(self.base, "Привет", self.out, speed=5)
        self.assertEqual(FakeVoice.last.text, "Привет")
        self.assertEqual(FakeVoice.last.cfg.length_scale, 0.7)        # быстрее → меньше
        with wave.open(self.out) as w:
            self.assertEqual(w.getnframes(), 100)

    def test_old_api(self):
        ov.unload()
        with self.piper(OldVoice, with_cfg=False):
            ov.synth_wav(self.base, "Привет", self.out, speed=-5)
        self.assertEqual(OldVoice.last.ls, 1.3)

    def test_not_downloaded_raises(self):
        ov.delete(self.base)
        with self.piper(FakeVoice), self.assertRaises(FileNotFoundError):
            ov.synth_wav(self.base, "x", self.out)

    def test_piper_missing_raises(self):
        ov.unload()
        with mock.patch.dict(sys.modules, {"piper": None}), self.assertRaises(ImportError):
            ov.synth_wav(self.base, "x", self.out)


class TestSpeakerOffline(Server, unittest.TestCase):
    def make(self, **t):
        cfg = Config(self.base / "config.json"); cfg.update({"tts": {"engine": "offline", **t}})
        return cfg, tts.Speaker(cfg)

    def test_uses_piper_when_ready(self):
        ov.download(self.base); ov.unload()
        cfg, sp = self.make()
        played = []
        with mock.patch.dict(sys.modules, {"piper": types.SimpleNamespace(PiperVoice=FakeVoice, SynthesisConfig=SynthCfg)}), \
             mock.patch.object(tts.Speaker, "_play", lambda s, p: played.append(p)), mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Привет")
        self.assertEqual(sp.engine_used, "piper"); sapi.assert_not_called()
        self.assertTrue(played[0].endswith(".wav"))
        self.assertFalse(Path(played[0]).exists())                   # временный файл убран

    def test_system_choice_or_not_downloaded_uses_sapi(self):
        cfg, sp = self.make()                                        # не скачан
        with mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Привет")
        sapi.assert_called_once(); self.assertEqual(sp.engine_used, "offline")
        ov.download(self.base)
        cfg, sp = self.make(offline="system")                        # скачан, но выбран системный
        with mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Привет")
        sapi.assert_called_once()

    def test_piper_failure_falls_back_with_warning(self):
        ov.download(self.base); ov.unload()
        cfg, sp = self.make()
        with mock.patch.dict(sys.modules, {"piper": None}), mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Привет")
        sapi.assert_called_once(); self.assertIn("Офлайн-голос не запустился", sp.warning)

    def test_online_fail_then_neural_offline(self):
        ov.download(self.base); ov.unload()
        cfg, sp = self.make(engine="auto")
        with mock.patch.object(tts.net, "is_online", return_value=False), \
             mock.patch.dict(sys.modules, {"piper": types.SimpleNamespace(PiperVoice=FakeVoice, SynthesisConfig=SynthCfg)}), \
             mock.patch.object(tts.Speaker, "_play"):
            sp.speak("Новый ответ, которого нет в кэше")
        self.assertEqual(sp.engine_used, "piper")


class TestTray(unittest.TestCase):
    def fake_modules(self, icon_cls):
        pystray = types.SimpleNamespace(Icon=icon_cls, Menu=lambda *a: a, MenuItem=lambda *a, **k: (a, k))
        pil = types.ModuleType("PIL"); pil.Image = types.SimpleNamespace(open=lambda p: object())
        return mock.patch.dict(sys.modules, {"pystray": pystray, "PIL": pil, "PIL.Image": pil.Image})

    def test_start_notify_stop(self):
        class Icon:
            HAS_NOTIFICATION = True
            def __init__(self, *a): self.notes, self.stopped, self.ran = [], False, False
            def run_detached(self): self.ran = True
            def notify(self, m, t): self.notes.append(m)
            def stop(self): self.stopped = True
        with self.fake_modules(Icon):
            t = tray.Tray("T", Path("x.png"), lambda: None, lambda: None)
            self.assertTrue(t.start()); self.assertTrue(t.active)
            icon = t._icon
            t.notify("hi"); t.stop()
        self.assertTrue(icon.ran); self.assertEqual(icon.notes, ["hi"]); self.assertTrue(icon.stopped); self.assertFalse(t.active)

    def test_failure_means_no_tray(self):
        class Icon:
            def __init__(self, *a): raise RuntimeError("нет оболочки")
        logs = []
        with self.fake_modules(Icon):
            t = tray.Tray("T", Path("x.png"), lambda: None, lambda: None, logs.append)
            self.assertFalse(t.start())
        self.assertFalse(t.active); self.assertIn("Трей не запущен", logs[0])
        t.notify("x"); t.stop()                                       # безопасно без значка

    def test_missing_pystray(self):
        with mock.patch.dict(sys.modules, {"pystray": None}):
            self.assertFalse(tray.Tray("T", Path("x.png"), lambda: None, lambda: None).start())


class TestSingle(unittest.TestCase):
    def test_non_windows_never_blocks(self):
        with mock.patch.object(single.sys, "platform", "linux"):
            self.assertTrue(single.acquire(wait=1))
            single.ask_existing_to_show(); single.listen_for_show(lambda: None)


if __name__ == "__main__":
    unittest.main()
