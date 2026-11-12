"""Голос Silero без torch и без скачивания модели: подставная модель проверяет разбиение текста, запись wav, выбор голоса и запасные пути."""
import sys, tempfile, unittest, wave
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from karina import silero_voice as sv, tts, net
from karina.config import Config


class FakeModel:
    def __init__(self, fail_ssml=False):
        self.calls, self.fail_ssml = [], fail_ssml
    def apply_tts(self, text=None, ssml_text=None, speaker="xenia", sample_rate=48000, **kw):
        if ssml_text and self.fail_ssml:
            raise ValueError("ssml не поддерживается")
        self.calls.append((text, ssml_text, speaker, sample_rate))
        return np.sin(np.linspace(0, 440, sample_rate // 2)).astype(np.float32) * 0.5      # полсекунды звука


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = Config(self.tmp / "c.json")
        sv.model_path(self.tmp).parent.mkdir(parents=True)
        sv.model_path(self.tmp).write_bytes(b"0" * 2_000_000)
        self.model = FakeModel()
        self.pt = [mock.patch.object(sv, "_load", lambda base: self.model), mock.patch.object(sv, "torch_available", lambda: True)]
        for p in self.pt: p.start()

    def tearDown(self):
        for p in self.pt: p.stop()

    def test_wav(self):
        out = str(self.tmp / "o.wav")
        sv.synth_wav(self.tmp, "Привет! Меня зовут Кари́на.", out, "baya")
        with wave.open(out) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 48000))
            self.assertGreater(w.getnframes(), 24000)
        self.assertEqual(self.model.calls[0][0], "Привет! Меня зовут Кар+ина.")
        self.assertEqual(self.model.calls[0][2], "baya")

    def test_rate_uses_ssml_and_falls_back(self):
        out = str(self.tmp / "o.wav")
        sv.synth_wav(self.tmp, "Тест & <проверка>", out, "xenia", 3)
        self.assertIn('rate="fast"', self.model.calls[0][1]); self.assertIn("&amp;", self.model.calls[0][1])
        self.model.fail_ssml = True; self.model.calls.clear()
        sv.synth_wav(self.tmp, "Тест", out, "xenia", -5)
        self.assertEqual(self.model.calls[0][0], "Тест")

    def test_long_text_is_chunked(self):
        out = str(self.tmp / "o.wav")
        sv.synth_wav(self.tmp, "Это предложение. " * 80, out)
        self.assertGreater(len(self.model.calls), 2)
        self.assertTrue(all(len(c[0]) <= sv.MAX_CHUNK for c in self.model.calls))

    def test_unknown_speaker_defaults(self):
        sv.synth_wav(self.tmp, "Тест", str(self.tmp / "o.wav"), "nobody")
        self.assertEqual(self.model.calls[0][2], "xenia")

    def test_speaker_selected_and_played(self):
        self.cfg.data["tts"]["voice"] = "silero_aidar"
        sp = tts.Speaker(self.cfg)
        played = []
        with mock.patch.object(sp, "_play", lambda path: played.append(path)), mock.patch.object(net, "is_online", lambda: True), \
             mock.patch.object(sp, "_edge", side_effect=AssertionError("онлайн-голос не должен вызываться")):
            sp.speak("Готово.")
        self.assertEqual(sp.engine_used, "silero")
        self.assertEqual(self.model.calls[0][2], "aidar")
        self.assertEqual(len(played), 1)

    def test_not_downloaded_falls_back(self):
        sv.model_path(self.tmp).unlink()
        self.cfg.data["tts"]["voice"] = "silero_xenia"
        sp = tts.Speaker(self.cfg)
        edge = []
        with mock.patch.object(net, "is_online", lambda: True), mock.patch.object(sp, "_edge", lambda text, vid: edge.append(vid)):
            sp.speak("Готово.")
        self.assertEqual(edge, ["svetlana_cute"])             # не silero-id, а обычный голос Microsoft
        self.assertIn("не скачан", sp.warning)

    def test_no_torch_message(self):
        self.pt[1].stop()
        self.cfg.data["tts"]["voice"] = "silero_xenia"
        sp = tts.Speaker(self.cfg)
        with mock.patch.object(sv, "torch_available", lambda: False), mock.patch.object(net, "is_online", lambda: False), mock.patch.object(sp, "_offline"):
            sp.speak("Готово.")
        self.assertIn("torch", sp.warning)
        self.pt[1].start()

    def test_online_only_ignores_silero(self):
        self.cfg.data["tts"].update(voice="silero_xenia", engine="online")
        sp = tts.Speaker(self.cfg)
        edge = []
        with mock.patch.object(net, "is_online", lambda: True), mock.patch.object(sp, "_edge", lambda text, vid: edge.append(vid)):
            sp.speak("Готово.")
        self.assertEqual(edge, ["svetlana_cute"])

    def test_all_voice_ids_valid(self):
        for vid, v in tts.VOICES.items():
            self.assertTrue(v["label"] and v["voice"], vid)
            if v.get("engine") == "silero":
                self.assertIn(v["speaker"], sv.SPEAKERS)
            tts.voice_params({"voice": vid, "rate": 0})


if __name__ == "__main__":
    unittest.main()
