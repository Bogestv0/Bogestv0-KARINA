"""1.20.4: окно «Звук» на острове: дорожки Bogestv0 MIXER, выбор дорожки для ползунка громкости."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from karina import island as isl_mod
from karina import island_tools as tools
from karina.mixer_remote import MixerError, db_to_pct, pct_to_db

HTML = (Path(__file__).resolve().parent.parent / "ui" / "island.html").read_text("utf-8")


class FakeMixer:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []
        self._strips = [{"i": 0, "name": "Мастер", "tag": "", "hidden": False, "gain": 0.0, "mute": False},
                        {"i": 1, "name": "Игры", "tag": "game", "hidden": False, "gain": -6.0, "mute": False},
                        {"i": 2, "name": "Служебная", "tag": "", "hidden": True, "gain": 0.0, "mute": False}]

    def strips(self):
        if self.fail:
            raise MixerError("Не получается связаться с микшером.")
        return [dict(s) for s in self._strips]

    def set_gain(self, i, db):
        self.calls.append(("gain", i, db))
        return db

    def set_mute(self, i, on):
        self.calls.append(("mute", i, on))


class FakeIsland:
    def __init__(self):
        self.s = dict(tools.ISLAND_DEFAULTS)

    def settings(self):
        return dict(self.s)

    def settings_set(self, patch):
        self.s = tools.clean_settings(patch, self.s)
        return self.s


def make(mixer):
    isl = FakeIsland()
    api = isl_mod.IslandApi(isl, SimpleNamespace(assistant=SimpleNamespace(mixer=mixer)), lambda: None)
    return api, isl


class SettingTest(unittest.TestCase):
    def test_mixer_strip_is_cleaned(self):
        self.assertEqual(tools.clean_settings({"mixer_strip": "  Игры   Музыка "})["mixer_strip"], "Игры Музыка")
        self.assertEqual(tools.clean_settings({"mixer_strip": None})["mixer_strip"], "")
        self.assertEqual(len(tools.clean_settings({"mixer_strip": "я" * 200})["mixer_strip"]), 60)
        self.assertEqual(tools.ISLAND_DEFAULTS["mixer_strip"], "")


class MixerApiTest(unittest.TestCase):
    def test_get_lists_visible_strips_in_percent(self):
        api, _ = make(FakeMixer())
        r = api.mixer_get()
        self.assertTrue(r["ok"])
        self.assertEqual([s["name"] for s in r["strips"]], ["Мастер", "Игры"])      # скрытые дорожки не показываем
        self.assertEqual([s["pct"] for s in r["strips"]], [100, db_to_pct(-6.0)])

    def test_get_reports_mixer_error_as_text(self):
        api, _ = make(FakeMixer(fail=True))
        r = api.mixer_get()
        self.assertFalse(r["ok"])
        self.assertIn("микшером", r["error"])

    def test_gain_and_mute_go_to_mixer(self):
        m = FakeMixer()
        api, _ = make(m)
        self.assertTrue(api.mixer_gain(1, 50)["ok"])
        self.assertTrue(api.mixer_mute(1, True)["ok"])
        self.assertEqual(m.calls, [("gain", 1, pct_to_db(50)), ("mute", 1, True)])
        api.mixer_gain(1, 9999)                                                     # потолок 150 %
        self.assertEqual(m.calls[-1][2], pct_to_db(150))


class VolumeRoutingTest(unittest.TestCase):
    def test_slider_controls_selected_strip(self):
        m = FakeMixer()
        api, isl = make(m)
        self.assertEqual(api.mixer_select("игры")["src"], "Игры")                   # имя без учёта регистра
        self.assertEqual(api.volume_get(), db_to_pct(-6.0))
        self.assertTrue(api.volume_set(40))
        self.assertTrue(api.mute_set(True))
        self.assertEqual(m.calls, [("gain", 1, pct_to_db(40)), ("mute", 1, True)])

    def test_falls_back_to_windows_volume_when_mixer_is_down(self):
        api, _ = make(FakeMixer(fail=True))
        api.mixer_select("Игры")
        from karina import actions
        with mock.patch.object(actions, "_get_volume", return_value=33), mock.patch.object(actions, "_set_volume", return_value=True) as sv:
            self.assertEqual(api.volume_get(), 33)
            self.assertTrue(api.volume_set(10))
            sv.assert_called_once_with(10)
        self.assertEqual(api.volume_src(), "")
        # после неудачи микшер не дёргаем на каждое движение ползунка
        m = api._api.assistant.mixer
        n = []
        m.strips = lambda: n.append(1) or (_ for _ in ()).throw(MixerError("x"))
        with mock.patch.object(actions, "_get_volume", return_value=33):
            api.volume_get(); api.volume_get()
        self.assertEqual(n, [])

    def test_no_selection_means_windows_volume(self):
        m = FakeMixer()
        api, _ = make(m)
        from karina import actions
        with mock.patch.object(actions, "_get_volume", return_value=70):
            self.assertEqual(api.volume_get(), 70)
        self.assertEqual(m.calls, [])
        self.assertEqual(api.volume_src(), "")

    def test_unknown_or_hidden_strip_falls_back(self):
        api, _ = make(FakeMixer())
        api.mixer_select("Служебная")
        self.assertEqual(api.volume_src(), "")


class PageTest(unittest.TestCase):
    def test_page_wires_sound_window(self):
        for w in ("sndView", "openSound", "'mixer_get'", "'mixer_gain'", "'mixer_mute'", "'mixer_select'", "'volume_src'", "volSrc"):
            self.assertIn(w, HTML)


if __name__ == "__main__":
    unittest.main()
