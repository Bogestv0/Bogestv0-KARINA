"""1.18.0: вкладка «ИИ» на острове (чат с Кариной) и 3 персонажа."""
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from karina import island as isl_mod
from karina import island_tools as tools

HTML = (Path(__file__).resolve().parent.parent / "ui" / "island.html").read_text("utf-8")


class FakeStore:
    def __init__(self):
        self.chats = {}

    def new(self):
        c = {"id": "c%d" % (len(self.chats) + 1), "messages": []}
        self.chats[c["id"]] = c
        return c

    def get(self, cid):
        return self.chats.get(cid)


class FakeChat:
    def __init__(self):
        self.store, self.busy, self.sent, self.stopped = FakeStore(), False, [], 0

    def send(self, cid, text, mode="auto", **kw):
        if self.busy:
            return None
        self.sent.append((cid, text, mode))
        self.store.chats[cid]["messages"] += [{"role": "user", "text": text}, {"role": "assistant", "text": "ответ"}]
        return cid

    def stop(self):
        self.stopped += 1


def make_api():
    chat = FakeChat()
    api = SimpleNamespace(assistant=SimpleNamespace(chat=chat, say=lambda t: None))
    opened = []
    return isl_mod.IslandApi(None, api, lambda: opened.append(1)), chat, opened


class AiChatTest(unittest.TestCase):
    def test_send_creates_chat_in_ask_mode_and_poll_returns_messages(self):
        a, chat, _ = make_api()
        self.assertEqual(a.ai_poll(), {"cid": None, "busy": False, "msgs": []})
        r = a.ai_send("  привет  ")
        self.assertTrue(r["ok"])
        self.assertEqual(chat.sent, [("c1", "привет", "ask")])
        p = a.ai_poll()
        self.assertEqual([m["role"] for m in p["msgs"]], ["user", "assistant"])
        a.ai_send("ещё")                                           # тот же диалог
        self.assertEqual(chat.sent[-1][0], "c1")

    def test_empty_and_busy(self):
        a, chat, _ = make_api()
        self.assertFalse(a.ai_send("   ")["ok"])
        chat.busy = True
        r = a.ai_send("привет")
        self.assertFalse(r["ok"])
        self.assertIn("отвечаю", r["error"])
        self.assertTrue(a.ai_poll()["busy"])

    def test_new_and_stop_and_poll_limit(self):
        a, chat, _ = make_api()
        a.ai_send("1")
        a.ai_new()
        a.ai_send("2")
        self.assertEqual(chat.sent[-1][0], "c2")
        for i in range(20):
            chat.store.chats["c2"]["messages"].append({"role": "user", "text": str(i)})
        self.assertEqual(len(a.ai_poll()["msgs"]), a.AI_SHOW)
        a.ai_stop()
        self.assertEqual(chat.stopped, 1)

    def test_open_main(self):
        a, _, opened = make_api()
        a.ai_open()
        for t in threading.enumerate():
            if t is not threading.current_thread():
                t.join(0.5)
        self.assertEqual(opened, [1])


class CharactersTest(unittest.TestCase):
    def test_only_three_characters(self):
        self.assertEqual(tools.CHARACTERS, ("logo", "eyes", "pixel", "anime"))
        for k in ("logo", "eyes", "pixel", "anime"):
            self.assertEqual(tools.clean_settings({"character": k})["character"], k)
        self.assertEqual(tools.clean_settings({"character": "nope"})["character"], "logo")

    def test_removed_character_in_old_config_falls_back_to_logo(self):
        for k in ("spark", "bot", "cat", "blob", "ginger", "deer"):
            self.assertEqual(tools.clean_settings({}, {"character": k})["character"], "logo")

    def test_page_has_only_those_characters_and_ai_tab(self):
        for k in ("eyes", "pixel"):
            self.assertRegex(HTML, r"\b%s:\s*`<svg" % k)
        for k in ("spark", "bot", "cat", "blob", "ginger", "deer"):
            self.assertNotRegex(HTML, r"\n    %s:\s*`<svg" % k)
        for n in ('idle', 'closed', 'left', 'right', 'up', 'down', 'hair', 'talk'):
            self.assertIn("'%s'" % n, HTML.split('const ANF')[1].split('\n')[0])
            self.assertTrue((Path(__file__).resolve().parent.parent / 'ui' / ('an_%s.png' % n)).exists())
        self.assertIn('data-t="ai"', HTML)
        self.assertIn('id="p-ai"', HTML)
        for m in ("ai_send", "ai_poll", "ai_stop", "ai_open", "ai_say"):
            self.assertIn("'%s'" % m, HTML)
            self.assertTrue(hasattr(isl_mod.IslandApi, m))


if __name__ == "__main__":
    unittest.main()
