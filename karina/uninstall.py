"""Удаление Карины по частям: «Настройки → Удаление». Можно убрать только скачанное (модели, голос, движки) или стереть всё до последнего файла.

Что можно выбрать (catalog): модели чата, распознавание речи, модели картинок, голос, компоненты (torch), движки llama.cpp и VPN, диалоги и вложения,
чему Карина научилась, остров и заметки, настройки (ключи, профиль, ссылки VPN), журналы, автозапуск и задача планировщика, сама программа.

Как удаляется. Часть папок свободна и стирается сразу (модели чата, движки, голос, автозапуск). Остальное Карина держит открытым, пока работает
(настройки, диалоги, torch, модели распознавания): такие папки стирает вторая копия Карины с ключом `--cleanup <файл-план>` уже после выхода
основной (cleanup_main). Она же в конце запускает деинсталлятор программы, если выбрано «Сама программа». Всё, что не удалось стереть, остаётся в отчёте."""
from __future__ import annotations
import json, os, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

IS_WIN = sys.platform == "win32"
FROZEN = bool(getattr(sys, "frozen", False))
NO_WINDOW = 0x08000000 if IS_WIN else 0
DETACHED = (0x00000008 | 0x00000200) if IS_WIN else 0
LNK = "Bogestv0 KARINA.lnk"

GROUPS = {"downloads": "Скачанное", "data": "Ваши данные", "system": "Система"}


# ====================================================================== что есть
def _models_other(base: Path) -> list[Path]:
    m = base / "models"
    if not m.is_dir():
        return []
    return [p for p in m.iterdir() if p.name not in ("llm", "image", "tagger")]


def _desktops() -> list[Path]:
    home = Path.home()
    out = [home / "Desktop", home / "OneDrive" / "Desktop", home / "OneDrive" / "Рабочий стол", home / "Рабочий стол"]
    if os.environ.get("PUBLIC"):
        out.append(Path(os.environ["PUBLIC"]) / "Desktop")
    return out


def unins_exe() -> Path | None:
    """Деинсталлятор программы (его кладёт установщик рядом с exe). Из исходников его нет."""
    if not FROZEN:
        return None
    for p in sorted(Path(sys.executable).parent.glob("unins*.exe")):
        return p
    return None


def catalog(base: Path) -> list[dict]:
    """[{id, group, title, desc, paths, now, kind}] — только то, что есть на диске. now — можно стереть, пока Карина работает."""
    b = Path(base)
    P = lambda *n: [b / x for x in n]
    items = [
        ("llm", "downloads", "Модели чата", "Скачанные нейросети для разговора (файлы .gguf).", P("models/llm"), True),
        ("stt", "downloads", "Распознавание речи", "Модели Whisper (small, tiny): по ним Карина слышит вас.", _models_other(b), False),
        ("imgmodels", "downloads", "Модели и движок картинок", "Модели для рисования на этом компьютере и их движок.", P("models/image", "models/tagger", "engine-sd"), False),
        ("voice", "downloads", "Голос Карины", "Скачанная копия голоса Silero. Голос, встроенный в программу, остаётся, пока не удалена сама программа.", P("voices"), True),
        ("components", "downloads", "Компоненты (torch)", "Библиотека torch, которую Карина докачала при первом запуске. Нужна голосу.", P("components"), False),
        ("engine", "downloads", "Движок llama.cpp", "Программа, которая запускает модели чата.", P("engine", "engine.pid"), True),
        ("vpn", "downloads", "Движок VPN (sing-box)", "Скачанный движок и его журнал. Ссылки на серверы хранятся в настройках.", P("tunnel"), True),
        ("chats", "data", "Диалоги и вложения", "История чатов и файлы, которые вы прикладывали.", P("chats", "uploads"), False),
        ("memory", "data", "Чему Карина научилась", "Выбранные вами приложения и узнаваемые фразы.", P("learned.json"), False),
        ("island", "data", "Остров, ярлыки и заметки", "Настройки виджета-острова, ярлыки, заметки, значки.", P("island.json", "shelf.json", "icons"), False),
        ("plugins", "data", "Свои плагины", "Файлы из папки plugins в данных Карины.", P("plugins"), True),
        ("settings", "data", "Настройки, ключи и профиль", "Всё из «Настроек»: ключи Claude и Gemini, профиль, ссылки на VPN-серверы, свои команды. Карина станет как после установки.", P("config.json", "config.tmp", "launch_args.json", "install_options.json"), False),
        ("logs", "data", "Журналы", "karina.log, crash.log и служебные файлы.", P("karina.log", "crash.log", "karina.lock"), False),
    ]
    out = []
    for iid, grp, title, desc, paths, now in items:
        have = [p for p in paths if p.exists()]
        if have:
            out.append({"id": iid, "group": grp, "title": title, "desc": desc, "paths": have, "now": now, "kind": "files"})
    # системные интеграции: не файлы данных, а следы в Windows
    sysbits = []
    if IS_WIN:
        try:
            from . import island_tools
            if island_tools.autostart_get():
                sysbits.append("автозапуск вместе с Windows")
        except Exception:
            pass
        try:
            from . import elevate
            if elevate.task_exists():
                sysbits.append("задача планировщика (запуск от администратора)")
        except Exception:
            pass
    lnk = [p / LNK for p in _desktops() if (p / LNK).exists()]
    if lnk:
        sysbits.append("ярлык на рабочем столе")
    if sysbits:
        out.append({"id": "system", "group": "system", "title": "Автозапуск и ярлыки", "desc": "Убрать: " + ", ".join(sysbits) + ".", "paths": lnk, "now": True, "kind": "system"})
    u = unins_exe()
    if u:
        out.append({"id": "program", "group": "system", "title": "Сама программа", "desc": "Запустит удаление Карины из Windows: файлы программы, ярлыки, меню «Пуск». Выполняется последним, после выхода.", "paths": [], "now": False, "kind": "program"})
    return out


def size_of(paths) -> int:
    total = 0
    for p in paths:
        try:
            if p.is_file():
                total += p.stat().st_size
                continue
            for root, _dirs, files in os.walk(p):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def describe(base: Path) -> list[dict]:
    """Для окна: без путей, с размерами."""
    return [{"id": i["id"], "group": i["group"], "title": i["title"], "desc": i["desc"], "size": size_of(i["paths"]), "now": i["now"]} for i in catalog(base)]


# ====================================================================== удаление
def _rw(func, path, _exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_path(p: Path, tries: int = 3, pause: float = 0.6) -> str:
    """Стирает файл или папку целиком, с повторами (антивирус, ещё не закрытый файл). '' — готово, иначе причина."""
    err = ""
    for k in range(tries):
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, onerror=_rw)
            elif p.exists() or p.is_symlink():
                try:
                    p.unlink()
                except PermissionError:
                    os.chmod(p, stat.S_IWRITE)
                    p.unlink()
        except OSError as e:
            err = str(e)
        if not (p.exists() or p.is_symlink()):
            return ""
        err = err or "файл занят другой программой"
        if k < tries - 1:
            time.sleep(pause)
    return err


def remove_system(base: Path) -> list[str]:
    """Автозапуск, задача планировщика, ярлык на рабочем столе. → список проблем."""
    bad = []
    if IS_WIN:
        try:
            from . import island_tools
            if island_tools.autostart_get() and not island_tools.autostart_set(False):
                bad.append("не удалось убрать автозапуск")
        except Exception as e:
            bad.append(f"автозапуск: {e}")
        try:
            from . import elevate
            if elevate.task_exists():
                r = elevate.disable()
                if not r.get("ok"):
                    bad.append("задача планировщика осталась (нужно подтвердить окно Windows)")
        except Exception as e:
            bad.append(f"задача планировщика: {e}")
    for d in _desktops():
        if (d / LNK).exists():
            e = remove_path(d / LNK, 2, 0.3)
            if e:
                bad.append(f"ярлык {d / LNK}: {e}")
    return bad


def plan(base: Path, ids: list[str], now_failed: list[str] | None = None) -> dict:
    """Что отложено до выхода: пути и запуск деинсталлятора. now_failed — «быстрые» пункты, которые не стёрлись сразу."""
    cat = {i["id"]: i for i in catalog(base)}
    paths: list[str] = []
    for iid in ids:
        it = cat.get(iid)
        if it and it["kind"] == "files" and (not it["now"] or iid in (now_failed or [])):
            paths += [str(p) for p in it["paths"]]
    prog = unins_exe() if "program" in ids else None
    return {"base": str(base), "paths": paths, "program": str(prog) if prog else ""}


def spawn_cleanup(pl: dict) -> bool:
    """Запускает вторую копию Карины, которая дождётся выхода этой и всё сотрёт. False — не вышло."""
    if not pl["paths"] and not pl["program"]:
        return False
    fd, name = tempfile.mkstemp(prefix="karina_cleanup_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dict(pl, pid=os.getpid()), f, ensure_ascii=False)
    if FROZEN:
        cmd = [sys.executable, "--cleanup", name]
    else:
        exe = Path(sys.executable)
        pw = exe.with_name("pythonw.exe")
        cmd = [str(pw if pw.exists() else exe), str(Path(__file__).resolve().parent.parent / "main.py"), "--cleanup", name]
    try:
        subprocess.Popen(cmd, creationflags=DETACHED, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        try:
            os.remove(name)
        except OSError:
            pass
        return False


# ====================================================================== вторая копия: ждёт выхода и стирает
def _wait_exit(pid: int, timeout: float = 40.0) -> None:
    end = time.time() + timeout
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x00100000, False, int(pid))         # SYNCHRONIZE
            if h:
                k.WaitForSingleObject(h, int(timeout * 1000))
                k.CloseHandle(h)
            return
        except Exception:
            pass
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.3)


def _prune(d: Path, base: Path) -> None:
    """Пустые папки, оставшиеся после удаления (например, models), убираем вплоть до папки данных."""
    try:
        while d != base and base in d.parents and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    except OSError:
        pass


def cleanup_main(plan_file: str) -> int:
    """Вход `main.py --cleanup <план>`: ничего не создаёт, кроме отчёта во временной папке."""
    report = Path(tempfile.gettempdir()) / "karina_cleanup.log"
    lines: list[str] = []
    try:
        pl = json.loads(Path(plan_file).read_text("utf-8"))
    except (OSError, ValueError):
        return 1
    _wait_exit(int(pl.get("pid") or 0))
    time.sleep(1.0)                                  # дочерние процессы (движки) закрываются вместе с основным
    if IS_WIN:                                       # на случай, если движок остался сиротой и держит папку
        for exe in ("llama-server.exe", "sing-box.exe", "sd.exe"):
            try:
                subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, creationflags=NO_WINDOW, timeout=15)
            except Exception:
                pass
    base = Path(pl.get("base") or "")
    for s in pl.get("paths", []):
        p = Path(s)
        e = remove_path(p, tries=12, pause=1.0)
        lines.append(f"{'ok ' if not e else 'ERR'} {p} {e}")
        if not e:
            _prune(p.parent, base)
    try:
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()                             # пустую папку данных убираем тоже
    except OSError:
        pass
    prog = pl.get("program") or ""
    if prog and Path(prog).exists():
        try:
            subprocess.Popen([prog, "/SILENT"], creationflags=DETACHED, close_fds=True)
            lines.append(f"запущено удаление программы: {prog}")
        except OSError as e:
            lines.append(f"ERR деинсталлятор: {e}")
    try:
        report.write_text("\n".join(lines) + "\n", "utf-8")
        Path(plan_file).unlink(missing_ok=True)
    except OSError:
        pass
    return 0
