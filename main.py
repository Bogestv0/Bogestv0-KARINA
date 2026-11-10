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



def run_console(cfg):
    from karina.assistant import Assistant
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
    from karina.assistant import Assistant
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
    """Проверка собранного exe в CI. Пишет ход проверки в selftest.log; сторожевой таймер не даёт зависнуть."""
    import importlib, os, threading, traceback
    from karina.config import DATA, ROOT
    DATA.mkdir(parents=True, exist_ok=True)
    logf = open(DATA / "selftest.log", "w", encoding="utf-8", buffering=1)
    step = {"now": "старт"}

    def watchdog():
        logf.write(f"ТАЙМАУТ 90 с на шаге: {step['now']}\n")
        logf.flush()
        os._exit(2)

    timer = threading.Timer(90, watchdog)
    timer.daemon = True
    timer.start()
    bad = []
    for m in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "sounddevice", "numpy", "rapidfuzz", "edge_tts",
              "pyautogui", "pyperclip", "PIL", "webview", "comtypes", "pycaw.pycaw"):
        step["now"] = "импорт " + m
        logf.write(step["now"] + "\n")
        try:
            importlib.import_module(m)
        except BaseException:
            bad.append(m + ": " + traceback.format_exc().strip().splitlines()[-1])
            logf.write("  ОШИБКА: " + bad[-1] + "\n")
    step["now"] = "проверка ресурсов"
    for rel in ("ui/index.html", "ui/app.js", "ui/app.css", "ui/fonts.css", "ui/logo.png"):
        if not (ROOT / rel).exists():
            bad.append("нет ресурса " + rel)
    try:
        import faster_whisper
        if not (Path(faster_whisper.__file__).parent / "assets").exists():
            bad.append("нет faster_whisper/assets")
    except Exception:
        pass
    timer.cancel()
    logf.write("ИТОГ: " + ("OK" if not bad else "ОШИБКИ\n" + "\n".join(bad)) + "\n")
    logf.close()
    return 1 if bad else 0


def _crash(exc: BaseException):
    """Ошибка при старте: в журнал и выход. Без этого собранный exe показывает окно с ошибкой и ждёт нажатия."""
    import traceback
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(DATA / "crash.log", "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except OSError:
        pass
    sys.exit(1)


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            sys.exit(selftest())
        c = Config()
        run_console(c) if "--console" in sys.argv else run_gui(c)
    except SystemExit:
        raise
    except BaseException as e:
        _crash(e)
