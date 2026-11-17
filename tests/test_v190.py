"""1.19.0: погода (повторы, кэш, причина ошибки) и проверка формы окна острова."""
import urllib.error
from unittest import mock

from karina import __version__, island_tools as t, weather


def test_version():
    assert __version__ == "1.19.0"


def _data():
    return {"current": {"temperature_2m": -3.4, "apparent_temperature": -8, "weather_code": 3, "wind_speed_10m": 4.2},
            "daily": {"time": [f"2026-11-{17 + i}" for i in range(7)], "weather_code": [3] * 7, "temperature_2m_max": [1.0] * 7,
                      "temperature_2m_min": [-6.0] * 7, "precipitation_probability_max": [10] * 7}}


def test_get_retries_then_succeeds():
    calls = []

    class Op:
        def open(self, req, timeout=0):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.URLError("boom")
            import io
            return io.BytesIO(b'{"ok": 1}')
    with mock.patch("urllib.request.build_opener", return_value=Op()), mock.patch("time.sleep"):
        assert weather._get("http://x", {}) == {"ok": 1}
    assert len(calls) == 3 and weather.LAST_ERROR == ""


def test_get_error_has_reason():
    class Op:
        def open(self, req, timeout=0):
            raise urllib.error.URLError(TimeoutError("timed out"))
    with mock.patch("urllib.request.build_opener", return_value=Op()), mock.patch("time.sleep"):
        try:
            weather._get("http://x", {})
        except weather.WeatherError as e:
            assert "время ожидания" in str(e)
        else:
            raise AssertionError


def test_week_cache_and_stale():
    t._WCACHE.clear()
    out = {"city": "Омск", "now": {}, "days": []}
    with mock.patch.object(t, "_weather_week", return_value=out) as w:
        assert t.weather_week("Омск") is out and t.weather_week("омск") is out
        assert w.call_count == 1
    key = "омск"
    t._WCACHE[key] = (0.0, out)                                  # кэш устарел, сети нет → прошлый ответ с пометкой
    with mock.patch.object(t, "_weather_week", side_effect=weather.WeatherError("нет")):
        assert t.weather_week("Омск")["stale"] is True
    t._WCACHE.clear()
    with mock.patch.object(t, "_weather_week", side_effect=weather.WeatherError("нет")):
        try:
            t.weather_week("Омск")
        except weather.WeatherError:
            pass
        else:
            raise AssertionError
