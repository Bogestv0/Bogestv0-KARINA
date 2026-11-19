"""Вкладка «Виджет»: положение острова из настроек, список мониторов, API страницы."""
from types import SimpleNamespace
from karina import island, island_tools


class _Cfg(dict):
    def update(self, patch):
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(self.get(k), dict):
                self[k].update(v)
            else:
                self[k] = v


def _isl():
    cfg = _Cfg({"island": {}, "ui": {"island": False}})
    return island.Island(cfg, None, lambda: None), cfg


def test_clean_settings_keeps_placed():
    assert island_tools.clean_settings({}, None)["placed"] is False
    assert island_tools.clean_settings({}, {"placed": True})["placed"] is True


def test_monitors_empty_off_windows():
    isl, _ = _isl()
    if not island.IS_WIN:
        assert isl.monitors() == []


def test_place_saves_and_where_reports():
    isl, _ = _isl()
    if island.IS_WIN:
        return
    r = isl.place("left", 0.25, 0)
    assert r["ok"] and not r["live"]
    w = isl.where()
    assert (w["dock"], w["pos"], w["pinned"], w["running"]) == ("left", 0.25, False, False)
    assert isl.settings()["placed"] is True


def test_place_clamps_bad_input():
    isl, _ = _isl()
    if island.IS_WIN:
        return
    isl.place("nonsense", 7)
    w = isl.where()
    assert w["dock"] == "top" and w["pos"] == 1.0
    isl.place("right", "abc")
    assert isl.where()["pos"] == 0.5


def test_widget_api_set_filters_keys():
    isl, cfg = _isl()
    isl.push = lambda ev: None
    from karina.ui_api import Api as UiApi
    api = SimpleNamespace(island=isl, cfg=cfg, WIDGET_KEYS=UiApi.WIDGET_KEYS)
    r = UiApi.widget_set(api, {"city": "Омск", "pinned": True, "evil": 1})
    assert r["city"] == "Омск"
    assert not isl.settings().get("pinned")
