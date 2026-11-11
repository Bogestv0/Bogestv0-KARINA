"""Кодер внутри чата: маршрутизация сообщения, ход работы и итог в диалоге, продолжение проекта, ответ «только прочитать»."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from karina import chat
from karina.chat import ChatService, ChatStore, route
from karina.coder import CoderService
from karina.config import Config
from karina.files import FileService
from karina.brain import Choice
from test_coder import Scripted


class FakeBrain:
    def __init__(self, backend): self.b = backend
    def choose(self, text, **kw): return Choice("local", self.b, "Тест")


class Route(unittest.TestCase):
    def test_with_files(self):
        self.assertEqual(route("исправь ошибку в main.py", True, False), "code")
        self.assertEqual(route("добавь тесты к приложенному коду", True, False), "code")
        for t in ("посмотри код и скажи, что не так", "объясни, что делает этот файл", "найди ошибку в коде", "сделай ревью"):
            self.assertEqual(route(t, True, False) if t != "сделай ревью" else "chat", "chat", t)

    def test_without_files(self):
        self.assertEqual(route("напиши программу, которая считает сумму по месяцам, с тестами", False, False), "code")
        self.assertEqual(route("напиши привет как дела", False, False), "chat")
        self.assertEqual(route("объясни, что такое декоратор", False, False), "chat")
        self.assertEqual(route("создай скрипт для переименования файлов", False, False), "chat")     # мелочь — остаётся обычным файлом в чате

    def test_follow_up_in_project(self):
        self.assertEqual(route("добавь в код обработку ошибок", False, True), "code")
        self.assertEqual(route("добавь в отчёт абзац про цены", False, True), "chat")                 # не про код

    def test_modes_and_voice(self):
        self.assertEqual(route("привет", False, False, mode="code"), "code")
        self.assertEqual(route("исправь main.py", True, True, mode="ask"), "chat")
        self.assertEqual(route("напиши программу для расчёта", False, False, voice=True), "chat")


def make(replies):
    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp / "c.json")
    cfg.update({"coder": {"run": True, "max_steps": 6}, "search": {"mode": "off"}})
    model = Scripted(replies)
    ev = []
    brain = FakeBrain(model)
    coder = CoderService(cfg, brain, ev.append, projects_dir=tmp / "Проекты")
    svc = ChatService(cfg, model, ev.append, store=ChatStore(tmp / "chats"), brain=brain, files=FileService(tmp / "Карина"), coder=coder)
    return svc, model, ev, tmp


GOOD = ("План.\n[[ЗАПИСАТЬ: calc.py]]\ndef add(a, b):\n    return a + b\n[[/ЗАПИСАТЬ]]\n"
        "[[ЗАПИСАТЬ: t.py]]\nfrom calc import add\nassert add(1, 2) == 3\n[[/ЗАПИСАТЬ]]\n[[ЗАПУСТИТЬ: python t.py]]")


class InChat(unittest.TestCase):
    def test_code_task_runs_inside_dialog(self):
        svc, model, ev, tmp = make([GOOD, "[[ГОТОВО]]Готово: calc.py, тест прошёл.[[/ГОТОВО]]"])
        src = tmp / "data.txt"
        src.write_text("сложение", "utf-8")
        cid = svc.send(None, "напиши функцию add и тест к ней, учти приложенный файл", paths=[str(src)], wait=True)
        c = svc.store.get(cid)
        user, reply = c["messages"]
        self.assertEqual(user["files_in"], ["data.txt"])
        self.assertIn("Готово: calc.py", reply["text"])
        self.assertIn("`calc.py`", reply["text"])                                   # список файлов проекта — в ответе
        self.assertTrue(reply["project"]["ok"])
        self.assertEqual(c["project"], reply["project"]["name"])
        self.assertTrue(any(s.startswith("✏") for s in reply["steps"]))             # «Ход работы» сохранён
        types = [e["type"] for e in ev]
        self.assertEqual(types[0], "chat_start"); self.assertEqual(types[-1], "chat_done")
        self.assertIn("chat_delta", types); self.assertIn("chat_step", types)
        self.assertNotIn("coder", types)                                            # в окно идут события чата, а не отдельной вкладки
        self.assertFalse(svc.busy); self.assertFalse(svc.coder.busy)

    def test_follow_up_continues_same_project(self):
        svc, model, ev, tmp = make([GOOD, "[[ГОТОВО]]первый[[/ГОТОВО]]", "[[ЧИТАТЬ: calc.py]]", "[[ГОТОВО]]второй[[/ГОТОВО]]"])
        cid = svc.send(None, "напиши программу сложения", mode="code", wait=True)
        first = svc.store.get(cid)["project"]
        svc.send(cid, "добавь в код вычитание", wait=True)
        c = svc.store.get(cid)
        self.assertEqual(c["project"], first)                                       # та же папка, новая не создана
        self.assertEqual(len(list((tmp / "Проекты").iterdir())) - len(list((tmp / "Проекты").glob("*.zip"))), 1)
        seen = model.seen[2][1]["content"]
        self.assertIn("ПРОДОЛЖЕНИЕ", seen); self.assertIn("calc.py", seen)
        self.assertIn("напиши программу сложения", seen)                            # прежняя переписка видна кодеру

    def test_attached_files_without_change_request_are_just_read(self):
        svc, model, ev, tmp = make(["В строке 2 деление на ноль."])
        src = tmp / "bug.py"
        src.write_text("def f(x):\n    return 1 / 0\n", "utf-8")
        cid = svc.send(None, "посмотри код и скажи, что не так", paths=[str(src)], wait=True)
        c = svc.store.get(cid)
        self.assertEqual(c["messages"][1]["text"], "В строке 2 деление на ноль.")
        self.assertNotIn("project", c)                                              # проект не создавался
        self.assertFalse((tmp / "Проекты").exists())
        sent = model.seen[0]
        self.assertIn("return 1 / 0", sent[-1]["content"])                          # модель увидела содержимое файла
        self.assertIn("Приложенные файлы", sent[0]["content"])                      # а в подсказке нет «не могу читать файлы»
        self.assertNotIn("Читать и открывать файлы на компьютере ты не можешь", sent[0]["content"])

    def test_big_attachment_is_reported_not_silently_cut(self):
        svc, model, ev, tmp = make(["Ок"])
        src = tmp / "big.py"
        src.write_text("x = 1\n" * 10000, "utf-8")
        cid = svc.send(None, "посмотри код", paths=[str(src)], wait=True)
        self.assertIn("не поместился", svc.store.get(cid)["messages"][1]["text"])

    def test_error_and_stop(self):
        svc, model, ev, tmp = make([])
        cid = svc.send(None, "напиши программу", mode="code", wait=True)           # у «модели» нет реплик → ошибка, а не зависание
        self.assertIn("⚠", svc.store.get(cid)["messages"][-1]["text"])
        self.assertFalse(svc.busy); self.assertFalse(svc.coder.busy)


if __name__ == "__main__":
    unittest.main()
