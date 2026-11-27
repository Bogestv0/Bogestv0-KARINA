"""Окно установки и удаления (ui/setup.html). Запуск: `python main.py --setup` | `--setup-update` | `--setup-uninstall`.

Окно общее для трёх режимов: install, update, uninstall. Удаление работает на настоящих функциях karina/uninstall.py (те же пункты, что в «Настройки → Удаление»),
но уже без запущенной Карины: нечего останавливать, всё стирается сразу, а занятое отложено на cleanup_main.
Установка: выбор из окна кладётся в install_options.json (его Карина подхватит при первом запуске, см. install_options.py). Само копирование файлов делает
`Installer.backend`; сейчас он не подключён (установщик остаётся на Inno Setup), поэтому setup_install честно отвечает ошибкой, а не рисует фальшивую полосу."""
from __future__ import annotations
import json, sys, threading
from pathlib import Path

from . import install_options, uninstall as un

UI = Path(__file__).resolve().parent.parent / "ui" / "setup.html"
MODES = ("install", "update", "uninstall")


class SetupApi:
    def __init__(self, mode: str, base: Path, version: str = "", backend=None):
        self.mode = mode if mode in MODES else "install"
        self.base = Path(base)
        self.version = version
        self.backend = backend            # backend(opts: dict, report: callable(pct, text, log=None)) -> None; исключение = ошибка установки
        self._p = {"pct": 0, "text": "", "log": [], "done": False, "ok": True, "error": ""}
        self._lock = threading.Lock()
        self.window = None

    # ---- общее
    def setup_state(self):
        return {"mode": self.mode, "version": self.version, "installed_version": "", "has_vpn": True}

    def win_minimize(self):
        if self.window:
            self.window.minimize()
        return True

    def win_close(self):
        if self.window:
            self.window.destroy()
        return True

    # ---- удаление
    def uninstall_list(self):
        return {"items": un.describe(self.base), "path": str(self.base)}

    def uninstall_run(self, ids):
        cat = {i["id"]: i for i in un.catalog(self.base)}
        ids = [i for i in (ids if isinstance(ids, list) else []) if isinstance(i, str) and i in cat]
        if not ids:
            return {"ok": False, "error": "Ничего не выбрано."}
        done, problems, failed = [], [], []
        for iid in ids:
            it = cat[iid]
            if it["kind"] == "system":
                bad = un.remove_system(self.base)
                problems += bad
                if not bad:
                    done.append(iid)
            elif it["kind"] == "files":
                errs = [e for e in (un.remove_path(p) for p in it["paths"]) if e]
                (failed if errs else done).append(iid)
        pl = un.plan(self.base, ids, failed)         # занятое и «Сама программа» доделает вторая копия после выхода
        later = bool(pl["paths"] or pl["program"])
        if later and not un.spawn_cleanup(pl):
            problems.append("Не удалось запустить завершающую очистку: закройте окно и повторите удаление.")
            later = False
        return {"ok": True, "done": done, "problems": problems, "quit": later}

    # ---- установка
    def setup_install(self, opts):
        if self.backend is None:
            return {"ok": False, "error": "Копирование файлов не подключено: пока устанавливайте через Bogestv0-KARINA-Setup.exe."}
        opts = opts if isinstance(opts, dict) else {}
        raw = {"ui": opts.get("ui") or {}, "profile": opts.get("profile") or {}}
        with self._lock:
            self._p = {"pct": 0, "text": "Подготовка", "log": [], "done": False, "ok": True, "error": ""}

        def run():
            try:
                self.base.mkdir(parents=True, exist_ok=True)
                (self.base / install_options.FILE).write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
                self.backend(opts, self._report)
                self._report(100, "Готово")
                self._finish(True)
            except Exception as e:                      # любая ошибка установки показывается в окне
                self._finish(False, str(e) or e.__class__.__name__)
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def _report(self, pct, text, log=None):
        with self._lock:
            self._p["pct"], self._p["text"] = pct, text
            if log:
                self._p["log"] += [{"text": log}]

    def _finish(self, ok, error=""):
        with self._lock:
            self._p.update(done=True, ok=ok, error=error)

    def setup_progress(self):
        with self._lock:
            out = dict(self._p)
            self._p["log"] = []           # строки журнала отдаются один раз
        return out

    def setup_finish(self, opts=None):
        self.win_close()
        return True


def run(mode: str, base: Path, version: str = "", backend=None) -> None:
    import webview
    api = SetupApi(mode, base, version, backend)
    api.window = webview.create_window("Bogestv0 KARINA", url=str(UI), js_api=api, width=820, height=560, resizable=False, frameless=True, easy_drag=True, background_color="#101116")
    webview.start()
