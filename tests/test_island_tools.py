"""Инструменты острова: разбор напоминаний, хранилище, настройки, буфер обмена."""
import time
from datetime import datetime
from karina import island_tools as t


def ts(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi).timestamp()


NOW = ts(2026, 11, 17, 8, 50)


def test_reminder_tomorrow_at_nine():
    r = t.parse_reminder("позвонить маме завтра в 9", NOW)
    assert r["what"] == "Позвонить маме" and r["at"] == ts(2026, 11, 18, 9, 0) and r["repeat"] == ""


def test_reminder_in_minutes():
    r = t.parse_reminder("через 20 минут выключить плиту", NOW)
    assert r["what"] == "Выключить плиту" and r["at"] == NOW + 1200


def test_reminder_daily_and_evening():
    r = t.parse_reminder("таблетки каждый день в 23:00", NOW)
    assert r["repeat"] == "daily" and r["at"] == ts(2026, 11, 17, 23, 0) and r["what"] == "Таблетки"
    assert t.parse_reminder("выпить чай в 9 вечера", NOW)["at"] == ts(2026, 11, 17, 21, 0)


def test_reminder_today_time_passed_moves_to_tomorrow():
    assert t.parse_reminder("позвонить в 7:30", NOW)["at"] == ts(2026, 11, 18, 7, 30)


def test_reminder_without_time_or_bad_time():
    assert t.parse_reminder("купить хлеб", NOW) is None
    assert t.parse_reminder("позвонить в 25", NOW) is None
    assert t.parse_reminder("", NOW) is None


def test_store_reminders_timer_note(tmp_path):
    p = tmp_path / "island.json"
    s = t.Store(p)
    s.note_set("привет")
    a = s.reminder_add("разовое", time.time() - 5)
    b = s.reminder_add("ежедневное", time.time() - 5, "daily")
    s.reminder_add("позже", time.time() + 3600)
    fired = {r["what"] for r in s.due_reminders()}
    assert fired == {"разовое", "ежедневное"}
    left = {r["what"] for r in s.reminders()}
    assert left == {"ежедневное", "позже"}                      # разовое удалено, ежедневное перенесено
    assert s.reminder_del(b["id"]) and not s.reminder_del(a["id"])
    s.timer_start(1)
    assert s.timer_due(time.time() + 2) and s.timer_get() is None
    s2 = t.Store(p)                                              # данные переживают перезапуск
    assert s2.note_get() == "привет" and len(s2.reminders()) == 1


def test_clean_settings_rejects_garbage():
    out = t.clean_settings({"character": "evil", "holiday": "x", "garland": "wave", "snow": 0, "city": " Омск "}, {"character": "eyes"})
    assert out["character"] == "eyes" and out["holiday"] == "dates" and out["garland"] == "wave" and out["snow"] is False and out["city"] == "Омск"


def test_clip_history_dedup_and_limits():
    c = t.ClipHistory(limit=3)
    for x in ("a", "b", "a", "   ", "x" * 5000, "c", "d"):
        c.add(x)
    assert c.get() == ["d", "c", "a"]
    c.clear()
    assert c.get() == []


def test_weather_icons_and_when_text():
    assert t._icon(0) == "☀️" and t._icon(75) == "❄️" and t._icon(95) == "⛈️"
    assert t.when_text(ts(2026, 11, 18, 9, 0), NOW) == "завтра в 09:00"
