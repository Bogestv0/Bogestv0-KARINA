"""Заметки к релизу из CHANGELOG.md: python build/release_notes.py v1.22.2 > notes.md
Берётся раздел «## 1.22.2 (…)»; если его нет, печатается короткая строка. Эти заметки Карина показывает в окне «Доступно обновление»."""
import re, sys
from pathlib import Path

tag = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("vV")
text = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text("utf-8")
m = re.search(r"^## " + re.escape(tag) + r"\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
print(m.group(1).strip() if m and tag else f"Bogestv0 KARINA {tag}.")
