"""Строгое имя, подтверждение перед нейросетью/интернетом, чистый виджет, фильтр зацикливания."""
import sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import assistant as asst
from karina.config import Config
from karina.textutil import is_loop
from karina.wake import is_name, split_wake


class Loop(unittest.TestCase):
    def test_screenshot_case(self):
        self.assertTrue(is_loop("Карина, " + "закрой, " * 40))
        self.assertTrue(is_loop("закрой закрой закрой закрой"))

    def test_phrase_loops(self):
        self.assertTrue(is_loop("открой блокнот открой блокнот открой блокнот"))

    def test_normal_speech_passes(self):
        for t in ("Карина, закрой хром", "нет нет нет", "громкость пятьдесят процентов", "да да", "Карина"):
            self.assertFalse(is_loop(t), t)


class Name(unittest.TestCase):
    NAMES = Config(Path(tempfile.mkdtemp()) / "c.json").wake_names()

    def test_clear_name_and_declensions(self):
        for w in ("карина", "карину", "карине", "каринка"):
            self.assertTrue(is_name(w, self.NAMES), w)

    def test_similar_words_do_not_wake(self):
        for w in ("марина", "кармина", "карен", "кар", "рина", "корзина", "каприна", "ирина"):
            self.assertFalse(is_name(w, self.NAMES), w)

    def test_name_in_the_middle_does_not_wake(self):
        self.assertFalse(split_wake("я вчера говорил с Кариной про это дело сегодня утром", self.NAMES)[0])


def make(**cfgpatch):
    cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
    if cfgpatch:
        cfg.update(cfgpatch)
    a = asst.Assistant.__new__(asst.Assistant)
    a.cfg, a._pending, a._choice, a.followup_until, a._conf = cfg, None, None, 0.0, None
    a.sleeping, a.said, a.logs, a.events = False, [], [], []
    a.say = lambda text, shown=None: a.said.append(text)
    a.emit = a.events.append
    a.memory = mock.MagicMock(); a.memory.recall.return_value = None
    a.apps = mock.MagicMock()
    a.brain = mock.MagicMock(); a.brain.usable.return_value = False
    a.chat = mock.MagicMock(); a.chat.busy = False; a.chat.preflight.return_value = ""
    a.chat.will_search.return_value = False; a.chat.voice_chat_id.return_value = "c1"
    return a


class Strict(unittest.TestCase):
    def test_no_name_means_silence_and_nothing_in_widget(self):
        a = make()
        a.handle("открой блокнот")
        self.assertEqual(a.said, [])
        self.assertEqual([e for e in a.events if e.get("type") == "log"], [])       # «не ко мне» в окно не попадает

    def test_followup_window_is_closed_after_a_reply(self):
        a = make()
        a.followup_until = time.time() + 5          # раньше после любой реплики окно открывалось само
        a.handle("открой блокнот")
        self.assertEqual(a.said, [])

    def test_followup_window_still_works_if_strict_is_off(self):
        a = make(wake_strict=False)
        a.followup_until = time.time() + 5
        with mock.patch.object(asst.actions, "dispatch", return_value=(True, "Готово.")):
            a.handle("открой блокнот")
        self.assertEqual(a.said, ["Готово."])

    def test_name_alone_opens_one_shot_window_and_shows_called(self):
        a = make()
        a.handle("Карина")
        self.assertEqual(a.said, ["Слушаю."])
        self.assertIn("called", [e.get("state") for e in a.events if e.get("type") == "state"])
        with mock.patch.object(asst.actions, "dispatch", return_value=(True, "Готово.")):
            a.handle("открой блокнот")                  # команда без имени — один раз можно
            a.handle("закрой блокнот")                  # второй раз — уже нужно имя
        self.assertEqual(a.said, ["Слушаю.", "Готово."])

    def test_loop_text_is_background(self):
        a = make(); a._conf = 0.9
        self.assertTrue(a._is_background("Карина, " + "закрой, " * 30))

    def test_bare_name_needs_higher_confidence(self):
        a = make(); a._conf = 0.55
        self.assertTrue(a._is_background("Карина"))
        self.assertFalse(a._is_background("Карина, открой хром"))

    def test_widget_state_is_not_repeated(self):
        a = make(); a.listening = True
        a._set_state("idle"); a._set_state("idle"); a._set_state("idle")
        self.assertEqual(len([e for e in a.events if e.get("type") == "state"]), 1)


class Confirm(unittest.TestCase):
    def test_model_question_asks_first_and_no_cancels(self):
        a = make()
        a._to_chat("сколько будет два плюс два")
        self.assertIn("Спросить у нейросети", a.said[0])
        a.chat.send.assert_not_called()
        a.handle("нет")
        self.assertEqual(a.said[-1], "Хорошо, не буду.")
        a.chat.send.assert_not_called()

    def test_stop_word_cancels(self):
        a = make()
        a._to_chat("сколько будет два плюс два")
        a.handle("замолчи")
        self.assertEqual(a.said[-1], "Хорошо, отменяю.")
        a.chat.send.assert_not_called()

    def test_yes_sends(self):
        a = make()
        a._to_chat("сколько будет два плюс два")
        a.handle("да")
        a.chat.send.assert_called_once()

    def test_search_asks_and_yes_searches(self):
        a = make(); a.chat.will_search.return_value = True
        a._to_chat("найди в интернете рецепт борща")
        self.assertIn("в интернете", a.said[0]); a.chat.send.assert_not_called()
        a.handle("да")
        self.assertTrue(a.chat.send.call_args.kwargs["search"])

    def test_typed_text_is_not_asked(self):
        a = make()
        a._to_chat("привет", ask=False)
        a.chat.send.assert_called_once()

    def test_can_be_turned_off(self):
        a = make(confirm_model=False)
        a._to_chat("сколько будет два плюс два")
        a.chat.send.assert_called_once()

    def test_unrecognised_command_asks_before_llm(self):
        a = make()
        with mock.patch.object(asst.actions, "dispatch", return_value=(False, "")), \
             mock.patch.object(a, "_llm_ready", return_value=True), mock.patch.object(a, "_try_llm") as llm:
            a.handle("Карина, сделай что-нибудь странное")
            llm.assert_not_called()
            self.assertIn("Спросить у нейросети", a.said[-1])

    def test_noise_does_not_make_her_reprompt(self):
        a = make(); a.ask_confirm(lambda: "ok"); a._pending["until"] = time.time() + 20
        a.handle("мммм ну")
        self.assertEqual(a.said, [])


if __name__ == "__main__":
    unittest.main()
