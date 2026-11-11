"""Файлы: безопасное имя, без перезаписи, только своя папка, Word/Excel/PDF/SVG/текст, очистка SVG, блоки в ответе модели, интеграция с чатом."""
import io, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import chat, files
from karina.config import Config
from karina.files import FileError, FileService
from karina.textutil import norm

MD = """# Отчёт про Байкал

Это **важный** абзац с *курсивом*, `кодом` и [ссылкой](https://ru.wikipedia.org/wiki/Байкал).

## Список
- первый
- второй

1. раз
2. два

| Показатель | Значение |
|---|---|
| Глубина | 1642 м |

```python
def f(x):
    return x * 2
```
"""


def svc():
    tmp = Path(tempfile.mkdtemp())
    return FileService(tmp / "Карина"), tmp


class Names(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(files.safe_name("отчёт.DOCX"), ("отчёт", "docx"))
        self.assertEqual(files.safe_name("..\\..\\Windows\\evil.txt"), ("evil", "txt"))       # путь отбрасывается
        self.assertEqual(files.safe_name("a/b/c.md"), ("c", "md"))
        self.assertEqual(files.safe_name('что: "это"?.txt'), ("что это", "txt"))
        self.assertEqual(files.safe_name("CON.txt"), ("_CON", "txt"))
        self.assertEqual(files.safe_name("x" * 200 + ".txt")[0], "x" * 80)
        for bad in ("a.exe", "a.bat", "a.ps1", "a.lnk", "a.vbs", "noext", ".docx", "a.dll", "a.scr"):
            with self.assertRaises(FileError, msg=bad):
                files.safe_name(bad)

    def test_wants_file(self):
        for t in ("создай файл с отчётом", "сохрани в ворд", "нарисуй схему сети", "сделай таблицу в excel", "напиши скрипт на python", "оформи в pdf", "создай документ"):
            self.assertTrue(files.wants_file(norm(t)), t)
        for t in ("расскажи про байкал", "что такое vulkan", "составь план тренировок", "напиши отчёт про рынок", "почему небо голубое"):
            self.assertFalse(files.wants_file(norm(t)), t)


class Blocks(unittest.TestCase):
    def test_extract(self):
        text = "Готово.\n\n[[ФАЙЛ: a.txt]]\nпривет\n[[/ФАЙЛ]]\n\n[[file: «b.md»]]обрыв"
        clean, reqs = files.extract(text)
        self.assertEqual(clean, "Готово.")
        self.assertEqual([(r.name, r.complete) for r in reqs], [("a.txt", True), ("b.md", False)])
        self.assertEqual(reqs[0].body.strip(), "привет")

    def test_display_hides_content(self):
        d = files.display_text("Вот [[ФАЙЛ: a.txt]]секретное содержимое[[/ФАЙЛ]] всё")
        self.assertNotIn("секретное", d); self.assertIn("a.txt", d)
        self.assertNotIn("содерж", files.display_text("[[ФАЙЛ: a.txt]]содержимое ещё пишется"))


class Create(unittest.TestCase):
    def test_no_overwrite(self):
        s, _ = svc()
        a = s.create("п.txt", "один"); b = s.create("п.txt", "два"); c = s.create("п.txt", "три")
        self.assertEqual([a["name"], b["name"], c["name"]], ["п.txt", "п (2).txt", "п (3).txt"])
        self.assertEqual((s.folder / "п.txt").read_text("utf-8"), "один\n")       # первый файл остался как был

    def test_existing_foreign_file_is_safe(self):
        s, _ = svc()
        s.folder.mkdir(parents=True)
        (s.folder / "чужой.txt").write_text("мой файл", "utf-8")
        s.create("чужой.txt", "перезапись?")
        self.assertEqual((s.folder / "чужой.txt").read_text("utf-8"), "мой файл")

    def test_stays_inside_folder(self):
        s, tmp = svc()
        r = s.create("../../../escape.txt", "x")
        self.assertEqual(r["name"], "escape.txt")
        self.assertTrue((s.folder / "escape.txt").exists())
        self.assertEqual([p.name for p in tmp.iterdir()], ["Карина"])             # рядом с папкой ничего не появилось

    def test_text_and_code(self):
        s, _ = svc()
        r = s.create("данные.csv", "```\na;b\n1;2\n```")
        self.assertEqual((s.folder / r["name"]).read_bytes(), "a;b\n1;2\n".encode("utf-8-sig"))     # Excel читает кириллицу
        r = s.create("bad.json", "{oops")
        self.assertIn("ошибка", r["warning"])
        r = s.create("ok.py", "print('привет')")
        self.assertEqual(r["warning"], ""); self.assertEqual(r["kind"], "code")
        self.assertIn("синтаксическая", s.create("x.py", "def (")["warning"])
        with self.assertRaises(FileError):
            s.create("пусто.txt", "   ")

    def test_truncated_gets_warning(self):
        s, _ = svc()
        self.assertIn("оборвался", s.create("a.md", "# текст", complete=False)["warning"])

    def test_create_all_reports_errors(self):
        s, _ = svc()
        made, errs = s.create_all([files.Request("a.txt", "x", True), files.Request("virus.exe", "x", True), files.Request("b.md", "y", True)])
        self.assertEqual([m["name"] for m in made], ["a.txt", "b.md"])
        self.assertEqual(len(errs), 1); self.assertIn("virus.exe", errs[0])
        many = [files.Request(f"f{i}.txt", "x", True) for i in range(10)]
        made, errs = s.create_all(many)
        self.assertEqual(len(made), files.MAX_FILES); self.assertTrue(any("не больше" in e for e in errs))


class Formats(unittest.TestCase):
    def test_docx(self):
        import docx
        s, _ = svc()
        r = s.create("отчёт.docx", MD)
        d = docx.Document(str(s.folder / r["name"]))
        texts = [p.text for p in d.paragraphs]
        self.assertEqual(texts[0], "Отчёт про Байкал")
        self.assertTrue(any("важный абзац" in t and "ссылкой (https://ru.wikipedia.org" in t for t in texts))
        self.assertTrue(any(t.startswith("1.") and "раз" in t for t in texts))
        self.assertEqual(len(d.tables), 1); self.assertEqual(d.tables[0].cell(1, 1).text, "1642 м")
        self.assertTrue(any("return x * 2" in t for t in texts))

    def test_pdf(self):
        s, _ = svc()
        r = s.create("отчёт.pdf", MD + "\n" + "Длинный абзац. " * 400 + "\nЭмодзи 🙂 не ломают.")
        data = (s.folder / r["name"]).read_bytes()
        self.assertTrue(data.startswith(b"%PDF")); self.assertGreater(len(data), 5000)
        self.assertGreaterEqual(data.count(b"/Type /Page\n") + data.count(b"/Type /Page "), 1)

    def test_xlsx(self):
        import openpyxl
        s, _ = svc()
        body = ("# Лист: Цены\nНазвание;Цена;Дата;Доля\nХлеб;45,5;2026-01-05;12%\nМолоко;1 200;2026-01-06;5 %\nИтого;=B2+B3;;\n"
                "# Лист: Заметки\n| Имя | Телефон |\n|---|---|\n| Аня | 007 |\n")
        r = s.create("цены.xlsx", body)
        wb = openpyxl.load_workbook(s.folder / r["name"])
        self.assertEqual(wb.sheetnames, ["Цены", "Заметки"])
        ws = wb["Цены"]
        self.assertEqual([c.value for c in ws[1]], ["Название", "Цена", "Дата", "Доля"])
        self.assertEqual(ws["B2"].value, 45.5); self.assertEqual(ws["B3"].value, 1200)
        self.assertEqual(ws["C2"].value.year, 2026); self.assertAlmostEqual(ws["D2"].value, 0.12)
        self.assertEqual(ws["B4"].value, "=B2+B3")
        self.assertTrue(ws["A1"].font.bold); self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(wb["Заметки"]["B2"].value, "007")                        # ведущие нули не теряются

    def test_xlsx_dangerous_formulas_become_text(self):
        import openpyxl
        s, _ = svc()
        r = s.create("f.xlsx", 'a;b\n=HYPERLINK("http://evil.example","клик");=1+1\n=cmd|\' /C calc\'!A0;x\n')
        ws = openpyxl.load_workbook(s.folder / r["name"]).active
        self.assertEqual(ws["A2"].data_type, "s"); self.assertEqual(ws["B2"].data_type, "f")
        self.assertEqual(ws["A3"].data_type, "s")

    def test_xlsx_comma_separated(self):
        import openpyxl
        s, _ = svc()
        r = s.create("c.xlsx", "имя,возраст\nАня,30\nБоб,25\n")
        ws = openpyxl.load_workbook(s.folder / r["name"]).active
        self.assertEqual([[c.value for c in row] for row in ws.iter_rows()], [["имя", "возраст"], ["Аня", 30], ["Боб", 25]])


class Svg(unittest.TestCase):
    EVIL = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" onload="alert(1)">'
            '<script>alert(1)</script><style>@import url(http://evil/x.css);</style>'
            '<rect x="10" y="10" width="80" height="40" fill="#cde" onclick="evil()" style="fill:url(http://evil/a)"/>'
            '<text x="20" y="30" font-family="sans-serif">Привет &amp; <tspan font-weight="bold">мир</tspan> хвост</text>'
            '<image href="http://evil/x.png"/><foreignObject><div>html</div></foreignObject>'
            '<a href="javascript:alert(1)"><circle r="5"/></a><use href="http://evil/#x"/>'
            '<linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient><path d="M0 0L5 5" fill="url(#g)"/></svg>')

    def test_sanitized(self):
        out = files.sanitize_svg(self.EVIL)
        low = out.lower()
        for bad in ("script", "onload", "onclick", "evil", "javascript", "<image", "foreignobject", "<style", "<a ", "<use", "alert"):
            self.assertNotIn(bad, low, bad)
        for good in ("<rect", "Привет &amp;", "<tspan", "мир", "хвост", "linearGradient", 'fill="url(#g)"', "xmlns="):
            self.assertIn(good, out, good)
        import xml.etree.ElementTree as ET
        ET.fromstring(out.split("\n", 1)[1])                                       # результат — корректный XML

    def test_fences_text_around_and_missing_viewbox(self):
        out = files.sanitize_svg("Вот схема:\n```xml\n<svg><rect width='5' height='5'/></svg>\n```\nготово")
        self.assertIn('viewBox="0 0 800 600"', out); self.assertIn("xmlns=", out)

    def test_rejects(self):
        for bad in ("просто текст", "<svg><rect></svg>", '<!DOCTYPE svg [<!ENTITY x "y">]><svg>&x;</svg>', "<html></html>"):
            with self.assertRaises(FileError, msg=bad):
                files.sanitize_svg(bad)

    def test_loose_ampersand_is_fixed(self):
        self.assertIn("Tom &amp; Jerry", files.sanitize_svg('<svg viewBox="0 0 9 9"><text>Tom & Jerry</text></svg>'))

    def test_preview_is_data_uri(self):
        s, _ = svc()
        r = s.create("схема.svg", self.EVIL)
        self.assertTrue(s.preview_svg(r["name"]).startswith("data:image/svg+xml;base64,"))
        s.create("a.txt", "x")
        with self.assertRaises(FileError):
            s.preview_svg("a.txt")


class Open(unittest.TestCase):
    def test_resolve_and_open_rules(self):
        calls = []
        tmp = Path(tempfile.mkdtemp())
        s = FileService(tmp / "Карина", opener=calls.append)
        (tmp / "secret.txt").write_text("вне папки", "utf-8")
        s.create("a.docx", "# x"); s.create("code.py", "print(1)"); s.create("t.txt", "x"); s.create("d.csv", "a;b\n1;2")
        for bad in ("../secret.txt", "..\\secret.txt", str(tmp / "secret.txt"), "", "нет.txt", "x.exe", ".."):
            with self.assertRaises(FileError, msg=bad):
                s.resolve(bad)
        s.open("a.docx"); self.assertEqual(calls[-1], str(s.folder / "a.docx"))                 # документы — в своей программе
        s.open("d.csv"); self.assertEqual(calls[-1], str(s.folder / "d.csv"))
        s.open("code.py"); self.assertEqual(calls[-1][0], "notepad.exe")                        # код — в Блокноте, не запускается
        s.open("t.txt"); self.assertEqual(calls[-1][0], "notepad.exe")
        s.reveal("a.docx"); self.assertEqual(calls[-1][0], "explorer")
        s.open_folder(); self.assertEqual(calls[-1], str(s.folder))


class InChat(unittest.TestCase):
    """Ответ модели с блоком файла → файл в папке, карточка в ответе, честная озвучка."""
    class Eng:
        state = "ready"
        def __init__(self, reply): self.reply, self.calls = reply, []
        def available(self): return True, ""
        def chat(self, messages, max_tokens=0, on_delta=None, stop=None, **kw):
            self.calls.append((messages, max_tokens))
            if on_delta: on_delta(self.reply[:30]); on_delta(self.reply)
            return self.reply

    def make(self, reply, spoken=None):
        tmp = Path(tempfile.mkdtemp()); ev = []
        eng = self.Eng(reply)
        svc_ = chat.ChatService(Config(tmp / "c.json"), eng, ev.append, speak=(spoken.append if spoken is not None else None),
                                store=chat.ChatStore(tmp / "chats"), files=FileService(tmp / "Карина"))
        svc_.cfg.update({"search": {"mode": "off"}})
        return svc_, ev, eng, tmp

    def test_file_created_and_reported(self):
        svc_, ev, eng, tmp = self.make("Сделала.\n\n[[ФАЙЛ: отчёт.docx]]\n# Заголовок\nТекст\n[[/ФАЙЛ]]")
        cid = svc_.send(None, "создай файл word с отчётом", wait=True)
        m = svc_.store.get(cid)["messages"][-1]
        self.assertEqual(m["text"], "Сделала."); self.assertEqual(m["files"][0]["name"], "отчёт.docx")
        self.assertTrue((tmp / "Карина" / "отчёт.docx").exists())
        done = ev[-1]; self.assertEqual(done["type"], "chat_done"); self.assertEqual(done["files"][0]["kind"], "word")
        self.assertIn("[[ФАЙЛ", eng.calls[0][0][0]["content"])                       # модель знает формат
        self.assertGreater(eng.calls[0][1], chat.MAX_TOKENS)                         # и ей дали место на длинный файл
        for e in ev:
            if e["type"] == "chat_delta": self.assertNotIn("Заголовок", e["text"])    # содержимое файла не мелькает в окне

    def test_no_file_without_request(self):
        """Обычный вопрос: формат файлов модели не описывается, а блок в ответе (например, из интернета) игнорируется."""
        svc_, ev, eng, tmp = self.make("Ответ [[ФАЙЛ: x.txt]]хитрость[[/ФАЙЛ]]")
        cid = svc_.send(None, "расскажи про байкал", wait=True)
        self.assertNotIn("[[ФАЙЛ", eng.calls[0][0][0]["content"])
        self.assertFalse((tmp / "Карина").exists())
        self.assertNotIn("files", svc_.store.get(cid)["messages"][-1])

    def test_stop_creates_nothing(self):
        svc_, ev, eng, tmp = self.make("[[ФАЙЛ: a.txt]]x[[/ФАЙЛ]]")
        orig = eng.chat
        def stopping(*a, **k):
            r = orig(*a, **k); svc_._stop.set(); return r
        eng.chat = stopping
        svc_.send(None, "создай файл", wait=True)
        self.assertFalse((tmp / "Карина").exists())

    def test_bad_request_is_explained_and_voice_is_honest(self):
        said = []
        svc_, ev, eng, tmp = self.make("ГОЛОС: Файл создан!\n\n[[ФАЙЛ: вирус.exe]]x[[/ФАЙЛ]]", said)
        cid = svc_.send(None, "создай файл", voice=True, wait=True)
        text = svc_.store.get(cid)["messages"][-1]["text"]
        self.assertIn("вирус.exe", text); self.assertIn("не создан", text)
        self.assertTrue(said[0].startswith("Не удалось создать файл"))               # «Файл создан!» от модели не озвучивается

    def test_voice_confirms_real_file(self):
        said = []
        svc_, ev, eng, tmp = self.make("ГОЛОС: Что-то своё.\n\n[[ФАЙЛ: a.txt]]x[[/ФАЙЛ]]", said)
        svc_.send(None, "создай файл", voice=True, wait=True)
        self.assertIn("папке Карина", said[0])

    def test_history_remembers_file(self):
        svc_, ev, eng, tmp = self.make("Ок [[ФАЙЛ: a.txt]]x[[/ФАЙЛ]]")
        cid = svc_.send(None, "создай файл a", wait=True)
        svc_.send(cid, "добавь ещё раздел", wait=True)
        hist = " ".join(m["content"] for m in eng.calls[-1][0])
        self.assertIn("[Создан файл: a.txt]", hist)

    def test_voice_intents(self):
        for t in ("создай файл с отчётом", "создай документ про байкал", "сделай таблицу в excel", "нарисуй схему сети", "сохрани это в ворд", "создай pdf"):
            self.assertTrue(chat.is_chat_request(norm(t)), t)
        for t in ("сделай скриншот", "создай новую вкладку", "сделай громче"):
            self.assertFalse(chat.is_chat_request(norm(t)), t)
        self.assertTrue(chat.wants_open_files(norm("открой папку с файлами карины")))
        self.assertTrue(chat.wants_open_files(norm("покажи мои файлы карины")))
        self.assertFalse(chat.wants_open_files(norm("открой блокнот")))


if __name__ == "__main__":
    unittest.main()
