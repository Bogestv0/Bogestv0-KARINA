"""1.14.1: окно не выходит за экран, версия, устойчивость острова к пропущенным событиям."""
import queue
import time
from types import SimpleNamespace

import karina
from karina import island, winframe


def test_version():
    assert karina.__version__ == "1.14.1"


WORK = (0, 0, 1536, 816)


def test_clamp_keeps_window_inside_work_area():
    # окно выше рабочей области и торчит под панель задач
    x, y, w, h = winframe.clamp_rect(100, 38, 1350, 950, WORK)
    assert (w, h) == (1350, 816) and y == 0 and 0 <= x <= WORK[2] - w
    # уехало влево-вверх
    assert winframe.clamp_rect(-500, -90, 800, 600, WORK)[:2] == (0, 0)
    # уехало вправо-вниз
    x, y, w, h = winframe.clamp_rect(1500, 800, 800, 600, WORK)
    assert x + w == WORK[2] and y + h == WORK[3]
    # нормальное положение не трогаем
    assert winframe.clamp_rect(100, 50, 800, 600, WORK) == (100, 50, 800, 600)


def test_size_fits_work_area():
    for scale in (1.0, 1.25, 1.5, 2.0):
        for work in ((0, 0, 1920, 1032), (0, 0, 1536, 816), (0, 0, 1280, 672)):
            w, h = winframe._size_for(work, scale)
            assert w <= work[2] and h <= work[3]


def test_frame_control_safe_off_windows():
    fc = winframe.FrameControl(lambda: None)
    if not winframe.IS_WIN:
        assert fc.ensure_visible() is False
        assert fc.begin_drag("move") is False


def test_island_init_loop_marks_ready_without_events():
    """Событие «загружено» могло сработать до подписки: поток проверки сам заметит это и выставит ready."""
    ev = SimpleNamespace(is_set=lambda: True)
    w = SimpleNamespace(events=SimpleNamespace(loaded=ev))
    isl = island.Island({}, None, lambda: None)
    isl.window = w
    isl._setup_window = lambda: True
    orig = time.sleep
    island.time.sleep = lambda s: None
    try:
        isl._init_loop(w)
    finally:
        island.time.sleep = orig
    assert isl.ready is True


def test_island_forward_after_ready():
    isl = island.Island({}, None, lambda: None)
    isl._q = queue.Queue()
    isl.ready = True
    isl.forward({"type": "state", "state": "idle"})
    assert isl._q.qsize() == 1


def test_setup_window_noop_without_window():
    isl = island.Island({}, None, lambda: None)
    assert isl._setup_window() is False
