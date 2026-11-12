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
    def test_files_go_to_agent_by_default(self):
        """Приложены файлы и «Код» включён: работает агент — и для «сделай», и для «посмотри» (он читает архивы и большие проекты по частям)."""
        for t in ("исправь ошибку в main.py", "добавь тесты к приложенному коду", "посмотри написаный код в файле", "вот что я хочу сделать", "переделай по описанию"):
            self.assertEqual(route(t, True, False), "code", t)

    def test_small_files_just_read(self):
        for t in ("посмотри код и скажи, что не так", "объясни, что делает этот файл", "найди ошибку в коде"):
            self.assertEqual(route(t, True, False, light=True), "chat", t)
        self.assertEqual(route("исправь ошибку", True, False, light=True), "code")           # просят изменить — агент
        self.assertEqual(route("посмотри код", True, False, light=False), "code")           # архив, папка, большой файл — агент

    def test_without_files(self):
        self.assertEqual(route("напиши программу, которая считает сумму по месяцам, с тестами", False, False), "code")
        self.assertEqual(route("создай скрипт для переименования файлов", False, False), "code")           # скрипты тоже пишем и проверяем
        self.assertEqual(route("сделай шаблон страницы для кофейни", False, False), "code")               # оформление: агент + предпросмотр
        self.assertEqual(route("напиши привет как дела", False, False), "chat")
        self.assertEqual(route("объясни, что такое декоратор", False, False), "chat")
        self.assertEqual(route("напиши письмо начальнику", False, False), "chat")
        self.assertEqual(route("создай документ Word с планом проекта", False, False), "chat")           # документ — это файл, а не программа

    def test_follow_up_in_project(self):
        self.assertEqual(route("добавь в код обработку ошибок", False, True), "code")
        self.assertEqual(route("посмотри, что не так с тестом", False, True), "code")
        self.assertEqual(route("добавь в отчёт абзац про цены", False, True), "chat")                 # не про код

    def test_modes_and_voice(self):
        self.assertEqual(route("привет", False, False, mode="code"), "code")
        self.assertEqual(route("исправь main.py", True, True, mode="ask"), "chat")                   # «Код» выключен — только чат
        self.assertEqual(route("напиши программу для расчёта", False, False, voice=True), "chat")

    def test_short_follow_up_uses_conversation(self):
        ctx = "хочу телеграм бота, который заливает файлы на гитхаб через воркер"
        self.assertEqual(route("сделай файлы", False, False, context=ctx), "code")
        self.assertEqual(route("сделай файлы", False, False), "chat")                       # без контекста — не гадаем
        self.assertEqual(route("сделай файлы", False, False, context="расскажи про погоду"), "chat")
        self.assertEqual(route("напиши это", False, False, context=ctx), "code")

    def test_images(self):
        self.assertEqual(route("Сделай изображения аниме девушки с белыми волосами", False, False), "image")
        self.assertEqual(route("нарисуй кота", False, False, mode="ask"), "image")                    # картинки работают и при выключенном «Коде»
        self.assertEqual(route("нарисуй схему базы данных", False, False), "chat")                   # схема — это SVG-файл, не картинка
        self.assertEqual(route("ещё вариант", False, False, last_image=True), "image")
        self.assertEqual(route("ещё вариант", False, False, last_image=False), "chat")


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
        self.assertEqual(sorted(f["path"] for f in reply["project"]["files"]), ["calc.py", "t.py"])   # файлы проекта — карточками
        self.assertTrue(reply["project"]["ok"])
        self.assertEqual(c["project"], reply["project"]["name"])
        kinds = [a["k"] for a in reply["activity"]]                                  # лента действий сохранена: слова → записи → запуск
        self.assertEqual(kinds[:1], ["start"]); self.assertIn("say", kinds); self.assertIn("write", kinds); self.assertIn("run", kinds)
        run = [a for a in reply["activity"] if a["k"] == "run"][0]
        self.assertEqual((run["cmd"], run["ok"]), ("python t.py", True))
        types = [e["type"] for e in ev]
        self.assertEqual(types[0], "chat_start"); self.assertEqual(types[-1], "chat_done")
        self.assertIn("chat_activity", types); self.assertIn("chat_step", types)
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

    def test_small_file_with_question_is_just_read(self):
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

    def test_big_attachment_goes_to_agent_who_reads_in_parts(self):
        svc, model, ev, tmp = make(["[[ЧИТАТЬ: input/big.py:1-3]]", "[[ГОТОВО]]Файл состоит из одинаковых строк.[[/ГОТОВО]]"])
        src = tmp / "big.py"
        src.write_text("x = 1\n" * 10000, "utf-8")
        cid = svc.send(None, "посмотри код", paths=[str(src)], wait=True)
        self.assertIn("Файл состоит", svc.store.get(cid)["messages"][1]["text"])
        self.assertIn("1: x = 1", model.seen[1][-1]["content"])                     # прочитан кусок со строками, а не весь файл

    def test_image_turn(self):
        from karina import images
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
        orig = images.generate
        seen = {}
        def fake(cfg, prompt, size=(768, 768), seed=None, stop=None, request="", progress=None):
            seen["prompt"], seen["size"] = prompt, size
            return png, "png", "Тестовый сервис"
        images.generate = fake
        try:
            svc, model, ev, tmp = make(["1girl, anime style, long white hair, masterpiece, high detail"])
            cid = svc.send(None, "Сделай изображения аниме девушки с белыми волосами", wait=True)
            reply = svc.store.get(cid)["messages"][1]
            self.assertTrue(seen["prompt"].startswith("anime illustration, 2D"))                                   # для аниме добавлен 2D-стиль
            self.assertIn("1girl, anime style, long white hair, masterpiece, high detail", seen["prompt"])                 # модель написала описание (дальше идёт хвост качества)
            self.assertEqual(reply["files"][0]["kind"], "image")
            self.assertTrue((tmp / "Карина" / reply["files"][0]["name"]).read_bytes().startswith(b"\x89PNG"))
            self.assertIn("нарисовала", reply["text"]); self.assertEqual(reply["image_prompt"], seen["prompt"])
            # «ещё вариант» — продолжает ту же картинку, прежнее описание передаётся модели
            model.replies.append("1girl, anime, long white hair, forest background")
            svc.send(cid, "сделай фон лесом", wait=True)
            self.assertIn("long white hair, masterpiece", model.seen[-1][-1]["content"])
            self.assertIn("1girl, anime, long white hair, forest background", seen["prompt"])
        finally:
            images.generate = orig

    def test_image_error_is_explained(self):
        from karina import images
        orig = images.generate
        def boom(*a, **k): raise images.ImageError("нет связи с сервисом картинок")
        images.generate = boom
        try:
            svc, model, ev, tmp = make(["anime girl"])
            cid = svc.send(None, "нарисуй кота", wait=True)
            reply = svc.store.get(cid)["messages"][1]
            self.assertIn("Не получилось нарисовать", reply["text"]); self.assertIn("нет связи", reply["text"])
            self.assertFalse(svc.busy)
        finally:
            images.generate = orig

    def test_error_and_stop(self):
        svc, model, ev, tmp = make([])
        cid = svc.send(None, "напиши программу", mode="code", wait=True)           # у «модели» нет реплик → ошибка, а не зависание
        self.assertIn("⚠", svc.store.get(cid)["messages"][-1]["text"])
        self.assertFalse(svc.busy); self.assertFalse(svc.coder.busy)


if __name__ == "__main__":
    unittest.main()
