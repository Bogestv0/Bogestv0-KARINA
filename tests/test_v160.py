"""1.16.0: полка и ярлыки острова (karina/shelf.py) и приём файлов."""
import json
import tempfile
import unittest
from pathlib import Path

from karina import island as isl_mod
from karina import island_tools as tools
from karina import shelf


class ShelfTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "a.pdf").write_bytes(b"%PDF" + b"0" * 2000)
        (self.d / "sub").mkdir()
        self.sh = shelf.Shelf(self.d / "shelf.json")

    def test_kinds_and_labels(self):
        self.assertEqual(shelf.path_kind(str(self.d / "sub")), "folder")
        self.assertEqual(shelf.path_kind("C:\\x\\Telegram.exe"), "app")
        self.assertEqual(shelf.path_kind("a.JPG"), "img")
        self.assertEqual(shelf.path_kind("https://ya.ru"), "url")
        self.assertEqual(shelf.path_kind("x.unknown"), "file")
        self.assertEqual(shelf.label_of("C:\\x\\Telegram.exe"), "Telegram")
        self.assertEqual(shelf.label_of("https://www.ya.ru/path"), "ya.ru")

    def test_add_dedup_view_remove_persist(self):
        a, sub = str(self.d / "a.pdf"), str(self.d / "sub")
        self.assertEqual(self.sh.add_paths([a, sub, a, str(self.d / "нет.txt")]), 2)       # повтор и несуществующий не добавлены
        self.assertTrue(self.sh.add_text("  Адрес: Мира, 12  "))
        self.assertFalse(self.sh.add_text("   "))
        v = self.sh.view()
        self.assertEqual([i["k"] for i in v], ["text", "folder", "doc"])                  # новые сверху
        self.assertEqual(v[2]["sub"], "2 КБ")
        self.assertEqual(v[1]["sub"], "папка")
        sh2 = shelf.Shelf(self.d / "shelf.json")                                           # хранится между запусками
        self.assertEqual(len(sh2.view()), 3)
        self.assertTrue(sh2.remove(v[0]["id"]))
        self.assertEqual(len(sh2.view()), 2)
        (self.d / "a.pdf").unlink()
        gone = [i for i in sh2.view() if not i["exists"]]
        self.assertEqual(len(gone), 1)                                                     # исчезнувший файл помечен
        sh2.clear()
        self.assertEqual(sh2.view(), [])

    def test_limit(self):
        for i in range(shelf.MAX_SHELF + 5):
            self.sh.add_text(f"t{i}")
        self.assertEqual(len(self.sh.view()), shelf.MAX_SHELF)

    def test_clean_links(self):
        out = shelf.clean_links([{"path": "C:\\a.exe"}, {"path": "c:\\A.exe", "name": "дубль"}, {"name": "x"}, "мусор",
                                 {"path": "https://ya.ru", "name": "  Я   Н  "}])
        self.assertEqual([l["name"] for l in out], ["a", "Я Н"])
        self.assertEqual(len(shelf.clean_links([{"path": f"C:\\{i}.txt"} for i in range(30)])), shelf.MAX_LINKS)
        s = tools.clean_settings({"links": [{"path": "C:\\a.exe"}]}, {})
        self.assertEqual(s["links"][0]["name"], "a")
        self.assertEqual(tools.clean_settings({}, {})["links"], [])

    def test_thumb(self):
        from PIL import Image
        Image.new("RGB", (400, 200), (200, 50, 50)).save(self.d / "p.png")
        self.assertTrue(shelf.thumb(str(self.d / "p.png")).startswith("data:image/jpeg;base64,"))
        self.assertEqual(shelf.thumb(str(self.d / "sub")), "")
        self.assertEqual(shelf.thumb(str(self.d / "a.pdf")), "")


class IslandDropTest(unittest.TestCase):
    def mk(self):
        isl = isl_mod.Island({}, None, lambda: None)
        isl.shelf = shelf.Shelf(Path(tempfile.mkdtemp()) / "s.json")
        isl.settings_set = lambda patch: tools.clean_settings(patch, isl.settings())     # без записи в конфиг
        self.pushed = []
        isl.push = self.pushed.append
        return isl

    def test_drop_to_shelf_and_link(self):
        isl = self.mk()
        f = Path(tempfile.mkdtemp()) / "a.txt"
        f.write_text("x")
        ev = {"dataTransfer": {"files": [{"pywebviewFullPath": str(f)}]}}
        isl._on_drop(ev)
        self.assertEqual(self.pushed[-1], {"type": "shelf", "n": 1})
        self.assertEqual(len(isl.shelf.view()), 1)
        isl._drop_target = "link"
        isl.settings = lambda: {"links": []}
        isl._on_drop(ev)
        self.assertTrue(self.pushed[-1].get("link"))
        self.assertEqual(isl._drop_target, "shelf")                                       # после приёма цель сбрасывается

    def test_drop_without_path_explains(self):
        isl = self.mk()
        isl._on_drop({"dataTransfer": {"files": [{"name": "a.txt"}]}})
        self.assertIn("pywebview", self.pushed[-1]["error"])

    def test_drop_ignored_while_dragging_out(self):
        import time
        isl = self.mk()
        isl._drag_until = time.time() + 5
        isl._on_drop({"dataTransfer": {"files": [{"pywebviewFullPath": __file__}]}})
        self.assertEqual(self.pushed, [])

    def test_api_surface_and_sizes(self):
        for name in ("shelf_get", "shelf_open", "shelf_del", "shelf_paste", "shelf_drag", "link_pick", "link_run", "links_set", "drop_target"):
            self.assertTrue(callable(getattr(isl_mod.IslandApi, name)), name)
        self.assertIn("drop", isl_mod.SIZES)

    def test_page_has_shelf_and_links(self):
        html = (isl_mod.ROOT / "ui" / "island.html").read_text(encoding="utf-8")
        for needle in ('data-t="shelf"', 'id="lnks"', 'id="dropz"', "shelf_drag", "drop_target"):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()
