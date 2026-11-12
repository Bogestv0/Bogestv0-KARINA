import sys, unittest, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import localimg


class LowVram(unittest.TestCase):
    def run_gen(self, mid, low="low", fail_first=False):
        calls, orig = [], (localimg._run, localimg.pick_installed, localimg.engine_ready, localimg.engine_supports, localimg.model_paths, localimg.find_exe)
        def fake_run(cmd, out, stop):
            calls.append(cmd)
            if fail_first and len(calls) == 1:
                localimg._LAST_TAIL[0] = "out of memory"
                return None
            return b"PNG"
        localimg._run = fake_run
        localimg.pick_installed = lambda mid="": localimg.by_id(mid)
        localimg.engine_ready = lambda: True
        localimg.engine_supports = lambda m: True
        localimg.model_paths = lambda mid: {"model": Path("m.safetensors"), "vae": Path("v"), "llm": Path("l")}
        localimg.find_exe = lambda fl: Path("sd-cli") if fl == "vulkan" else None
        try:
            return localimg.generate("1girl", (768, 768), 1, mid, "bad", None, low), calls
        finally:
            (localimg._run, localimg.pick_installed, localimg.engine_ready, localimg.engine_supports, localimg.model_paths, localimg.find_exe) = orig

    def test_sdxl_low_vram_flags(self):
        for mid in ("noobai", "ponyxl"):
            (data, ext), calls = self.run_gen(mid)
            self.assertEqual(data, b"PNG")
            c = calls[0]
            for f in ("--offload-to-cpu", "--clip-on-cpu", "--type"):
                self.assertIn(f, c)
            self.assertEqual(c[c.index("--type") + 1], "q8_0")
            self.assertEqual(c[-2], "-o")                       # путь вывода по-прежнему в конце

    def test_balanced_has_no_quantization(self):
        _, calls = self.run_gen("noobai", low="balanced")
        self.assertIn("--offload-to-cpu", calls[0]); self.assertNotIn("--type", calls[0])

    def test_falls_back_to_plain_mode(self):
        (data, ext), calls = self.run_gen("noobai", fail_first=True)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--offload-to-cpu", calls[1])

    def test_can_be_disabled_and_flux_unchanged(self):
        _, calls = self.run_gen("noobai", low="fast")
        self.assertNotIn("--offload-to-cpu", calls[0])
        _, calls = self.run_gen("flux2klein")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()


class EngineAssets(unittest.TestCase):
    """Имена архивов в релизах stable-diffusion.cpp менялись: «Не нашла сборку для Windows» — именно из-за этого."""
    def rel(self, *names):
        return {"tag_name": "master-x", "assets": [{"name": n, "browser_download_url": "u/" + n, "size": 1} for n in names]}

    def test_new_names(self):
        r = self.rel("sd-master-1c11b2d-bin-win-cpu-x64.zip", "sd-master-1c11b2d-bin-win-vulkan-x64.zip",
                     "sd-master-1c11b2d-bin-win-cuda12-x64.zip", "sd-master-1c11b2d-bin-Linux-Ubuntu-24.04-x86_64.zip")
        self.assertIn("win-cpu", localimg._asset(r, "cpu")["name"])
        self.assertIn("win-vulkan", localimg._asset(r, "vulkan")["name"])

    def test_old_names_still_work(self):
        r = self.rel("sd-master-abc-bin-win-avx2-x64.zip", "sd-master-abc-bin-win-vulkan-x64.zip")
        self.assertIn("avx2", localimg._asset(r, "cpu")["name"])

    def test_find_release_picks_first_complete(self):
        import json
        rels = [self.rel("sd-a-bin-win-cuda12-x64.zip"),
                self.rel("sd-b-bin-win-cpu-x64.zip", "sd-b-bin-win-vulkan-x64.zip")]
        orig = localimg._open
        class R:
            def read(self_): return json.dumps(rels).encode()
        localimg._open = lambda *a, **k: R()
        try:
            self.assertEqual(localimg.find_release()["assets"][0]["name"], "sd-b-bin-win-cpu-x64.zip")
        finally:
            localimg._open = orig
