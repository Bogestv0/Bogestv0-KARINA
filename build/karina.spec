# PyInstaller: сборка папки dist/Karina с Bogestv0-KARINA.exe (запуск из корня репозитория: pyinstaller build/karina.spec)
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
datas, binaries, hidden = [], [], ["pystray._win32", "pycaw.pycaw", "comtypes.client", "pyautogui", "pyperclip"]
for pkg in ("numpy", "faster_whisper", "ctranslate2", "onnxruntime", "av", "webview", "sounddevice", "pycaw", "comtypes", "tokenizers", "huggingface_hub", "docx", "openpyxl", "reportlab", "lxml", "et_xmlfile", "pystray", "winrt", "zxingcpp", "segno", "imageio_ffmpeg"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
# torch лежит ВНЕ exe (докачиваемый архив компонентов) и тянет модули стандартной библиотеки (graphlib, unittest, pdb, ...),
# которые PyInstaller не вложил бы сам: внутри exe их никто не импортирует. Вкладываем всю стандартную библиотеку целиком (несколько МБ).
_skip = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "antigravity", "this", "__main__", "__phello__", "ensurepip", "venv", "pydoc_data"}
hidden += sorted(m for m in sys.stdlib_module_names if not m.startswith("_") and m not in _skip)
datas += [(str(ROOT / "ui"), "ui"), (str(ROOT / "plugins"), "plugins")]
# torch, голос Карины (Silero) и модели распознавания в установщик НЕ входят (он весит 200–300 МБ): их одним архивом докачивает сама Карина
# при первом запуске (karina/components.py, архив собирает build/make_pack.py)
if (ROOT / "engine").exists():          # движок llama.cpp кладёт в сборку build/fetch_engine.py
    datas += [(str(ROOT / "engine"), "engine")]

a = Analysis([str(ROOT / "main.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "pandas", "IPython", "pytest", "torch", "torchvision", "torchaudio", "torchgen", "functorch", "sympy", "networkx", "mpmath"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Bogestv0-KARINA", console=False,
          icon=str(ROOT / "ui" / "logo.ico"), version=str(ROOT / "build" / "version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Karina")
