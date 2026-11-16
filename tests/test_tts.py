"""Голос Карины (Silero «Байя»): единственный голос, словарь произношений, запасной системный голос — без сети и звука."""
import os, pathlib, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import tts
from karina.config import Config


def speaker(**t):
    cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
    cfg.update({"tts": t})
    return cfg, tts.Speaker(cfg)


class TestVoices(unittest.TestCase):
    def test_only_one_voice_karina_is_silero_baya(self):
        self.assertEqual(list(tts.VOICES), ["karina"])
        v = tts.VOICES["karina"]
        self.assertEqual((v["label"], v["engine"], v["speaker"]), ("Карина", "silero", "baya"))
        self.assertEqual(list(tts.silero_voice.SPEAKERS), ["baya"])

    def test_ui_has_no_voice_choice(self):
        html = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text("utf-8")
        for old in ("svetlana_cute", "silero_xenia", "silero_aidar", "dmitry", 'name="voice"', 'name="engine"', 'name="offline"', "offDl"):
            self.assertNotIn(old, html)

    def test_default_config_voice_is_karina_and_old_config_loads(self):
        d = Path(tempfile.mkdtemp()) / "c.json"
        d.write_text('{"tts": {"voice": "silero_xenia", "engine": "online", "rate": 1}}', "utf-8")   # конфиг прошлой версии
        cfg = Config(d)
        self.assertEqual(cfg["tts"]["rate"], 1)
        self.assertIn("discord", cfg["tts"]["pronounce"])
        self.assertEqual(Config(Path(tempfile.mkdtemp()) / "n.json")["tts"]["voice"], "karina")

    def test_assistant_forces_karina_voice(self):
        import inspect
        from karina import assistant
        self.assertIn('"voice": "karina"', inspect.getsource(assistant.Assistant.__init__))


class TestPrep(unittest.TestCase):
    def test_pronounce_whole_words_ignore_case(self):
        t = {"pronounce": {"discord": "дискорд", "steam": "стим"}}
        self.assertEqual(tts.prep_text("Открываю «Discord» и Steam.", t), "Открываю дискорд и стим.")
        self.assertEqual(tts.prep_text("Steamer остаётся", t), "Steamer остаётся")
        self.assertEqual(tts.prep_text("  привет ", {}), "привет")

    def test_replacement_with_backslash_is_literal(self):
        self.assertEqual(tts.prep_text("ab", {"pronounce": {"ab": r"a\1b"}}), r"a\1b")


class TestSpeakFlow(unittest.TestCase):
    def test_karina_speaks_with_silero_without_internet_or_system_voice(self):
        cfg, sp = speaker()
        with mock.patch.object(tts.silero_voice, "is_ready", return_value=True), mock.patch.object(tts.silero_voice, "torch_available", return_value=True), \
             mock.patch.object(tts.silero_voice, "synth_wav", side_effect=lambda base, text, out, spk, rate: Path(out).write_text(text, encoding="utf-8")) as syn, \
             mock.patch.object(tts.Speaker, "_play"), mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Готово.")
        self.assertEqual(sp.engine_used, "silero")
        self.assertEqual(syn.call_args[0][3], "baya")
        sapi.assert_not_called()

    def test_not_downloaded_temporarily_uses_system_voice(self):
        cfg, sp = speaker()
        with mock.patch.object(tts.silero_voice, "is_ready", return_value=False), mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Готово.")
        sapi.assert_called_once()
        self.assertEqual(sp.engine_used, "offline")
        self.assertIn("Silero", sp.warning)

    def test_no_online_voices_left(self):
        self.assertFalse(hasattr(tts.Speaker, "_edge"))
        self.assertFalse(hasattr(tts.Speaker, "prewarm"))
        self.assertFalse(hasattr(tts.Speaker, "_offline"))


class TestStreaming(unittest.TestCase):
    """Длинный ответ озвучивается по предложениям: первое — сразу, остальное готовится, пока играет предыдущее."""

    def make(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"tts": {"voice": "karina"}})
        return cfg, tts.Speaker(cfg)

    def test_split_speech_first_sentence_alone(self):
        text = "Привет, это первое предложение. " + "Это ещё одно длинное предложение про погоду и планы на вечер. " * 6
        parts = tts.split_speech(text, 220)
        self.assertEqual(parts[0], "Привет, это первое предложение.")
        self.assertTrue(all(len(p) <= 220 for p in parts))
        self.assertEqual(tts.split_speech("Коротко."), ["Коротко."])
        self.assertEqual(tts.split_speech("   "), [])

    def test_pipeline_plays_in_order_and_overlaps(self):
        cfg, sp = self.make()
        order, events = [], []
        sp._play = lambda path: (events.append(("play", Path(path).read_text(encoding="utf-8"))), time.sleep(0.15))

        def synth(part, tmp):
            events.append(("synth", part))
            Path(tmp).write_text(part, encoding="utf-8")
            return tmp, True
        played, err = sp._pipeline(["раз", "два", "три"], synth)
        self.assertEqual((played, err), (3, None))
        self.assertEqual([e[1] for e in events if e[0] == "play"], ["раз", "два", "три"])
        # синтез второго куска начался до того, как доиграл первый: в журнале «synth два» раньше «play три»
        self.assertLess(events.index(("synth", "два")), events.index(("play", "три")))

    def test_pipeline_error_on_first_part_reports_nothing_played(self):
        cfg, sp = self.make()
        sp._play = mock.MagicMock()

        def synth(part, tmp):
            raise RuntimeError("нет модели")
        played, err = sp._pipeline(["раз", "два"], synth)
        self.assertEqual(played, 0)
        self.assertIsInstance(err, RuntimeError)
        sp._play.assert_not_called()

    def test_stop_interrupts_and_cleans_temp_files(self):
        cfg, sp = self.make()
        made = []

        def synth(part, tmp):
            made.append(tmp)
            Path(tmp).write_text(part, encoding="utf-8")
            return tmp, True

        def play(path):
            sp.stop()                                # «замолчи» посреди речи
        sp._play = play
        played, err = sp._pipeline(["раз", "два", "три", "четыре"], synth)
        self.assertEqual(played, 1)
        time.sleep(0.2)
        self.assertTrue(all(not Path(m).exists() for m in made), "временные записи должны удаляться")

    def test_play_error_does_not_hang(self):
        cfg, sp = self.make()
        sp._play = mock.MagicMock(side_effect=OSError("устройство пропало"))
        done = []
        th = threading.Thread(target=lambda: done.append(sp._pipeline(["а"] * 6, lambda p, t: (Path(t).write_text(p, encoding="utf-8"), (t, True))[1])), daemon=True)
        th.start(); th.join(5)
        self.assertTrue(done, "конвейер завис")
        self.assertIsInstance(done[0][1], OSError)

    def test_silero_not_ready_falls_back_with_hint(self):
        cfg, sp = self.make()
        with mock.patch.object(tts.silero_voice, "is_ready", return_value=False):
            self.assertFalse(sp._silero("Привет.", tts.VOICES["karina"]))
        self.assertIn("Silero", sp.warning)

    def test_silero_speaks_through_pipeline(self):
        cfg, sp = self.make()
        spoken = []
        sp._play = lambda path: spoken.append(Path(path).read_text(encoding="utf-8"))
        with mock.patch.object(tts.silero_voice, "is_ready", return_value=True), mock.patch.object(tts.silero_voice, "torch_available", return_value=True), \
             mock.patch.object(tts.silero_voice, "synth_wav", side_effect=lambda base, text, out, spk, rate: Path(out).write_text(text, encoding="utf-8")):
            long = "Первое предложение. " + "Второе предложение достаточно длинное, чтобы разбить ответ на части. " * 8
            self.assertTrue(sp._silero(long, tts.VOICES["karina"]))
        self.assertEqual(spoken[0], "Первое предложение.")
        self.assertGreater(len(spoken), 2)
        self.assertEqual(sp.warning, "")

if __name__ == "__main__":
    unittest.main()


class StressMark(unittest.TestCase):
    def test_accent_only_for_silero(self):
        from karina import config
        t = config.DEFAULTS["tts"]
        self.assertEqual(tts.prep_text("Привет, Карина!", t), "Привет, Карина!")                 # системный голос: без знака ударения
        self.assertIn("́", tts.prep_text("Привет, Карина!", t, stress=True))
        from karina import silero_voice
        self.assertIn("Кар+ина", silero_voice.to_plus_stress(tts.prep_text("Карина", t, stress=True)))