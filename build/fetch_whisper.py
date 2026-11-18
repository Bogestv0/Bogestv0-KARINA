"""Кладёт модели распознавания речи (faster-whisper: small и tiny) в папку whisper_models/ перед сборкой установщика.
Установщик копирует их в %APPDATA%\\Bogestv0 KARINA\\models, туда же, куда Карина сама их скачивает, поэтому после установки
ничего качать не нужно. Запуск из корня репозитория: python build/fetch_whisper.py"""
import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "whisper_models"
MODELS = ("small", "tiny")      # small — основная («Хорошая»), tiny — быстрая проверка имени

DEST.mkdir(exist_ok=True)
from faster_whisper.utils import download_model
for name in MODELS:
    print("скачиваю модель", name, flush=True)
    download_model(name, cache_dir=str(DEST))

# проверка: модели открываются только из папки, без сети
from faster_whisper import WhisperModel
for name in MODELS:
    WhisperModel(name, device="cpu", compute_type="int8", download_root=str(DEST), local_files_only=True)
    print("модель готова:", name, flush=True)
total = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
print("всего", total // 1_000_000, "МБ")
