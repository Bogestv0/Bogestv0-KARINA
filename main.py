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


def _hook_dotnet():
    """Необработанное исключение .NET (код 0xE0434352) убивает процесс без следа в Python. Пишем его текст в журнал."""
    try:
        trace(f"pywebview {getattr(__import__('webview'), '__version__', '?')}, WebView2 {webview2_version()}")
        import clr  # noqa: F401  (pythonnet)
        from System import AppDomain
        def on_unhandled(sender, e):
            trace("НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ .NET: " + str(e.ExceptionObject).replace("\r", "")[:3000])
        AppDomain.CurrentDomain.UnhandledException += on_unhandled
        try:
            from System.Windows.Forms import Application, UnhandledExceptionMode
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException)
            def on_thread(sender, e):
                trace("ИСКЛЮЧЕНИЕ .NET в потоке окна: " + str(e.Exception).replace("\r", "")[:3000])
            Application.ThreadException += on_thread
        except Exception as e:
            trace(f"WinForms-перехват не поставлен: {e}")
    except Exception as e:
        trace(f"Перехват исключений .NET не поставлен: {e}")


def webview2_version() -> str:
    if sys.platform != "win32":
        return "-"
    try:
        import winreg
        key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + key[8:]), (winreg.HKEY_LOCAL_MACHINE, key), (winreg.HKEY_CURRENT_USER, key)):
            try:
                with winreg.OpenKey(root, sub) as k:
                    return str(winreg.QueryValueEx(k, "pv")[0])
            except OSError:
                continue
    except Exception:
        pass
    return "не найден"


def run_gui(cfg):
    if not webview2_installed():
        trace("WebView2 не найден")
        fatal_box("Для окна Карины нужен Microsoft Edge WebView2, он не установлен.\n\nСкачайте «Evergreen Standalone Installer» на странице\nhttps://developer.microsoft.com/microsoft-edge/webview2/\nи запустите Карину снова.")
        sys.exit(1)
    trace("Загружаю библиотеки окна")
    import webview
    _hook_dotnet()
    from karina.assistant import Assistant
    holder = {}
    from karina.ui_api import Api
    from karina import single, tray as traymod
    api = Api(cfg, lambda: holder.get("w"))
    assistant = Assistant(cfg, api.emit)
    api.bind(assistant)
    # --background (автозапуск с Windows): стартуем сразу в трей, окно не показываем
    start_hidden = "--background" in sys.argv and bool(cfg["ui"].get("background", True))
    # размер окна — по рабочей области монитора и масштабу Windows; своя рамка (заголовок рисует интерфейс)
    from karina import winframe
    frameless = sys.platform == "win32" and bool(cfg["ui"].get("frameless", True))
    ww, wh = winframe.initial_logical_size()
    kw = dict(url=str(ROOT / "ui" / "index.html"), js_api=api, width=ww, height=wh,
              min_size=winframe.min_logical_size(), background_color="#131318", hidden=start_hidden)
    if frameless:
        kw.update(frameless=True, easy_drag=False, shadow=True)
    try:
        holder["w"] = webview.create_window("Bogestv0 KARINA", **kw)
    except TypeError as e:                      # старая pywebview без части параметров
        trace(f"create_window: {e}; повтор без shadow")
        kw.pop("shadow", None)
        holder["w"] = webview.create_window("Bogestv0 KARINA", **kw)
    if frameless:
        api.frame = winframe.FrameControl(lambda: holder.get("w"), lambda m: api.emit({"type": "winstate", "max": m}), trace)
        holder["w"].events.shown += lambda: api.frame.setup()
        holder["w"].events.loaded += lambda: api.frame.fit() if not start_hidden else None
    try:
        holder["w"].events.loaded += lambda: trace("Окно: страница загружена")
        holder["w"].events.closed += lambda: trace("Окно: закрыто")
        holder["w"].events.shown += lambda: trace("Окно: показано")
        holder["w"].events.closing += lambda: trace("Окно: получена команда закрыть")
    except Exception as e:
        trace(f"Окно: не удалось подписаться на события ({e})")
    # ---- фоновый режим: закрытие окна прячет его в трей, а не выключает Карину ----
    state = {"tray": None, "hinted": False}

    def show_window():
        try:
            holder["w"].show()
            holder["w"].restore()
            if api.frame:
                api.frame.bring_to_front()
        except Exception as e:
            trace(f"Окно: не удалось показать ({e})")

    def quit_app():
        trace("Выход из трея")
        api.quitting = True
        if state["tray"]:
            state["tray"].stop()
        try:
            holder["w"].destroy()
        except Exception as e:
            trace(f"Окно: destroy не удался ({e})")

    def ensure_tray():
        tr = state["tray"]
        if tr is None or not tr.active:
            tr = traymod.Tray("Bogestv0 KARINA", ROOT / "ui" / "logo.png", show_window, quit_app, trace)
            state["tray"] = tr if tr.start() else None
        return state["tray"]

    def on_closing():
        """False — отменить закрытие и спрятать окно в трей. Без трея или при «выходе» закрываем по-настоящему."""
        if api.quitting or not cfg["ui"].get("background", True):
            return None
        tr = ensure_tray()
        if tr is None:
            return None
        # прятать нужно уже после возврата из обработчика: окно ждёт его ответа, и hide() изнутри повесил бы его
        threading.Timer(0.05, lambda: holder["w"].hide()).start()
        trace("Окно спрятано в трей")
        if not state["hinted"]:
            state["hinted"] = True
            tr.notify("Карина работает в фоне. Двойной щелчок по значку — открыть окно, «Выход» — выключить.")
        return False

    try:
        holder["w"].events.closing += on_closing
    except Exception as e:
        trace(f"Окно: фоновый режим недоступен ({e})")
    try:
        from Microsoft.Win32 import SystemEvents       # выключение/выход из Windows не должны упираться в «спрятанное» окно
        SystemEvents.SessionEnding += lambda s, e: setattr(api, "quitting", True)
    except Exception as e:
        trace(f"SessionEnding не подписан: {e}")
    try:
        faulthandler.dump_traceback_later(25, file=_log)      # если за 25 с запуск не завершён — в журнал стеки всех потоков
    except Exception:
        pass
    def heartbeat():
        import time
        for i in range(1, 16):
            time.sleep(2)
            trace(f"жив ({i * 2} с), потоков: {threading.active_count()}")
    threading.Thread(target=heartbeat, daemon=True, name="heartbeat").start()
    trace("Окно создано, запускаю")
    trace("webview.start: вход")
    # icon не передаём на Windows: окно принимает только .ico, а PNG даёт System.ArgumentException в Icon.Initialize
    # и роняет весь процесс (код 0xE0434352). Значок окна и так берётся из exe (build/karina.spec: icon=logo.ico).
    icon = None if sys.platform == "win32" else (str(ROOT / "ui" / "logo.png") if (ROOT / "ui" / "logo.png").exists() else None)
    def after_start():
        trace("Окно открыто, запускаю ассистента")
        assistant.start()
        trace("Ассистент запущен")
        single.listen_for_show(show_window)         # повторный запуск Карины просто открывает это окно (в т.ч. из трея и свёрнутое)
        if start_hidden and ensure_tray() is None:
            show_window()                           # трея нет — прятать окно некуда, показываем

    webview.start(after_start, icon=icon)
    trace("Окно закрыто, завершение")
    if state["tray"]:
        state["tray"].stop()
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
              "pyautogui", "pyperclip", "PIL", "webview", "comtypes", "pycaw.pycaw", "docx", "openpyxl", "reportlab", "lxml"):
        step["now"] = "импорт " + m
        logf.write(step["now"] + "\n")
        try:
            importlib.import_module(m)
        except BaseException:
            bad.append(m + ": " + traceback.format_exc().strip().splitlines()[-1])
            logf.write("  ОШИБКА: " + bad[-1] + "\n")
    for m in ("pystray", "piper"):          # необязательные: без них нет трея / офлайн-голоса, но Карина работает
        step["now"] = "импорт (необязательный) " + m
        logf.write(step["now"] + "\n")
        try:
            importlib.import_module(m)
        except BaseException:
            logf.write("  ПРЕДУПРЕЖДЕНИЕ: " + traceback.format_exc().strip().splitlines()[-1] + "\n")
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
    step["now"] = "файлы (Word, Excel, PDF, SVG)"
    try:
        import tempfile, zipfile
        from karina.files import FileService
        tmp = Path(tempfile.mkdtemp(prefix="karina-selftest-"))
        svc = FileService(tmp / "Карина")
        md = "# Проверка\n\nАбзац с **жирным** и `кодом`.\n\n- пункт\n\n| а | б |\n|---|---|\n| 1 | 2 |\n"
        for name, body in (("t.docx", md), ("t.xlsx", "а;б\n1;2\n"), ("t.pdf", md), ("t.svg", '<svg viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'), ("t.txt", "привет")):
            r = svc.create(name, body)
            logf.write(f"файл {r['name']}: {r['size']} байт\n")
            if r["size"] < 100 and not name.endswith((".svg", ".txt")):
                bad.append("файл слишком маленький: " + name)
        for name in ("t.docx", "t.xlsx"):
            with zipfile.ZipFile(tmp / "Карина" / name) as z:
                if z.testzip() is not None:
                    bad.append("повреждён " + name)
        if not (tmp / "Карина" / "t.pdf").read_bytes().startswith(b"%PDF"):
            bad.append("t.pdf не PDF")
    except BaseException:
        bad.append("файлы: " + traceback.format_exc().strip().splitlines()[-1])
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
        if "--install-admin-task" in sys.argv:              # служебный запуск с UAC из настроек
            from karina import elevate
            sys.exit(0 if elevate.install_task() else 1)
        if "--remove-admin-task" in sys.argv:
            from karina import elevate
            sys.exit(0 if elevate.remove_task() else 1)
        c = None
        if "--console" not in sys.argv:
            from karina import single, elevate
            elevate.merge_saved_args(sys.argv)              # аргументы исходного запуска (--background), если нас открыла задача планировщика
            c = Config()
            if sys.platform == "win32" and c["ui"].get("admin") and not elevate.is_admin() and "--elevated" not in sys.argv:
                # «Запускать от имени администратора» включено: открываем себя с правами и закрываемся
                if elevate.relaunch_as_admin(sys.argv[1:], trace):
                    sys.exit(0)
                trace("Права администратора не получены — работаю с обычными")
            if not single.acquire(wait=20 if "--restart" in sys.argv else 6):   # если Карина уже есть — acquire просит её показать окно; ждём: после обновления/перезапуска прежний процесс ещё доживает
                trace("Карина уже запущена, вторую копию не открываю")
                sys.exit(0)
            single.prepare()
        c = c or Config()
        run_console(c) if "--console" in sys.argv else run_gui(c)
    except SystemExit:
        raise
    except BaseException as e:
        _crash(e)
