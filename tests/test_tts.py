"""Голоса: параметры edge-tts, словарь произношений, запасной голос — без сети и звука."""
import os, pathlib, sys, tempfile, unittest
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
    def test_params_both_voices(self):
        self.assertEqual(tts.voice_params({"voice": "svetlana_cute"}), ("ru-RU-SvetlanaNeural", "+4%", "+24Hz"))
        self.assertEqual(tts.voice_params({"voice": "ava_calm"}), ("en-US-AvaMultilingualNeural", "-4%", "+10Hz"))

    def test_slider_adds_and_clamps(self):
        self.assertEqual(tts.voice_params({"voice": "ava_calm", "rate": 2})[1], "+16%")
        self.assertEqual(tts.voice_params({"voice": "ava_calm", "rate": -5})[1], "-54%")
        self.assertEqual(tts.voice_params({"voice": "svetlana_cute", "rate": 99})[1], "+60%")

    def test_unknown_or_old_config_falls_back(self):
        self.assertEqual(tts.voice_params({"voice": "нет такого"})[0], "ru-RU-SvetlanaNeural")
        self.assertEqual(tts.voice_params({"voice_online": "ru-RU-SvetlanaNeural"})[0], "ru-RU-SvetlanaNeural")

    def test_ui_options_match_catalog(self):
        html = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text("utf-8")
        for vid in tts.VOICES:
            self.assertIn(f'<option value="{vid}">', html)

    def test_default_config_has_voice_and_old_config_loads(self):
        d = Path(tempfile.mkdtemp()) / "c.json"
        d.write_text('{"tts": {"voice_online": "ru-RU-DariyaNeural", "rate": 1}}', "utf-8")   # конфиг прошлой версии
        cfg = Config(d)
        self.assertEqual(cfg["tts"]["voice"], "svetlana_cute")
        self.assertEqual(cfg["tts"]["rate"], 1)
        self.assertIn("discord", cfg["tts"]["pronounce"])


class TestPrep(unittest.TestCase):
    def test_pronounce_whole_words_ignore_case(self):
        t = {"pronounce": {"discord": "дискорд", "steam": "стим"}}
        self.assertEqual(tts.prep_text("Открываю «Discord» и Steam.", t), "Открываю дискорд и стим.")
        self.assertEqual(tts.prep_text("Steamer остаётся", t), "Steamer остаётся")
        self.assertEqual(tts.prep_text("  привет ", {}), "привет")

    def test_replacement_with_backslash_is_literal(self):
        self.assertEqual(tts.prep_text("ab", {"pronounce": {"ab": r"a\1b"}}), r"a\1b")


class TestSpeakFlow(unittest.TestCase):
    def run_speak(self, voice, failing):
        cfg, sp = speaker(voice=voice, engine="auto")
        tried = []

        def fake_edge(text, vid):
            tried.append(vid)
            if vid in failing:
                raise RuntimeError("отклонено")
        with mock.patch.object(tts.net, "is_online", return_value=True), \
             mock.patch.object(tts.Speaker, "_edge", side_effect=fake_edge), \
             mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Привет")
        return tried, sp, sapi

    def test_chosen_voice_used(self):
        tried, sp, sapi = self.run_speak("ava_calm", set())
        self.assertEqual(tried, ["ava_calm"]); self.assertEqual(sp.engine_used, "online"); sapi.assert_not_called()

    def test_rejected_voice_falls_back_to_russian_then_system(self):
        tried, sp, sapi = self.run_speak("ava_calm", {"ava_calm"})
        self.assertEqual(tried, ["ava_calm", "svetlana_cute"]); self.assertEqual(sp.engine_used, "online"); sapi.assert_not_called()
        tried, sp, sapi = self.run_speak("ava_calm", {"ava_calm", "svetlana_cute"})
        self.assertEqual(tried, ["ava_calm", "svetlana_cute"]); self.assertEqual(sp.engine_used, "offline"); sapi.assert_called_once()


class FakeEdge:
    """Подставной edge_tts: «озвучка» пишет байты в файл и считает обращения к серверу."""
    calls = []
    class Communicate:
        def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
            FakeEdge.calls.append((text, voice, rate, pitch)); self.text = text
        async def save(self, path):
            pathlib.Path(path).write_bytes(b"ID3" + self.text.encode("utf-8"))


class TestCache(unittest.TestCase):
    def setUp(self):
        FakeEdge.calls = []
        self.mods = mock.patch.dict(sys.modules, {"edge_tts": FakeEdge})
        self.mods.start(); self.addCleanup(self.mods.stop)
        self.played = []
        pl = mock.patch.object(tts.Speaker, "_play", lambda sp, path: self.played.append(path))
        pl.start(); self.addCleanup(pl.stop)

    def test_online_phrase_is_cached_and_played_offline_in_same_voice(self):
        cfg, sp = speaker(voice="ava_calm", engine="auto")
        with mock.patch.object(tts.net, "is_online", return_value=True):
            sp.speak("Готово.")
        self.assertEqual(len(FakeEdge.calls), 1)
        self.assertEqual(FakeEdge.calls[0][1], "en-US-AvaMultilingualNeural")
        with mock.patch.object(tts.net, "is_online", return_value=False), \
             mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Готово.")
        self.assertEqual(len(FakeEdge.calls), 1)          # сервер не трогали
        self.assertEqual(len(self.played), 2); self.assertEqual(self.played[0], self.played[1])
        sapi.assert_not_called(); self.assertEqual(sp.engine_used, "online")

    def test_other_voice_or_speed_is_a_different_record(self):
        cfg, sp = speaker(voice="ava_calm", engine="auto")
        with mock.patch.object(tts.net, "is_online", return_value=True):
            sp.speak("Готово.")
        cfg.update({"tts": {"voice": "svetlana_cute"}})
        with mock.patch.object(tts.net, "is_online", return_value=False), mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Готово.")
        sapi.assert_called_once()                         # записи другого голоса нет → системный

    def test_offline_engine_ignores_cache(self):
        cfg, sp = speaker(voice="ava_calm", engine="auto")
        with mock.patch.object(tts.net, "is_online", return_value=True):
            sp.speak("Готово.")
        cfg.update({"tts": {"engine": "offline"}})
        with mock.patch.object(tts.Speaker, "_sapi") as sapi:
            sp.speak("Готово.")
        sapi.assert_called_once()

    def test_long_text_not_cached(self):
        cfg, sp = speaker(voice="svetlana_cute", engine="auto")
        with mock.patch.object(tts.net, "is_online", return_value=True):
            sp.speak("Длинный ответ. " * 20)
        self.assertFalse(list(sp.cache_dir.glob("*.mp3")) if sp.cache_dir.exists() else [])
        self.assertEqual(len(self.played), 1)

    def test_prewarm_fills_cache_skips_existing_and_stops_offline(self):
        cfg, sp = speaker(voice="svetlana_cute", engine="auto")
        with mock.patch.object(tts.net, "is_online", return_value=True):
            sp._prewarm(["Готово.", "Хорошо.", "Готово."])
            self.assertEqual(len(FakeEdge.calls), 2)
            sp._prewarm(["Готово.", "Слушаю."])
            self.assertEqual(len(FakeEdge.calls), 3)      # только новая
        n = len(FakeEdge.calls)
        with mock.patch.object(tts.net, "is_online", return_value=False):
            sp._prewarm(["Новая фраза."])
        self.assertEqual(len(FakeEdge.calls), n)
        self.assertEqual(len(list(sp.cache_dir.glob("*.mp3"))), 3)
        self.assertEqual(list(sp.cache_dir.glob("*.part")), [])

    def test_prune_keeps_newest(self):
        cfg, sp = speaker(voice="svetlana_cute")
        sp.cache_dir.mkdir(parents=True)
        for i in range(5):
            f = sp.cache_dir / f"{i}.mp3"; f.write_bytes(b"x"); os.utime(f, (1000 + i, 1000 + i))
        with mock.patch.object(tts, "CACHE_MAX_FILES", 2):
            sp._prune()
        self.assertEqual(sorted(f.name for f in sp.cache_dir.glob("*.mp3")), ["3.mp3", "4.mp3"])

    def test_every_fixed_reply_of_assistant_is_prewarmed(self):
        """Все self.say("…") без подстановок в assistant.py должны быть в STATIC_PHRASES (иначе без сети прозвучат системным голосом)."""
        import re
        karina = Path(__file__).resolve().parent.parent / "karina"
        src = (karina / "assistant.py").read_text("utf-8")
        block = src[src.index("STATIC_PHRASES = ["):src.index("YES = {")]
        said = re.findall(r'self\.say\("([^"{]+)"\)', src)
        self.assertGreater(len(said), 5)
        # чат тоже говорит фиксированными фразами: self.speak("…") и «"…" if … else "…"»
        chat = (karina / "chat.py").read_text("utf-8")
        said += [m for m in re.findall(r'self\.speak\(\s*"([^"{]+?)"(?:\s+if|\s*\))', chat)]
        said += re.findall(r'else "(Готово[^"]+)"', chat)
        for phrase in said:
            self.assertIn(phrase, block, phrase)


if __name__ == "__main__":
    unittest.main()
