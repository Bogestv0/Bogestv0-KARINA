"""1.17.0: остров плавнее раскрывается, не теряет форму после смены темы Windows, переносится к краям экрана и закрепляется.

Win32 тут подменён: настоящий user32/gdi32 есть только в Windows, а логика (геометрия, перетаскивание, полёт, сторож) от них зависит."""
import ctypes
import json
import threading
import time
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from karina import island, island_tools as tools

WORK = (0, 0, 1280, 680)


# ---------------------------------------------------------------- чистая геометрия
def test_vertical_only_at_side_docks_and_for_collapsed_modes():
    assert island.is_vert("left", "mini") and island.is_vert("right", "info") and island.is_vert("left", "music")
    assert not island.is_vert("top", "mini") and not island.is_vert("bottom", "info")
    for m in ("wide", "drop", "full"):
        assert not island.is_vert("left", m)                    # текст и панель остаются горизонтальными
    w, h, _ = island.mode_size("left", "info", 5000)             # у бокового края ext — высота, зажатая в пределы
    assert w == island.VSIZES["info"][0] and h == island.VEXT_MAX
    w, h, _ = island.mode_size("top", "info", 5000)
    assert w == island.WIDTH_MAX and h == island.SIZES["info"][1]


def test_pill_xy_hugs_the_edge_and_stays_on_screen():
    assert island.pill_xy("top", 0.5, 150, 36, WORK, 6) == (565, 6)
    assert island.pill_xy("bottom", 0.5, 150, 36, WORK, 6) == (565, 680 - 6 - 36)
    assert island.pill_xy("left", 0.5, 36, 76, WORK, 6) == (6, 302)
    assert island.pill_xy("right", 0.5, 36, 76, WORK, 6) == (1280 - 6 - 36, 302)
    # у самого угла плашка не вылезает за экран
    assert island.pill_xy("top", 0.0, 150, 36, WORK, 6)[0] == 6
    assert island.pill_xy("top", 1.0, 150, 36, WORK, 6)[0] == 1280 - 6 - 150
    assert island.pill_xy("left", 1.0, 36, 76, WORK, 6)[1] == 680 - 6 - 76
    # рабочая область меньше плашки: ставим по центру, не падаем
    assert island.pill_xy("top", 0.2, 500, 36, (0, 0, 300, 200), 6)[0] == -100


def test_snap_picks_nearest_edge_and_magnets_to_middle():
    assert island.snap_dock(30, 340, WORK)[0] == "left"
    assert island.snap_dock(1250, 300, WORK)[0] == "right"
    assert island.snap_dock(640, 20, WORK) == ("top", 0.5)
    assert island.snap_dock(900, 660, WORK)[0] == "bottom"
    d, pos = island.snap_dock(30, 170, WORK)
    assert d == "left" and abs(pos - 0.25) < 1e-6
    assert island.snap_dock(-500, 300, WORK)[1] >= 0.0          # курсор за экраном: доля всё равно в пределах 0..1


def test_union_rect():
    assert island.union_rect([(10, 10, 20, 20), (0, 15, 5, 5), (25, 0, 10, 12)]) == (0, 0, 35, 30)


def test_ease_is_monotonic_without_overshoot():
    for grow in (True, False):
        vals = [island._ease(i / 50, grow) for i in range(51)]
        assert vals[0] == 0 and abs(vals[-1] - 1) < 1e-9
        assert all(b >= a for a, b in zip(vals, vals[1:])) and max(vals) <= 1.0       # раньше было «пружинка» с перелётом выше 1


# ---------------------------------------------------------------- настройки положения
def test_settings_validate_place():
    s = tools.clean_settings({})
    assert (s["pinned"], s["dock"], s["pos"], s["mon"]) == (False, "top", 0.5, None)
    s = tools.clean_settings({"pinned": 1, "dock": "left", "pos": 0.25, "mon": [1920, 0]})
    assert (s["pinned"], s["dock"], s["pos"], s["mon"]) == (True, "left", 0.25, [1920, 0])
    s = tools.clean_settings({"dock": "middle", "pos": 7, "mon": "x"}, s)           # мусор не принимаем, остаётся прежнее разумное
    assert s["dock"] == "left" and s["pos"] == 1.0 and s["mon"] is None
    s = tools.clean_settings({}, {"dock": "??", "pos": "abc", "mon": [1]})            # и испорченный config.json не ломает остров
    assert (s["dock"], s["pos"], s["mon"]) == ("top", 0.5, None)


def test_pinned_position_survives_restart_unpinned_does_not():
    cfg = {"island": {"pinned": True, "dock": "right", "pos": 0.3, "mon": [0, 0]}}
    isl = island.Island(SimpleNamespace(get=lambda k, d=None: cfg.get(k, d), update=lambda d: cfg.update(d)), None, lambda: None)
    isl._load_place()
    assert (isl._pinned, isl._dock, isl._pos, isl._mon) == (True, "right", 0.3, (0, 0))
    cfg["island"]["pinned"] = False
    isl._load_place()
    assert (isl._pinned, isl._dock, isl._pos, isl._mon) == (False, "top", 0.5, None)


def test_off_windows_is_safe():
    isl = island.Island({}, None, lambda: None)
    assert isl.set_mode("full") is None and isl.drag() == {"ok": False} and isl.layout_info() is None
    isl.reassert()
    isl._guard_once()
    api = island.IslandApi(isl, SimpleNamespace(assistant=SimpleNamespace(state="idle", sleeping=False, listening=True, cfg={"name": "К"})), lambda: None)
    assert api.set_mode("full") is True                          # не Windows: просто «принято»


# ---------------------------------------------------------------- Windows, подменённый
class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MonInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _Rect), ("rcWork", _Rect), ("dwFlags", wintypes.DWORD)]


class FakeWin:
    """Минимум Win32, который использует остров: одно окно, его прямоугольник и регион, курсор по сценарию."""

    def __init__(self, scale=1.0):
        self.scale, self.rect, self.region, self.style = scale, [0, 0, 150, 36], None, 0
        self.script, self.pressed, self.calls = [], 0, []
        self.user32 = SimpleNamespace(
            MonitorFromPoint=lambda pt, f: 1,
            GetMonitorInfoW=self._monitor_info,
            GetDpiForWindow=lambda h: int(96 * self.scale),
            GetSystemMetrics=lambda n: 0 if n == 23 else (1280 if n == 0 else 680),
            GetWindowRect=lambda h, r: self._fill(r._obj),
            SetWindowPos=self._set_pos,
            SetWindowRgn=self._set_rgn,
            GetWindowRgn=lambda h, tmp: 2 if self.region else 0,
            IsWindow=lambda h: True,
            GetWindowLongPtrW=lambda h, i: self.style,
            SetWindowLongPtrW=lambda h, i, v: setattr(self, "style", v) if i == -16 else 0,
            GetCursorPos=self._cursor,
            GetAsyncKeyState=self._key,
            GetForegroundWindow=lambda: 0,
            SetForegroundWindow=lambda h: 1,
        )
        self.gdi32 = SimpleNamespace(CreateRoundRectRgn=lambda *a: ("rgn", a), CreateRectRgn=lambda *a: ("rect", a), DeleteObject=lambda o: 1)

    def _monitor_info(self, mon, ref):
        mi = ref._obj
        mi.rcMonitor.right, mi.rcMonitor.bottom = 1280, 720
        mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom = WORK
        return 1

    def _fill(self, r):
        r.left, r.top, r.right, r.bottom = self.rect[0], self.rect[1], self.rect[0] + self.rect[2], self.rect[1] + self.rect[3]
        return 1

    def _set_pos(self, h, after, x, y, w, hh, flags):
        if flags & 0x0001:                                       # SWP_NOSIZE
            self.rect[0], self.rect[1] = x, y
        else:
            self.rect = [x, y, w, hh]
        return 1

    def _set_rgn(self, h, rgn, redraw):
        self.region = rgn
        self.calls.append(("rgn", rgn[1], tuple(self.rect)))
        return 1

    def _cursor(self, ref):
        if self.script:
            ref._obj.x, ref._obj.y = self.script[min(self.pressed, len(self.script) - 1)]
        return 1

    def _key(self, vk):
        self.pressed += 1
        return 0x8000 if self.pressed <= len(self.script) else 0


@pytest.fixture
def win(monkeypatch):
    f = FakeWin()
    for k, v in dict(IS_WIN=True, user32=f.user32, gdi32=f.gdi32, ctypes=ctypes, wintypes=wintypes, _RECT=_Rect, _MONITORINFO=_MonInfo,
                     GWL_STYLE=-16, GWL_EXSTYLE=-20, WS_CAPTION=0x00C00000, WS_THICKFRAME=0x00040000, WS_EX_TOOLWINDOW=0x80,
                     WS_EX_NOACTIVATE=0x08000000, WS_EX_APPWINDOW=0x40000, SWP_NOACTIVATE=0x10, SWP_FRAMECHANGED=0x20,
                     SWP_ASYNCWINDOWPOS=0x4000, SWP_NOSIZE=1, HWND_TOPMOST=-1).items():
        monkeypatch.setattr(island, k, v, raising=False)
    cfg = {"island": {}}
    isl = island.Island(SimpleNamespace(get=lambda k, d=None: cfg.get(k, d), update=lambda d: cfg.update(d)), None, lambda s: None)
    js = []
    isl.window = SimpleNamespace(evaluate_js=lambda c: js.append(c), events=SimpleNamespace())
    isl._hwnd, isl.ready, isl._painting = 100, True, True
    monkeypatch.setattr(island.Island, "_hires", staticmethod(lambda on: None))
    isl._load_place()
    isl._setup_window()
    isl.js, isl.fake, isl.cfg_ = js, f, cfg
    yield isl
    isl._gen += 1
    isl.window = None


def _wait(cond, t=3.0):
    end = time.time() + t
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_window_is_fixed_big_and_region_is_the_pill(win):
    f = win.fake
    full = win._rect_for("full", None, 1.0, WORK)
    assert tuple(f.rect) == win._win and win._win[2:] == (480, 330) and win._win[0] == full[0]        # окно под самую большую панель
    assert f.region is not None
    x, y, w, h, r = win._rect_cur                                  # а форма — маленькая плашка сверху по центру
    assert (w, h, y) == (150, 36, 6) and x == (1280 - 150) // 2
    lay = win.layout_info()
    assert lay["dock"] == "top" and lay["pinned"] is False and lay["mode"] == "mini"
    assert lay["s"][2:] == [150, 36] and lay["f"] == [0, 0]


def test_mode_change_animates_region_only_and_never_resizes_window(win):
    f = win.fake
    win_before, sizes = tuple(f.rect), set()
    orig = f.user32.SetWindowPos
    f.user32.SetWindowPos = lambda *a: (sizes.add(tuple(f.rect[2:])), orig(*a))[1]
    lay = win.set_mode("full")
    assert lay["mode"] == "full" and lay["s"][2:] == [480, 330]
    assert _wait(lambda: win._rect_cur[2:4] == (480, 330) and not win._animating)
    assert tuple(f.rect) == win_before                              # окно не двигалось и не менялось
    assert len([c for c in f.calls if c[0] == "rgn"]) > 8          # а регион шёл кадрами
    assert all(c[2] == win_before for c in f.calls[-5:])
    win.set_mode("info", 232)
    assert _wait(lambda: win._rect_cur[2:4] == (232, 36) and not win._animating)
    assert sizes <= {win_before[2:]}


def test_retarget_mid_animation_is_smooth(win):
    win.set_mode("full")
    time.sleep(0.1)
    mid = win._rect_cur[2]
    assert island.SIZES["mini"][0] < mid < 480
    win.set_mode("mini")
    assert _wait(lambda: win._rect_cur[2] == 150 and not win._animating)


def test_drag_to_left_edge_snaps_vertical_and_reports_layout(win):
    f, mid = win.fake, win._rect_cur
    cx, cy = int(mid[0] + mid[2] / 2), int(mid[1] + mid[3] / 2)
    f.script = [(cx, cy), (cx - 100, cy + 40), (cx - 400, cy + 150), (cx - 600, cy + 250), (cx - 610, cy + 250)]
    f.pressed = 0
    r = win.drag()
    assert r["ok"] and r["moved"] and r["dock"] == "left"
    assert (win._dock, win._mon) == ("left", (0, 0)) and 0.0 < win._pos < 1.0
    x, y, w, h, _ = win._rect_cur
    assert x == 6 and (w, h) == island.VSIZES["mini"][:2]            # встала вертикально у левого края
    assert win._win[2:] == (480, 330) and tuple(f.rect) == win._win
    assert win.js[0].endswith("fly(true)") and win.js[-1].endswith("fly(false)")
    lay = json.loads(win.js[-2].split("layout(")[1].rstrip(")"))
    assert lay["dock"] == "left" and lay["s"][2:] == [36, 76]
    # режим после переезда считается уже по-вертикальному: высота из ext
    win.set_mode("info", 140)
    assert _wait(lambda: win._rect_cur[2:4] == (48, 140) and not win._animating)
    # панель открывается вправо, к центру экрана
    win.set_mode("full")
    assert _wait(lambda: win._rect_cur[2:4] == (480, 330) and not win._animating)
    assert win._rect_cur[0] == 6


def test_click_without_moving_is_not_a_drag(win):
    f, mid = win.fake, win._rect_cur
    f.script = [(10, 10), (11, 10), (10, 11)]
    f.pressed = 0
    r = win.drag()
    assert r == {"ok": True, "moved": False} and win._dock == "top" and not win._busy


def test_pinned_island_cannot_be_dragged_and_is_saved(win):
    win.fake.script, win.fake.pressed = [(0, 0), (500, 500)], 0
    lay = win.set_pinned(True)
    assert lay["pinned"] is True and win.cfg_["island"]["pinned"] is True and win.cfg_["island"]["dock"] == "top"
    assert win.drag() == {"ok": False}
    win.set_pinned(False)
    assert win.cfg_["island"]["pinned"] is False


def test_dock_reset_returns_to_top_center_unpinned(win):
    win._pinned = True
    win._dock, win._pos = "right", 0.2
    win.settings_set({"pinned": True, "dock": "right", "pos": 0.2})
    win._ext = None
    win._rect_cur = win._rect_for("mini", None, 1.0, WORK)
    win.fake.rect = [0, 0, 1, 1]
    lay = win.dock_reset()
    assert lay["dock"] == "top" and lay["pinned"] is False and win._pos == 0.5
    assert win.cfg_["island"]["pinned"] is False and not win._busy


def test_mode_requested_during_drag_is_applied_after(win):
    win._busy = True
    lay = win.set_mode("full")
    assert lay["mode"] == "full" and win._rect_cur[2] == 150        # пока тащат, только запомнили
    win._busy = False
    win._go()
    assert _wait(lambda: win._rect_cur[2:4] == (480, 330) and not win._animating)


def test_guard_restores_lost_shape_after_theme_change(win):
    """Смена темы Windows сбрасывала регион: остров становился прямоугольником. Сторож это замечает и возвращает форму."""
    f = win.fake
    f.region = None                                                  # регион слетел
    win._guard_once()
    assert f.region is not None
    f.style = 0x00C00000                                             # вернулся заголовок окна
    f.rect = [0, 0, 150, 36]                                         # и окно уехало
    win._guard_once()
    assert tuple(f.rect) == win._win
    calls = len(f.calls)
    win._guard_once()                                                # всё цело: ничего не трогаем
    assert len(f.calls) == calls
    win._hidden_by_us = True
    f.region = None
    win._guard_once()                                                # остров спрятан намеренно (снимок): не вмешиваемся
    assert f.region is None


def test_scaled_screen_keeps_css_sizes_logical():
    f = FakeWin(scale=1.5)
    assert f.user32.GetDpiForWindow(1) == 144
    w, h, r = island.mode_size("top", "full")
    assert (round(w * 1.5), round(h * 1.5)) == (720, 495)
