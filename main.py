"""Bogestv0 KARINA — запуск. `python main.py` — окно; `python main.py --console` — без окна (для отладки)."""
import sys, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from karina.config import Config, DATA

# В собранном exe без консоли stdout/stderr отсутствуют: пишем их в журнал, чтобы ошибки не пропадали.
if sys.stdout is None or sys.stderr is None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        _log = open(DATA / "karina.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or _log
        sys.stderr = sys.stderr or _log
    except OSError:
        pass

from karina.assistant import Assistant


def run_console(cfg):
    def emit(ev):
        if ev["type"] == "log":
            print(f"[{ev['ts']}] {ev['kind']:8} {ev['text']}")
        elif ev["type"] == "state" and ev.get("detail"):
            print("…", ev["detail"])
    a = Assistant(cfg, emit)
    a.start()
    print(f"Скажите: «{cfg['name']}, открой блокнот». Или печатайте команды здесь. Ctrl+C — выход.")
    try:
        for line in sys.stdin:
            if line.strip():
                a.handle(line.strip(), direct=True)
    except KeyboardInterrupt:
        pass
    a.stop()


def run_gui(cfg):
    import webview
    holder = {}
    from karina.ui_api import Api
    api = Api(cfg, lambda: holder.get("w"))
    assistant = Assistant(cfg, api.emit)
    api.bind(assistant)
    holder["w"] = webview.create_window(
        f"Bogestv0 KARINA", url=str(ROOT / "ui" / "index.html"), js_api=api,
        width=1080, height=760, min_size=(760, 560), background_color="#131318")
    webview.start(lambda: assistant.start(), icon=str(ROOT / "ui" / "logo.png") if (ROOT / "ui" / "logo.png").exists() else None)
    assistant.stop()


def selftest() -> int:
    """Проверка собранного exe в CI: все библиотеки на месте, ресурсы найдены. Результат — в код выхода и в selftest.log."""
    import importlib, traceback
    from karina.config import DATA, ROOT
    bad = []
    for m in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "sounddevice", "numpy", "rapidfuzz", "edge_tts",
              "pyautogui", "pyperclip", "PIL", "webview", "comtypes", "pycaw.pycaw"):
        try:
            importlib.import_module(m)
        except Exception:
            bad.append(m + ": " + traceback.format_exc().strip().splitlines()[-1])
    for rel in ("ui/index.html", "ui/app.js", "ui/app.css", "ui/fonts.css", "ui/logo.png"):
        if not (ROOT / rel).exists():
            bad.append("нет ресурса " + rel)
    try:
        import faster_whisper, os
        if not (Path(faster_whisper.__file__).parent / "assets").exists():
            bad.append("нет faster_whisper/assets")
    except Exception:
        pass
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "selftest.log").write_text("OK\n" if not bad else "\n".join(bad), "utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    c = Config()
    run_console(c) if "--console" in sys.argv else run_gui(c)
