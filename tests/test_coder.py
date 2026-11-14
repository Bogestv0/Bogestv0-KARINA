"""Кодер: разбор команд модели, границы путей и запуска, весь цикл «написать → проверить → исправить → отдать»."""
import sys, tempfile, threading, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import coder
from karina.coder import CoderError, CoderService, Runner, Workspace, parse
from karina.config import Config


class Parse(unittest.TestCase):
    def test_blocks(self):
        t = "План.\n[[ЗАПИСАТЬ: a/b.py]]\n```python\nprint(1)\n```\n[[/ЗАПИСАТЬ]]\n[[ЗАПУСТИТЬ: python a/b.py]]\n[[ЧИТАТЬ: x.txt]]\n[[ГОТОВО]]Итог[[/ГОТОВО]]"
        p = parse(t)
        self.assertEqual([a[0] for a in p["actions"]], ["write", "run", "read"])
        self.assertEqual(p["actions"][0][1:], ("a/b.py", "print(1)\n"))      # обёртка ``` снята
        self.assertEqual(p["actions"][1][1], "python a/b.py")
        self.assertEqual(p["done"], "Итог")
        self.assertEqual(p["say"], "План.")

    def test_unclosed_write_stops_at_next_command(self):
        p = parse("[[ЗАПИСАТЬ: a.py]]\nx = 1\n[[ЗАПУСТИТЬ: python a.py]]")
        self.assertEqual(p["actions"][0][2], "x = 1\n")
        self.assertEqual(p["actions"][1][0], "run")

    def test_plain_text(self):
        p = parse("Просто ответ")
        self.assertEqual((p["actions"], p["done"]), ([], None))


class Safety(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(Path(tempfile.mkdtemp()))

    def test_paths(self):
        for bad in ("../x.py", "a/../../x.py", "/etc/passwd", "C:/x.py", "~/x.py", "a/con.py", ""):
            with self.assertRaises(CoderError, msg=bad):
                self.ws.safe(bad, for_write=True)
        self.assertTrue(str(self.ws.safe("src/main.py", for_write=True)).startswith(str(self.ws.root.resolve())))

    def test_blocked_extensions(self):
        for bad in ("run.bat", "x.exe", "s.ps1", "a.vbs", "m.dll"):
            with self.assertRaises(CoderError, msg=bad):
                self.ws.safe(bad, for_write=True)

    def test_import_copies_and_skips(self):
        src = Path(tempfile.mkdtemp())
        (src / "data.csv").write_text("a;b\n1;2\n", "utf-8")
        (src / "evil.bat").write_text("del /q *", "utf-8")
        copied, warns = self.ws.import_paths([str(src / "data.csv"), str(src / "evil.bat"), str(src / "нет.txt")])
        self.assertEqual(copied, ["input/data.csv"])
        self.assertEqual(len(warns), 2)
        self.assertTrue((src / "data.csv").exists())                          # оригинал на месте

    def test_runner_rules(self):
        r = Runner(self.ws)
        for bad in ("rm -rf .", "cmd /c dir", "python -c \"print(1)\"", "node -e 1", "python ../x.py", "python C:\\x.py", "powershell x", "C:\\Python\\python.exe a.py"):
            with self.assertRaises(CoderError, msg=bad):
                r.build(bad)
        self.assertTrue(r.build("python main.py")[-1] == "main.py")
        self.assertIn("pytest", r.build("pytest -q"))

    def test_dangerous_code_not_run(self):
        self.ws.write("a.py", "import subprocess\nsubprocess.run(['x'])\n")
        with self.assertRaises(CoderError):
            Runner(self.ws).run("python a.py")
        self.ws.write("a.py", "print('ok')\n")
        code, out = Runner(self.ws).run("python a.py")
        self.assertEqual((code, out.strip()), (0, "ok"))

    def test_timeout(self):
        self.ws.write("a.py", "while True: pass\n")
        code, out = Runner(self.ws, timeout=1).run("python a.py")
        self.assertEqual(code, 124)


class Scripted:
    """Ненастоящая модель: отвечает заранее заданными репликами и запоминает, что ей прислали."""
    state = "ready"
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []
    def chat(self, messages, max_tokens=0, temperature=0, on_delta=None, stop=None, **kw):
        self.seen.append(messages)
        return self.replies.pop(0)


class Choice:
    name, label = "local", "Тест"
    def __init__(self, backend): self.backend = backend


class FakeBrain:
    def __init__(self, backend): self.b = backend
    def choose(self, text, **kw): return Choice(self.b)


def make(replies, run=True):
    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp / "c.json")
    cfg.update({"coder": {"run": run, "max_steps": 6}})
    model = Scripted(replies)
    ev = []
    svc = CoderService(cfg, FakeBrain(model), ev.append, projects_dir=tmp / "Проекты")
    return svc, model, ev, tmp


class Loop(unittest.TestCase):
    def test_write_fail_fix_pass(self):
        bad = "def add(a, b):\n    return a - b\n"
        good = "def add(a, b):\n    return a + b\n"
        test = "from calc import add\nassert add(2, 3) == 5, 'add сломан'\nprint('OK')\n"
        svc, model, ev, tmp = make([
            f"План.\n[[ЗАПИСАТЬ: calc.py]]\n{bad}[[/ЗАПИСАТЬ]]\n[[ЗАПИСАТЬ: test_calc.py]]\n{test}[[/ЗАПИСАТЬ]]\n[[ЗАПУСТИТЬ: python test_calc.py]]",
            f"Ошибка в знаке.\n[[ЗАПИСАТЬ: calc.py]]\n{good}[[/ЗАПИСАТЬ]]\n[[ЗАПУСТИТЬ: python test_calc.py]]",
            "[[ГОТОВО]]Сделано: calc.py, тест проходит.[[/ГОТОВО]]",
        ])
        src = tmp / "in.txt"
        src.write_text("сложение", "utf-8")
        r = svc.start("Сделай функцию add", [str(src)], wait=True)
        self.assertTrue(r["ok"], r)
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertTrue(done["ok"], done)
        self.assertIn("прошла", done["verdict"])
        self.assertEqual(sorted(f["path"] for f in done["files"]), ["calc.py", "test_calc.py"])
        self.assertTrue(done["zip"].endswith(".zip"))
        # модель получила и провал, и вывод ошибки
        second = model.seen[1][-1]["content"]
        self.assertIn("add сломан", second)
        self.assertIn("input/in.txt", model.seen[0][1]["content"])             # приложенный файл виден модели

    def test_without_running_is_honest(self):
        svc, model, ev, _ = make(["[[ЗАПИСАТЬ: a.py]]\nprint(1)\n[[/ЗАПИСАТЬ]]\n[[ЗАПУСТИТЬ: python a.py]]", "[[ГОТОВО]]ок[[/ГОТОВО]]"], run=False)
        svc.start("задача", [], wait=True)
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertFalse(done["ok"])
        self.assertIn("не запускался", done["verdict"])
        self.assertIn("НЕДОСТУПЕН", model.seen[0][0]["content"])

    def test_model_cannot_escape(self):
        svc, model, ev, tmp = make(["[[ЗАПИСАТЬ: ../evil.py]]\nx\n[[/ЗАПИСАТЬ]]\n[[ЗАПИСАТЬ: go.bat]]\ndel\n[[/ЗАПИСАТЬ]]", "[[ГОТОВО]]х[[/ГОТОВО]]"])
        svc.start("задача", [], wait=True)
        self.assertFalse((tmp / "Проекты" / "evil.py").exists())
        self.assertIn("✖", model.seen[1][-1]["content"])
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertEqual(done["files"], [])

    def test_unfinished_run_after_edit_not_ok(self):
        svc, *_ , = make([])
        svc, model, ev, _ = make(["[[ЗАПИСАТЬ: a.py]]\nprint(1)\n[[/ЗАПИСАТЬ]]\n[[ЗАПУСТИТЬ: python a.py]]\n[[ЗАПИСАТЬ: a.py]]\nprint(2)\n[[/ЗАПИСАТЬ]]\n[[ГОТОВО]]ок[[/ГОТОВО]]"])
        svc.start("задача", [], wait=True)
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertFalse(done["ok"])
        self.assertIn("не проверена", done["verdict"])

    def test_step_limit_and_empty_task(self):
        svc, model, ev, _ = make(["[[ЧИТАТЬ: нет.txt]]"] * 6)
        self.assertFalse(svc.start("  ", [])["ok"])
        svc.start("задача", [], wait=True)
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertIn("шаги", done["summary"])


if __name__ == "__main__":
    unittest.main()


class EditBlocks(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(Path(tempfile.mkdtemp()))
        self.ws.write("a.py", "def f(x):\n    if x:\n        return 1\n    return 0\n\n\ndef g(x):\n    return x\n")

    def hunk(self, old, new): return f"<<<<<<< НАЙТИ\n{old}\n=======\n{new}\n>>>>>>> ЗАМЕНА"

    def test_exact_and_multiple(self):
        n = self.ws.edit("a.py", self.hunk("        return 1", "        return 2") + "\n" + self.hunk("def g(x):\n    return x", "def g(x):\n    return x * 2"))
        self.assertEqual(n, 2)
        t = self.ws.read("a.py"); self.assertIn("return 2", t); self.assertIn("x * 2", t)

    def test_indent_tolerant(self):
        self.ws.edit("a.py", self.hunk("if x:\nreturn 1", "if x:\nreturn 5"))               # модель потеряла отступы
        self.assertIn("        return 5", self.ws.read("a.py"))

    def test_ambiguous_missing_and_atomic(self):
        self.ws.write("b.py", "x = 1\nx = 1\n")
        with self.assertRaises(CoderError) as c:
            self.ws.edit("b.py", self.hunk("x = 1", "x = 2"))
        self.assertIn("2 раз", str(c.exception))
        with self.assertRaises(CoderError) as c:
            self.ws.edit("a.py", self.hunk("        return 1", "        return 9") + "\n" + self.hunk("def h(y):", "def h2(y):"))
        self.assertIn("не найден", str(c.exception))
        self.assertIn("return 1", self.ws.read("a.py"))                                      # одна пара не подошла — файл не тронут
        with self.assertRaises(CoderError):
            self.ws.edit("нет.py", self.hunk("a", "b"))

    def test_parse_edit_search_open(self):
        p = parse("Правлю.\n[[ПРАВИТЬ: a.py]]\n" + self.hunk("a", "b") + "\n[[/ПРАВИТЬ]]\n[[ИСКАТЬ: как вычитать]]\n[[ОТКРЫТЬ: https://x.org/p]]")
        self.assertEqual([a[0] for a in p["actions"]], ["edit", "search", "open"])
        self.assertEqual(p["actions"][1][1], "как вычитать"); self.assertEqual(p["say"], "Правлю.")

    def test_changes_counts(self):
        before = self.ws.snapshot()
        self.ws.edit("a.py", self.hunk("        return 1", "        return 2\n        # новая строка"))
        self.ws.write("new.py", "print(1)\n")
        ch = {c["path"]: c for c in self.ws.changes(before)}
        self.assertEqual((ch["a.py"]["kind"], ch["a.py"]["added"], ch["a.py"]["removed"]), ("changed", 2, 1))
        self.assertEqual(ch["new.py"]["kind"], "new")

    def test_plan_reply(self):
        c = coder.classify_plan_reply
        self.assertEqual(c("✅ Делать вариант 2", 2), ("go", 2)); self.assertEqual(c("да", 1), ("go", 0)); self.assertEqual(c("второй", 2), ("go", 2))
        self.assertEqual(c("✖ Отмена", 2), ("cancel", 0)); self.assertEqual(c("✏️ Изменить план", 2), ("edit_ask", 0))
        self.assertEqual(c("только без классов", 2), ("feedback", 0))
        self.assertEqual(coder.plan_variants("Варианты:\n1. a\n2. b\nЧто изменю: x"), 2)
