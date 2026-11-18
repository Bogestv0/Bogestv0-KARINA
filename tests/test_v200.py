"""1.20.0: снег из-под острова, Wi-Fi/Bluetooth, автозапуск, звук и горячие клавиши острова."""
import random, sys, types, unittest
from types import SimpleNamespace
from unittest import mock

from karina import hotkeys, island, island_tools as tools, snow


class FakeCfg:
    def __init__(self, data):
        self.data = data

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, d=None):
        return self.data.get(k, d)

    def update(self, patch):
        for k, v in patch.items():
            if isinstance(v, dict):
                self.data.setdefault(k, {}).update(v)
            else:
                self.data[k] = v


class SnowTests(unittest.TestCase):
    def test_flakes_fall_and_respawn_inside_window(self):
        fl = snow.Flakes(300, 150, 40, 260, 1.0, random.Random(1))
        self.assertGreaterEqual(len(fl.items), 8)
        for _ in range(400):
            fl.step(0.05)
            for f in fl.items:
                self.assertLessEqual(f[1], fl.h + 3 + 50)          # упал за нижний край: появляется заново сверху
        self.assertTrue(all(-10 <= f[1] for f in fl.items))

    def test_alpha_fades_in_under_island_and_out_at_bottom(self):
        fl = snow.Flakes(300, 100, 40, 260, 1.0, random.Random(2))
        top, mid, low = [40, 0.0, 30, 0, 0], [40, 4.0, 30, 0, 0], [40, 99.0, 30, 0, 0]
        self.assertEqual(fl.alpha(top), 0.0)
        self.assertGreater(fl.alpha([40, 30, 30, 0, 0]), fl.alpha(mid))
        self.assertLess(fl.alpha(low), 0.05)

    def test_render_size_and_premultiplied_white(self):
        fl = snow.Flakes(120, 60, 20, 100, 1.0, random.Random(3))
        for f in fl.items:
            f[1] = 30.0
        data = snow.render(fl, snow.flake_sprites())
        self.assertEqual(len(data), 120 * 60 * 4)
        px = [data[i:i + 4] for i in range(0, len(data), 4) if data[i + 3]]
        self.assertTrue(px)
        self.assertTrue(all(p[0] == p[1] == p[2] == p[3] for p in px))      # белый цвет: каждый канал равен альфе


class ToolsTests(unittest.TestCase):
    def test_off_windows_is_safe(self):
        if tools.IS_WIN:
            self.skipTest("только не-Windows")
        self.assertEqual(tools.radios_get(), {"wifi": None, "bluetooth": None})
        self.assertFalse(tools.radio_set("wifi", True)["ok"])
        self.assertFalse(tools.radio_set("nope", True)["ok"])
        self.assertIsNone(tools.autostart_get())
        self.assertFalse(tools.autostart_set(True))

    def test_autostart_command_background(self):
        self.assertTrue(tools.autostart_command().endswith("--background"))


class RadioTests(unittest.TestCase):
    """Windows подменяем: фальшивый winrt.windows.devices.radios с одним Wi-Fi (включён) и одним Bluetooth (выключен)."""

    def fake(self, access=1):
        state = {"wifi": 1, "bt": 2}

        class Radio:
            def __init__(self, kind, key):
                self.kind, self.key = kind, key

            @property
            def state(self):
                return state[self.key]

            async def set_state_async(self, st):
                if access == 1:
                    state[self.key] = int(st)
                return access

            @staticmethod
            async def request_access_async():
                return access

            @staticmethod
            async def get_radios_async():
                return [Radio(1, "wifi"), Radio(3, "bt")]

        class State(int):
            pass
        mod = types.ModuleType("winrt.windows.devices.radios")
        mod.Radio, mod.RadioAccessStatus, mod.RadioKind, mod.RadioState = Radio, object, object, State
        mods = {"winrt": types.ModuleType("winrt"), "winrt.windows": types.ModuleType("winrt.windows"),
                "winrt.windows.devices": types.ModuleType("winrt.windows.devices"), "winrt.windows.devices.radios": mod}
        return mods, state

    def test_get_and_toggle(self):
        mods, state = self.fake()
        with mock.patch.dict(sys.modules, mods), mock.patch.object(tools, "IS_WIN", True):
            self.assertEqual(tools.radios_get(), {"wifi": True, "bluetooth": False})
            r = tools.radio_set("bluetooth", True)
            self.assertTrue(r["ok"], r)
            self.assertEqual(state["bt"], 1)
            self.assertEqual(tools.radios_get()["bluetooth"], True)
            self.assertTrue(tools.radio_set("wifi", False)["ok"])
            self.assertEqual(state["wifi"], 2)

    def test_access_denied_is_reported(self):
        mods, _ = self.fake(access=2)
        with mock.patch.dict(sys.modules, mods), mock.patch.object(tools, "IS_WIN", True):
            r = tools.radio_set("wifi", False)
            self.assertFalse(r["ok"])
            self.assertTrue(r["error"])


class HotkeyTests(unittest.TestCase):
    def test_defaults_parse_and_are_unique(self):
        parsed = [hotkeys.parse(v) for v in hotkeys.DEFAULTS.values()]
        self.assertTrue(all(parsed))
        self.assertEqual(len(set(parsed)), len(parsed))

    def api(self, keys=None):
        a = SimpleNamespace(cfg=FakeCfg({"hotkeys": dict(keys or {}), "ui": {"island": False}}))
        a.assistant = SimpleNamespace(hotkeys=SimpleNamespace(errors=[], reload=lambda: None))
        return island.IslandApi(SimpleNamespace(), a, lambda: None)

    def test_hotkey_set_validates(self):
        api = self.api()
        self.assertFalse(api.hotkey_set("island", "i")["ok"])                       # без модификатора перехватили бы обычную клавишу
        self.assertFalse(api.hotkey_set("nope", "ctrl+alt+i")["ok"])
        self.assertFalse(api.hotkey_set("snip", "ctrl+alt+i")["ok"])                # уже занято «островом»
        r = api.hotkey_set("snip", "Ctrl + Shift + F5")
        self.assertTrue(r["ok"])
        self.assertEqual(api._api.cfg["hotkeys"]["snip"], "ctrl+shift+f5")
        self.assertTrue(api.hotkey_set("snip", "")["ok"])                           # пусто — клавиша выключена

    def test_autostart_turns_island_on(self):
        api = self.api()
        with mock.patch.object(island.tools, "autostart_set", return_value=True), mock.patch.object(island.tools, "autostart_get", return_value=True):
            self.assertTrue(api.autostart_set(True)["ok"])
        self.assertTrue(api._api.cfg["ui"]["island"])


if __name__ == "__main__":
    unittest.main()
