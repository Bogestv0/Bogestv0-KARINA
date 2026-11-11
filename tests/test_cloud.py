"""Бесплатные облачные модели: потоковый ответ, переход к следующему провайдеру при лимите, большие запросы, ключи, режим «мозга».
Настоящий интернет не нужен: вместо провайдеров — маленький локальный сервер, который отвечает как OpenAI-совместимый API."""
import json, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import chat, cloud
from karina.brain import Brain, BrainUnavailable
from karina.config import Config
from karina.files import FileService
from test_brain import Local, FakeClaude

K1, K2, K3 = "gsk_SECRET_KEY_111111", "AIza_SECRET_KEY_222222", "sk-or-SECRET_KEY_333333"


class Fake:
    """Что отвечает «провайдер»: по ключу — список сценариев (каждый запрос берёт следующий; последний повторяется)."""
    def __init__(self):
        self.script, self.hits, self.bodies, self.models = {}, {}, {}, {}


def chunk(text):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False) + "\n\n"


class Handler(BaseHTTPRequestHandler):
    fake: Fake = None

    def log_message(self, *a):
        pass

    def _key(self):
        return self.headers.get("Authorization", "").replace("Bearer ", "")

    def do_GET(self):          # /models
        pre = self.path.split("/")[1]
        body = json.dumps({"data": [{"id": m} for m in self.fake.models.get(pre, [])]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        pre = self.path.split("/")[1]
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.fake.hits[pre] = self.fake.hits.get(pre, 0) + 1
        self.fake.bodies.setdefault(pre, []).append(body)
        steps = self.fake.script.get(pre, [("ok", "ответ " + pre)])
        kind, val = steps[min(self.fake.hits[pre] - 1, len(steps) - 1)]
        if kind == "http":
            code, text, headers = val
            data = text.encode()
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        if kind == "ok":
            for part in val if isinstance(val, list) else [val]:
                self.wfile.write(chunk(part).encode())
            self.wfile.write(b"data: [DONE]\n\n")
        elif kind == "midError":
            self.wfile.write(chunk("начало").encode())
            self.wfile.write(("data: " + json.dumps({"error": {"message": "обрыв"}}) + "\n\n").encode())
        elif kind == "empty":
            self.wfile.write(b"data: [DONE]\n\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        Handler.fake = self.fake
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        port = self.srv.server_address[1]
        self.saved = {k: dict(v) for k, v in cloud.PRESETS.items()}
        for pid in ("groq", "gemini", "openrouter"):
            cloud.PRESETS[pid]["url"] = f"http://127.0.0.1:{port}/{pid}"
        self.addCleanup(lambda: [cloud.PRESETS.__setitem__(k, v) for k, v in self.saved.items()])
        self.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.pool = cloud.CloudPool(self.cfg)

    def enable(self, **keys):
        provs = {pid: {"enabled": True, "key": key} for pid, key in keys.items()}
        self.cfg.update({"cloud": {"providers": provs}})

    def ask(self, text="привет", **kw):
        return self.pool.chat([{"role": "system", "content": "Ты Карина"}, {"role": "user", "content": text}], **kw)


class TestStreaming(Base):
    def test_streams_and_labels_provider(self):
        self.enable(groq=K1)
        self.fake.script["groq"] = [("ok", ["При", "вет, ", "мир"])]
        seen = []
        self.assertEqual(self.ask(on_delta=seen.append), "Привет, мир")
        self.assertEqual(seen[-1], "Привет, мир")
        self.assertEqual(self.pool.last_label, "Groq · openai/gpt-oss-120b")
        d = {x["id"]: x for x in self.pool.describe()}
        self.assertEqual((d["groq"]["state"], d["groq"]["used_today"]), ("ok", 1))

    def test_request_shape_and_headers(self):
        self.enable(groq=K1)
        self.ask(max_tokens=9999, temperature=0.3)
        b = self.fake.bodies["groq"][0]
        self.assertEqual(b["model"], "openai/gpt-oss-120b")
        self.assertTrue(b["stream"])
        self.assertEqual(b["max_tokens"], cloud.PRESETS["groq"]["max_out"])       # потолок ответа у провайдера
        self.assertEqual(b["reasoning_effort"], "low")
        self.assertEqual(b["messages"][0]["role"], "system")

    def test_think_blocks_are_hidden(self):
        self.enable(groq=K1)
        self.fake.script["groq"] = [("ok", ["<think>рассуждаю", " долго</think>", "Ответ"])]
        seen = []
        self.assertEqual(self.ask(on_delta=seen.append), "Ответ")
        self.assertTrue(all("<think>" not in s and "рассуждаю" not in s for s in seen))

    def test_json_mode_adds_instruction(self):
        self.enable(groq=K1)
        self.ask(json_mode=True)
        self.assertIn("JSON", self.fake.bodies["groq"][0]["messages"][0]["content"])

    def test_stop_returns_partial_without_error(self):
        self.enable(groq=K1)
        ev = threading.Event(); ev.set()
        self.assertEqual(self.ask(stop=ev), "")

    def test_nothing_configured(self):
        with self.assertRaises(cloud.CloudError) as cm:
            self.ask()
        self.assertIn("не настроены", str(cm.exception))


class TestRotation(Base):
    def test_limit_moves_to_next_and_provider_rests(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (429, json.dumps({"error": {"message": "Rate limit reached. Please try again in 2m30s."}}), {}))]
        self.assertEqual(self.ask(), "ответ gemini")
        self.assertEqual(self.pool.last_label.split(" · ")[0], "Google Gemini")
        d = {x["id"]: x for x in self.pool.describe()}
        self.assertEqual(d["groq"]["state"], "cooldown")
        self.assertTrue(140 <= d["groq"]["wait"] <= 151, d["groq"]["wait"])
        self.assertIn("вернусь через", d["groq"]["text"])
        self.assertEqual(self.ask(), "ответ gemini")
        self.assertEqual(self.fake.hits["groq"], 1)           # пока отдыхает — к нему не ходим

    def test_retry_after_header(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (429, "{}", {"Retry-After": "42"}))]
        self.ask()
        self.assertTrue(38 <= {x["id"]: x for x in self.pool.describe()}["groq"]["wait"] <= 43)

    def test_daily_limit_rests_long(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (429, json.dumps({"error": {"message": "Rate limit reached for model in organization on tokens per day (TPD)"}}), {}))]
        self.ask()
        self.assertGreaterEqual({x["id"]: x for x in self.pool.describe()}["groq"]["wait"], 3000)

    def test_provider_comes_back_after_rest(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (429, "{}", {"Retry-After": "5"})), ("ok", "ответ groq")]
        self.ask()
        with mock.patch.object(cloud.time, "time", return_value=cloud.time.time() + 10):
            self.assertEqual(self.ask(), "ответ groq")

    def test_all_limited_gives_clear_error(self):
        self.enable(groq=K1, gemini=K2)
        for p in ("groq", "gemini"):
            self.fake.script[p] = [("http", (429, "{}", {"Retry-After": "120"}))]
        with self.assertRaises(cloud.CloudError) as cm:
            self.ask()
        self.assertIn("Groq", str(cm.exception))
        self.assertIn("Gemini", str(cm.exception))
        with self.assertRaises(cloud.CloudError) as cm2:
            self.ask()                                         # теперь уже без запросов: все отдыхают
        self.assertIn("исчерпаны", str(cm2.exception))
        self.assertIn("2 мин", str(cm2.exception))
        self.assertEqual((self.fake.hits["groq"], self.fake.hits["gemini"]), (1, 1))

    def test_bad_key_is_reported_and_next_used(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (401, json.dumps({"error": {"message": f"Invalid API Key {K1}"}}), {}))]
        self.assertEqual(self.ask(), "ответ gemini")
        d = {x["id"]: x for x in self.pool.describe()}
        self.assertEqual(d["groq"]["state"], "error")
        self.assertIn("ключ не принят", d["groq"]["text"])
        self.assertNotIn(K1, json.dumps(d))

    def test_server_error_and_midstream_failure_fall_through(self):
        self.enable(groq=K1, gemini=K2, openrouter=K3)
        self.fake.script["groq"] = [("http", (503, "{}", {}))]
        self.fake.script["gemini"] = [("midError", None)]
        self.fake.models["openrouter"] = ["x/y:free"]
        self.assertEqual(self.ask(), "ответ openrouter")

    def test_empty_answer_falls_through(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("empty", None)]
        self.assertEqual(self.ask(), "ответ gemini")

    def test_disabled_or_keyless_provider_is_skipped(self):
        self.enable(groq=K1, gemini=K2)
        self.cfg.update({"cloud": {"providers": {"groq": {"enabled": False}}}})
        self.ask()
        self.assertNotIn("groq", self.fake.hits)

    def test_order_can_be_changed(self):
        self.enable(groq=K1, gemini=K2)
        self.cfg.update({"cloud": {"order": ["gemini", "groq"]}})
        self.ask()
        self.assertEqual(self.pool.last_label.split(" · ")[0], "Google Gemini")
        self.assertNotIn("groq", self.fake.hits)

    def test_spread_alternates(self):
        self.enable(groq=K1, gemini=K2)
        self.cfg.update({"cloud": {"strategy": "spread"}})
        used = []
        for _ in range(4):
            self.ask()
            used.append(self.pool.last_label.split(" · ")[0])
        self.assertEqual(used, ["Groq", "Google Gemini", "Groq", "Google Gemini"])


class TestSizing(unittest.TestCase):
    def msgs(self, n, size=400):
        out = [{"role": "system", "content": "Ты Карина"}]
        for i in range(n):
            out += [{"role": "user", "content": f"вопрос {i} " + "я" * size}, {"role": "assistant", "content": f"ответ {i} " + "а" * size}]
        out.append({"role": "user", "content": "последний вопрос"})
        return out

    def test_old_history_dropped_last_question_kept(self):
        m = self.msgs(10)
        fit = cloud.fit_messages(m, 800)
        self.assertLess(cloud.estimate_tokens(fit), 800)
        self.assertEqual(fit[0]["role"], "system")
        self.assertEqual(fit[-1]["content"], "последний вопрос")
        self.assertEqual(fit[1]["role"], "user")

    def test_small_request_untouched_and_impossible_returns_none(self):
        m = self.msgs(1)
        self.assertEqual(cloud.fit_messages(m, 5000), m)
        self.assertIsNone(cloud.fit_messages([{"role": "user", "content": "я" * 20000}], 500))

    def test_force_fit_cuts_the_middle(self):
        m = [{"role": "system", "content": "s"}, {"role": "user", "content": "начало " + "я" * 20000 + " конец"}]
        out = cloud.force_fit(m, 1000)
        self.assertLessEqual(cloud.estimate_tokens(out), 1100)
        self.assertTrue(out[1]["content"].startswith("начало"))
        self.assertTrue(out[1]["content"].endswith("конец"))
        self.assertIn("пропущена", out[1]["content"])

    def test_strip_think(self):
        self.assertEqual(cloud.strip_think("<think>a</think> ответ"), "ответ")
        self.assertEqual(cloud.strip_think("<think>недодумал"), "")
        self.assertEqual(cloud.strip_think("обычный"), "обычный")


class TestBigRequests(Base):
    def test_big_request_skips_small_window_provider(self):
        self.enable(groq=K1, gemini=K2)
        big = "я" * 30000                                    # ≈11 тыс. токенов: у Groq-пресета окно 5500
        self.assertEqual(self.ask(big), "ответ gemini")
        self.assertNotIn("groq", self.fake.hits)

    def test_small_request_still_uses_first(self):
        self.enable(groq=K1, gemini=K2)
        self.ask("коротко")
        self.assertEqual(self.pool.last_label.split(" · ")[0], "Groq")

    def test_too_big_for_everyone_is_trimmed_and_sent_to_largest(self):
        self.enable(groq=K1)
        self.ask("я" * 60000)
        sent = self.fake.bodies["groq"][0]["messages"][-1]["content"]
        self.assertLess(len(sent), 20000)

    def test_ctx_tokens_follows_biggest_enabled(self):
        self.enable(groq=K1)
        self.assertEqual(self.pool.ctx_tokens, 5500)
        self.enable(gemini=K2)
        self.assertEqual(self.pool.ctx_tokens, 32768)


class TestRetriesAndModels(Base):
    def test_400_retries_without_extra_parameters(self):
        self.enable(groq=K1)
        self.fake.script["groq"] = [("http", (400, json.dumps({"error": {"message": "unknown parameter reasoning_effort"}}), {})), ("ok", "готово")]
        self.assertEqual(self.ask(), "готово")
        first, second = self.fake.bodies["groq"]
        self.assertIn("reasoning_effort", first)
        self.assertNotIn("reasoning_effort", second)
        self.assertNotIn("temperature", second)

    def test_model_is_picked_automatically_and_remembered(self):
        self.enable(openrouter=K3)
        self.fake.models["openrouter"] = ["a/paid-model", "z/nice:free", "text-embedding-3", "b/other:free"]
        self.ask()
        self.assertEqual(self.fake.bodies["openrouter"][0]["model"], "b/other:free")
        self.assertEqual(self.pool.describe()[2]["model_auto"], "b/other:free")
        self.assertIn("openrouter · b/other:free".replace("openrouter", "OpenRouter"), self.pool.last_label)

    def test_wrong_model_forgets_pick_and_rests_briefly(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (404, json.dumps({"error": {"message": "model not found"}}), {}))]
        self.assertEqual(self.ask(), "ответ gemini")
        self.assertEqual({x["id"]: x for x in self.pool.describe()}["groq"]["state"], "error")

    def test_list_models_filters_and_puts_free_first(self):
        self.enable(openrouter=K3)
        self.fake.models["openrouter"] = ["a/paid", "whisper-large", "b/free:free"]
        self.assertEqual(self.pool.list_models("openrouter"), ["b/free:free", "a/paid"])

    def test_list_models_needs_key(self):
        with self.assertRaises(cloud.CloudError):
            self.pool.list_models("groq")

    def test_ping_ok_and_failed(self):
        self.enable(groq=K1)
        ok, text, sec = self.pool.ping("groq")
        self.assertTrue(ok)
        self.assertIn("Groq", text)
        self.fake.script["groq"] = [("http", (401, "{}", {}))]
        ok, text, _ = self.pool.ping("groq")
        self.assertFalse(ok)
        self.assertIn("ключ не принят", text)
        self.assertFalse(self.pool.ping("gemini")[0])          # ключа нет

    def test_custom_server(self):
        port = self.srv.server_address[1]
        self.cfg.update({"cloud": {"providers": {"custom": {"enabled": True, "key": "k-custom-12345", "url": f"http://127.0.0.1:{port}/openrouter/", "name": "Мой Ollama", "model": "llama3"}}}})
        self.assertEqual(self.ask(), "ответ openrouter")
        self.assertEqual(self.pool.last_label, "Мой Ollama · llama3")


class TestBrainModes(Base):
    def brain(self, mode, local_ok=True, claude_key=False, online=True):
        self.cfg.update({"brain": {"mode": mode}, "search": {"mode": "off"}})
        p = mock.patch("karina.brain.net.is_online", return_value=online)
        p.start()
        self.addCleanup(p.stop)
        return Brain(self.cfg, Local(local_ok), FakeClaude(claude_key), self.pool)

    def test_cloud_mode(self):
        self.enable(groq=K1)
        c = self.brain("cloud").choose("привет")
        self.assertEqual((c.name, c.label), ("cloud", "Бесплатное облако"))
        self.assertIs(c.backend, self.pool)

    def test_cloud_mode_without_keys_says_why(self):
        with self.assertRaises(BrainUnavailable) as cm:
            self.brain("cloud").choose("привет")
        self.assertIn("не настроены", str(cm.exception))

    def test_cloud_mode_offline(self):
        self.enable(groq=K1)
        with self.assertRaises(BrainUnavailable) as cm:
            self.brain("cloud", online=False).choose("привет")
        self.assertIn("интернета", str(cm.exception))

    def test_cloud_mode_all_resting(self):
        self.enable(groq=K1)
        self.pool._mark_fail(self.pool.provider("groq"), cloud._Fail("лимит", 300, "limit"))
        with self.assertRaises(BrainUnavailable) as cm:
            self.brain("cloud").choose("привет")
        self.assertIn("5 мин", str(cm.exception))

    def test_auto_simple_is_local_complex_prefers_cloud_when_no_claude(self):
        self.enable(groq=K1)
        b = self.brain("auto")
        self.assertEqual(b.choose("привет").name, "local")
        c = b.choose("напиши подробный отчёт о Байкале")
        self.assertEqual(c.name, "cloud")
        self.assertIn("Бесплатное облако", c.note)

    def test_auto_weak_pc_without_local_uses_cloud(self):
        self.enable(groq=K1)
        c = self.brain("auto", local_ok=False).choose("привет")
        self.assertEqual(c.name, "cloud")

    def test_auto_complex_still_prefers_claude(self):
        self.enable(groq=K1)
        self.assertEqual(self.brain("auto", claude_key=True).choose("напиши подробный отчёт").name, "claude")

    def test_auto_nobody_message_unchanged_without_cloud(self):
        with self.assertRaises(BrainUnavailable) as cm:
            self.brain("auto", local_ok=False).choose("привет")
        self.assertNotIn("Облако", str(cm.exception))

    def test_describe_has_cloud(self):
        self.enable(groq=K1, gemini=K2)
        d = self.brain("cloud").describe()
        self.assertEqual((d["cloud"]["ok"], d["cloud"]["count"]), (True, 2))

    def test_chat_answers_through_cloud_and_names_provider(self):
        self.enable(groq=K1, gemini=K2)
        self.fake.script["groq"] = [("http", (429, "{}", {"Retry-After": "60"}))]
        b = self.brain("cloud")
        tmp = Path(tempfile.mkdtemp())
        ev = []
        svc = chat.ChatService(self.cfg, b.engine, ev.append, speak=lambda t: None, store=chat.ChatStore(tmp / "chats"), brain=b, files=FileService(tmp / "К"))
        cid = svc.send(None, "привет", wait=True)
        m = svc.store.get(cid)["messages"][-1]
        self.assertEqual(m["text"], "ответ gemini")
        self.assertTrue(m["brain"].startswith("Google Gemini"), m["brain"])      # ответил второй — так и подписано


class TestApi(Base):
    def api(self):
        from karina.ui_api import Api
        a = Api(self.cfg, lambda: None)
        asst = mock.Mock()
        asst.brain = Brain(self.cfg, Local(), FakeClaude(False), self.pool)
        a.bind(asst)
        return a

    def test_save_keeps_key_hidden_from_window(self):
        a = self.api()
        st = a.cloud_save("groq", {"key": K1, "enabled": True, "model": "openai/gpt-oss-20b"})
        g = {p["id"]: p for p in st["providers"]}["groq"]
        self.assertTrue(g["has_key"] and g["enabled"])
        self.assertEqual(g["model"], "openai/gpt-oss-20b")
        self.assertNotIn(K1, json.dumps(st))
        self.assertNotIn(K1, json.dumps(a._safe_cfg()))
        self.assertTrue(a._safe_cfg()["cloud"]["providers"]["groq"]["has_key"])

    def test_empty_key_keeps_old_and_clear_removes(self):
        a = self.api()
        a.cloud_save("groq", {"key": K1, "enabled": True})
        a.cloud_save("groq", {"key": "", "model": "x"})
        self.assertEqual(self.cfg["cloud"]["providers"]["groq"]["key"], K1)
        st = a.cloud_clear_key("groq")
        g = {p["id"]: p for p in st["providers"]}["groq"]
        self.assertFalse(g["has_key"] or g["enabled"])

    def test_junk_input_ignored(self):
        a = self.api()
        a.cloud_save("нет-такого", {"key": K1})
        a.cloud_save("groq", {"key": "с пробелом внутри"})
        a.cloud_save("custom", {"url": "file:///etc/passwd", "name": "x" * 100})
        self.assertNotIn("нет-такого", self.cfg["cloud"]["providers"])
        self.assertEqual(self.cfg["cloud"]["providers"].get("groq", {}).get("key", ""), "")
        self.assertEqual(self.cfg["cloud"]["providers"]["custom"]["url"], "")
        self.assertEqual(len(self.cfg["cloud"]["providers"]["custom"]["name"]), 40)

    def test_move_and_strategy(self):
        a = self.api()
        st = a.cloud_move("gemini", -1)
        self.assertEqual([p["id"] for p in st["providers"]][:2], ["gemini", "groq"])
        st = a.cloud_move("gemini", -1)                       # выше некуда
        self.assertEqual(st["providers"][0]["id"], "gemini")
        self.assertEqual(a.cloud_strategy("spread")["strategy"], "spread")
        self.assertEqual(a.cloud_strategy("взлом")["strategy"], "spread")

    def test_cloud_cannot_be_changed_through_save_settings(self):
        a = self.api()
        a.assistant.reload_audio = mock.Mock()
        a.save_settings({"cloud": {"providers": {"groq": {"key": K1, "enabled": True}}}})
        self.assertEqual(self.cfg["cloud"]["providers"], {})

    def test_changing_key_forgets_old_failure(self):
        a = self.api()
        a.cloud_save("groq", {"key": K1, "enabled": True})
        self.pool._mark_fail(self.pool.provider("groq"), cloud._Fail("ключ не принят", 3600, "auth"))
        a.cloud_save("groq", {"key": K2})
        self.assertEqual({p["id"]: p for p in a.cloud_state()["providers"]}["groq"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
