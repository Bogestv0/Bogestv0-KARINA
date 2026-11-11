"""Права администратора для Карины.

Windows не умеет «повысить» уже запущенную программу, поэтому делаем так:
  * один раз (с окном UAC) создаём задачу планировщика «Bogestv0 KARINA» с наивысшими правами;
  * при каждом запуске, если в настройках включено «Запускать от имени администратора», обычный запуск
    просит эту задачу открыть Карину с правами администратора — без вопросов UAC — и сам закрывается;
  * если задачи нет или планировщик недоступен, запасной путь — обычное окно UAC при каждом запуске.
Выключили галочку — Карина запускается как обычно, задача удаляется. Только Windows."""
from __future__ import annotations
import json, os, subprocess, sys, tempfile

IS_WIN = sys.platform == "win32"
TASK = "Bogestv0 KARINA"
FROZEN = bool(getattr(sys, "frozen", False))
_NO_WINDOW = 0x08000000


def is_admin() -> bool:
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _command() -> tuple[str, list[str]]:
    """Что запускать: собранный exe или python + main.py (запуск из исходников)."""
    if FROZEN:
        return sys.executable, []
    from .config import ROOT
    exe = sys.executable
    pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pw):
        exe = pw
    return exe, [str(ROOT / "main.py")]


def _args_file():
    from .config import DATA
    return DATA / "launch_args.json"


# ---------- задача планировщика ----------
def task_exists() -> bool:
    if not IS_WIN:
        return False
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, creationflags=_NO_WINDOW, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _task_xml() -> str:
    from xml.sax.saxutils import escape
    exe, args = _command()
    arguments = subprocess.list2cmdline(args + ["--elevated"])
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Bogestv0 KARINA с правами администратора (без запроса UAC при каждом запуске)</Description></RegistrationInfo>
  <Triggers/>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(exe)}</Command><Arguments>{escape(arguments)}</Arguments></Exec></Actions>
</Task>
'''


def install_task() -> bool:
    """Создать задачу. Вызывать из процесса с правами администратора."""
    if not IS_WIN:
        return False
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(_task_xml())
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", path, "/F"], capture_output=True, creationflags=_NO_WINDOW, timeout=30)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def remove_task() -> bool:
    if not IS_WIN:
        return True
    try:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, creationflags=_NO_WINDOW, timeout=30)
        return r.returncode == 0 or not task_exists()
    except Exception:
        return False


# ---------- запуск с повышением ----------
def run_elevated(args: list[str], wait: bool = False):
    """Запустить Карину (или её служебный режим) через окно UAC. Возвращает код выхода, True (запущено, не ждали)
    или None, если пользователь отказал в UAC / запуск не удался."""
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class SEI(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                        ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                        ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                        ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
        exe, base = _command()
        sei = SEI()
        sei.cbSize = ctypes.sizeof(SEI)
        sei.fMask = 0x00000040                      # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = exe
        sei.lpParameters = subprocess.list2cmdline(base + list(args))
        sei.lpDirectory = os.path.dirname(exe)
        sei.nShow = 0 if wait else 1
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            return None                              # 1223 — отказ в UAC
        if not wait or not sei.hProcess:
            return True
        k = ctypes.windll.kernel32
        k.WaitForSingleObject(sei.hProcess, 60000)
        code = wintypes.DWORD(1)
        k.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        k.CloseHandle(sei.hProcess)
        return int(code.value)
    except Exception:
        return None


def relaunch_as_admin(extra_args: list[str] | None = None, trace=lambda s: None) -> bool:
    """Открыть Карину с правами администратора. True — запрос принят и текущий процесс можно закрывать."""
    extra = [a for a in (extra_args or []) if a in ("--background", "--restart")]
    if task_exists():
        try:
            if extra:
                _args_file().parent.mkdir(parents=True, exist_ok=True)
                _args_file().write_text(json.dumps(extra), encoding="utf-8")
            r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, creationflags=_NO_WINDOW, timeout=20)
            if r.returncode == 0:
                trace("Права администратора: запущено через задачу планировщика")
                return True
            trace(f"Права администратора: задача не запустилась (код {r.returncode})")
        except Exception as e:
            trace(f"Права администратора: ошибка задачи ({e})")
    ok = run_elevated(list(extra) + ["--elevated"])
    trace("Права администратора: " + ("запущено через UAC" if ok else "пользователь отказал в UAC"))
    return bool(ok)


def merge_saved_args(argv: list[str]) -> None:
    """В процессе, запущенном задачей: вернуть аргументы исходного запуска (например, --background из автозапуска)."""
    if "--elevated" not in argv:
        return
    try:
        p = _args_file()
        if p.exists():
            for a in json.loads(p.read_text(encoding="utf-8")):
                if a not in argv:
                    argv.append(a)
            p.unlink()
    except Exception:
        pass


# ---------- включение/выключение из настроек ----------
def enable() -> dict:
    """Создать задачу (при необходимости с окном UAC). {ok, error}"""
    if is_admin():
        return {"ok": install_task(), "error": "" if task_exists() else "Не удалось создать задачу планировщика."}
    code = run_elevated(["--install-admin-task"], wait=True)
    if code is None:
        return {"ok": False, "error": "Вы отказались в окне Windows — права не выданы."}
    if task_exists():
        return {"ok": True}
    return {"ok": False, "error": "Не удалось создать задачу планировщика Windows."}


def disable() -> dict:
    if not task_exists():
        return {"ok": True}
    if is_admin():
        return {"ok": remove_task()}
    code = run_elevated(["--remove-admin-task"], wait=True)
    return {"ok": not task_exists(), "pending": code is None}
