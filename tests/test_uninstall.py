"""Удаление по частям: что показывается, что стирается сразу, что откладывается до выхода, полная очистка. Без Windows."""
import json, os, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import uninstall as un

ROOT = Path(__file__).resolve().parent.parent


def fake_data() -> Path:
    b = Path(tempfile.mkdtemp()) / "Bogestv0 KARINA"
    files = {"models/llm/qwen.gguf": 5000, "models/small/model.bin": 3000, "models/tagger/t.onnx": 100, "models/image/sd.safetensors": 700,
             "voices/silero.pt": 2000, "components/pylibs/torch/x.dll": 4000, "engine/llama-server.exe": 900, "engine.pid": 3, "tunnel/sing-box.exe": 1200,
             "chats/a.json": 50, "uploads/pic.png": 80, "learned.json": 10, "island.json": 10, "shelf.json": 10, "icons/i.png": 5,
             "config.json": 400, "karina.log": 30, "karina.lock": 0, "plugins/p.py": 20}
    for rel, n in files.items():
        p = b / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * n)
    return b


class Catalog(unittest.TestCase):
    def test_lists_only_what_exists_and_groups(self):
        b = fake_data()
        ids = {i["id"]: i for i in un.catalog(b)}
        for k in ("llm", "stt", "imgmodels", "voice", "components", "engine", "vpn", "chats", "memory", "island", "plugins", "settings", "logs"):
            self.assertIn(k, ids, k)
        self.assertEqual(ids["llm"]["group"], "downloads")
        self.assertEqual(ids["settings"]["group"], "data")
        self.assertNotIn("program", ids)                      # из исходников деинсталлятора нет
        un.remove_path(b / "voices")
        self.assertNotIn("voice", {i["id"] for i in un.catalog(b)})

    def test_stt_does_not_swallow_llm_or_images(self):
        b = fake_data()
        stt = [i for i in un.catalog(b) if i["id"] == "stt"][0]
        self.assertEqual([p.name for p in stt["paths"]], ["small"])

    def test_describe_has_sizes_and_no_paths(self):
        d = {i["id"]: i for i in un.describe(fake_data())}
        self.assertEqual(d["llm"]["size"], 5000)
        self.assertEqual(d["imgmodels"]["size"], 800)
        self.assertNotIn("paths", d["llm"])

    def test_empty_data(self):
        self.assertEqual(un.describe(Path(tempfile.mkdtemp()) / "none"), [])


class Remove(unittest.TestCase):
    def test_remove_dir_and_file_and_missing(self):
        b = fake_data()
        self.assertEqual(un.remove_path(b / "models"), "")
        self.assertEqual(un.remove_path(b / "config.json"), "")
        self.assertFalse((b / "models").exists() or (b / "config.json").exists())
        self.assertEqual(un.remove_path(b / "nothing"), "")

    def test_readonly_file_is_removed(self):
        b = fake_data()
        f = b / "chats" / "a.json"
        os.chmod(f, 0o444)
        self.assertEqual(un.remove_path(b / "chats"), "")

    def test_selected_only_is_removed(self):
        b = fake_data()
        for p in {i["id"]: i for i in un.catalog(b)}["llm"]["paths"]:
            un.remove_path(p)
        self.assertFalse((b / "models" / "llm").exists())
        self.assertTrue((b / "config.json").exists() and (b / "chats").exists() and (b / "models" / "small").exists())


class Plan(unittest.TestCase):
    def test_busy_items_are_deferred_free_ones_are_not(self):
        b = fake_data()
        pl = un.plan(b, ["llm", "settings", "chats", "components"])
        names = {Path(p).name for p in pl["paths"]}
        self.assertEqual(names, {"config.json", "chats", "uploads", "components"})
        self.assertEqual(pl["program"], "")

    def test_failed_free_item_moves_to_deferred(self):
        b = fake_data()
        pl = un.plan(b, ["llm"], ["llm"])
        self.assertEqual([Path(p).name for p in pl["paths"]], ["llm"])

    def test_nothing_to_defer_means_no_second_process(self):
        b = fake_data()
        self.assertFalse(un.spawn_cleanup(un.plan(b, ["llm", "vpn"])))


class Cleanup(unittest.TestCase):
    def run_cleanup(self, b, ids):
        pl = un.plan(b, ids)
        pl["pid"] = 2 ** 22 + 12345                         # такого процесса нет: ждать нечего
        f = Path(tempfile.mkdtemp()) / "plan.json"
        f.write_text(json.dumps(pl), "utf-8")
        with mock.patch.object(un.time, "sleep"):
            self.assertEqual(un.cleanup_main(str(f)), 0)
        self.assertFalse(f.exists())

    def test_full_wipe_leaves_no_data_folder(self):
        b = fake_data()
        free = [i for i in un.catalog(b) if i["now"]]
        for it in free:
            for p in it["paths"]:
                un.remove_path(p)
        self.run_cleanup(b, [i["id"] for i in un.catalog(b)])
        self.assertFalse(b.exists())

    def test_partial_keeps_the_rest(self):
        b = fake_data()
        self.run_cleanup(b, ["chats", "components"])
        self.assertFalse((b / "chats").exists() or (b / "uploads").exists() or (b / "components").exists())
        self.assertTrue((b / "config.json").exists() and (b / "models" / "llm").exists())

    def test_program_uninstaller_is_started_last(self):
        b = fake_data()
        pl = {"base": str(b), "paths": [], "program": str(Path(tempfile.mkdtemp()) / "unins000.exe"), "pid": 2 ** 22 + 1}
        Path(pl["program"]).write_bytes(b"x")
        f = Path(tempfile.mkdtemp()) / "p.json"
        f.write_text(json.dumps(pl), "utf-8")
        with mock.patch.object(un.time, "sleep"), mock.patch.object(un.subprocess, "Popen") as po:
            un.cleanup_main(str(f))
        self.assertEqual(po.call_args[0][0], [pl["program"], "/SILENT"])

    def test_real_second_process_via_main_py(self):
        """Настоящий запуск `main.py --cleanup`: после выхода «родителя» стирает всё и не создаёт ни журнала, ни папки данных."""
        b = fake_data()
        pl = un.plan(b, [i["id"] for i in un.catalog(b) if not i["now"]])
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        pl["pid"] = parent.pid
        threading.Thread(target=parent.wait, daemon=True).start()      # без этого в Linux завершённый процесс остаётся «зомби», и ожидание не заканчивается
        f = Path(tempfile.mkdtemp()) / "plan.json"
        f.write_text(json.dumps(pl), "utf-8")
        t0 = time.time()
        r = subprocess.run([sys.executable, str(ROOT / "main.py"), "--cleanup", str(f)], capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertGreaterEqual(time.time() - t0, 1.0)      # дождалась выхода основной
        for n in ("config.json", "chats", "components", "models/small", "island.json", "karina.log"):
            self.assertFalse((b / n).exists(), n)
        self.assertTrue((b / "models" / "llm").exists())    # «быстрые» пункты не выбирали — остались


if __name__ == "__main__":
    unittest.main()


class ApiRun(unittest.TestCase):
    """UiApi.uninstall_run: что стирается сразу, что откладывается, закрывается ли Карина."""
    def make(self):
        from karina import ui_api
        b = fake_data()
        api = ui_api.Api.__new__(ui_api.Api)
        api.cfg = mock.Mock(path=b / "config.json")
        api.assistant = mock.Mock()
        api._downloads = {"x": {"cancel": mock.Mock()}}
        api.quitting = False
        api._win = lambda: None
        return api, b

    def test_free_items_only_do_not_close_karina(self):
        api, b = self.make()
        with mock.patch.object(un, "spawn_cleanup") as sp, mock.patch("threading.Timer") as tm:
            r = api.uninstall_run(["llm", "vpn", "engine"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["quit"])
        sp.assert_not_called()
        tm.assert_not_called()
        self.assertFalse((b / "models" / "llm").exists() or (b / "tunnel").exists() or (b / "engine").exists())
        self.assertTrue((b / "config.json").exists())
        api.assistant.engine.stop.assert_called()
        api.assistant.tunnel.stop.assert_called()
        api._downloads["x"]["cancel"].set.assert_called()

    def test_busy_items_start_cleanup_and_close(self):
        api, b = self.make()
        with mock.patch.object(un, "spawn_cleanup", return_value=True) as sp, mock.patch("threading.Timer") as tm:
            r = api.uninstall_run(["chats", "settings", "bogus", 5])
        self.assertTrue(r["quit"])
        self.assertEqual({Path(p).name for p in sp.call_args[0][0]["paths"]}, {"chats", "uploads", "config.json"})
        tm.assert_called_once()
        self.assertTrue((b / "config.json").exists())          # стирает вторая копия после выхода, не сейчас

    def test_nothing_selected(self):
        api, _ = self.make()
        self.assertFalse(api.uninstall_run([])["ok"])
        self.assertFalse(api.uninstall_run(["bogus"])["ok"])

    def test_failed_cleanup_start_keeps_karina_open_and_says_so(self):
        api, _ = self.make()
        with mock.patch.object(un, "spawn_cleanup", return_value=False), mock.patch("threading.Timer") as tm:
            r = api.uninstall_run(["chats"])
        self.assertFalse(r["quit"])
        self.assertTrue(r["problems"])
        tm.assert_not_called()
