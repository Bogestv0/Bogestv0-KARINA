"""1.10.1: громкость только названной дорожки, интернет только по просьбе, память команд, музыка."""
import json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest import mock
import tempfile
from karina import actions, assistant as asst, integrations as I, memory, music, research, winutil
from karina.apps import AppIndex
from karina.actions import Ctx, dispatch
from karina.config import Config
from karina.textutil import norm
from karina.wake import split_wake


def strips(names):
    return [{"i": i, "name": n, "tag": t, "hidden": False} for i, (n, t) in enumerate(names)]


DEFAULT = I.DEFAULT_STRIPS
SWAPPED = [("Микрофон 1", "HW1"), ("Микрофон 2", "HW2"), ("Игры", "V1"), ("Музыка", "V2"), ("Discord", "V3"), ("Браузер", "V4"), ("Донаты", "V5"), ("Браузер 2", "V6")]
LONG = [("Микрофон 1", "HW1"), ("Микрофон 2", "HW2"), ("Игры", "V1"), ("Музыка", "V2"), ("Discord", "V3"), ("Донаты в браузере", "V4"), ("Браузер", "V5"), ("Браузер 2", "V6")]


def who(phrase, names, aliases=None):
    return [s["name"] for s in I.resolve_strips(norm(phrase), strips(names), aliases or {})]


class MixerOnlyNamedTrack(unittest.TestCase):
    def test_browser_alone_in_every_layout(self):
        for names in (DEFAULT, SWAPPED, LONG):
            for p in ("убавь громкость браузера", "прибавь громкость браузера", "громче браузер", "убавь звук в браузере", "убавь громкость миксера браузера"):
                self.assertEqual(who(p, names), ["Браузер"], (p, names[5]))

    def test_name_beats_tag_when_tracks_were_renamed(self):
        # у дорожки с тегом V5 теперь имя «Донаты», а «Браузер» стоит на месте V4: ориентируемся на имя, а не на тег
        self.assertEqual(who("убавь громкость браузера", SWAPPED), ["Браузер"])
        self.assertEqual(who("убавь громкость донатов", SWAPPED), ["Донаты"])

    def test_misheard_word_still_finds_the_track(self):
        for p in ("убавь громкость барузера", "громче барузер"):
            self.assertEqual(who(p, DEFAULT), ["Браузер"], p)
            self.assertEqual(who(p, SWAPPED), ["Браузер"], p)

    def test_several_tracks_only_when_asked(self):
        self.assertEqual(sorted(who("убавь донаты и браузер", DEFAULT)), ["Браузер", "Донаты"])
        self.assertEqual(sorted(who("сделай тише музыку и игры", DEFAULT)), ["Игры", "Музыка"])
        self.assertEqual(who("убавь громкость донатов", DEFAULT), ["Донаты"])

    def test_pairs_still_work(self):
        self.assertEqual(who("убавь второй браузер", DEFAULT), ["Браузер 2"])
        self.assertEqual(who("убавь firefox", DEFAULT), ["Браузер 2"])
        self.assertEqual(sorted(who("заглуши микрофон", DEFAULT)), ["Микрофон 1", "Микрофон 2"])

    def test_own_alias_is_authoritative(self):
        self.assertEqual(who("убавь стримерский шум", DEFAULT, {"Донаты": ["стримерск"]}), ["Донаты"])


class InternetOnlyOnRequest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")

    def say(self, phrase, assistant=None):
        _, rest = split_wake(phrase, self.cfg.wake_names())
        return dispatch(Ctx(self.cfg, mock.MagicMock(), assistant, raw=phrase), rest)

    def test_small_talk_is_answered_without_internet(self):
        for phrase, word in (("Карина, как дела", ""), ("как тебя зовут", "Карина"), ("кто ты", "помощник"), ("как дела сегодня", ""), ("ты меня слышишь", ""),
                             ("сколько тебе лет", "программа"), ("до свидания", ""), ("что ты делаешь", "")):
            with mock.patch.object(actions.webbrowser, "open") as op:
                ok, reply = self.say(phrase)
            self.assertTrue(ok, phrase)
            self.assertIn(word, reply)
            op.assert_not_called()

    def test_small_talk_does_not_swallow_real_questions(self):
        for phrase in ("как ты думаешь это правильно", "как дела у курса доллара", "как открыть файл"):
            self.assertFalse(self.say(phrase)[0] and "Слушаю" in self.say(phrase)[1], phrase)
        self.assertFalse(self.say("как ты думаешь это правильно")[0])

    def test_search_asks_before_opening_browser(self):
        a = mock.MagicMock()
        with mock.patch.object(actions.net, "is_online", return_value=True), mock.patch.object(actions.webbrowser, "open") as op:
            ok, reply = self.say("Карина, найди в интернете рецепт борща", a)
            self.assertTrue(ok)
            self.assertIn("Искать в интернете: рецепт борща?", reply)
            self.assertIn("да", reply)
            op.assert_not_called()                                   # браузер ещё не открыт
            action = a.ask_confirm.call_args[0][0]
            self.assertEqual(action(), "Ищу: рецепт борща.")         # после «да»
            op.assert_called_once()
            self.assertIn("borsch" if False else "%D0%B1%D0%BE%D1%80%D1%89%D0%B0", op.call_args[0][0])

    def test_search_without_confirmation_setting_goes_straight(self):
        self.cfg.update({"search": {"confirm": False}})
        with mock.patch.object(actions.net, "is_online", return_value=True), mock.patch.object(actions.webbrowser, "open") as op:
            ok, reply = self.say("найди в интернете погоду", mock.MagicMock())
        self.assertEqual(reply, "Ищу: погоду.")
        op.assert_called_once()

    def test_weather_phrase_with_search_words_is_a_search_not_weather(self):
        with mock.patch.object(actions.net, "is_online", return_value=True), mock.patch.object(actions.webbrowser, "open"):
            ok, reply = self.say("найди в интернете погоду в омске", mock.MagicMock())
        self.assertIn("Искать в интернете", reply)

    def make_assistant(self):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = self.cfg
        a._pending = None
        a.said = []
        a.say = a.said.append
        a.emit = lambda e: None
        a.chat = mock.MagicMock()
        a.chat.preflight.return_value = ""
        a.chat.busy = False
        a.chat.will_search.side_effect = lambda t: research.wants_search(norm(t))
        a.chat.voice_chat_id.return_value = "c1"
        return a

    def test_chat_search_waits_for_yes(self):
        a = self.make_assistant()
        a._to_chat("найди в интернете рецепт борща и расскажи")
        self.assertIn("посмотрю в интернете", a.said[0])
        a.chat.send.assert_not_called()                              # пока не сказали «да» — ничего не ушло в интернет
        action = a._pending["action"]
        self.assertEqual(action(), "Хорошо, поищу в интернете.")
        self.assertEqual(a.chat.send.call_args.kwargs["search"], True)

    def test_chat_question_without_request_does_not_ask_or_search(self):
        a = self.make_assistant()
        a._to_chat("сколько будет два плюс два сегодня")
        self.assertIn("Спросить у нейросети", a.said[0])              # к нейросети — только с разрешения
        a.chat.send.assert_not_called()
        self.assertEqual(a._pending["action"](), "Хорошо, подумаю.")
        self.assertFalse(a.chat.send.call_args.kwargs["search"])     # интернет не включали
        self.assertFalse(a.chat.will_search("как дела сейчас"))


class FakeAssistant:
    def __init__(self):
        self.pending, self.choice = None, None
        self._pending, self._choice = None, None

    def ask_confirm(self, a):
        self.pending = a

    def ask_choice(self, h):
        self.choice = h


class MusicTests(unittest.TestCase):
    YT = '{"contents":[{"videoRenderer":{"videoId":"abcdefghijk","thumbnail":{},"title":{"runs":[{"text":"Кино - Группа крови"}]}}}]}'

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.cfg = Config(Path(tmp) / "c.json")
        with mock.patch.object(AppIndex, "refresh_menu", lambda s: None):
            self.apps = AppIndex(self.cfg)
        self.apps._menu = [("Spotify", "C:\\lnk\\Spotify.lnk"), ("Foobar2000", "C:/lnk/Foobar2000.lnk")]
        self.apps._menu_ts = 9e18
        self.fa = FakeAssistant()
        self.started, self.opened, self.pressed = [], [], []
        self.running = {"v": False}
        patches = [mock.patch.object(winutil, "start", lambda t, a="": self.started.append(t)),
                   mock.patch.object(winutil, "press", lambda k, n=1: self.pressed.append(k)),
                   mock.patch.object(winutil, "focus_window", lambda t: True),
                   mock.patch("webbrowser.open", lambda u: self.opened.append(u)),
                   mock.patch.object(music, "_running", lambda procs: self.running["v"]),
                   mock.patch.object(music, "_is_playing", lambda procs: False),
                   mock.patch.object(music, "_wait_running", lambda *a, **k: True),
                   mock.patch.object(music.time, "sleep", lambda s: None),
                   mock.patch.object(music, "_http_get", lambda url, timeout=8.0: self.YT)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        memory._INSTANCES.clear()

    def say(self, phrase):
        _, rest = split_wake(phrase, self.cfg.wake_names())
        ctx = Ctx(self.cfg, self.apps, self.fa, raw=phrase)
        return dispatch(ctx, rest)

    # ---- разбор фраз
    def test_parse(self):
        P = music.parse
        self.assertEqual(P("включи музыку")["op"], "play")
        self.assertEqual(P("поставь какую-нибудь музыку")["op"], "play")
        self.assertEqual(P("включи музыку в спотифай"), {"op": "play", "cat": "music", "q": "", "app": "спотифай"})
        self.assertEqual(P("включи песню группа крови")["q"], "группа крови")
        self.assertEqual(P("хочу послушать queen")["q"], "queen")
        self.assertEqual(P("включи музыку кино")["q"], "кино")
        for t in ("переключи", "переключи песню", "следующую", "включи другую песню", "давай следующую"):
            self.assertEqual((P(t) or {}).get("op"), "next", t) if t != "следующую" else None
        self.assertEqual(P("смени приложение для музыки"), {"op": "change", "cat": "music"})
        self.assertEqual(P("включи видео про котов")["cat"], "video")
        for t in ("включи дискорд", "переключи окно", "включи впн", "открой музыку", "как дела", "включи звук"):
            self.assertIsNone(P(t), t)

    def test_app_named_at_the_end_of_song_request(self):
        q, app = music.split_app(self.cfg, "music", None, "кино группа крови в яндекс музыке")
        self.assertEqual((q, app), ("кино группа крови", "яндекс музыке"))
        self.assertEqual(music.split_app(self.cfg, "music", None, "песня про лес на опушке"), ("песня про лес на опушке", ""))    # хвост — не приложение

    # ---- первый раз спрашивает, дальше помнит
    def test_asks_once_then_remembers(self):
        ok, reply = self.say("Карина, включи музыку")
        self.assertTrue(ok)
        self.assertIn("В каком приложении", reply)
        self.assertEqual((self.started, self.opened, self.pressed), ([], [], []))        # пока ничего не запущено
        out, again = self.fa.choice("в спотифай")
        self.assertFalse(again)
        self.assertIn("Запомнила", out)
        self.assertEqual(memory.get(self.cfg).app("music")["key"], "spotify")
        self.assertEqual(self.started, ["C:\\lnk\\Spotify.lnk"])                           # приложение запущено
        self.assertEqual(self.pressed, ["play"])                                          # и музыка включена
        self.fa.choice = None
        self.started.clear(); self.pressed.clear(); self.running["v"] = True
        ok, reply = self.say("включи музыку")
        self.assertIsNone(self.fa.choice)                                                 # второй раз не спрашивает
        self.assertEqual(self.pressed, ["play"])
        self.assertIn("Spotify", reply)

    def test_unknown_answer_asks_again_and_cancel_works(self):
        self.say("включи музыку")
        out, again = self.fa.choice("абракадабра хз")
        self.assertTrue(again)
        self.assertIn("Не поняла", out)
        out, again = self.fa.choice("отмена")
        self.assertEqual((out, again), ("Хорошо, ничего не включаю.", False))
        self.assertIsNone(memory.get(self.cfg).app("music"))

    def test_own_program_as_answer(self):
        self.say("включи музыку")
        out, _ = self.fa.choice("foobar")        # любая программа из меню «Пуск»
        self.assertEqual(memory.get(self.cfg).app("music")["name"], "Foobar2000")

    def test_already_playing_is_not_toggled_off(self):
        memory.get(self.cfg).set_app("music", "spotify", "Spotify")
        self.running["v"] = True
        with mock.patch.object(music, "_is_playing", lambda procs: True):
            ok, reply = self.say("включи музыку")
        self.assertIn("уже играет", reply)
        self.assertEqual(self.pressed, [])

    def test_change_app_forgets_choice(self):
        memory.get(self.cfg).set_app("music", "spotify", "Spotify")
        ok, reply = self.say("смени приложение для музыки")
        self.assertIn("забыла", reply)
        self.assertIsNone(memory.get(self.cfg).app("music"))
        self.assertIn("В каком приложении", self.say("включи музыку")[1])

    # ---- конкретная песня
    def test_youtube_title_with_escapes(self):
        with mock.patch.object(music, "_http_get", lambda url, timeout=8.0: '{"videoRenderer":{"videoId":"abcdefghijk","title":{"runs":[{"text":"AC\\u0026DC \\"Back\\" in Black"}]}}}'):
            self.assertEqual(music.resolve_youtube("acdc"), ("abcdefghijk", 'AC&DC "Back" in Black'))

    def test_song_plays_on_youtube_music(self):
        ok, reply = self.say("Карина, включи песню Кино группа крови в ютуб музыке")
        self.assertTrue(ok)
        self.assertEqual(self.opened, ["https://music.youtube.com/watch?v=abcdefghijk"])      # сразу играет
        self.assertIn("Группа крови", reply)
        self.assertEqual(memory.get(self.cfg).app("music")["key"], "ytmusic")                # приложения не было — запомнили названное

    def test_song_on_saved_youtube_without_naming_app(self):
        memory.get(self.cfg).set_app("music", "youtube", "YouTube")
        self.say("поставь песню Кино группа крови")
        self.assertEqual(self.opened, ["https://www.youtube.com/watch?v=abcdefghijk"])
        self.assertIsNone(self.fa.choice)

    def test_song_falls_back_to_search_when_youtube_changed(self):
        memory.get(self.cfg).set_app("music", "youtube", "YouTube")
        with mock.patch.object(music, "_http_get", side_effect=OSError("нет сети")):
            ok, reply = self.say("включи песню Кино")
        self.assertTrue(self.opened[0].startswith("https://www.youtube.com/results?search_query="))
        self.assertIn("поиск", reply)

    def test_song_in_spotify_opens_search(self):
        memory.get(self.cfg).set_app("music", "spotify", "Spotify")
        self.running["v"] = True
        ok, reply = self.say("включи песню Кино группа крови")
        self.assertTrue(self.started[-1].startswith("spotify:search:"))
        self.assertIn("поиск в Spotify", reply)

    def test_video_has_its_own_remembered_app(self):
        memory.get(self.cfg).set_app("music", "spotify", "Spotify")
        ok, reply = self.say("включи видео про котов")
        self.assertIn("В каком приложении", reply)                                           # для видео — отдельный выбор
        self.fa.choice("ютуб")
        self.assertEqual(self.opened, ["https://www.youtube.com/watch?v=abcdefghijk"])
        self.assertEqual(memory.get(self.cfg).app("music")["key"], "spotify")

    def test_next_and_ordinary_open_are_not_broken(self):
        self.say("переключи")
        self.assertEqual(self.pressed, ["next"])
        self.say("открой спотифай")
        self.assertIn("Spotify.lnk", self.started[-1])                                       # «открой» — это обычный запуск программы


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "learned.json"
        self.m = memory.Memory(self.path)

    def test_remember_and_recall_exact_only(self):
        self.assertTrue(self.m.remember("убавь громкость браузера", "mixer_volume", {"t": "убавь громкость браузера"}))
        self.assertEqual(self.m.recall("убавь громкость браузера")[0], "mixer_volume")
        self.assertIsNone(self.m.recall("прибавь громкость браузера"))           # похожая фраза — не та же самая
        self.assertFalse(self.m.remember("убавь громкость браузера", "mixer_volume", {}))     # повтор — не новая

    def test_dangerous_commands_are_never_learned(self):
        for cmd in ("shutdown", "restart", "sleep_pc", "type_text", "search", "lock"):
            self.assertFalse(self.m.remember("сделай это", cmd, {}), cmd)
            self.assertIsNone(self.m.recall("сделай это"))

    def test_persists_and_clears(self):
        self.m.set_app("music", "spotify", "Spotify")
        self.m.remember("открой блокнот", "open_app", {"target": "блокнот"})
        again = memory.Memory(self.path)
        self.assertEqual(again.app("music")["name"], "Spotify")
        self.assertEqual(again.recall("открой блокнот"), ("open_app", {"target": "блокнот"}))
        self.assertIn("Spotify", again.summary())
        again.clear()
        self.assertIsNone(memory.Memory(self.path).app("music"))
        self.assertIn("ничего не запомнила", memory.Memory(self.path).summary())

    def test_broken_file_does_not_break(self):
        self.path.write_text("{не json", "utf-8")
        self.assertIsNone(memory.Memory(self.path).app("music"))

    def test_limit(self):
        for i in range(memory.MAX_PHRASES + 30):
            self.m.remember(f"открой программу {i}", "open_app", {"target": str(i)})
        self.assertLessEqual(len(self.m.data["phrases"]), memory.MAX_PHRASES)
        self.assertIsNotNone(self.m.recall(f"открой программу {memory.MAX_PHRASES + 29}"))     # свежие остались


class LearningInAssistant(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.cfg = Config(Path(tmp) / "c.json")
        memory._INSTANCES.clear()
        with mock.patch.object(AppIndex, "refresh_menu", lambda s: None):
            self.apps = AppIndex(self.cfg)
        self.apps._menu = [("Discord", "C:\\lnk\\Discord.lnk")]
        self.apps._menu_ts = 9e18
        self.started = []
        p = mock.patch.object(winutil, "start", lambda t, a="": self.started.append(t))
        p.start()
        self.addCleanup(p.stop)
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg, a.memory, a._pending, a._choice = self.cfg, memory.get(self.cfg), None, None
        self.a = a

    def phrase(self, text):
        ctx = Ctx(self.cfg, self.apps, self.a, raw=text)
        before = (self.a._pending, self.a._choice)
        handled, reply = self.a._recall(ctx, text)
        recalled = handled
        if not handled:
            handled, reply = dispatch(ctx, text)
            self.a._learn(ctx, text, handled, reply, before)
        return handled, reply, recalled

    def test_second_time_is_recalled(self):
        _, reply, recalled = self.phrase("открой дискорд")
        self.assertFalse(recalled)
        self.assertEqual(self.a.memory.recall("открой дискорд")[0], "open_app")
        self.started.clear()
        _, reply2, recalled = self.phrase("открой дискорд")
        self.assertTrue(recalled)
        self.assertEqual(reply2, reply)
        self.assertEqual(len(self.started), 1)                                              # снова запущен

    def test_failures_and_questions_are_not_learned(self):
        self.phrase("открой зюзюкину программу")
        self.assertIsNone(self.a.memory.recall("открой зюзюкину программу"))
        with mock.patch.object(actions.net, "is_online", return_value=True):
            ctx = Ctx(self.cfg, self.apps, FakeAssistant(), raw="x")
            self.a._learn(ctx, "найди в интернете кошек", *dispatch(ctx, "найди в интернете кошек"), (None, None))
        self.assertIsNone(self.a.memory.recall("найди в интернете кошек"))

    def test_broken_memory_entry_is_forgotten_and_reparsed(self):
        self.a.memory.remember("открой дискорд", "open_app", {"target": "несуществующая штука"})
        handled, reply, recalled = self.phrase("открой дискорд")
        self.assertFalse(recalled)                                                           # не сработало по памяти...
        self.assertTrue(handled)
        self.assertIn("Discord", reply)                                                      # ...но разобрано заново, как обычно
        self.assertEqual(self.a.memory.recall("открой дискорд")[1], {"target": "дискорд"})   # и запомнено правильно

    def test_voice_info_and_forget(self):
        self.a.memory.set_app("music", "spotify", "Spotify")
        fa = FakeAssistant()
        ctx = Ctx(self.cfg, self.apps, fa, raw="что ты выучила")
        self.assertIn("Spotify", dispatch(ctx, "что ты выучила")[1])
        ok, reply = dispatch(ctx, "забудь выученное")
        self.assertIn("«да»", reply)
        self.assertIsNotNone(self.a.memory.app("music"))                                     # без «да» ничего не стёрто
        fa.pending()
        self.assertIsNone(self.a.memory.app("music"))


if __name__ == "__main__":
    unittest.main()
