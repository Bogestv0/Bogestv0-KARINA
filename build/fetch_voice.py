"""Кладёт голос Карины (Silero «Байя») в папку voices/ перед упаковкой, чтобы он ехал внутри установщика.
Запуск из корня репозитория: python build/fetch_voice.py   (потом pyinstaller build/karina.spec подхватит voices/ сам).

Без этого шага сборка падает: установщик без голоса заставил бы человека скачивать его в настройках."""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from karina.silero_voice import MODEL_ID, URL, UA

MIN_SIZE = 100 * 1024 * 1024        # модель около 141 МБ; всё, что меньше 100 МБ, — обрыв или страница с ошибкой

dest_dir = ROOT / "voices"
dest_dir.mkdir(exist_ok=True)
dest = dest_dir / f"silero_{MODEL_ID}.pt"
if dest.exists() and dest.stat().st_size >= MIN_SIZE:
    print("голос уже есть:", dest, dest.stat().st_size // 1_000_000, "МБ")
    sys.exit(0)

part = dest.with_suffix(".pt.part")
print("скачиваю", URL)
last = None
for attempt in range(3):
    try:
        req = urllib.request.Request(URL, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if part.stat().st_size >= MIN_SIZE:
            part.replace(dest)
            break
        last = RuntimeError(f"файл слишком маленький: {part.stat().st_size} байт")
    except Exception as e:
        last = e
        print(f"попытка {attempt + 1} не удалась: {e}")
else:
    sys.exit(f"Не удалось скачать голос: {last}")

# проверка, что модель действительно открывается (нужен torch, он в requirements.txt)
import torch
imp = torch.package.PackageImporter(str(dest))
model = imp.load_pickle("tts_models", "model")
print("голос готов:", dest, dest.stat().st_size // 1_000_000, "МБ;", type(model).__name__)
