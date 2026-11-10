"""Загрузка модели распознавания: офлайн-кэш, понятная ошибка без доступа к huggingface.co, прогресс скачивания."""
import sys, types, unittest, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import stt
from karina.config import Config


def fake_fw(missing_first=True, calls=None):
    mod = types.ModuleType("faster_whisper")
    class WhisperModel:
        def __init__(self, name, **kw):
            (calls if calls is not None else []).append(kw.get("local_files_only", False))
            if kw.get("local_files_only") and missing_first:
                raise OSError("нет файлов модели")
    mod.WhisperModel = WhisperModel
    return mod


class T(unittest.TestCase):
    def make(self):
        return stt.STT(Config(Path(tempfile.mkdtemp()) / "c.json"))

    def test_cached_model_loads_without_network(self):
        calls = []
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw(False, calls)}), mock.patch.object(stt, "hf_reachable", side_effect=AssertionError("сеть не нужна")):
            s = self.make(); s.load()
        self.assertIsNotNone(s.model); self.assertEqual(calls, [True])

    def test_unreachable_gives_clear_error(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw()}), mock.patch.object(stt, "hf_reachable", return_value=(False, "timed out")):
            s = self.make()
            with self.assertRaises(RuntimeError) as c: s.load()
        self.assertIn("huggingface.co недоступен", str(c.exception)); self.assertIn("VPN", str(c.exception)); self.assertIsNone(s.model)

    def test_downloads_when_reachable(self):
        calls = []
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_fw(True, calls)}), mock.patch.object(stt, "hf_reachable", return_value=(True, "")):
            s = self.make(); s.load(progress=lambda mb: None)
        self.assertEqual(calls, [True, False]); self.assertIsNotNone(s.model)


if __name__ == "__main__":
    unittest.main()
