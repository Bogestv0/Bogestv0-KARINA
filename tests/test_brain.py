"""«Мозг»: режимы local / claude / auto, понятные ошибки, клиент Claude (поток, ошибки, ключ не утекает), интеграция с чатом и голосом."""
import json, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import brain as brainmod, chat, claude
from karina.brain import Brain, BrainUnavailable
from karina.config import Config
from karina.files import FileService

KEY = "sk-ant-SECRET-KEY-1234567890"


class Local:
    state = "ready"
    def __init__(self, ok=True, reply="локальный ответ"):
        self.ok, self.reply, self.calls = ok, reply, []
    def available(self):
        return (True, "") if self.ok else (False, "Локальная модель не выбрана. Откройте «Модели»…")
    def chat(self, messages, max_tokens=0, on_delta=None, stop=None, **kw):
        self.calls.append((messages, max_tokens))
        if on_delta: on_delta(self.reply)
        return self.reply


class FakeClaude:
    state = "ready"
    model_id = "claude-sonnet-5-5"
    def __init__(self, key=True, reply="ответ Claude"):
        self.key, self.reply, self.calls = key, reply, []
    def has_key(self): return self.key
    def chat(self, messages, max_tokens=0, on_delta=None, stop=None, **kw):
        self.calls.append((messages, max_tokens))
        if on_delta: on_delta(self.reply)
        return self.reply


def setup(mode="local", local_ok=True, key=True, online=True, local_reply="локальный ответ"):
    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp / "c.json")
    cfg.update({"brain": {"mode": mode}, "search": {"mode": "off"}})
    loc, cl = Local(local_ok, local_reply), FakeClaude(key)
    b = Brain(cfg, loc, cl)
    ev, said = [], []
    svc = chat.ChatService(cfg, loc, ev.append, speak=said.append, store=chat.ChatStore(tmp / "chats"), brain=b, files=FileService(tmp / "Карина"))
    patcher = mock.patch("karina.brain.net.is_online", return_value=online)
    patcher.start()
    return b, svc, loc, cl, ev, said, patcher


class Modes(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_default_is_local(self):
        self.assertEqual(Config(Path(tempfile.mkdtemp()) / "c.json")["brain"]["mode"], "local")
        b, *_ = setup("мусор")
        self.assertEqual(b.mode, "local")                        # неизвестное значение → безопасный режим по умолчанию

    def test_local_and_claude_explicit(self):
        b, *_ = setup("local")
        self.assertEqual(b.choose("привет").name, "local")
        b, *_ = setup("claude")
        c = b.choose("привет"); self.assertEqual(c.name, "claude"); self.assertEqual(c.label, "Claude Sonnet")

    def test_unavailable_is_explained_not_replaced(self):
        b, *_ = setup("local", local_ok=False)
        with self.assertRaises(BrainUnavailable) as cm: b.choose("привет")
        self.assertIn("не выбрана", str(cm.exception))
        b, *_ = setup("claude", key=False)                       # без ключа — НЕ подменяем локальной моделью
        with self.assertRaises(BrainUnavailable) as cm: b.choose("привет")
        self.assertIn("Ключ Claude не задан", str(cm.exception))
        b, *_ = setup("claude", online=False)
        with self.assertRaises(BrainUnavailable) as cm: b.choose("привет")
        self.assertIn("Нет интернета", str(cm.exception))

    def test_auto_routing(self):
        b, *_ = setup("auto")
        for t in ("привет", "как дела", "сколько будет два плюс два", "что такое vulkan"):
            self.assertEqual(b.choose(t).name, "local", t)
        for t in ("напиши подробный отчёт про рынок", "найди ошибку в этом коде", "сравни два подхода", "x" * 300):
            self.assertEqual(b.choose(t).name, "claude", t)
        self.assertEqual(b.choose("привет", want_files=True).name, "claude")
        self.assertEqual(b.choose("привет", deep_search=True).name, "claude")

    def test_auto_fallback_is_visible(self):
        b, *_ = setup("auto", key=False)                          # сложное, но Claude нет → локальная, с пояснением
        c = b.choose("напиши подробный отчёт")
        self.assertEqual(c.name, "local"); self.assertIn("«Claude Sonnet»", c.note); self.assertIn("Ключ Claude не задан", c.note)
        b, *_ = setup("auto", local_ok=False)                     # простое, но локальной нет → Claude
        c = b.choose("привет")
        self.assertEqual(c.name, "claude"); self.assertIn("«Локальная модель»", c.note); self.assertIn("не выбрана", c.note)
        b, *_ = setup("auto", local_ok=False, key=False)
        with self.assertRaises(BrainUnavailable) as cm: b.choose("привет")
        self.assertIn("Локальная модель", str(cm.exception)); self.assertIn("Claude", str(cm.exception))

    def test_usable_and_describe(self):
        b, *_ = setup("claude", key=False)
        self.assertFalse(b.usable()); self.assertIn("Ключ", b.unavailable_reason())
        d = b.describe(); self.assertEqual(d["mode"], "claude"); self.assertFalse(d["claude"]["ok"]); self.assertTrue(d["local"]["ok"])
        self.assertNotIn("SECRET", json.dumps(d))


class InChat(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_claude_answers_and_is_labelled(self):
        b, svc, loc, cl, ev, said, _ = setup("claude")
        cid = svc.send(None, "привет", wait=True)
        m = svc.store.get(cid)["messages"][-1]
        self.assertEqual(m["text"], "ответ Claude"); self.assertEqual(m["brain"], "Claude Sonnet")
        self.assertEqual(len(cl.calls), 1); self.assertEqual(loc.calls, [])
        self.assertEqual(ev[-1]["brain"], "Claude Sonnet")
        self.assertEqual(cl.calls[0][1], chat.LIMITS["claude"][0])

    def test_local_default_never_calls_claude(self):
        b, svc, loc, cl, *_ = setup("local")
        svc.send(None, "напиши подробный отчёт", wait=True)
        self.assertEqual(cl.calls, []); self.assertEqual(len(loc.calls), 1)

    def test_unavailable_reported_immediately_and_spoken(self):
        b, svc, loc, cl, ev, said, _ = setup("claude", key=False)
        self.assertIn("Ключ Claude не задан", svc.preflight("привет"))
        cid = svc.send(None, "привет", voice=True, wait=True)
        m = svc.store.get(cid)["messages"][-1]
        self.assertTrue(m["text"].startswith("⚠ Ключ Claude не задан")); self.assertEqual((cl.calls, loc.calls), ([], []))
        self.assertTrue(said[0].startswith("Не получилось: Ключ Claude не задан"))
        self.assertFalse(svc.busy)

    def test_auto_routes_each_question(self):
        b, svc, loc, cl, ev, said, _ = setup("auto")
        cid = svc.send(None, "привет", wait=True)
        svc.send(cid, "напиши подробный отчёт про рынок", wait=True)
        msgs = svc.store.get(cid)["messages"]
        self.assertEqual([m.get("brain") for m in msgs if m["role"] == "assistant"], ["Локальная модель", "Claude Sonnet"])

    def test_fallback_note_in_reply(self):
        b, svc, loc, cl, ev, said, _ = setup("auto", key=False)
        cid = svc.send(None, "напиши подробный отчёт", wait=True)
        text = svc.store.get(cid)["messages"][-1]["text"]
        self.assertTrue(text.startswith("ⓘ ")); self.assertIn("локальный ответ", text)

    def test_claude_gets_files_budget(self):
        b, svc, loc, cl, *_ = setup("claude")
        svc.send(None, "создай файл", wait=True)
        self.assertEqual(cl.calls[0][1], chat.LIMITS["claude"][1])


# ---------------------------------------------------------------- клиент Claude против поддельного сервера
class FakeAnthropic(BaseHTTPRequestHandler):
    mode = "ok"; seen: list = []; temp_rejected = False

    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAnthropic.seen.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        m = FakeAnthropic.mode
        if m in ("401", "404", "429", "529", "credit"):
            code = {"401": 401, "404": 404, "429": 429, "529": 529, "credit": 400}[m]
            msg = "Your credit balance is too low" if m == "credit" else f"boom {KEY}"
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"type": "error", "error": {"type": "x", "message": msg}}).encode()); return
        if m == "no_temp" and "temperature" in body:
            self.send_response(400); self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "`temperature` is deprecated for this model"}}).encode()); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        evs = [{"type": "message_start"}, {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "При"}},
               {"type": "ping"}, {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "вет"}}]
        if m == "stream_error":
            evs.append({"type": "error", "error": {"type": "overloaded_error", "message": f"Overloaded {KEY}"}})
        evs.append({"type": "message_stop"})
        for e in evs:
            self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()); self.wfile.flush()


class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnthropic)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1/messages"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        FakeAnthropic.mode, FakeAnthropic.seen = "ok", []
        self.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.cfg.update({"llm": {"api_key": KEY}, "brain": {"claude_model": "claude-haiku-5-5"}})
        self.c = claude.ClaudeClient(self.cfg)
        p = mock.patch.object(claude, "API_URL", self.url); p.start(); self.addCleanup(p.stop)

    def test_stream_and_request_shape(self):
        got = []
        out = self.c.chat([{"role": "system", "content": "Ты Карина"}, {"role": "user", "content": "а"}, {"role": "user", "content": "б"},
                           {"role": "assistant", "content": "в"}, {"role": "user", "content": "привет"}], max_tokens=50, on_delta=got.append)
        self.assertEqual(out, "Привет"); self.assertEqual(got, ["При", "Привет"])
        req = FakeAnthropic.seen[0]
        self.assertEqual(req["headers"]["x-api-key"], KEY); self.assertEqual(req["headers"]["anthropic-version"], "2023-06-01")
        b = req["body"]
        self.assertEqual(b["model"], "claude-haiku-5-5"); self.assertEqual(b["system"], "Ты Карина"); self.assertTrue(b["stream"]); self.assertEqual(b["max_tokens"], 50)
        self.assertEqual(b["messages"], [{"role": "user", "content": "а\n\nб"}, {"role": "assistant", "content": "в"}, {"role": "user", "content": "привет"}])  # роли чередуются

    def test_history_starting_with_assistant_is_trimmed(self):
        self.c.chat([{"role": "assistant", "content": "старое"}, {"role": "user", "content": "привет"}])
        self.assertEqual(FakeAnthropic.seen[0]["body"]["messages"], [{"role": "user", "content": "привет"}])

    def test_no_key(self):
        self.cfg.update({"llm": {"api_key": ""}})
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(claude.ClaudeError) as cm: self.c.chat([{"role": "user", "content": "x"}])
        self.assertIn("Ключ Claude не задан", str(cm.exception)); self.assertEqual(FakeAnthropic.seen, [])

    def test_env_key_works(self):
        self.cfg.update({"llm": {"api_key": ""}})
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-env"}):
            self.c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(FakeAnthropic.seen[0]["headers"]["x-api-key"], "sk-env")

    def test_errors_are_human_and_never_contain_key(self):
        expect = {"401": "не принял ключ", "404": "недоступна для вашего ключа", "429": "Слишком много запросов", "529": "перегружен", "credit": "закончились средства"}
        for mode, frag in expect.items():
            FakeAnthropic.mode = mode
            with self.assertRaises(claude.ClaudeError) as cm: self.c.chat([{"role": "user", "content": "x"}])
            self.assertIn(frag, str(cm.exception), mode); self.assertNotIn(KEY, str(cm.exception), mode)
        FakeAnthropic.mode = "stream_error"
        with self.assertRaises(claude.ClaudeError) as cm: self.c.chat([{"role": "user", "content": "x"}])
        self.assertNotIn(KEY, str(cm.exception)); self.assertIn("***", str(cm.exception))

    def test_temperature_rejected_is_retried_without(self):
        FakeAnthropic.mode = "no_temp"
        self.assertEqual(self.c.chat([{"role": "user", "content": "x"}]), "Привет")
        self.assertIn("temperature", FakeAnthropic.seen[0]["body"]); self.assertNotIn("temperature", FakeAnthropic.seen[1]["body"])
        self.c.chat([{"role": "user", "content": "x"}]); self.assertNotIn("temperature", FakeAnthropic.seen[2]["body"])   # запоминаем

    def test_network_error(self):
        with mock.patch.object(claude, "API_URL", "http://127.0.0.1:1/v1/messages"):
            with self.assertRaises(claude.ClaudeError) as cm: self.c.chat([{"role": "user", "content": "x"}])
        self.assertIn("Нет связи с Claude", str(cm.exception)); self.assertNotIn(KEY, str(cm.exception))

    def test_ping_and_stop(self):
        ok, text, sec = self.c.ping(); self.assertTrue(ok); self.assertEqual(text, "Привет")
        FakeAnthropic.mode = "401"
        ok, text, _ = self.c.ping(); self.assertFalse(ok); self.assertIn("ключ", text)
        FakeAnthropic.mode = "ok"
        ev = threading.Event(); ev.set()
        self.assertEqual(self.c.chat([{"role": "user", "content": "x"}], stop=ev), "")

    def test_default_model_and_labels(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.assertEqual(claude.ClaudeClient(cfg).model_id, "claude-sonnet-5-5")
        self.assertEqual(claude.model_label("claude-opus-5-5"), "Claude Opus")
        self.assertEqual(claude.model_label("claude-future-9"), "claude-future-9")

    def test_key_not_in_logs_or_state(self):
        """Ключ не должен попадать ни в описание состояния для окна, ни в события чата."""
        b = Brain(self.cfg, Local(), self.c)
        self.assertNotIn(KEY, json.dumps(b.describe()))
        FakeAnthropic.mode = "401"
        ev = []
        svc = chat.ChatService(self.cfg, Local(), ev.append, store=chat.ChatStore(Path(tempfile.mkdtemp()) / "chats"), brain=b)
        self.cfg.update({"brain": {"mode": "claude"}, "search": {"mode": "off"}})
        with mock.patch("karina.brain.net.is_online", return_value=True):
            cid = svc.send(None, "привет", wait=True)
        self.assertNotIn(KEY, json.dumps(ev, ensure_ascii=False)); self.assertNotIn(KEY, json.dumps(svc.store.get(cid), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
