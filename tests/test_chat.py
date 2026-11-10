"""Чат: история, обрезка по контексту, голосовой формат, остановка, ошибки, распознавание голосовых запросов."""
import sys, tempfile, threading, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import chat
from karina.config import Config
from karina.engine import EngineError
from karina.textutil import norm


class FakeEngine:
    state = "ready"
    def __init__(self, reply="Ответ", err=None):
        self.reply, self.err, self.calls = reply, err, []
    def chat(self, messages, max_tokens=0, on_delta=None, stop=None, **kw):
        self.calls.append(messages)
        if self.err:
            raise self.err
        if on_delta:
            on_delta(self.reply[:3]); on_delta(self.reply)
        return self.reply


def make(engine, spoken=None):
    tmp = Path(tempfile.mkdtemp())
    ev = []
    svc = chat.ChatService(Config(tmp / "c.json"), engine, ev.append, speak=(spoken.append if spoken is not None else None), store=chat.ChatStore(tmp / "chats"))
    return svc, ev


class T(unittest.TestCase):
    def test_intents(self):
        yes = ["найди и напиши отчёт про солнечные панели", "напиши отчёт про рынок", "объясни что такое vulkan", "что такое llama", "расскажи про Байкал",
               "составь план тренировок", "почему небо голубое", "помоги мне написать письмо"]
        no = ["найди в интернете погоду", "напиши привет как дела", "открой блокнот", "громкость пятьдесят", "найди рецепт борща", "который час"]
        for t in yes: self.assertTrue(chat.is_chat_request(norm(t)), t)
        for t in no: self.assertFalse(chat.is_chat_request(norm(t)), t)
        self.assertTrue(chat.wants_open_chat("открой чат")); self.assertFalse(chat.wants_open_chat("открой блокнот"))

    def test_parse_voice(self):
        self.assertEqual(chat.parse_voice("ГОЛОС: Готово, отчёт ниже.\n\n## Отчёт\nтекст"), ("Готово, отчёт ниже.", "## Отчёт\nтекст"))
        sp, body = chat.parse_voice("Это длинный ответ без формата. Вторая фраза. Третья фраза.")
        self.assertEqual(sp, "Это длинный ответ без формата. Вторая фраза."); self.assertTrue(body.endswith("Третья фраза."))

    def test_send_saves_history_and_streams(self):
        eng = FakeEngine("Привет, я Карина")
        svc, ev = make(eng)
        cid = svc.send(None, "привет", wait=True)
        c = svc.store.get(cid)
        self.assertEqual([m["role"] for m in c["messages"]], ["user", "assistant"])
        self.assertEqual(c["messages"][1]["text"], "Привет, я Карина"); self.assertEqual(c["title"], "привет")
        types = [e["type"] for e in ev]
        self.assertEqual(types[0], "chat_start"); self.assertIn("chat_delta", types); self.assertEqual(types[-1], "chat_done")
        # второй вопрос видит историю
        svc.send(cid, "а ещё?", wait=True)
        sent = eng.calls[-1]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual([m["content"] for m in sent[1:]], ["привет", "Привет, я Карина", "а ещё?"])

    def test_history_trimmed(self):
        msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 100} for i in range(20)]
        out = chat.fit_history(msgs, 500)
        self.assertLess(len(out), 6); self.assertEqual(out[0]["role"], "user"); self.assertEqual(out[-1], msgs[-1])

    def test_voice_speaks_short_and_shows_full(self):
        said = []
        svc, ev = make(FakeEngine("ГОЛОС: Отчёт готов.\n\n## Отчёт\nПодробности"), said)
        svc.cfg.update({"search": {"mode": "off"}})      # здесь проверяем только голосовой формат; поиск — в test_research.py
        cid = svc.send(None, "найди и напиши отчёт про х", voice=True, wait=True)
        self.assertEqual(said, ["Отчёт готов."])
        self.assertEqual(svc.store.get(cid)["messages"][-1]["text"], "## Отчёт\nПодробности")
        self.assertIn("ГОЛОС", chat.system_prompt(True)); self.assertNotIn("ГОЛОС", chat.system_prompt(False))

    def test_error_is_reported_not_raised(self):
        said = []
        svc, ev = make(FakeEngine(err=EngineError("Модель не выбрана.")), said)
        cid = svc.send(None, "привет", voice=True, wait=True)
        self.assertIn("Модель не выбрана", svc.store.get(cid)["messages"][-1]["text"])
        self.assertEqual(ev[-1]["error"], "Модель не выбрана."); self.assertTrue(said[0].startswith("Не получилось"))
        self.assertFalse(svc.busy)                                   # блокировка снята

    def test_busy_rejects_second(self):
        gate = threading.Event()
        class Slow(FakeEngine):
            def chat(self, *a, **k): gate.wait(3); return "ok"
        svc, _ = make(Slow())
        cid = svc.send(None, "первый")
        for _ in range(50):
            if svc.busy: break
            threading.Event().wait(0.02)
        self.assertIsNone(svc.send(cid, "второй"))
        gate.set()
        for _ in range(100):
            if not svc.busy: break
            threading.Event().wait(0.02)
        self.assertFalse(svc.busy)

    def test_store_list_delete_and_safe_ids(self):
        svc, _ = make(FakeEngine())
        a, b = svc.store.new(), svc.store.new()
        self.assertEqual(len(svc.store.list()), 2)
        self.assertTrue(svc.store.delete(a["id"])); self.assertEqual(len(svc.store.list()), 1)
        self.assertIsNone(svc.store.get("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
