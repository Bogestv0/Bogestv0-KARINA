"""Собирает архив компонентов dist/Bogestv0-KARINA-Components-<версия>.zip: torch (CPU) с зависимостями, голос Silero и модели распознавания.
В установщик они не входят (чтобы он весил 200–300 МБ): Карина докачивает этот архив из релиза при первом запуске (karina/components.py).
Перед запуском нужны voices/silero_*.pt (build/fetch_voice.py) и whisper_models/ (build/fetch_whisper.py). Запуск из корня: python build/make_pack.py"""
import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from karina import __version__
from karina.components import COMPONENTS_VERSION, pack_name

work = ROOT / "build" / "pack"
shutil.rmtree(work, ignore_errors=True)
py = work / "pylibs"
py.mkdir(parents=True)

voices = sorted((ROOT / "voices").glob("silero_*.pt"))
models = [d for d in (ROOT / "whisper_models").glob("models--*") if d.is_dir()]
if not voices:
    sys.exit("Нет voices/silero_*.pt: сначала python build/fetch_voice.py")
if not models:
    sys.exit("Нет whisper_models/models--*: сначала python build/fetch_whisper.py")

print("ставлю torch и зависимости в", py, flush=True)
subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(py), "--no-warn-script-location", "--only-binary=:all:",
                "--extra-index-url", "https://download.pytorch.org/whl/cpu", "torch>=2.1"], check=True)

# numpy уже есть внутри exe: вторая копия рядом с torch только мешала бы
for p in list(py.glob("numpy")) + list(py.glob("numpy.libs")) + list(py.glob("numpy-*")):
    shutil.rmtree(p, ignore_errors=True)
shutil.rmtree(py / "bin", ignore_errors=True)
# ненужное для работы: заголовки и библиотеки импорта для сборки расширений, тесты
shutil.rmtree(py / "torch" / "include", ignore_errors=True)
shutil.rmtree(py / "torch" / "test", ignore_errors=True)
for f in (py / "torch" / "lib").glob("*.lib"):
    f.unlink()
for d in py.rglob("__pycache__"):
    shutil.rmtree(d, ignore_errors=True)
if not (py / "torch" / "__init__.py").exists():
    sys.exit("torch не установился")

dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
out = dist / pack_name()
out.unlink(missing_ok=True)
print("пакую", out, flush=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
    for f in py.rglob("*"):
        if f.is_file():
            z.write(f, Path("pylibs") / f.relative_to(py))
    for v in voices:
        z.write(v, Path("voices") / v.name)
    for m in models:
        for f in m.rglob("*"):
            if f.is_file():
                z.write(f, Path("models") / m.name / f.relative_to(m))
size = out.stat().st_size
print(f"готово: {out.name}, {size // 1_000_000} МБ (состав {COMPONENTS_VERSION}, версия {__version__})")
if size > 1_900_000_000:
    sys.exit("архив больше 1,9 ГБ: GitHub не примет файл больше 2 ГБ в релиз")
