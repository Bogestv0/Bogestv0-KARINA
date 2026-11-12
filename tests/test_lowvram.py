import unittest, tempfile
from pathlib import Path
from karina import localimg


class LowVram(unittest.TestCase):
    def run_gen(self, mid, low=True, fail_first=False):
        calls, orig = [], (localimg._run, localimg.pick_installed, localimg.engine_ready, localimg.engine_supports, localimg.model_paths, localimg.find_exe, localimg.LOW_VRAM)
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
        localimg.LOW_VRAM = low
        try:
            return localimg.generate("1girl", (768, 768), 1, mid, "bad"), calls
        finally:
            (localimg._run, localimg.pick_installed, localimg.engine_ready, localimg.engine_supports, localimg.model_paths, localimg.find_exe, localimg.LOW_VRAM) = orig

    def test_sdxl_low_vram_flags(self):
        for mid in ("noobai", "ponyxl"):
            (data, ext), calls = self.run_gen(mid)
            self.assertEqual(data, b"PNG")
            c = calls[0]
            for f in ("--offload-to-cpu", "--clip-on-cpu", "--type"):
                self.assertIn(f, c)
            self.assertEqual(c[c.index("--type") + 1], "q8_0")
            self.assertEqual(c[-2], "-o")                       # путь вывода по-прежнему в конце

    def test_falls_back_to_plain_mode(self):
        (data, ext), calls = self.run_gen("noobai", fail_first=True)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--offload-to-cpu", calls[1])

    def test_can_be_disabled_and_flux_unchanged(self):
        _, calls = self.run_gen("noobai", low=False)
        self.assertNotIn("--offload-to-cpu", calls[0])
        _, calls = self.run_gen("flux2klein")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
