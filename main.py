"""Bogestv0 KARINA — запуск. `python main.py` — окно; `python main.py --console` — без окна (для отладки)."""
import sys, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from karina.config import Config, DATA

# В собранном exe без консоли stdout/stderr отсутствуют: пишем их в журнал, чтобы ошибки не пропадали.
_log = None
try:
    DATA.mkdir(parents=True, exist_ok=True)
    _log = open(DATA / "karina.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stdout or _log
        sys.stderr = sys.stderr or _log
except OSError:
    pass


def trace(msg: str):
    """Строка в karina.log: по ней видно, на каком этапе остановилась программа."""
    if _log:
        try:
            import time
            _log.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
        except (OSError, ValueError):
            pass


def fatal_box(text: str):
    """Окно с ошибкой (в exe без консоли иначе программа исчезает молча)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "Bogestv0 KARINA", 0x10)
        except Exception:
            pass


def webview2_installed() -> bool:
    """Окно Карины рисует Microsoft Edge WebView2. Без него процесс закрывается без единого слова."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + key[8:]), (winreg.HKEY_LOCAL_MACHINE, key), (winreg.HKEY_CURRENT_USER, key)):
            try:
                with winreg.OpenKey(root, sub) as k:
                    if winreg.QueryValueEx(k, "pv")[0] not in ("", "0.0.0.0"):
                        return True
            except OSError:
                continue
        return False
    except Exception:
        return True          # не смогли проверить — не мешаем запуску


try:
    import faulthandler
    if _log:
        faulthandler.enable(file=_log, all_threads=True)      # аварийное падение (не Python-ошибка) оставит след в журнале
except Exception:
    pass

def _install_hooks():
    """Необработанные ошибки (в том числе в потоках) — в журнал, а не в пустоту."""
    import threading, traceback
    def eh(t, v, tb):
        trace("НЕОБРАБОТАННАЯ ОШИБКА: " + "".join(traceback.format_exception(t, v, tb)).strip().splitlines()[-1])
    def th(a):
        trace(f"ОШИБКА В ПОТОКЕ {a.thread.name if a.thread else '?'}: {a.exc_type.__name__}: {a.exc_value}")
    sys.excepthook, threading.excepthook = eh, th
    import atexit
    atexit.register(lambda: trace("Процесс завершается штатно"))


_install_hooks()
from karina import __version__
trace(f"Запуск Карины {__version__} (Python {sys.version.split()[0]}, {'exe' if getattr(sys, 'frozen', False) else 'исходники'}, аргументы: {sys.argv[1:]})")


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
    if not webview2_installed():
        trace("WebView2 не найден")
        fatal_box("Для окна Карины нужен Microsoft Edge WebView2, он не установлен.\n\nСкачайте «Evergreen Standalone Installer» на странице\nhttps://developer.microsoft.com/microsoft-edge/webview2/\nи запустите Карину снова.")
        sys.exit(1)
    trace("Загружаю библиотеки окна")
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
    trace("Окно создано, запускаю")
    webview.start(lambda: (trace("Окно открыто, запускаю ассистента"), assistant.start(), trace("Ассистент запущен")), icon=str(ROOT / "ui" / "logo.png") if (ROOT / "ui" / "logo.png").exists() else None)
    trace("Окно закрыто, завершение")
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
    step["now"] = "движок моделей"
    try:
        import subprocess
        from karina.engine import find_server
        exe = find_server("cpu")
        if not exe:
            bad.append("нет движка llama-server (cpu)")
        else:
            r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=40, cwd=str(exe.parent))
            logf.write(f"llama-server --version -> код {r.returncode}\n{(r.stdout + r.stderr).strip()[-300:]}\n")
            if r.returncode != 0:
                bad.append("llama-server (cpu) не запускается")
        if not find_server("vulkan"):
            bad.append("нет движка llama-server (vulkan)")
    except BaseException:
        bad.append("движок: " + traceback.format_exc().strip().splitlines()[-1])
    timer.cancel()
    logf.write("ИТОГ: " + ("OK" if not bad else "ОШИБКИ\n" + "\n".join(bad)) + "\n")
    logf.close()
    return 1 if bad else 0


def _crash(exc: BaseException):
    """Ошибка при старте: в журнал и выход. Без этого собранный exe показывает окно с ошибкой и ждёт нажатия."""
    import traceback
    tb = traceback.format_exc()
    trace("ОШИБКА: " + tb.strip().splitlines()[-1])
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(DATA / "crash.log", "a", encoding="utf-8") as f:
            f.write(tb + "\n")
    except OSError:
        pass
    if "--selftest" not in sys.argv:
        fatal_box(f"Карина не запустилась:\n{tb.strip().splitlines()[-1]}\n\nПодробности: {DATA / 'crash.log'}")
    sys.exit(1)


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            sys.exit(selftest())
        if len(sys.argv) > 1 and sys.argv[1] == "--stt-probe":      # дочерний процесс: проверка загрузки модели распознавания
            from karina.stt import probe_main
            sys.exit(probe_main(sys.argv[2:]))
        c = Config()
        run_console(c) if "--console" in sys.argv else run_gui(c)
    except SystemExit:
        raise
    except BaseException as e:
        _crash(e)
