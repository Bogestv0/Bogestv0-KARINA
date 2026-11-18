"""1.20.7: выбор мастера установки доходит до Карины; скрипт установщика цел."""
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import install_options as io
from karina.config import Config

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "build" / "installer.iss"


class Options(unittest.TestCase):
    def cfg(self):
        d = Path(tempfile.mkdtemp())
        return Config(d / "config.json"), d

    def test_applies_and_removes_file(self):
        c, d = self.cfg()
        (d / io.FILE).write_text(json.dumps({"ui": {"island": True, "admin": True}, "profile": {"name": "Анна", "city": "Омск", "birth_day": 5, "birth_month": 3, "birth_year": 1995}}), "utf-8-sig")
        self.assertTrue(io.apply(c))
        self.assertTrue(c["ui"]["island"] and c["ui"]["admin"])
        self.assertEqual((c["profile"]["name"], c["profile"]["city"], c["profile"]["birth_day"], c["profile"]["birth_year"]), ("Анна", "Омск", 5, 1995))
        self.assertFalse((d / io.FILE).exists())

    def test_existing_profile_not_overwritten_and_empty_is_ignored(self):
        c, d = self.cfg()
        c.update({"profile": {"name": "Иван", "city": ""}})
        (d / io.FILE).write_text(json.dumps({"ui": {"island": False}, "profile": {"name": "Анна", "city": "Томск", "birth_day": 0}}), "utf-8")
        io.apply(c)
        self.assertEqual(c["profile"]["name"], "Иван")
        self.assertEqual(c["profile"]["city"], "Томск")
        self.assertFalse(c["ui"]["island"])
        self.assertNotIn("admin", io.build_patch({"ui": {"island": True}}).get("ui", {}))

    def test_garbage_is_harmless(self):
        c, d = self.cfg()
        for bad in ("не json", "[1,2]", '{"ui": 5, "profile": "x"}', '{"profile": {"birth_day": "abc"}}'):
            (d / io.FILE).write_text(bad, "utf-8")
            io.apply(c)
            self.assertFalse((d / io.FILE).exists())
        self.assertEqual(c["profile"]["name"], "")

    def test_no_file_no_change(self):
        c, _ = self.cfg()
        self.assertFalse(io.apply(c))


class InstallerScript(unittest.TestCase):
    def setUp(self):
        self.raw = ISS.read_bytes()
        self.src = self.raw.decode("utf-8-sig")

    def test_utf8_bom_so_russian_survives(self):
        self.assertTrue(self.raw.startswith(b"\xef\xbb\xbf"))

    def test_referenced_images_exist(self):
        for name in ("wizard_side.bmp", "wizard_side_2x.bmp", "wizard_small.bmp", "wizard_small_2x.bmp"):
            self.assertIn(name, self.src)
            self.assertTrue((ROOT / "build" / name).exists(), name)

    def test_options_and_upgrade_flow_present(self):
        for needle in ("Запускать Карину с правами администратора", "движок для VPN", "вместе с Windows", "виджет-остров", "Добавить Карину на рабочий стол",
                       "Запустить Карину", "Обновление Карины", "IsUpd", "install_options.json", "--install-admin-task"):
            self.assertIn(needle, self.src)

    def test_begin_end_balanced(self):
        import re
        code = self.src.split("[Code]", 1)[1]
        code = re.sub(r"'[^'\n]*'", "''", code)
        code = re.sub(r"\{[^}\n]*\}", "", code)
        words = re.findall(r"\b(begin|end|try|case)\b", code, re.I)
        opens = sum(1 for w in words if w.lower() in ("begin", "try", "case"))
        ends = sum(1 for w in words if w.lower() == "end")
        self.assertEqual(opens, ends)
        self.assertEqual(len(re.findall(r"\(", code)), len(re.findall(r"\)", code)))

    def test_workflow_fetches_engine_before_installer(self):
        wf = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
        self.assertLess(wf.index("fetch_vpn_engine.py"), wf.index("Установщик (Inno Setup)"))


if __name__ == "__main__":
    unittest.main()
