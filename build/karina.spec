# PyInstaller: сборка папки dist/Karina с Bogestv0-KARINA.exe (запуск из корня репозитория: pyinstaller build/karina.spec)
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
datas, binaries, hidden = [], [], ["pystray._win32", "pycaw.pycaw", "comtypes.client", "pyautogui", "pyperclip"]
for pkg in ("numpy", "faster_whisper", "ctranslate2", "onnxruntime", "av", "webview", "sounddevice", "pycaw", "comtypes", "tokenizers", "huggingface_hub", "docx", "openpyxl", "reportlab", "lxml", "et_xmlfile", "pystray", "torch", "winrt", "zxingcpp", "segno", "imageio_ffmpeg"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
datas += [(str(ROOT / "ui"), "ui"), (str(ROOT / "plugins"), "plugins")]
# голос Карины (Silero) кладёт в сборку build/fetch_voice.py: он обязан быть в установщике, чтобы ничего не нужно было скачивать после установки
if not any((ROOT / "voices").glob("silero_*.pt")):
    raise SystemExit("Нет voices/silero_*.pt: сначала запустите python build/fetch_voice.py")
datas += [(str(ROOT / "voices"), "voices")]
if (ROOT / "engine").exists():          # движок llama.cpp кладёт в сборку build/fetch_engine.py
    datas += [(str(ROOT / "engine"), "engine")]

a = Analysis([str(ROOT / "main.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "pandas", "IPython", "pytest", "torchvision", "torchaudio", "torch.utils.tensorboard"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Bogestv0-KARINA", console=False,
          icon=str(ROOT / "ui" / "logo.ico"), version=str(ROOT / "build" / "version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Karina")
