"""1.14.2: главное окно снова двигается/растягивается/разворачивается, остров закрывается и открывается заново, край острова сглажен."""
import queue
import re
from pathlib import Path
from types import SimpleNamespace

from karina import halo, island

KARINA = Path(__file__).resolve().parent.parent / "karina"


def test_win32_modules_do_not_share_windll_user32():
    """ctypes.windll.user32 — один объект на весь процесс: argtypes остова перебивали argtypes главного окна
    (GetMonitorInfoW с другим классом структуры → ArgumentError → окно не двигалось). Каждому модулю — своя копия."""
    for name in ("winframe.py", "island.py", "halo.py"):
        src = (KARINA / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(user32|gdi32)\s*=\s*ctypes\.windll", src, re.M), name


def test_halo_alpha_is_smooth_pill():
    a = halo.pill_alpha(150, 36, 18)
    assert a.shape == (36, 150)
    assert a[18, 75] == 255 and a[0, 0] == 0                      # центр залит, угол пуст
    inner = ((a > 0) & (a < 255)).sum()
    assert inner > 60                                             # на краю есть промежуточные значения (сглаживание)
    assert (a[:, :75] == a[:, ::-1][:, :75]).all()                # симметрия


def test_halo_bgra_is_premultiplied_black():
    px = halo.pill_bgra(40, 20, 10)
    assert len(px) == 40 * 20 * 4
    assert set(px[0::4]) == {0} and set(px[1::4]) == {0} and set(px[2::4]) == {0}


def test_halo_inactive_off_windows():
    h = halo.Halo()
    assert h.update(1, 0, 0, 100, 30, 15) is False
    h.stop()


def test_island_close_is_cancelled_but_allowed_when_stopping():
    isl = island.Island({}, SimpleNamespace(quitting=False), lambda: None)
    assert isl._on_closing() is False                             # пользователь/система закрывают окно — остров остаётся
    isl._stopping = True
    assert isl._on_closing() is None
    isl._stopping = False
    isl.api.quitting = True                                       # выход из Карины / выключение Windows — не мешаем
    assert isl._on_closing() is None


def test_island_can_be_reopened_after_window_closed():
    isl = island.Island({}, None, lambda: None)
    w = object()
    isl.window, isl.ready, isl._q = w, True, queue.Queue()
    isl._on_closed(w)
    assert isl.window is None and not isl.running and not isl.ready
    assert isl._stopping is False                                 # следующий stop()/close работают как обычно
    isl._on_closed(object())                                      # чужое/старое окно состояние не трогает
    isl.window = w
    isl._on_closed(object())
    assert isl.window is w


def test_island_page_radius_matches_python():
    css = (Path(__file__).resolve().parent.parent / "ui" / "island.html").read_text(encoding="utf-8")
    assert f"--r:min({island.RADIUS_MAX}px,50vh)" in css
    for m, (w, h, r) in island.SIZES.items():
        assert min(island.RADIUS_MAX, h / 2) == r, m             # формула радиуса даёт те же значения, что и таблица размеров
