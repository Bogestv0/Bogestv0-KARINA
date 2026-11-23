"""Служба питания: Windows-служба «KarinaPower», которая сообщает кабинету о запуске, выключении, перезагрузке и сне компьютера.

Зачем отдельная служба. Окно Карины работает только после входа в Windows и закрывается вместе с сеансом. Служба стартует вместе с системой
(до входа), живёт, пока включён компьютер, и успевает отправить «выключается» в момент завершения работы (сигнал PRESHUTDOWN, сеть ещё есть).

Что делает
  * при запуске системы: «запустился» (и если прошлый раз компьютер выключили не штатно, то с пометкой и временем последнего сигнала);
  * при выключении или перезагрузке: «выключается» / «перезагружается»;
  * при сне и пробуждении (по умолчанию кабинет об этом молчит, но состояние учитывает);
  * раз в минуту пульс: если пульс пропал, а выключения не было (отключили питание, нет интернета), кабинет сам сообщит «пропал из сети».
Ничего больше служба не делает: микрофон, экран, файлы и пароли ей недоступны, учётная запись LocalService (минимальные права).

Файлы службы лежат в %ProgramData%\\Bogestv0 KARINA: cabinet.json (ключ устройства, его туда кладёт Карина), power_state.json (штатно ли выключили),
queue.json (события, которые не удалось отправить), service.log.

Режимы командной строки (main.py):
  --power-service    запуск самой службы (его вызывает Windows)
  --power-install    установка (нужны права администратора, Карина просит их через UAC)
  --power-remove     удаление
Логика (PowerAgent) не зависит от Windows и проверяется тестами; обвязка службы на ctypes работает только в Windows."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import cabinet as cab

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0
SERVICE = "KarinaPower"
DISPLAY = "Bogestv0 KARINA: питание ПК"
DESCRIPTION = "Сообщает кабинету Bogestv0 о запуске, выключении и сне компьютера. Устанавливается из Карины (вкладка «Часы»)."
BEAT_EVERY = 60.0
UNEXPECTED_GAP_MS = 180_000          # служба молчала дольше 3 минут без «штатного» выключения: это было выключение питания, а не перезапуск службы
SHUTDOWN_SEND_TIMEOUT = 4.0          # на отправку «выключается» даём считаные секунды: система ждать не станет


def shared_dir() -> str:
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Bogestv0 KARINA")


# ---------------------------------------------------------------- перезагрузка или выключение
def parse_shutdown_kind(text: str) -> str:
    """Из записи журнала Windows (событие 1074) достаёт «restart» / «shutdown»; иначе «unknown». Работает и на русской Windows."""
    m = re.search(r"(?:Shutdown Type|Тип завершения работы|Тип выключения)\s*:\s*(.+)", text or "", re.I)
    v = (m.group(1) if m else text or "").lower()
    if "restart" in v or "перезагруз" in v:
        return "restart"
    if "power off" in v or "poweroff" in v or "shutdown" in v or "выключ" in v or "отключ" in v:
        return "shutdown"
    return "unknown"


def shutdown_kind(timeout: float = 3.0) -> str:
    """Что именно началось: выключение или перезагрузка. Смотрим свежую (до 2 минут) запись журнала; не получилось — unknown."""
    if not IS_WIN:
        return "unknown"
    q = "*[System[Provider[@Name='User32'] and (EventID=1074) and TimeCreated[timediff(@SystemTime) <= 120000]]]"
    try:
        r = subprocess.run(["wevtutil.exe", "qe", "System", f"/q:{q}", "/c:1", "/rd:true", "/f:text"], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        out = r.stdout.decode("utf-8", "ignore") or r.stdout.decode("cp866", "ignore")
        return parse_shutdown_kind(out) if r.returncode == 0 and out.strip() else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- логика службы (без Windows)
class PowerAgent:
    """Решает, какие события отправлять. Состояние между запусками лежит в power_state.json:
      "running"  — служба работает (рядом пишется время последнего сигнала),
      "shutdown" — система выключалась штатно,
      "stopped"  — службу остановил человек (удаление, обслуживание)."""

    def __init__(self, data_dir, clock=time.time, kind_fn=shutdown_kind, log=lambda m: None, post=cab.post_events):
        self.dir = str(data_dir)
        self.clock, self.kind_fn, self.log, self.post = clock, kind_fn, log, post
        self.state_path = os.path.join(self.dir, "power_state.json")
        self.cred_path = os.path.join(self.dir, "cabinet.json")
        self.outbox = cab.Outbox(os.path.join(self.dir, "queue.json"))
        self._down = False                      # «выключается» уже отправлено (приходят и PRESHUTDOWN, и SHUTDOWN)
        self._mu = threading.Lock()

    # ---- помощники
    def ms(self) -> int:
        return int(self.clock() * 1000)

    def creds(self):
        return cab.read_creds(self.cred_path)

    def _state(self) -> dict:
        try:
            v = json.loads(open(self.state_path, encoding="utf-8").read())
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: str):
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"state": state, "last_seen": self.ms()}, f)
            os.replace(tmp, self.state_path)
        except OSError as e:
            self.log(f"не удалось записать состояние: {e}")

    def _emit(self, type_: str, timeout: float = 10.0, **fields):
        self.outbox.add(cab.make_event(type_, "service", t=self.ms(), **fields))
        self._flush(timeout)

    def _flush(self, timeout: float = 10.0) -> str:
        res = self.outbox.flush(self.creds(), timeout)
        if res == "revoked":
            self.log("кабинет отвязал этот компьютер: ключ удалён")
            try:
                os.remove(self.cred_path)
            except OSError:
                pass
        return res

    # ---- события
    def on_start(self):
        """Служба запущена. «Запустился» только если до этого система выключалась или питание пропало; перезапуск службы — не запуск ПК."""
        st = self._state()
        prev, last = st.get("state"), int(st.get("last_seen") or 0)
        boot = None
        if prev == "shutdown":
            boot = {"prev_clean": True}
        elif prev == "running" and last and self.ms() - last > UNEXPECTED_GAP_MS:
            boot = {"prev_clean": False, "last_seen": last}
        self._down = False
        self._write_state("running")
        self.log(f"служба запущена (прошлое состояние: {prev or 'нет'}, запуск ПК: {'да' if boot else 'нет'})")
        if boot:
            self._emit("boot", **boot)
        else:
            self._flush()

    def tick(self):
        """Раз в минуту: отметка «жива», очередь и пульс."""
        if self._down:
            return
        self._write_state("running")
        creds = self.creds()
        if self._flush() == "ok" and creds:
            if self.post(creds, [cab.make_event("beat", "service", t=self.ms())]) == "revoked":
                self._flush()

    def on_shutdown(self):
        with self._mu:
            if self._down:
                return
            self._down = True
        kind = self.kind_fn()
        self._write_state("shutdown")
        self.log(f"система выключается ({kind})")
        self._emit("shutdown", timeout=SHUTDOWN_SEND_TIMEOUT, kind=kind)

    def on_stop(self):
        """Службу остановили не из-за выключения компьютера."""
        with self._mu:
            if self._down:
                return
            self._down = True
        self._write_state("stopped")
        self.log("служба остановлена")
        self._emit("service_stop", timeout=SHUTDOWN_SEND_TIMEOUT)

    def on_sleep(self):
        self.log("сон")
        self._write_state("running")
        self._emit("sleep", timeout=SHUTDOWN_SEND_TIMEOUT)

    def on_wake(self):
        self.log("пробуждение")
        self._write_state("running")
        self._emit("wake", timeout=SHUTDOWN_SEND_TIMEOUT)


# ---------------------------------------------------------------- обвязка службы Windows (ctypes)
_LOG_MAX = 200_000


def _logger(path):
    def log(msg: str):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass
    return log


def service_main(data_dir: str | None = None) -> int:
    """Вход службы: вызывается Windows через `--power-service`. Блокируется, пока служба не остановлена."""
    if not IS_WIN:
        print("Служба работает только в Windows")
        return 1
    import ctypes
    from ctypes import wintypes as wt

    data_dir = data_dir or shared_dir()
    log = _logger(os.path.join(data_dir, "service.log"))
    agent = PowerAgent(data_dir, log=log)
    stop = threading.Event()
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class STATUS(ctypes.Structure):
        _fields_ = [("dwServiceType", wt.DWORD), ("dwCurrentState", wt.DWORD), ("dwControlsAccepted", wt.DWORD), ("dwWin32ExitCode", wt.DWORD),
                    ("dwServiceSpecificExitCode", wt.DWORD), ("dwCheckPoint", wt.DWORD), ("dwWaitHint", wt.DWORD)]
    HANDLER = ctypes.WINFUNCTYPE(wt.DWORD, wt.DWORD, wt.DWORD, wt.LPVOID, wt.LPVOID)
    MAIN = ctypes.WINFUNCTYPE(None, wt.DWORD, ctypes.POINTER(wt.LPWSTR))

    class ENTRY(ctypes.Structure):
        _fields_ = [("lpServiceName", wt.LPWSTR), ("lpServiceProc", MAIN)]

    adv.RegisterServiceCtrlHandlerExW.restype = wt.HANDLE
    adv.RegisterServiceCtrlHandlerExW.argtypes = [wt.LPCWSTR, HANDLER, wt.LPVOID]
    adv.SetServiceStatus.argtypes = [wt.HANDLE, ctypes.POINTER(STATUS)]
    adv.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(ENTRY)]

    STOPPED, START_PENDING, STOP_PENDING, RUNNING = 1, 2, 3, 4
    ACCEPT_STOP, ACCEPT_POWER, ACCEPT_PRESHUTDOWN = 0x1, 0x40, 0x100
    C_STOP, C_INTERROGATE, C_SHUTDOWN, C_POWER, C_PRESHUTDOWN = 1, 4, 5, 0x0D, 0x0F
    PBT_SUSPEND, PBT_RESUME_SUSPEND, PBT_RESUME_AUTO = 0x4, 0x7, 0x12
    box = {"h": None, "check": 0}

    def report(state, accepted=0, hint=0):
        s = STATUS(0x10, state, accepted, 0, 0, box["check"] if state in (START_PENDING, STOP_PENDING) else 0, hint)
        if state in (START_PENDING, STOP_PENDING):
            box["check"] += 1
        adv.SetServiceStatus(box["h"], ctypes.byref(s))

    def run_bounded(fn, seconds):
        """Действие в отдельном потоке с потолком по времени: система выключается, ждать долго нельзя."""
        t = threading.Thread(target=lambda: _safe(fn), daemon=True)
        t.start()
        t.join(seconds)

    def _safe(fn):
        try:
            fn()
        except Exception as e:
            log(f"ошибка: {e.__class__.__name__}: {e}")

    def handler(control, event_type, event_data, context):
        try:
            if control in (C_PRESHUTDOWN, C_SHUTDOWN):
                report(STOP_PENDING, 0, 15000)
                run_bounded(agent.on_shutdown, 10)
                stop.set()
                report(STOPPED)
            elif control == C_STOP:
                report(STOP_PENDING, 0, 15000)
                run_bounded(agent.on_stop, 8)
                stop.set()
                report(STOPPED)
            elif control == C_POWER:
                if event_type == PBT_SUSPEND:
                    run_bounded(agent.on_sleep, 3)
                elif event_type in (PBT_RESUME_AUTO, PBT_RESUME_SUSPEND):
                    threading.Thread(target=lambda: _safe(agent.on_wake), daemon=True).start()     # после пробуждения сеть появляется не сразу: не держим систему
        except Exception as e:
            log(f"обработчик: {e.__class__.__name__}: {e}")
        return 0

    handler_c = HANDLER(handler)

    def svc_main(argc, argv):
        box["h"] = adv.RegisterServiceCtrlHandlerExW(SERVICE, handler_c, None)
        if not box["h"]:
            log("не удалось зарегистрировать обработчик службы")
            return
        report(START_PENDING, 0, 10000)
        report(RUNNING, ACCEPT_STOP | ACCEPT_POWER | ACCEPT_PRESHUTDOWN)
        _safe(agent.on_start)
        while not stop.wait(BEAT_EVERY):
            _safe(agent.tick)
        log("служба завершает работу")

    main_c = MAIN(svc_main)
    table = (ENTRY * 2)(ENTRY(SERVICE, main_c), ENTRY(None, ctypes.cast(None, MAIN)))
    log("запуск процесса службы")
    if not adv.StartServiceCtrlDispatcherW(table):
        log(f"StartServiceCtrlDispatcher: ошибка {ctypes.get_last_error()} (служба запускается только Windows)")
        return 1
    return 0


# ---------------------------------------------------------------- установка и удаление
def _sc(*args, timeout: float = 30.0):
    try:
        return subprocess.run(["sc.exe", *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except Exception:
        return None


_STATE_RE = re.compile(rb"\b([1-7])\s+(STOPPED|START_PENDING|STOP_PENDING|RUNNING|CONTINUE_PENDING|PAUSE_PENDING|PAUSED)\b")


def status() -> dict:
    """{"supported", "installed", "running"}."""
    if not IS_WIN:
        return {"supported": False, "installed": False, "running": False}
    r = _sc("query", SERVICE, timeout=10)
    if r is None or r.returncode != 0:
        return {"supported": True, "installed": False, "running": False}
    m = _STATE_RE.search(r.stdout or b"")
    return {"supported": True, "installed": True, "running": bool(m and m.group(2) == b"RUNNING")}


def _launch_command() -> list[str]:
    from . import elevate
    exe, base = elevate._command()
    if not getattr(sys, "frozen", False):                   # из исходников: python.exe, а не pythonw (службе консоль не нужна, но pythonw в службах капризен)
        exe = sys.executable
    return [exe] + base


def _current_sid() -> str:
    try:
        r = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=10, creationflags=NO_WINDOW)
        m = re.search(r"(S-1-[0-9\-]+)", r.stdout.decode("utf-8", "ignore"))
        return m.group(1) if m else ""
    except Exception:
        return ""


def _prepare_dir(path: str, sid: str):
    """Каталог службы: полный доступ у SYSTEM и администраторов, запись у LocalService (журнал, очередь) и у владельца (Карина кладёт ключ)."""
    os.makedirs(path, exist_ok=True)
    grants = ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-19:(OI)(CI)M"] + ([f"*{sid}:(OI)(CI)M"] if sid else [])
    cmd = ["icacls.exe", path, "/inheritance:r"]
    for g in grants:
        cmd += ["/grant:r", g]
    subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW)


def _allow_read(path: str):
    """LocalService должен читать и запускать Карину. Выдаём право чтения только на её каталог (родителям оно не нужно)."""
    subprocess.run(["icacls.exe", path, "/grant", "*S-1-5-19:(OI)(CI)RX"], capture_output=True, timeout=60, creationflags=NO_WINDOW)


def install() -> int:
    """Коды выхода: 0 готово; 2 нет прав администратора; 3 кабинет не подключён; 4 не удалось создать службу; 5 создана, но не запустилась."""
    from . import elevate
    from .config import DATA
    if not IS_WIN:
        return 1
    if not elevate.is_admin():
        return 2
    creds = cab.read_creds(DATA / "config.json")
    if not creds:
        return 3
    d = shared_dir()
    _prepare_dir(d, _current_sid())
    with open(os.path.join(d, "cabinet.json"), "w", encoding="utf-8") as f:
        json.dump(creds, f)
    cmd = _launch_command()
    app_dir = os.path.dirname(cmd[0]) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(cmd[-1]))
    _allow_read(app_dir)
    if not getattr(sys, "frozen", False):
        _allow_read(os.path.dirname(os.path.abspath(sys.executable)))
    if status()["installed"]:
        _sc("stop", SERVICE, timeout=30)
        time.sleep(1.5)
        _sc("delete", SERVICE)
        time.sleep(1.0)
    binpath = subprocess.list2cmdline(cmd + ["--power-service"])
    r = _sc("create", SERVICE, "binPath=", binpath, "start=", "auto", "obj=", r"NT AUTHORITY\LocalService", "DisplayName=", DISPLAY)
    if r is None or r.returncode != 0:
        return 4
    _sc("description", SERVICE, DESCRIPTION)
    _sc("failure", SERVICE, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/60000")
    r = _sc("start", SERVICE)
    time.sleep(1.5)
    return 0 if status()["running"] else 5


def remove() -> int:
    from . import elevate
    if not IS_WIN:
        return 1
    if not elevate.is_admin():
        return 2
    if status()["installed"]:
        _sc("stop", SERVICE, timeout=30)
        time.sleep(2.0)
        r = _sc("delete", SERVICE)
        if r is None or r.returncode not in (0, 1060, 1072):
            return 4
    return 0


def install_main() -> int:
    return install()


def remove_main() -> int:
    return remove()


INSTALL_ERRORS = {
    2: "Нужны права администратора. Подтвердите окно Windows.",
    3: "Сначала подключите кабинет (карточка «Кабинет» выше): службе нужен ключ.",
    4: "Windows не дала создать службу. Подробности в журнале службы.",
    5: "Служба установлена, но не запустилась. Проверьте файл service.log в папке ProgramData\\Bogestv0 KARINA.",
}
