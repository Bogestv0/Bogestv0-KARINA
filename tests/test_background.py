"""Защита от ложных срабатываний: фон (телевизор, чужая речь, шум) не должен выполняться как команда."""
import sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import assistant as asst
from karina.config import Config
from karina.wake import split_wake, is_name


class Names(unittest.TestCase):
    def setUp(self):
        self.names = Config(Path(tempfile.mkdtemp()) / "c.json").wake_names()

    def test_real_calls_still_work(self):
        for p in ("Карина, открой хром", "эй карина сделай тише", "открой дискорд, Карина", "Карина", "каринка включи музыку"):
            self.assertTrue(split_wake(p, self.names)[0], p)

    def test_other_people_names_do_not_wake(self):
        for p in ("Карен пойдём домой", "Карим открой дверь", "Марина открой блокнот", "Карл у Клары украл"):
            self.assertFalse(split_wake(p, self.names)[0], p)
        self.assertFalse(is_name("карен", self.names))

    def test_name_at_the_end_only_in_short_phrase(self):
        long = "вчера мы с друзьями ходили в кино и потом долго обсуждали фильм вместе с Кариной"
        self.assertFalse(split_wake("мы вчера ходили в кино и потом долго обсуждали фильм вместе с Карина", self.names)[0])
        self.assertTrue(split_wake("открой хром Карина", self.names)[0])
        self.assertTrue(split_wake("открой хром Карина", self.names, tail_words=7)[0])
        self.assertFalse(split_wake("а потом она сказала мне привет Карина", self.names, tail_words=5)[0])


class Gate(unittest.TestCase):
    def make(self, wake_required=True, **kw):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"wake_required": wake_required, **kw})
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg, a._pending, a._choice, a.followup_until, a._conf = cfg, None, None, 0.0, None
        a.logged = []
        a.log = lambda kind, text: a.logged.append((kind, text))
        return a

    def test_named_confident_command_passes(self):
        a = self.make(); a._conf = 0.8
        self.assertFalse(a._is_background("Карина, открой хром"))

    def test_named_but_unclear_is_dropped(self):
        a = self.make(); a._conf = 0.2
        self.assertTrue(a._is_background("Карина, открой хром"))
        self.assertEqual(a.logged[-1][0], "ignored")

    def test_without_name_outside_followup_is_left_to_handle_to_ignore(self):
        a = self.make(); a._conf = 0.9
        self.assertFalse(a._is_background("сегодня хорошая погода"))     # дальше handle() сам запишет «не мне»

    def test_followup_accepts_short_clear_command(self):
        a = self.make(wake_strict=False); a._conf = 0.8; a.followup_until = time.time() + 5
        self.assertFalse(a._is_background("сделай потише"))

    def test_followup_rejects_unclear_background(self):
        a = self.make(wake_strict=False); a._conf = 0.45; a.followup_until = time.time() + 5     # выше порога с именем, но ниже порога для фразы без имени
        self.assertTrue(a._is_background("сделай потише"))

    def test_followup_rejects_long_speech(self):
        a = self.make(wake_strict=False); a._conf = 0.9; a.followup_until = time.time() + 5
        speech = "и тогда он сказал что завтра мы обязательно пойдём туда вместе со всеми друзьями и родными"
        self.assertTrue(a._is_background(speech))
        self.assertIn("длинная", a.logged[-1][1])

    def test_yes_no_answer_is_not_blocked(self):
        a = self.make(); a._conf = 0.5; a._pending = (lambda: "ok", time.time() + 10)
        self.assertFalse(a._is_background("да"))

    def test_without_wake_requirement_only_confidence_matters(self):
        a = self.make(wake_required=False); a._conf = 0.9
        self.assertFalse(a._is_background("открой хром"))
        a._conf = 0.1
        self.assertTrue(a._is_background("открой хром"))

    def test_unknown_confidence_does_not_block(self):
        a = self.make(); a._conf = None
        self.assertFalse(a._is_background("Карина, открой хром"))

    def test_thresholds_are_configurable(self):
        a = self.make(); a._conf = 0.3
        a.cfg.update({"stt": {"min_conf": 0.2}})
        self.assertFalse(a._is_background("Карина, открой хром"))


class Junk(unittest.TestCase):
    def test_common_hallucinations_are_filtered(self):
        from karina import stt
        for j in ("ставьте лайк", "спасибо за внимание", "музыка играет"):
            self.assertTrue(any(x in j for x in stt._JUNK), j)


if __name__ == "__main__":
    unittest.main()
