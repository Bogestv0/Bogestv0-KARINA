"""1.15.2: остров создаётся показанным и сам перерисовывается, если страница перестала выдавать кадры."""
import time
import unittest

from karina import island as isl_mod


class W:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")


class RepaintTest(unittest.TestCase):
    def mk(self):
        isl = isl_mod.Island({}, None, lambda: None)
        isl.window, isl.ready = W(), True
        return isl

    def test_noop_off_windows(self):
        if isl_mod.IS_WIN:
            self.skipTest("только не Windows")
        isl = self.mk()
        self.assertFalse(isl.repaint())
        self.assertEqual(isl.window.calls, [])

    def test_not_repainted_while_hidden_on_purpose(self):
        isl = self.mk()
        isl._hidden_by_us = True
        self.assertFalse(isl.repaint())
        self.assertEqual(isl.window.calls, [])

    def test_throttled(self):
        isl = self.mk()
        isl._last_repaint = time.time()
        self.assertFalse(isl.repaint())

    def test_page_has_frame_watchdog(self):
        html = (isl_mod.ROOT / "ui" / "island.html").read_text(encoding="utf-8")
        self.assertIn("api.repaint", html)
        self.assertIn("visibilityState", html)

    def test_api_exposes_repaint(self):
        self.assertTrue(callable(getattr(isl_mod.IslandApi, "repaint")))


if __name__ == "__main__":
    unittest.main()


class RingTest(unittest.TestCase):
    def test_halo_is_ring_not_filled_pill(self):
        from karina import halo
        a = halo.ring_alpha(150, 36, 18)
        self.assertEqual(a[18, 75], 0)            # середина прозрачна: остров под/над подложкой виден
        self.assertGreaterEqual(a[18, 0], 250)          # кромка непрозрачная
        self.assertEqual(a[0, 0], 0)
        self.assertEqual(a[0, 75], 255)

    def test_ring_small_window_falls_back(self):
        from karina import halo
        self.assertEqual(halo.ring_alpha(4, 4, 2).shape, (4, 4))
