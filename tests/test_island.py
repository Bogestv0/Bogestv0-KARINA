"""Динамический остров: настройка по умолчанию, размеры, пересылка событий, API для страницы острова."""
import queue
from types import SimpleNamespace
from karina import island
from karina.config import DEFAULTS


def test_off_by_default():
    assert DEFAULTS["ui"]["island"] is False


def test_sizes_grow_and_ease_endpoints():
    m, w, f = (island.SIZES[k] for k in ("mini", "wide", "full"))
    assert m[0] < w[0] < f[0] and m[1] < f[1]
    for grow in (True, False):
        assert abs(island._ease(0, grow)) < 1e-9 and abs(island._ease(1, grow) - 1) < 1e-9


def test_forward_filters_and_needs_ready():
    isl = island.Island({}, None, lambda: None)
    isl._q = queue.Queue()
    isl.forward({"type": "state"})                 # страница ещё не загружена
    assert isl._q.empty()
    isl.ready = True
    isl.forward({"type": "state", "state": "idle"})
    isl.forward({"type": "level", "v": 0.1})
    isl.forward({"type": "model_progress"})        # острову не нужно
    assert isl._q.qsize() == 2


def test_apply_is_safe_without_window():
    isl = island.Island({}, None, lambda: None)
    isl.apply(False)
    isl.stop()
    assert not isl.running


def test_api_state_and_modes():
    a = SimpleNamespace(state="idle", sleeping=False, listening=True, cfg={"name": "Карина"})
    isl = island.Island({}, SimpleNamespace(assistant=a), lambda: None)
    api = island.IslandApi(isl, SimpleNamespace(assistant=a), lambda: None)
    st = api.get_state()
    assert (st["state"], st["sleeping"], st["listening"], st["name"]) == ("idle", False, True, "Карина")
    assert st["settings"]["character"] == "logo"
    assert api.set_mode("bogus") is True           # неизвестный режим просто игнорируется
