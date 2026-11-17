"""1.15.1: инструменты острова (karina/screen_tools.py): чистый текст, QR, цвет, область, запись, раскладка, свои инструменты."""
import io
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from karina import island_tools as tools
from karina import screen_tools as st


class ScreenToolsTest(unittest.TestCase):
    def test_clean_text(self):
        raw = "  Привет\u00a0мир \u200b\r\n\r\n\r\n\r\nстрока   \r\n\u00adслово\u2009ещё  "
        self.assertEqual(st.clean_text(raw), "Привет мир\n\nстрока\nслово ещё")
        self.assertEqual(st.clean_text(""), "")
        self.assertEqual(st.clean_text(None), "")

    def test_color_info(self):
        c = st.color_info(106, 179, 243)
        self.assertEqual(c["hex"], "#6AB3F3")
        self.assertEqual(c["rgb"], "rgb(106, 179, 243)")
        self.assertEqual(st.color_info(-5, 300, 0)["hex"], "#00FF00")

    def test_pixel_at_clamps(self):
        img = Image.new("RGB", (4, 3), (10, 20, 30))
        img.putpixel((3, 2), (1, 2, 3))
        self.assertEqual(st.pixel_at(img, 99, 99)["hex"], "#010203")
        self.assertEqual(st.pixel_at(img, -5, -5)["hex"], "#0A141E")

    def test_crop_fraction(self):
        img = Image.new("RGB", (200, 100))
        self.assertEqual(st.crop_fraction(img, (0.5, 0.0, 0.25, 1.0)).size, (50, 100))
        self.assertEqual(st.crop_fraction(img, (-1, -1, 2, 2)).size, (200, 100))
        with self.assertRaises(st.ToolError):
            st.crop_fraction(img, (0.1, 0.1, 0.11, 0.11))
        with self.assertRaises(st.ToolError):
            st.crop_fraction(img, None)

    def test_qr_roundtrip(self):
        try:
            import segno  # noqa: F401
            import zxingcpp  # noqa: F401
        except ImportError as e:
            self.skipTest(f"missing optional dep: {e}")
        text = "https://bogestv0.ru/karina?x=Привет"
        png = st.qr_png(text)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(st.qr_read(Image.open(io.BytesIO(png))), [text])
        self.assertTrue(st.png_data_url(png).startswith("data:image/png;base64,"))
        with self.assertRaises(st.ToolError):
            st.qr_png("   ")
        with self.assertRaises(st.ToolError):
            st.qr_png("x" * 2000)

    def test_is_url_and_custom_kind(self):
        import tempfile
        self.assertTrue(st.is_url("https://a.ru/x"))
        self.assertTrue(st.is_url("www.a.ru"))
        self.assertFalse(st.is_url("открой хром"))
        self.assertEqual(st.custom_kind("https://a.ru"), "url")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(st.custom_kind(d), "path")
        self.assertEqual(st.custom_kind("C:\\Windows\\notepad.exe"), "path")
        self.assertEqual(st.custom_kind("открой блокнот"), "say")

    def test_clean_custom_and_settings(self):
        items = (
            [{"name": "  Блокнот ", "cmd": "открой блокнот"},
             {"name": "", "cmd": "x"},
             {"name": "y", "cmd": ""},
             "bad"]
            + [{"name": f"n{i}", "cmd": "c"} for i in range(20)]
        )
        out = st.clean_custom(items)
        self.assertEqual(out[0], {"name": "Блокнот", "cmd": "открой блокнот"})
        self.assertEqual(len(out), st.MAX_CUSTOM)
        self.assertEqual(st.clean_custom("nope"), [])
        s = tools.clean_settings({"tools": [{"name": "A", "cmd": "b"}]})
        self.assertEqual(s["tools"], [{"name": "A", "cmd": "b"}])
        self.assertEqual(tools.clean_settings({}, s)["tools"], s["tools"])
        self.assertEqual(tools.clean_settings({}, None)["tools"], [])

    def test_pick_text_prefers_clean_language(self):
        ru, en_garbage = "Счёт к оплате 18 400 рублей", "Cчёt к oплaтe 18 400 pублeй"
        self.assertEqual(st.pick_text(["", en_garbage, ru]), ru)
        self.assertEqual(st.pick_text(["", "  "]), "")

    def test_layout_names(self):
        self.assertEqual(st.layout_name(0x04190419), "RU")
        self.assertEqual(st.layout_name(0x04090409), "EN")
        self.assertEqual(st.layout_name(0x0809), "EN")
        self.assertEqual(st.next_layout("RU"), "EN")
        self.assertEqual(st.next_layout("EN"), "RU")

    def test_fmt_time(self):
        self.assertEqual(st.fmt_time(42), "00:42")
        self.assertEqual(st.fmt_time(75), "01:15")
        self.assertEqual(st.fmt_time(3725), "1:02:05")

    def test_ffmpeg_cmd(self):
        c = st.ffmpeg_cmd("ff", "out.mp4", win=True)
        self.assertEqual(c[0], "ff")
        self.assertIn("gdigrab", c)
        self.assertEqual(c[-1], "out.mp4")
        self.assertIn("yuv420p", c)


class FakeProc:
    def __init__(self, cmd, dies=False, **kw):
        self.cmd, self.dies, self.sent, self.stdin = cmd, dies, [], self
        self.stderr = io.BytesIO(b"Could not find device\n")
        self._rc = 1 if dies else None
        if not dies:
            open(cmd[-1], "wb").write(b"video")

    def poll(self):
        return self._rc

    def write(self, b):
        self.sent.append(b)
        if b == b"q\n":
            self._rc = 0

    def flush(self):
        pass

    def wait(self, timeout=None):
        return self._rc

    def terminate(self):
        self._rc = -15


class RecorderTest(unittest.TestCase):
    def test_recorder_start_stop(self):
        with mock.patch.object(time, "sleep", lambda s: None), \
             mock.patch("karina.screen_tools.time.sleep", lambda s: None):
            made = []
            rec = st.Recorder(
                popen=lambda cmd, **kw: made.append(FakeProc(cmd, **kw)) or made[-1],
                ffmpeg="ff",
                out_dir=Path(self._tmpdir()),
            )
            self.assertFalse(rec.on)
            r = rec.start()
            self.assertTrue(rec.on)
            self.assertTrue(r["on"])
            self.assertTrue(r["path"].startswith(str(self._tmpdir())))
            self.assertTrue(r["path"].endswith(".mp4"))
            self.assertEqual(rec.start()["path"], r["path"])
            self.assertEqual(len(made), 1)
            done = rec.stop()
            self.assertEqual(made[0].sent, [b"q\n"])
            self.assertFalse(rec.on)
            self.assertEqual(done["path"], r["path"])
            self.assertEqual(rec.stop()["path"], "")

    def test_recorder_reports_ffmpeg_failure(self):
        with mock.patch.object(time, "sleep", lambda s: None), \
             mock.patch("karina.screen_tools.time.sleep", lambda s: None):
            rec = st.Recorder(
                popen=lambda cmd, **kw: FakeProc(cmd, dies=True),
                ffmpeg="ff",
                out_dir=Path(self._tmpdir()),
            )
            with self.assertRaises(st.ToolError) as e:
                rec.start()
            self.assertIn("Could not find device", str(e.exception))
            self.assertFalse(rec.on)

    def test_recorder_without_ffmpeg(self):
        with mock.patch.object(st, "find_ffmpeg", lambda: None):
            with self.assertRaises(st.ToolError):
                st.Recorder().start()

    def _tmpdir(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d


class OcrPasteTest(unittest.TestCase):
    def test_ocr_and_paste_need_windows_or_text(self):
        if not st.IS_WIN:
            with self.assertRaises(st.ToolError):
                st.ocr_image(Image.new("RGB", (10, 10)))
        with mock.patch.object(st, "clip_get_text", lambda: "   "):
            with self.assertRaises(st.ToolError):
                st.paste_clean()


class IslandShowTest(unittest.TestCase):
    def test_island_shown_through_winforms(self):
        """Остров показываем через window.show(), а не голым ShowWindow: иначе WebView2 не рисует страницу (чёрная плашка)."""
        from karina import island as isl_mod

        class W:
            def __init__(self, fail=False):
                self.fail, self.calls = fail, []

            def show(self):
                self.calls.append("show")
                if self.fail:
                    raise RuntimeError("no")

            def hide(self):
                self.calls.append("hide")

        isl = isl_mod.Island({}, None, lambda: None)
        isl.window = W()
        self.assertTrue(isl._show_native(1))
        self.assertTrue(isl._painting)
        self.assertEqual(isl.window.calls, ["show"])

        isl._painting, isl.window = False, W(fail=True)
        self.assertFalse(isl._show_native(1))
        self.assertFalse(isl._painting)


if __name__ == "__main__":
    unittest.main()
