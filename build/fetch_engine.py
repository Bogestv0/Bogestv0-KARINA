"""Скачивает сборки llama.cpp (Vulkan и CPU) в папку engine/ перед упаковкой. Запуск из корня: python build/fetch_engine.py"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from karina.engine import fetch_engine

dest = ROOT / "engine"
dest.mkdir(exist_ok=True)
for flavor in ("cpu", "vulkan"):
    exe = fetch_engine(flavor, dest)
    print(flavor, "->", exe, "|", (dest / flavor / "VERSION.txt").read_text("utf-8").splitlines()[0])
