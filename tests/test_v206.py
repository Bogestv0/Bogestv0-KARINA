"""1.20.6: погода из искажённой речи, ответ «да» не теряется, состояние «Жду ответ», голос в комплекте, без синей кромки острова."""
import sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest import mock

from karina import actions, assistant as asst, silero_voice, weather
from karina.config import Config
from karina.textutil import norm

ROOT = Path(__file__).resolve().parent.parent


class WeatherHeardWrong(unittest.TestCase):
    def city(self, phrase):
        return weather.parse_request(norm(phrase))

    def test_po_godu_and_pogodov_are_weather(self):
        self.assertEqual(self.city("скажи по-году в Кемерово"), ("кемерово", "today"))
        self.assertEqual(self.city("Погодов Кемерова"), ("кемерова", "today"))
        self.assertEqual(self.city("прогноз омск завтра"), ("омск", "tomorrow"))

    def test_no_city_means_profile_city(self):
        for p in ("какая погода сегодня", "погода на неделю", "погода у меня", "нужен ли зонт"):
            self.assertEqual(self.city(p)[0], "", p)

    def test_genitive_city_finds_nominative(self):
        self.assertIn("кемерово", weather.city_variants("кемерова"))
        self.assertIn("кемерово", weather.city_variants("кемерове"))

    def test_command_runs_for_misheard_phrase(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "config.json")
        for phrase in ("скажи по-году в Кемерово", "Погодов Кемерова", "скажи по году в кемерово"):
            with mock.patch.object(weather, "report", return_value=("Кемерово, сейчас минус 3 градуса.", False)) as rep:
                ctx = actions.Ctx(cfg, mock.MagicMock(), mock.MagicMock(), raw=phrase)
                handled, reply = actions.dispatch(ctx, norm(phrase))
            self.assertTrue(handled, phrase)
            self.assertIn("Кемерово", reply)
            self.assertIn("кемеров", rep.call_args.args[0], phrase)

    def test_fallback_used_when_open_meteo_down(self):
        fake = {"city": "омск", "now": {"t": -5, "feels": -9, "text": "небольшой снег", "wind": 3}, "days": [{"hi": -3, "lo": -8, "text": "снег", "prob": 60}]}
        from karina import island_tools
        with mock.patch.object(weather, "find_city", side_effect=weather.WeatherNetError("нет связи")), mock.patch.object(island_tools, "_weather_week_wttr", return_value=fake):
            text, wet = weather.report("омск")
        self.assertIn("минус 5", text)
        self.assertTrue(wet)
        with mock.patch.object(weather, "find_city", side_effect=weather.WeatherNetError("нет связи")), mock.patch.object(island_tools, "_weather_week_wttr", side_effect=OSError("нет")):
            with self.assertRaises(weather.WeatherError):
                weather.report("омск")


class Harness(unittest.TestCase):
    def make(self):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = Config(Path(tempfile.mkdtemp()) / "config.json")
        a._pending = a._choice = None
        a._conf = None
        a.said, a.events, a.logs = [], [], []
        a.say = a.said.append
        a.emit = a.events.append
        a.log = lambda k, t: a.logs.append((k, t))
        a._last_state = None
        a.sleeping = False
        a.listening = True
        a.followup_until = a.ptt_until = 0.0
        return a


class AnswerIsNotLost(Harness):
    def test_short_yes_with_low_confidence_is_accepted_while_waiting(self):
        a = self.make()
        a.ask_confirm(lambda: "Хорошо, поищу.")
        a._pending["until"] = time.time() + 20
        a._conf = 0.25                          # короткое «да» Whisper слышит неуверенно; раньше порога 0,5 не хватало
        self.assertFalse(a._is_background("Да."))
        a._conf = 0.05
        self.assertTrue(a._is_background("Да."))
        self.assertTrue(any(k == "info" and "Жду" in t for k, t in a.logs))   # а то, что отбросила, видно человеку

    def test_low_confidence_still_rejected_when_not_waiting(self):
        a = self.make()
        a._conf = 0.25
        self.assertTrue(a._is_background("карина открой блокнот"))

    def test_unrelated_phrase_is_shown_not_silently_dropped(self):
        a = self.make()
        a.ask_confirm(lambda: "ок")
        a._pending["until"] = time.time() + 20
        a.cfg.wake_names = lambda: ["карина"]
        a.handle("расскажи про кошек")
        self.assertTrue(any(k == "info" and "расскажи про кошек" in t for k, t in a.logs))
        self.assertIsNotNone(a._pending)

    def test_speech_extends_wait(self):
        a = self.make()
        a.ask_confirm(lambda: "ок")
        a._pending["until"] = time.time() + 2
        a.cfg.wake_names = lambda: ["карина"]
        a.handle("мммм ну")
        self.assertGreater(a._pending["until"], time.time() + 8)

    def test_yes_runs_action(self):
        a = self.make()
        a.ask_confirm(lambda: "Хорошо, поищу в интернете.")
        a._pending["until"] = time.time() + 20
        a.cfg.wake_names = lambda: ["карина"]
        a.handle("да")
        self.assertEqual(a.said, ["Хорошо, поищу в интернете."])


class WaitingState(Harness):
    def test_confirm_state_while_question_open(self):
        a = self.make()
        a.ask_confirm(lambda: "ок")
        a._rest_state()
        self.assertEqual(a.events[-1]["state"], "confirm")
        a._pending = None
        a._rest_state()
        self.assertEqual(a.events[-1]["state"], "idle")

    def test_question_does_not_echo_request(self):
        a = self.make()
        a.chat = mock.MagicMock()
        a.chat.preflight.return_value = ""
        a.chat.busy = False
        a.chat.will_search.return_value = True
        a.repeat_gate = lambda t: ("ok", "")
        a._to_chat("скажи погоду в кемерово")
        self.assertIn("посмотрю в интернете", a.said[0])
        self.assertNotIn("кемерово", a.said[0])


class VoiceBundled(unittest.TestCase):
    def test_bundled_model_wins_and_is_not_downloaded(self):
        tmp = Path(tempfile.mkdtemp())
        fake = tmp / "app" / "voices" / f"silero_{silero_voice.MODEL_ID}.pt"
        fake.parent.mkdir(parents=True)
        fake.write_bytes(b"0" * 1_200_000)
        with mock.patch.object(silero_voice, "APP_ROOT", tmp / "app"):
            self.assertTrue(silero_voice.has_bundled())
            self.assertEqual(silero_voice.model_path(tmp / "data"), fake)
            self.assertTrue(silero_voice.is_ready(tmp / "data"))
            with mock.patch.object(silero_voice, "download") as dl:
                self.assertTrue(silero_voice.ensure_model(tmp / "data"))
                dl.assert_not_called()
            silero_voice.delete(tmp / "data")                      # удаляет только скачанную копию
            self.assertTrue(fake.exists())

    def test_build_ships_the_voice(self):
        spec = (ROOT / "build" / "karina.spec").read_text("utf-8")
        wf = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
        self.assertIn("voices", spec)
        self.assertIn("fetch_voice.py", wf)
        self.assertLess(wf.index("fetch_voice.py"), wf.index("pyinstaller build/karina.spec"))
        self.assertTrue((ROOT / "build" / "fetch_voice.py").exists())


class IslandHasNoBlueRim(unittest.TestCase):
    def test_no_colored_inset_ring_on_island_edge(self):
        css = (ROOT / "ui" / "island.html").read_text("utf-8")
        self.assertNotIn("#rim::after", css)
        self.assertIn("confirm:", css)


if __name__ == "__main__":
    unittest.main()
