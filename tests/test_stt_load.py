"""Загрузка модели распознавания: проверка в отдельном процессе, офлайн-кэш, понятные ошибки, падение нативной библиотеки."""
import os, sys, tempfile, textwrap, types, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import stt
from karina.config import Config


def fake_fw():
    mod = types.ModuleType("faster_whisper")
    class WhisperModel:
        def __init__(self, name, **kw): self.kw = kw
    mod.WhisperModel = WhisperModel
    return mod


class T(unittest.TestCase):
    def make(self):
        return stt.STT(Config(Path(tempfile.mkdtemp()) / "c.json"))

    def load(self, probes, reach=(True, ""), **kw):
        """probes — ответы run_probe по очереди: сначала проверка локальной модели, потом скачивание."""
        calls = []
        def fake(name, root, download, progress=None, timeout=None):
            calls.append(download); return probes.pop(0)
        s = self.make()
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw()}), mock.patch.object(stt, "run_probe", fake), \
             mock.patch.object(stt, "hf_reachable", return_value=reach):
            try: s.load(**kw)
            finally: self.calls = calls
        return s

    def test_cached_model_loads_without_network(self):
        calls = []
        def fake(name, root, download, progress=None, timeout=None): calls.append(download); return ("ok", "")
        s = self.make()
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw()}), mock.patch.object(stt, "run_probe", fake), \
             mock.patch.object(stt, "hf_reachable", side_effect=AssertionError("сеть не нужна")):
            s.load()
        self.assertIsNotNone(s.model); self.assertEqual(calls, [False]); self.assertTrue(s.model.kw["local_files_only"])

    def test_unreachable_gives_clear_error(self):
        with self.assertRaises(RuntimeError) as c:
            self.load([("missing", "нет файлов")], reach=(False, "timed out"))
        self.assertIn("huggingface.co недоступен", str(c.exception)); self.assertIn("VPN", str(c.exception))

    def test_downloads_when_reachable(self):
        s = self.load([("missing", "нет"), ("ok", "")], progress=lambda mb: None)
        self.assertEqual(self.calls, [False, True]); self.assertIsNotNone(s.model)

    def test_native_crash_does_not_load_in_process(self):
        with self.assertRaises(RuntimeError) as c:
            self.load([("crash", "процессор не поддерживает нужные инструкции [код 0xC000001D]")])
        self.assertIn("аварийно", str(c.exception)); self.assertIn("0xC000001D", str(c.exception))


class RealProcess(unittest.TestCase):
    """Настоящий дочерний процесс с подменённой faster_whisper."""
    def run_with(self, fw_code, mode="local"):
        d = Path(tempfile.mkdtemp()); (d / "faster_whisper.py").write_text(textwrap.dedent(fw_code), encoding="utf-8")
        env = {"PYTHONPATH": str(d) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        with mock.patch.dict(os.environ, env):
            return stt.run_probe("small", d / "models", download=(mode == "download"), timeout=60)

    def test_ok(self):
        self.assertEqual(self.run_with("""
            class WhisperModel:
                def __init__(self, *a, **k): pass
                def transcribe(self, *a, **k): return iter(()), None
        """), ("ok", ""))

    def test_missing(self):
        st, why = self.run_with("""
            class WhisperModel:
                def __init__(self, *a, local_files_only=False, **k):
                    if local_files_only: raise OSError('нет файлов')
        """)
        self.assertEqual(st, "missing"); self.assertIn("нет файлов", why)

    def test_hard_crash_is_reported_not_fatal(self):
        st, why = self.run_with("""
            import os
            class WhisperModel:
                def __init__(self, *a, **k): os._exit(-1073741795 if os.name == 'nt' else 139)   # 0xC000001D как знаковое int
        """)
        self.assertEqual(st, "crash"); self.assertTrue(why)
        if os.name == "nt": self.assertIn("0xC000001D", why)

    def test_python_error_in_transcribe(self):
        st, why = self.run_with("""
            class WhisperModel:
                def __init__(self, *a, **k): pass
                def transcribe(self, *a, **k): raise RuntimeError('boom')
        """)
        self.assertEqual(st, "error"); self.assertIn("пробное распознавание", why); self.assertIn("boom", why)


if __name__ == "__main__":
    unittest.main()
