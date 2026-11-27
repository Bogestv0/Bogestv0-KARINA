"""1.13.0: ответы «да/нет/стоп» после озвучки вопроса, мастер приложений VPN, одна речь за раз, дерзкий режим 18+ (болтовня, повторы)."""
import sys, tempfile, threading, time, types, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest import mock
from karina import actions, assistant as asst, dialog, integrations, memory, rude, tunnel_cmds as tc  # noqa: F401
from karina.actions import Ctx, dispatch
from karina.apps import AppIndex
from karina.config import Config
from karina.textutil import norm
from karina.wake import split_wake


class Classify(unittest.TestCase):
    def test_yes_no_stop(self):
        for t in ("Да.", "да да", "Давай!", "ага", "Карина, да", "ну да", "конечно", "угу", "включай"):
            self.assertEqual(dialog.classify(t), "yes", t)
        for t in ("нет", "Не надо.", "не нужно", "неа", "не хочу"):
            self.assertEqual(dialog.classify(t), "no", t)
        for t in ("стоп", "Карина, стоп", "замолчи", "хватит", "отмена", "ладно, замолчи", "перестань"):
            self.assertEqual(dialog.classify(t), "stop", t)

    def test_not_an_answer(self):
        for t in ("", "открой браузер", "что-то там на телевизоре про погоду и новости дня", "громкость пятьдесят"):
            self.assertIsNone(dialog.classify(t), t)

    def test_negative_wins(self):
        self.assertEqual(dialog.classify("да нет"), "no")
        self.assertEqual(dialog.classify("да стоп"), "stop")


class FakeTunnel:
    def __init__(self, apps=None, running=False):
        self.s = {"servers": [{"id": "a", "name": "Хельсинки", "link": "x"}], "active": "a", "mode": "vpn", "scope": "all", "apps": list(apps or []), "confirm": True}
        self.run, self.started = running, []

    def configured(self): return True
    def settings(self): return dict(self.s)
    def save(self, **p): self.s.update(p)
    def active_server(self, sid=""): return self.s["servers"][0]
    def status(self): return {"running": self.run}
    def running(self): return self.run
    def describe(self): return "Включён."
    def start(self, mode=None, scope=None, apps=None, sid=""):
        self.started.append((mode, scope, list(apps or []))); self.run = True
        return "Включён."


class Dialogs(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        memory._INSTANCES.clear()
        with mock.patch.object(AppIndex, "refresh_menu", lambda s: None):
            self.apps = AppIndex(self.cfg)
        self.apps._menu, self.apps._menu_ts = [], 9e18
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg, a.memory, a._pending, a._choice = self.cfg, memory.get(self.cfg), None, None
        a.sleeping, a.followup_until, a._conf = False, 0.0, None
        a.said, a.logs, a.events = [], [], []
        a.say = lambda text, shown=None: (a.said.append(text), a._arm_dialogs())      # озвучка закончилась → вопрос «взводится»
        a.log = lambda k, t: a.logs.append((k, t))
        a.emit = a.events.append
        a.tunnel = FakeTunnel()
        a.apps = self.apps
        a.brain = mock.MagicMock(); a.brain.usable.return_value = False
        a.chat = mock.MagicMock(); a.chat.busy = False
        self.a = a

    def hear(self, text, named=False):
        self.a.handle(("Карина, " if named else "") + text)
        return self.a.said[-1] if self.a.said else ""

    def test_yes_after_long_question_is_still_accepted(self):
        """Баг из жизни: вопрос озвучивался 16 секунд, а ответ ждали 12 — «да» терялось."""
        ctx = Ctx(self.cfg, self.apps, self.a)
        done = []
        q = ctx.confirm("Включить VPN?", lambda: done.append(1) or "Включила.")
        self.assertIsNone(self.a._pending["until"])                     # пока говорит — таймера нет
        time.sleep(0.05)
        self.a._arm_dialogs()
        self.a._pending["until"] = time.time() + 29                     # ответ можно ждать почти полминуты после речи
        self.assertEqual(self.hear("Да."), "Включила.")
        self.assertEqual(done, [1])
        self.assertIsNone(self.a._pending)

    def test_pending_without_speech_end_does_not_expire(self):
        self.a.ask_confirm(lambda: "ok")
        time.sleep(0.05)
        self.assertTrue(self.a._alive(self.a._pending))

    def test_no_and_stop_cancel(self):
        for word in ("нет", "стоп", "замолчи", "не надо"):
            self.a.said.clear()
            self.a.ask_confirm(lambda: "НЕ ДОЛЖНО СРАБОТАТЬ"); self.a._arm_dialogs()
            self.assertEqual(self.hear(word), "Хорошо, отменяю.", word)
            self.assertIsNone(self.a._pending)

    def test_unclear_answer_asks_again_and_keeps_listening(self):
        self.a.ask_confirm(lambda: "ok"); self.a._arm_dialogs()
        self.assertEqual(self.hear("мммм ну"), "")                       # шум без имени — молчим, не переспрашиваем
        self.assertIsNotNone(self.a._pending)                            # всё ещё ждём
        self.a.said.clear()
        self.assertEqual(self.hear("да"), "ok")

    def test_long_background_speech_is_ignored_while_waiting(self):
        self.a.ask_confirm(lambda: "ok"); self.a._arm_dialogs()
        self.a.handle("а потом он пришёл домой и сказал что вообще ничего не понимает в этом")
        self.assertEqual(self.a.said, [])
        self.assertIsNotNone(self.a._pending)

    def test_named_other_command_drops_question(self):
        self.a.ask_confirm(lambda: "ok"); self.a._arm_dialogs()
        with mock.patch.object(actions, "dispatch", return_value=(True, "Готово.")):
            self.a.handle("Карина, открой блокнот")
        self.assertIsNone(self.a._pending)

    def test_expired_dialog_says_so(self):
        self.a.ask_confirm(lambda: "ok"); self.a._arm_dialogs()
        self.a._pending["until"] = time.time() - 1
        self.a._expire_dialogs()
        self.assertIsNone(self.a._pending)
        self.assertIn("отменяю", self.a.said[-1])

    def test_search_question_waits_for_yes_and_no_answers_without_internet(self):
        self.a.chat.will_search.return_value = True
        self.a.chat.preflight.return_value = ""
        self.a.chat.voice_chat_id.return_value = "c1"
        self.a._to_chat("найди в интернете рецепт борща")
        self.assertIn("посмотрю в интернете", self.a.said[-1])
        self.a.chat.send.assert_not_called()
        self.assertEqual(self.hear("нет"), "Хорошо, не буду.")          # «нет» — полная отмена, в нейросеть ничего не уходит
        self.a.chat.send.assert_not_called()
        self.a.said.clear()
        self.a._to_chat("найди в интернете рецепт борща"); self.a._arm_dialogs()
        self.hear("давай")
        self.assertEqual(self.a.chat.send.call_args.kwargs["search"], True)


class VpnApps(Dialogs):
    def phrase(self, text):
        _, rest = split_wake(text, self.cfg.wake_names())
        return dispatch(Ctx(self.cfg, self.apps, self.a, raw=text), rest)

    def test_selected_apps_with_empty_list_starts_wizard(self):
        ok, reply = self.phrase("включи впн для выбранных приложений")
        self.assertTrue(ok)
        self.assertIn("пуст", reply); self.assertIn("«да» или «нет»", reply)
        self.assertIsNotNone(self.a._choice)

    def test_selected_apps_with_list_asks_to_enable(self):
        self.a.tunnel = FakeTunnel(apps=["chrome.exe"])
        ok, reply = self.phrase("включи впн для выбранных приложений")
        self.assertIn("Гугл Хром", reply); self.assertIn("«да»", reply)
        self.a._arm_dialogs()
        self.hear("да")
        self.assertEqual(self.a.tunnel.started, [("vpn", "apps", ["chrome.exe"])])

    def test_full_wizard(self):
        ok, reply = self.phrase("включи впн для выбранных приложений")
        self.assertIn("пуст", reply)
        self.a._arm_dialogs()
        self.assertIn("Какие приложения", self.hear("да"))
        self.a._arm_dialogs()
        r = self.hear("хром и дискорд")                                  # без «Карина»
        self.assertIn("Гугл Хром и Дискорд", r); self.assertIn("«да» или «нет»", r)
        self.a._arm_dialogs()
        r = self.hear("нет")                                             # не те — назвать заново
        self.assertIn("ещё раз", r)
        self.a._arm_dialogs()
        r = self.hear("телеграм")
        self.assertIn("Телеграм", r)
        self.a._arm_dialogs()
        r = self.hear("да")
        self.assertIn("Добавила", r); self.assertIn("Есть ещё", r)
        self.assertEqual(self.a.tunnel.s["apps"], ["Telegram.exe"])
        self.a._arm_dialogs()
        self.assertIn("Назовите", self.hear("да"))                      # есть ещё
        self.a._arm_dialogs()
        self.hear("хром")
        self.a._arm_dialogs()
        self.assertIn("Есть ещё", self.hear("да"))
        self.a._arm_dialogs()
        r = self.hear("нет")                                             # больше нет → предлагает включить
        self.assertIn("Включить VPN только для", r)
        self.assertEqual(sorted(self.a.tunnel.s["apps"]), ["Telegram.exe", "chrome.exe"])
        self.a._arm_dialogs()
        self.hear("да")
        self.assertEqual(self.a.tunnel.started[-1][:2], ("vpn", "apps"))
        self.assertEqual(self.a.tunnel.s["scope"], "apps")

    def test_wizard_stop_at_any_step(self):
        self.phrase("включи впн для выбранных приложений"); self.a._arm_dialogs()
        self.hear("да"); self.a._arm_dialogs()
        self.assertEqual(self.hear("стоп"), "Хорошо, отменяю.")
        self.assertIsNone(self.a._choice)
        self.assertEqual(self.a.tunnel.s["apps"], [])

    def test_unknown_app_asks_again(self):
        self.phrase("добавь приложения в впн"); self.a._arm_dialogs()
        r = self.hear("абракадабра")
        self.assertIn("Не нашла", r)
        self.assertIsNotNone(self.a._choice)

    def test_add_named_app_asks_confirmation(self):
        ok, reply = self.phrase("добавь хром в впн")
        self.assertIn("Гугл Хром", reply)
        self.a._arm_dialogs()
        self.hear("да")
        self.assertEqual(self.a.tunnel.s["apps"], ["chrome.exe"])

    def test_remove_and_list(self):
        self.a.tunnel = FakeTunnel(apps=["chrome.exe", "Discord.exe"])
        self.assertIn("Гугл Хром и Дискорд", self.phrase("какие приложения в впн")[1])
        self.assertIn("Убрала", self.phrase("убери хром из впн")[1])
        self.assertEqual(self.a.tunnel.s["apps"], ["Discord.exe"])

    def test_settings_jump(self):
        ok, reply = self.phrase("открой настройки впн")
        self.assertTrue(ok)
        self.assertIn({"type": "goto", "tab": "settings", "focus": "tnCard"}, self.a.events)


class OneVoiceAtATime(unittest.TestCase):
    def test_say_calls_never_overlap(self):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        a._pending = a._choice = None
        a.sleeping, a.listening, a.followup_until = False, True, 0.0
        a.stt = types.SimpleNamespace(model=None)
        a.listener = types.SimpleNamespace(speaking=False, muted=False)
        a.emit = lambda e: None
        state = {"now": 0, "max": 0}
        lock = threading.Lock()

        def speak(text):
            with lock:
                state["now"] += 1; state["max"] = max(state["max"], state["now"])
            time.sleep(0.08)
            with lock:
                state["now"] -= 1
        a.tts = types.SimpleNamespace(speak=speak, speaking=False, stop=lambda: None)
        ts = [threading.Thread(target=a.say, args=(f"фраза {i}",)) for i in range(4)]
        [t.start() for t in ts]; [t.join(5) for t in ts]
        self.assertEqual(state["max"], 1)

    def test_hush_drops_queued_phrases(self):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        a._pending = a._choice = None
        a.sleeping, a.listening, a.followup_until = False, True, 0.0
        a.stt = types.SimpleNamespace(model=None)
        a.listener = types.SimpleNamespace(speaking=False, muted=False)
        a.emit = lambda e: None
        spoken = []
        a.tts = types.SimpleNamespace(speak=lambda t: (spoken.append(t), time.sleep(0.15)), speaking=False, stop=lambda: None)
        t1 = threading.Thread(target=a.say, args=("первая",)); t1.start(); time.sleep(0.03)
        t2 = threading.Thread(target=a.say, args=("вторая",)); t2.start(); time.sleep(0.03)
        a.hush()
        t1.join(3); t2.join(3)
        self.assertEqual(spoken, ["первая"])


class Rude(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.cfg.update({"persona": {"rude": True}})
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg, a._pending, a._choice, a._repeats = self.cfg, None, None, rude.RepeatGuard()
        a.said, a.followup_until = [], 0.0
        a.say = lambda text, shown=None: (a.said.append(text), a._arm_dialogs())
        a.log = lambda k, t: None
        a.emit = lambda e: None
        a.sleeping = False
        self.a = a
        self.apps = mock.MagicMock()

    def ask(self, phrase):
        _, rest = split_wake(phrase, self.cfg.wake_names())
        return dispatch(Ctx(self.cfg, self.apps, self.a, raw=phrase), rest)[1]

    def test_repeated_question_escalates(self):
        self.ask("Карина, как дела")
        r2 = self.ask("Карина, как дела")
        r3 = self.ask("Карина, как дела")
        self.assertTrue(any(r2.startswith(x) for x in rude.PHRASES["repeat_2"]))
        self.assertIn(r3, rude.PHRASES["repeat_3"])
        self.assertIn(self.ask("Карина, как дела"), rude.PHRASES["repeat_4"])

    def test_repeat_is_off_in_polite_mode(self):
        self.cfg.update({"persona": {"rude": False}})
        for _ in range(4):
            r = self.ask("как дела")
            self.assertNotIn(r, rude.PHRASES["repeat_3"] + rude.PHRASES["repeat_4"])
        self.assertEqual(self.a._repeats.hit("как дела"), 1)         # в вежливом режиме повторы не считаются

    def test_commands_like_volume_are_not_guarded(self):
        for _ in range(4):
            r = self.ask("громче")
            self.assertNotIn(r, rude.PHRASES["repeat_3"])

    def test_how_are_you_asks_back_and_continues(self):
        r = self.ask("как дела")
        self.assertTrue(any(x in r for x in rude.PHRASES["how_ask"]))
        self.assertIsNotNone(self.a._choice)
        self.a._arm_dialogs()
        reply, nxt = self.a._choice["fn"]("у меня всё отлично")
        self.assertIn(reply, rude.PHRASES["chat_good"]); self.assertTrue(callable(nxt))
        reply, nxt = nxt("работал")
        self.assertIn(reply, rude.PHRASES["chat_more"]); self.assertTrue(callable(nxt))
        reply, nxt = nxt("да так")
        self.assertIn(reply, rude.PHRASES["chat_end"]); self.assertFalse(nxt)

    def test_chat_passes_real_commands_through(self):
        self.assertEqual(rude.chat_turn("открой ютуб", 0), (None, False))
        self.assertEqual(rude.chat_turn("какая сегодня погода", 0), (None, False))

    def test_bad_mood_gets_support(self):
        r, cont = rude.chat_turn("мне хреново", 0)
        self.assertIn(r, rude.PHRASES["chat_bad"]); self.assertTrue(cont)

    def test_small_talk_has_rude_variants(self):
        for q in ("ты тупая", "расскажи анекдот", "мне скучно", "я устал", "что ты любишь", "ты гений", "где ты живешь", "как ты выглядишь", "что ты можешь"):
            r = self.ask(q)
            self.assertIn(r, actions._TALK[next(k for k, v in actions._TALK.items() if r in v[1])][1], q)

    def test_flavor_skips_questions_and_errors(self):
        for t in ("Включить VPN? Скажите «да» или «нет».", "Не нашла такой сервер.", "x" * 200, ""):
            self.assertEqual(rude.flavor(t, chance=1.0), t)
        self.assertNotEqual(rude.flavor("Готово.", chance=1.0), "Готово.")

    def test_persona_prompt_covers_voice_line_and_questions(self):
        from karina import prompts
        p = prompts.PERSONA_RUDE
        self.assertIn("«ГОЛОС:»", p); self.assertIn("встречный вопрос", p); self.assertIn("повторно", p)


if __name__ == "__main__":
    unittest.main()
