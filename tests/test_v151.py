"""1.15.1: инструменты острова (karina/screen_tools.py): чистый текст, QR, цвет, область, запись, раскладка, свои инструменты."""
import io
import time

import pytest
from PIL import Image

from karina import island_tools as tools
from karina import screen_tools as st


def test_clean_text():
    raw = "  Привет\u00a0мир \u200b\r\n\r\n\r\n\r\nстрока   \r\n\u00adслово\u2009ещё  "
    assert st.clean_text(raw) == "Привет мир\n\nстрока\nслово ещё"
    assert st.clean_text("") == "" and st.clean_text(None) == ""


def test_color_info():
    c = st.color_info(106, 179, 243)
    assert c["hex"] == "#6AB3F3" and c["rgb"] == "rgb(106, 179, 243)"
    assert st.color_info(-5, 300, 0)["hex"] == "#00FF00"


def test_pixel_at_clamps():
    img = Image.new("RGB", (4, 3), (10, 20, 30))
    img.putpixel((3, 2), (1, 2, 3))
    assert st.pixel_at(img, 99, 99)["hex"] == "#010203"
    assert st.pixel_at(img, -5, -5)["hex"] == "#0A141E"


def test_crop_fraction():
    img = Image.new("RGB", (200, 100))
    assert st.crop_fraction(img, (0.5, 0.0, 0.25, 1.0)).size == (50, 100)        # углы можно задать в любом порядке
    assert st.crop_fraction(img, (-1, -1, 2, 2)).size == (200, 100)
    with pytest.raises(st.ToolError):
        st.crop_fraction(img, (0.1, 0.1, 0.11, 0.11))
    with pytest.raises(st.ToolError):
        st.crop_fraction(img, None)


def test_qr_roundtrip():
    pytest.importorskip("segno")
    zx = pytest.importorskip("zxingcpp")
    text = "https://bogestv0.ru/karina?x=Привет"
    png = st.qr_png(text)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert st.qr_read(Image.open(io.BytesIO(png))) == [text]
    assert st.png_data_url(png).startswith("data:image/png;base64,")
    with pytest.raises(st.ToolError):
        st.qr_png("   ")
    with pytest.raises(st.ToolError):
        st.qr_png("x" * 2000)
    assert zx


def test_is_url_and_custom_kind(tmp_path):
    assert st.is_url("https://a.ru/x") and st.is_url("www.a.ru") and not st.is_url("открой хром")
    assert st.custom_kind("https://a.ru") == "url"
    assert st.custom_kind(str(tmp_path)) == "path"
    assert st.custom_kind("C:\\Windows\\notepad.exe") == "path"
    assert st.custom_kind("открой блокнот") == "say"


def test_clean_custom_and_settings():
    items = [{"name": "  Блокнот ", "cmd": "открой блокнот"}, {"name": "", "cmd": "x"}, {"name": "y", "cmd": ""}, "bad"] + [{"name": f"n{i}", "cmd": "c"} for i in range(20)]
    out = st.clean_custom(items)
    assert out[0] == {"name": "Блокнот", "cmd": "открой блокнот"} and len(out) == st.MAX_CUSTOM
    assert st.clean_custom("nope") == []
    s = tools.clean_settings({"tools": [{"name": "A", "cmd": "b"}]})
    assert s["tools"] == [{"name": "A", "cmd": "b"}]
    assert tools.clean_settings({}, s)["tools"] == s["tools"]                    # без правки список сохраняется
    assert tools.clean_settings({}, None)["tools"] == []


def test_pick_text_prefers_clean_language():
    ru, en_garbage = "Счёт к оплате 18 400 рублей", "Cчёt к oплaтe 18 400 pублeй"
    assert st.pick_text(["", en_garbage, ru]) == ru
    assert st.pick_text(["", "  "]) == ""


def test_layout_names():
    assert st.layout_name(0x04190419) == "RU" and st.layout_name(0x04090409) == "EN" and st.layout_name(0x0809) == "EN"
    assert st.next_layout("RU") == "EN" and st.next_layout("EN") == "RU"


def test_fmt_time():
    assert st.fmt_time(42) == "00:42" and st.fmt_time(75) == "01:15" and st.fmt_time(3725) == "1:02:05"


def test_ffmpeg_cmd():
    c = st.ffmpeg_cmd("ff", "out.mp4", win=True)
    assert c[0] == "ff" and "gdigrab" in c and c[-1] == "out.mp4" and "yuv420p" in c


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


def test_recorder_start_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    made = []
    rec = st.Recorder(popen=lambda cmd, **kw: made.append(FakeProc(cmd, **kw)) or made[-1], ffmpeg="ff", out_dir=tmp_path)
    assert not rec.on
    r = rec.start()
    assert rec.on and r["on"] and r["path"].startswith(str(tmp_path)) and r["path"].endswith(".mp4")
    assert rec.start()["path"] == r["path"] and len(made) == 1                    # второй старт ничего не запускает
    done = rec.stop()
    assert made[0].sent == [b"q\n"] and not rec.on and done["path"] == r["path"]
    assert rec.stop()["path"] == ""


def test_recorder_reports_ffmpeg_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    rec = st.Recorder(popen=lambda cmd, **kw: FakeProc(cmd, dies=True), ffmpeg="ff", out_dir=tmp_path)
    with pytest.raises(st.ToolError) as e:
        rec.start()
    assert "Could not find device" in str(e.value) and not rec.on


def test_recorder_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(st, "find_ffmpeg", lambda: None)
    with pytest.raises(st.ToolError):
        st.Recorder().start()


def test_ocr_and_paste_need_windows_or_text(monkeypatch):
    if not st.IS_WIN:
        with pytest.raises(st.ToolError):
            st.ocr_image(Image.new("RGB", (10, 10)))
    monkeypatch.setattr(st, "clip_get_text", lambda: "   ")
    with pytest.raises(st.ToolError):
        st.paste_clean()


def test_island_shown_through_winforms(tmp_path, monkeypatch):
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
    assert isl._show_native(1) is True and isl._painting and isl.window.calls == ["show"]
    isl._painting, isl.window = False, W(fail=True)
    assert isl._show_native(1) is False and not isl._painting
