import json, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import imgpower, localimg
from karina.config import Config


def cfg(**images):
    c = Config(Path(tempfile.mkdtemp()) / "config.json")
    c.update({"images": images})
    return c


def hw(vram, integrated=False, detected=True):
    return {"detected": detected, "gpu": "Test GPU", "vram_gb": vram, "integrated": integrated, "ram_gb": 16.0, "cpu": 8, "source": "auto"}


class Tiers(unittest.TestCase):
    def test_tier(self):
        self.assertEqual(imgpower.tier(hw(24)), "high")
        self.assertEqual(imgpower.tier(hw(8)), "mid")
        self.assertEqual(imgpower.tier(hw(4)), "low")
        self.assertEqual(imgpower.tier(hw(2)), "tiny")
        self.assertEqual(imgpower.tier(hw(0.5, integrated=True)), "none")
        self.assertEqual(imgpower.tier(hw(0, detected=False)), "unknown")


class Detect(unittest.TestCase):
    def test_nvidia_output(self):
        got = imgpower.parse_nvidia("NVIDIA GeForce GTX 1650, 4096\nNVIDIA GeForce RTX 4090, 24564\nмусор\n")
        self.assertEqual([(g["name"], g["vram_gb"]) for g in got], [("NVIDIA GeForce GTX 1650", 4.0), ("NVIDIA GeForce RTX 4090", 24.0)])

    def test_registry_qword(self):
        self.assertEqual(imgpower.reg_bytes_to_gb((4 * 2 ** 30).to_bytes(8, "little")), 4.0)       # REG_BINARY
        self.assertEqual(imgpower.reg_bytes_to_gb(8 * 2 ** 30), 8.0)                               # REG_QWORD
        self.assertEqual(imgpower.reg_bytes_to_gb(None), 0.0)

    def test_pick_prefers_discrete_over_integrated(self):
        g = imgpower.pick_gpu([{"name": "Intel(R) UHD Graphics 630", "vram_gb": 0.1}, {"name": "NVIDIA GeForce GTX 1650", "vram_gb": 4.0},
                               {"name": "Microsoft Basic Display Adapter", "vram_gb": 0.0}])
        self.assertEqual(g["name"], "NVIDIA GeForce GTX 1650")
        self.assertFalse(g["integrated"])
        only = imgpower.pick_gpu([{"name": "Intel(R) UHD Graphics 630", "vram_gb": 0.1}])
        self.assertTrue(only["integrated"])
        self.assertIsNone(imgpower.pick_gpu([]))

    def test_manual_vram_overrides_detection(self):
        old = imgpower._HW
        imgpower._HW = {"detected": True, "gpu": "X", "vram_gb": 4.0, "integrated": False, "gpus": []}
        try:
            self.assertEqual(imgpower.hardware(cfg())["vram_gb"], 4.0)
            h = imgpower.hardware(cfg(vram_gb=12))
            self.assertEqual((h["vram_gb"], h["source"]), (12.0, "manual"))
        finally:
            imgpower._HW = old


class Sizes(unittest.TestCase):
    def test_fit_size_keeps_aspect_and_rounds_to_64(self):
        self.assertEqual(imgpower.fit_size((832, 1216), 0.55), (640, 896))
        self.assertEqual(imgpower.fit_size((1024, 1024), 0.55), (768, 768))
        w, h = imgpower.fit_size((1216, 704), 0.55)
        self.assertTrue(w % 64 == 0 and h % 64 == 0 and w * h <= 0.6e6 and w > h)

    def test_never_enlarges(self):
        self.assertEqual(imgpower.fit_size((832, 1216), 1.05), (832, 1216))
        self.assertEqual(imgpower.fit_size((512, 512), 0.55), (512, 512))

    def test_shape_caps_steps_for_sdxl_but_not_klein(self):
        pony, klein = localimg.by_id("ponyxl"), localimg.by_id("flux2klein")
        p = {"quality": "fast", "area": imgpower.AREA["fast"]}
        self.assertEqual(imgpower.shape(pony, (832, 1216), p)[2], 20)
        self.assertEqual(imgpower.shape(klein, (832, 1216), p)[2], 4)
        self.assertEqual(imgpower.shape(pony, (832, 1216), {})[2], pony["steps"])          # без плана ничего не урезаем
        self.assertEqual(imgpower.shape(pony, (832, 1216), {})[:2], (832, 1216))

    def test_cpu_pass_is_smaller_and_shorter(self):
        noob = localimg.by_id("noobai")
        p = {"quality": "normal", "area": imgpower.AREA["normal"]}
        gw, gh, gs = imgpower.shape(noob, (832, 1216), p)
        cw, ch, cs = imgpower.shape(noob, (832, 1216), p, cpu=True)
        self.assertLess(cw * ch, gw * gh)
        self.assertLessEqual(cs, gs)


class Plans(unittest.TestCase):
    def plan(self, vram, m="noobai", integrated=False, **images):
        old = imgpower._HW
        imgpower._HW = {"detected": True, "gpu": "X", "vram_gb": vram, "integrated": integrated, "gpus": []}
        try:
            return imgpower.plan(cfg(**images), localimg.by_id(m))
        finally:
            imgpower._HW = old

    def test_auto_follows_video_memory(self):
        self.assertEqual(self.plan(24)["mode"], "fast")
        self.assertEqual(self.plan(8)["mode"], "balanced")
        p4 = self.plan(4)
        self.assertEqual((p4["mode"], p4["quality"]), ("low", "fast"))
        self.assertEqual(self.plan(0.5, integrated=True)["mode"], "cpu")
        self.assertTrue(p4["auto"])

    def test_manual_power_overrides_auto(self):
        p = self.plan(24, power="low")
        self.assertEqual(p["mode"], "low")
        self.assertFalse(p["auto"])
        self.assertEqual(self.plan(4, power="max")["mode"], "fast")
        self.assertEqual(self.plan(24, power="cpu")["mode"], "cpu")

    def test_manual_quality(self):
        self.assertEqual(self.plan(4, quality="high")["area"], imgpower.AREA["high"])
        self.assertLess(self.plan(24, quality="fast")["area"], self.plan(24)["area"])

    def test_unload_llm_rules(self):
        self.assertTrue(self.plan(4)["unload_llm"])
        self.assertTrue(self.plan(8)["unload_llm"])
        self.assertFalse(self.plan(24)["unload_llm"])                       # на 16+ ГБ помещаются обе
        self.assertFalse(self.plan(8, unload_llm="no")["unload_llm"])
        self.assertTrue(self.plan(24, unload_llm="yes")["unload_llm"])

    def test_bad_values_fall_back(self):
        p = self.plan(4, power="turbo", quality="ultra", threads="x")
        self.assertEqual((p["power"], p["mode"], p["threads"]), ("auto", "low", 0))

    def test_label_mentions_size_and_steps(self):
        self.assertIn("640×896", self.plan(4)["label"])
        self.assertIn("20 шагов", self.plan(4)["label"])


class Fit(unittest.TestCase):
    def test_klein_recommended_everywhere_sdxl_only_with_memory(self):
        klein, noob, pony = (localimg.by_id(x) for x in ("flux2klein", "noobai", "ponyxl"))
        for v in (3, 4, 8, 24):
            self.assertTrue(imgpower.fit(klein, hw(v))["recommended"])
        self.assertFalse(imgpower.fit(noob, hw(4))["recommended"])
        self.assertEqual(imgpower.fit(noob, hw(4))["level"], "slow")
        self.assertEqual(imgpower.fit(pony, hw(1, integrated=True))["level"], "heavy")
        self.assertTrue(imgpower.fit(noob, hw(12))["recommended"])
        self.assertFalse(imgpower.fit(pony, hw(12))["recommended"])


class Order(unittest.TestCase):
    def test_fast_model_goes_first_on_weak_pc_in_auto(self):
        weak = {"auto": True, "mode": "low", "hw": hw(4)}
        order, note = imgpower.prefer_fast(["ponyxl", "noobai", "flux2klein"], weak, localimg.by_id)
        self.assertEqual(order, ["flux2klein", "ponyxl", "noobai"])
        self.assertIn("FLUX.2 Klein", note)

    def test_untouched_when_strong_manual_or_no_fast_model(self):
        for p in ({"auto": True, "mode": "fast", "hw": hw(24)}, {"auto": False, "mode": "low", "hw": hw(4)}):
            self.assertEqual(imgpower.prefer_fast(["ponyxl", "flux2klein"], p, localimg.by_id), (["ponyxl", "flux2klein"], ""))
        self.assertEqual(imgpower.prefer_fast(["ponyxl", "noobai"], {"auto": True, "mode": "low", "hw": hw(4)}, localimg.by_id)[1], "")


class Progress(unittest.TestCase):
    def test_parse_progress_lines(self):
        self.assertEqual(localimg.parse_progress("  |==============>                   | 7/20 - 3.40s/it"), (7, 20, 3.4))
        step, total, per = localimg.parse_progress("  |=====>   | 3/4 - 2.00it/s")
        self.assertEqual((step, total, per), (3, 4, 0.5))
        self.assertIsNone(localimg.parse_progress("[INFO ] loading model from 'x.safetensors'"))

    def test_run_reports_progress_and_last_error_line(self):
        code = ("import sys,time\n"
                "sys.stdout.write('loading model\\n'); sys.stdout.flush()\n"
                "for i in range(1,4):\n"
                "    sys.stdout.write('  |==>   | %d/3 - 0.01s/it\\r' % i); sys.stdout.flush(); time.sleep(0.05)\n"
                "sys.stdout.write('\\nfatal: out of memory\\n'); sys.exit(2)\n")
        seen = []
        res = localimg._run([sys.executable, "-c", code], Path(tempfile.mkdtemp()) / "o.png", None, lambda *a: seen.append(a))
        self.assertIsNone(res)
        self.assertEqual([s[0] for s in seen], [1, 2, 3])
        self.assertEqual(localimg._LAST_TAIL[0], "fatal: out of memory")       # строки прогресса не затирают причину ошибки


class Learning(unittest.TestCase):
    def setUp(self):
        self._dir = localimg.MODELS_DIR
        localimg.MODELS_DIR = Path(tempfile.mkdtemp())

    def tearDown(self):
        localimg.MODELS_DIR = self._dir

    def test_remember_recall_expire_forget(self):
        self.assertEqual(localimg._recall("noobai"), "")
        localimg._remember("noobai", "low")
        self.assertEqual(localimg._recall("noobai"), "low")
        d = json.loads(localimg._tuning_path().read_text())
        d["noobai"]["t"] = time.time() - 30 * 86400
        localimg._tuning_path().write_text(json.dumps(d))
        self.assertEqual(localimg._recall("noobai"), "")
        localimg._remember("noobai", "cpu")
        localimg.forget_tuning()
        self.assertEqual(localimg._recall("noobai"), "")

    def run_gen(self, plan, results):
        names = ("_run", "pick_installed", "engine_ready", "engine_supports", "model_paths", "find_exe")
        saved = {n: getattr(localimg, n) for n in names}
        calls = []

        def fake(cmd, out, stop, progress=None):
            calls.append(cmd)
            localimg._LAST_TAIL[0] = "out of memory"
            return b"PNG" if results[len(calls) - 1] else None
        localimg._run = fake
        localimg.pick_installed = lambda mid="": localimg.by_id("noobai")
        localimg.engine_ready = lambda: True
        localimg.engine_supports = lambda m: True
        localimg.model_paths = lambda mid: {"model": Path("m")}
        localimg.find_exe = lambda fl: Path("sd-" + fl)
        try:
            return localimg.generate("x", (832, 1216), 1, "noobai", "", None, plan=plan), calls
        finally:
            for n, v in saved.items():
                setattr(localimg, n, v)

    def test_auto_remembers_working_mode_and_skips_failures_next_time(self):
        plan = {"mode": "balanced", "auto": True, "quality": "normal", "area": 0.8}
        _, calls = self.run_gen(plan, [False, True])               # balanced упал по памяти → low сработал
        self.assertEqual(len(calls), 2)
        self.assertEqual(localimg._recall("noobai"), "low")
        _, calls = self.run_gen(plan, [True])                      # теперь сразу «экономно»
        self.assertEqual(len(calls), 1)
        self.assertIn("--type", calls[0])

    def test_manual_mode_is_not_overridden_or_remembered(self):
        plan = {"mode": "balanced", "auto": False}
        _, calls = self.run_gen(plan, [False, True])
        self.assertEqual(localimg._recall("noobai"), "")

    def test_cpu_fallback_is_smaller(self):
        plan = {"mode": "low", "auto": True, "quality": "normal", "area": 0.8, "threads": 6}
        _, calls = self.run_gen(plan, [False, True])               # vulkan low упал, «обычный» не пробуем (память), процессор сработал
        self.assertEqual(len(calls), 2)
        cpu = calls[-1]
        self.assertEqual(cpu[0], "sd-cpu")
        self.assertIn("-t", cpu)
        w, h = int(cpu[cpu.index("-W") + 1]), int(cpu[cpu.index("-H") + 1])
        self.assertLess(w * h, 0.6e6)
        self.assertEqual(localimg._recall("noobai"), "cpu")
        self.assertEqual(localimg.LAST_RUN["flavor"], "cpu")


if __name__ == "__main__":
    unittest.main()
