"""Цифры, знаки и латиница перед озвучкой: голос их пропускал («минус 8 градусов» → «мис градусов», «Браузер: 30 процентов» → «Браузер процентов»)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina.speechnorm import for_speech as f, number_words
from karina import tts


def test_weather_phrase():
    s = f("Кемерова, сейчас минус 8 градусов, солнечно, ветер 2 м/с. Днём от минус 9 градусов до плюс 1 градус.")
    assert "минус восемь градусов" in s and "два метра в секунду" in s and "плюс один градус" in s
    assert not any(ch.isdigit() for ch in s)


def test_mixer_percent():
    assert f("Браузер: 30 процентов.") == "Браузер: тридцать процентов."
    assert f("Браузер тише на 20%.") == "Браузер тише на двадцать процентов."
    assert f("Музыка: 0%") == "Музыка: ноль процентов"


def test_numbers():
    assert number_words(21, "f") == "двадцать одна"
    assert number_words(2000) == "две тысячи"
    assert number_words(1002, "f") == "одна тысяча две"
    assert f("Через 1 минуту") == "Через одну минуту"
    assert f("2,5 градуса") == "две целых пять десятых градуса"
    assert f("5-6 минут") == "пять-шесть минут"


def test_time_and_version():
    assert f("в 14:30") == "в четырнадцать тридцать"
    assert f("Версия 1.22.1") == "Версия один точка двадцать два точка один"


def test_latin():
    assert f("открой Discord") == "открой дискорд"
    assert not any("a" <= c.lower() <= "z" for c in f("Запусти Foobar и GPU"))


def test_prep_text_uses_it():
    assert "восемь" in tts.prep_text("минус 8 градусов", {})
