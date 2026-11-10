"""Скачивает сборки llama.cpp (Vulkan и CPU) в папку engine/ перед упаковкой. Запуск из корня: python build/fetch_engine.py"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from karina.engine import fetch_engine, find_release

dest = ROOT / "engine"
dest.mkdir(exist_ok=True)
rel = find_release(("cpu", "vulkan"))          # обе сборки берём из одного релиза
print("llama.cpp", rel.get("tag_name"))
for flavor in ("cpu", "vulkan"):
    exe = fetch_engine(flavor, dest, rel=rel)
    print(flavor, "->", exe, "|", (dest / flavor / "VERSION.txt").read_text("utf-8").splitlines()[0])
