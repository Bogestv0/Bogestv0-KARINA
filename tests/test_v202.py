"""1.20.2: ссылки и значки ярлыков, клавиатура в поле ввода, RU/EN, Wi-Fi и Bluetooth (поток COM), VPN без прав администратора, запасная погода."""
import io
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from karina import island as isl_mod
from karina import island_tools as tools
from karina import screen_tools as st
from karina import shelf as shf

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "ui" / "island.html").read_text("utf-8")


class UrlTest(unittest.TestCase):
    def test_as_url(self):
        self.assertEqual(shf.as_url("https://ya.ru/x?y=1"), "https://ya.ru/x?y=1")
        self.assertEqual(shf.as_url("www.ya.ru"), "https://www.ya.ru")
        self.assertEqual(shf.as_url("habr.com/ru/news"), "https://habr.com/ru/news")
        self.assertEqual(shf.as_url("  <https://a.io>  "), "https://a.io")
        for bad in ("readme.md", "C:\\a\\b.com", "site.exe", "две слова.ru", "", "привет"):
            self.assertEqual(shf.as_url(bad), "", bad)
        self.assertEqual(shf.url_host("https://www.ya.ru:80/a?b"), "ya.ru")


class FakeIsland:
    def __init__(self, d):
        self.links, self.shelf = [], SimpleNamespace(path=Path(d) / "shelf.json")
        self.tr = []

    def settings(self):
        return {"links": self.links}

    def links_add(self, paths):
        self.links += [{"path": p, "name": shf.label_of(p)} for p in paths]
        return {"ok": True, "n": len(paths), "links": self.links}

    def trace(self, m):
        self.tr.append(m)


class LinksTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.isl = FakeIsland(self.d)
        self.api = isl_mod.IslandApi(self.isl, SimpleNamespace(), lambda: None)

    def test_add_plain_domain_and_reject_junk(self):
        self.assertTrue(self.api.link_add("ya.ru")["ok"])
        self.assertEqual(self.isl.links[0]["path"], "https://ya.ru")
        self.assertFalse(self.api.link_add("не ссылка")["ok"])

    def test_paste_from_clipboard(self):
        with mock.patch.object(st, "clip_get_text", return_value="https://habr.com/ru\nещё"):
            self.assertTrue(self.api.link_paste()["ok"])
        with mock.patch.object(st, "clip_get_text", return_value=""):
            self.assertIn("пусто", self.api.link_paste()["error"])

    def test_favicon_is_fetched_and_cached(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(buf, "PNG")
        calls = []

        class R:
            def read(self, n=-1):
                return buf.getvalue()
        with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or R()):
            cache = Path(self.d) / "icons"
            a = shf.icon_for("https://ya.ru/x", cache)
            b = shf.icon_for("ya.ru", cache)
        self.assertTrue(a.startswith("data:image/png;base64,"))
        self.assertEqual(a, b)
        self.assertEqual(len(calls), 1)                         # второй раз из кэша на диске

    def test_failed_favicon_is_negative_cached_and_empty(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("нет сети")) as m:
            cache = Path(self.d) / "icons"
            self.assertEqual(shf.icon_for("https://nope.ru", cache), "")
            n = m.call_count
            self.assertEqual(shf.icon_for("https://nope.ru", cache), "")
            self.assertEqual(m.call_count, n)

    def test_non_windows_has_no_file_icon(self):
        with mock.patch.object(shf, "IS_WIN", False):
            self.assertEqual(shf.icon_for("C:\\x\\a.exe", Path(self.d)), "")
        self.assertEqual(shf.icon_for("C:\\папка", Path(self.d)), "")      # у папок свой нарисованный значок


class LayoutTest(unittest.TestCase):
    def test_falls_back_to_win_space_when_message_is_ignored(self):
        f = st.Focus()
        f.last = 1
        keys = []
        fake = types.SimpleNamespace(FAILSAFE=True, hotkey=lambda *a: keys.append(a))
        with mock.patch.object(st, "IS_WIN", True), mock.patch.dict(sys.modules, {"pyautogui": fake}), \
                mock.patch.object(f, "poll", return_value=1), mock.patch.object(f, "layout_get", return_value="RU"), \
                mock.patch.object(f, "layout_set", return_value=True), mock.patch.object(f, "restore"):
            f.toggle_layout()
        self.assertEqual(keys, [("win", "space")])

    def test_no_hotkey_when_message_worked(self):
        f = st.Focus()
        seq = iter(["RU", "EN", "EN", "EN"])
        keys = []
        fake = types.SimpleNamespace(FAILSAFE=True, hotkey=lambda *a: keys.append(a))
        with mock.patch.object(st, "IS_WIN", True), mock.patch.dict(sys.modules, {"pyautogui": fake}), \
                mock.patch.object(f, "poll", return_value=1), mock.patch.object(f, "layout_get", side_effect=lambda: next(seq)), \
                mock.patch.object(f, "layout_set", return_value=True):
            self.assertEqual(f.toggle_layout(), "EN")
        self.assertEqual(keys, [])


class RadioThreadTest(unittest.TestCase):
    def test_winrt_work_runs_in_its_own_thread(self):
        seen = {}

        class Radio:
            @staticmethod
            async def request_access_async():
                return 1

            @staticmethod
            async def get_radios_async():
                return []
        mod = types.ModuleType("winrt.windows.devices.radios")
        mod.Radio, mod.RadioAccessStatus, mod.RadioKind, mod.RadioState = Radio, object, object, int
        mods = {"winrt": types.ModuleType("winrt"), "winrt.windows": types.ModuleType("winrt.windows"),
                "winrt.windows.devices": types.ModuleType("winrt.windows.devices"), "winrt.windows.devices.radios": mod}

        async def work(*a):
            seen["thread"] = threading.current_thread().name
            return 5
        with mock.patch.dict(sys.modules, mods):
            self.assertEqual(tools._radio_call(work), 5)
        self.assertEqual(seen["thread"], "karina-radio")                # не поток окна (там COM в режиме STA)

    def test_import_error_is_passed_to_caller(self):
        async def work(*a):
            return 0
        with mock.patch.dict(sys.modules, {"winrt.windows.devices.radios": None}):
            with self.assertRaises(ImportError):
                tools._radio_call(work)


class VpnTest(unittest.TestCase):
    def make(self, first):
        calls = []
        tun = SimpleNamespace(status=lambda: {"running": False}, start=lambda mode=None: calls.append(mode) or "ok")
        api = SimpleNamespace(assistant=SimpleNamespace(tunnel=tun), tunnel_start=lambda: first,
                              _tunnel_do=lambda fn: {"ok": True, "result": fn()}, tunnel_stop=lambda: {"ok": True})
        return isl_mod.IslandApi(FakeIsland(tempfile.mkdtemp()), api, lambda: None), calls

    def test_without_admin_falls_back_to_proxy_mode(self):
        a, calls = self.make({"ok": False, "error": "Для режима VPN нужны права администратора."})
        r = a.sys_action("vpn")
        self.assertTrue(r["ok"])
        self.assertEqual(calls, ["proxy"])
        self.assertIn("прокси", r["msg"])

    def test_other_errors_are_shown(self):
        a, calls = self.make({"ok": False, "error": "Сначала добавьте сервер"})
        r = a.sys_action("vpn")
        self.assertFalse(r["ok"])
        self.assertIn("сервер", r["error"])
        self.assertEqual(calls, [])


class WeatherFallbackTest(unittest.TestCase):
    WTTR = {"current_condition": [{"temp_C": "-3", "FeelsLikeC": "-8", "weatherCode": "122", "windspeedKmph": "18", "lang_ru": [{"value": "Пасмурно"}]}],
            "weather": [{"date": "2026-11-18", "maxtempC": "-1", "mintempC": "-6", "hourly": [{}, {}, {}, {}, {"weatherCode": "338", "chanceofrain": "40", "lang_ru": [{"value": "Снег"}]}]}],
            "nearest_area": [{"areaName": [{"value": "Krasnoyarsk"}]}]}

    def test_falls_back_to_wttr_when_open_meteo_fails(self):
        tools._WCACHE.clear()
        with mock.patch.object(tools, "_weather_week", side_effect=RuntimeError("нет связи")), mock.patch.object(tools, "_http_json", return_value=self.WTTR):
            out = tools.weather_week("Красноярск")
        self.assertEqual(out["source"], "wttr.in")
        self.assertEqual(out["now"]["t"], -3)
        self.assertEqual(out["now"]["icon"], "☁️")
        self.assertEqual(out["days"][0]["icon"], "❄️")
        self.assertEqual(out["days"][0]["day"], "Сегодня")

    def test_both_fail_gives_readable_error(self):
        tools._WCACHE.clear()
        with mock.patch.object(tools, "_weather_week", side_effect=RuntimeError("не получилось (таймаут)")), mock.patch.object(tools, "_http_json", side_effect=OSError("блок")):
            with self.assertRaises(RuntimeError) as c:
                tools.weather_week("Омск")
        self.assertIn("запасной", str(c.exception))


class KeyboardTest(unittest.TestCase):
    def test_page_and_island_wire_keyboard_mode(self):
        for w in ("'keys_set'", "focusin", "focusout", "kbd(false)", "lnkPaste", "link_paste", "link_icons", "putIcon"):
            self.assertIn(w, HTML)
        src = (ROOT / "karina" / "island.py").read_text("utf-8")
        self.assertIn("def set_keys", src)
        self.assertIn("def keys_set", src)
        self.assertIn("(0 if self._kbd else WS_EX_NOACTIVATE)", src)    # сторож не отнимает клавиатуру обратно, пока печатают


class WakeNameTest(unittest.TestCase):
    """Карина «не слышала» имя: порог 94 пропускал только точное «карина». Распознавалка ослышивается в безударных гласных."""
    NAMES = None

    @classmethod
    def setUpClass(cls):
        from karina.config import Config
        cls.NAMES = Config(Path(tempfile.mkdtemp()) / "c.json").wake_names()

    def test_misheard_vowels_still_wake(self):
        from karina.wake import is_name, split_wake
        for w in ("карина", "корина", "каррина", "карино", "каринн", "карена", "кариной"):
            self.assertTrue(is_name(w, self.NAMES), w)
        self.assertEqual(split_wake("Каррина открой блокнот", self.NAMES), (True, "открой блокнот"))
        self.assertEqual(split_wake("громкость пятьдесят Корина", self.NAMES), (True, "громкость пятьдесят"))

    def test_other_names_and_words_still_do_not(self):
        from karina.wake import is_name
        for w in ("марина", "ирина", "кармина", "корзина", "каприна", "кристина", "катрина", "каролина", "корона", "коран", "карен", "карим", "кар", "рина"):
            self.assertFalse(is_name(w, self.NAMES), w)


class NoGarlandTest(unittest.TestCase):
    def test_widget_has_no_garland(self):
        self.assertNotIn("garland", HTML)
        self.assertNotIn("Гирлянда", HTML)


if __name__ == "__main__":
    unittest.main()
