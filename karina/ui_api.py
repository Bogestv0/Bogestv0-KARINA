"""Мост между окном (HTML) и ассистентом. Все методы вызываются из JavaScript: pywebview.api.<метод>()."""
from __future__ import annotations
import json, os, queue, subprocess, sys, threading, time
from .diag import trace
from . import actions, net, models, updater, __version__
from .audio import Listener
from .config import DATA, DEFAULTS


class Api:
    def __init__(self, cfg, window_getter):
        self.cfg = cfg
        self._win = window_getter
        self.assistant = None
        self._downloads: dict = {}
        self._events: queue.Queue = queue.Queue(maxsize=500)
        threading.Thread(target=self._emit_loop, daemon=True, name="karina-emit").start()

    def bind(self, assistant):
        self.assistant = assistant

    # ---- события в окно ----
    def emit(self, ev: dict):
        """Только кладёт событие в очередь и сразу возвращается: ассистент никогда не ждёт окно
        (если страница не загрузилась, evaluate_js может не вернуться — раньше это вешало загрузку модели)."""
        try:
            self._events.put_nowait(ev)
        except queue.Full:
            pass

    def _emit_loop(self):
        first = True
        while True:
            ev = self._events.get()
            w = self._win()
            if not w:
                continue
            try:
                if first:
                    trace("Окно: первое событие, жду ответа evaluate_js")
                w.evaluate_js(f"window.karina && window.karina.event({json.dumps(ev, ensure_ascii=False)})")
                if first:
                    trace("Окно: evaluate_js ответил")
                    first = False
            except Exception as e:
                if first:
                    trace(f"Окно: evaluate_js ошибка {e.__class__.__name__}: {e}")
                    first = False

    # ---- состояние ----
    def get_state(self):
        return {"status": self.assistant.status(), "config": self._safe_cfg(), "mics": self._mics(), "version": __version__}

    def get_status(self):
        return self.assistant.status()

    def _safe_cfg(self):
        d = json.loads(json.dumps(self.cfg.data))
        d["llm"]["has_key"] = bool(d["llm"].get("api_key"))
        d["llm"]["api_key"] = ""       # ключ в окно не отдаём
        return d

    def _mics(self):
        try:
            return Listener.devices()
        except Exception:
            return []

    def get_commands(self):
        return [{"name": c.name, "desc": c.desc, "examples": c.examples} for c in actions.REGISTRY]

    # ---- действия ----
    def send_text(self, text: str):
        threading.Thread(target=self.assistant.handle, args=(text, True), daemon=True).start()
        return True

    def set_listening(self, on: bool):
        self.assistant.set_listening(bool(on))
        return True

    def wake_up(self):
        self.assistant.sleeping = False
        self.assistant.set_listening(True)
        return True

    def test_voice(self):
        threading.Thread(target=self.assistant.say, args=(f"Привет! Меня зовут {self.cfg['name']}. Я вас слышу.",), daemon=True).start()
        return True

    def save_settings(self, patch: dict):
        old_model = self.cfg["stt"]["model"]
        old_mic = self.cfg["stt"].get("mic_device")
        llm = patch.get("llm") or {}
        if "api_key" in llm and not llm["api_key"]:
            llm.pop("api_key")          # пустое поле = «оставить как было»
        patch.pop("apps", None)
        patch.pop("commands", None)
        self.cfg.update(patch)
        if self.cfg["stt"]["model"] != old_model:
            self.assistant.reload_model()
        if self.cfg["stt"].get("mic_device") != old_mic:
            self.assistant.reload_audio()
        return self._safe_cfg()

    def clear_api_key(self):
        self.cfg.update({"llm": {"api_key": "", "enabled": False}})
        return self._safe_cfg()

    # ---- чат ----
    def chat_list(self):
        return {"chats": self.assistant.chat.store.list(), "busy": self.assistant.chat.busy, "current": self.assistant.chat.current}

    def chat_new(self):
        return self.assistant.chat.store.new()

    def chat_get(self, cid: str):
        return self.assistant.chat.store.get(cid)

    def chat_delete(self, cid: str):
        return self.assistant.chat.store.delete(cid)

    def chat_send(self, cid: str, text: str, search=None):
        r = self.assistant.chat.send(cid or None, text, search=None if search is None else bool(search))
        return {"ok": r is not None, "chat": r, "error": "" if r else "Я ещё отвечаю. Нажмите «Стоп» или подождите."}

    def chat_stop(self):
        self.assistant.chat.stop()
        return True

    def search_test(self):
        """Проверка поиска: каждый поисковик отдельно + чтение одной страницы. Результат придёт событием search_test."""
        from . import websearch

        def run():
            lines, ok = [], False
            if not net.is_online():
                lines.append("Нет подключения к интернету.")
            else:
                for name, fn in websearch.PROVIDERS:
                    try:
                        res = fn("Байкал глубина озера", 5, None)
                        lines.append(f"✔ {name}: найдено {len(res)}" if res else f"✖ {name}: пусто")
                        ok = ok or bool(res)
                    except Exception as e:
                        lines.append(f"✖ {name}: {e}")
                try:
                    p = websearch.fetch_page("https://ru.wikipedia.org/wiki/Байкал")
                    lines.append(f"✔ Чтение страниц: {len(p['text'])} символов")
                except Exception as e:
                    lines.append(f"✖ Чтение страниц: {e}")
                    ok = False
            self.emit({"type": "search_test", "ok": ok, "lines": lines})
        threading.Thread(target=run, daemon=True, name="search-test").start()
        return True

    def open_url(self, url: str):
        import webbrowser
        if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
            webbrowser.open(url)
            return True
        return False

    # ---- обновление ----
    def check_update(self, manual: bool = True):
        """manual=False — тихая проверка при запуске: ошибки не показываем."""
        if not manual and not self.cfg["updates"].get("auto", True):
            return {"available": False, "skipped": True, "current": __version__}
        try:
            r = updater.check()
        except Exception as e:
            return {"available": False, "error": "Не удалось проверить обновления: нет связи с GitHub." if not manual else f"Не удалось проверить обновления: {e}", "current": __version__}
        self._update = r
        return r

    def install_update(self):
        info = getattr(self, "_update", None)
        if not info or not info.get("available"):
            return {"ok": False, "error": "Сначала проверьте обновления"}
        if not info.get("can_install"):
            import webbrowser
            webbrowser.open(info.get("page") or updater.PAGE)
            return {"ok": True, "opened": True}
        cancel = self._upd_cancel = threading.Event()

        def run():
            err = ""
            try:
                p = updater.download(info, lambda d, t: self.emit({"type": "update_progress", "done": d, "total": t}), cancel)
                self.emit({"type": "update_ready"})
                time.sleep(0.6)
                updater.launch_installer(p)
                time.sleep(0.5)
                w = self._win()
                if w:
                    w.destroy()
                os._exit(0)
            except updater.Cancelled:
                err = "cancelled"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            self.emit({"type": "update_done", "error": err})
        threading.Thread(target=run, daemon=True, name="updater").start()
        return {"ok": True}

    def cancel_update(self):
        c = getattr(self, "_upd_cancel", None)
        if c:
            c.set()
        return True

    # ---- модели ----
    def _engine(self):
        return self.assistant.engine

    def models_state(self):
        ram = models.system_ram_gb()
        inst = set(models.installed_ids())
        act = self.cfg["models"].get("active", "")
        items = []
        for m in models.CATALOG:
            dl = self._downloads.get(m["id"])
            items.append({**{k: m[k] for k in ("id", "title", "tier", "size_gb", "ram", "ru", "desc")},
                          "tier_label": models.TIER_LABEL[m["tier"]], "installed": m["id"] in inst, "active": m["id"] == act,
                          "dl": dl and {"done": dl["done"], "total": dl["total"], "stage": dl["stage"]},
                          "too_big": bool(ram) and ram < m["ram"] * 0.75})
        return {"models": items, "ram": ram, "recommended": models.recommended_id(ram), "engine": self._engine().info(),
                "gpu": self.cfg["models"].get("gpu", "auto")}

    def model_download(self, mid: str):
        if mid in self._downloads or not models.by_id(mid):
            return False
        cancel = threading.Event()
        self._downloads[mid] = {"done": 0, "total": 0, "stage": "search", "cancel": cancel}

        def run():
            def prog(done, total, stage):
                self._downloads[mid].update(done=done, total=total, stage=stage)
                self.emit({"type": "model_progress", "id": mid, "done": done, "total": total, "stage": stage})
            err = ""
            try:
                models.download(mid, prog, cancel)
                if not self.cfg["models"].get("active"):
                    self.cfg.update({"models": {"active": mid}})
            except models.Cancelled:
                err = "cancelled"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            finally:
                self._downloads.pop(mid, None)
                self.emit({"type": "model_done", "id": mid, "error": err})
        threading.Thread(target=run, daemon=True, name="model-download").start()
        return True

    def model_cancel(self, mid: str):
        d = self._downloads.get(mid)
        if d:
            d["cancel"].set()
        return True

    def model_delete(self, mid: str):
        if self.cfg["models"].get("active") == mid:
            self._engine().stop()
            self.cfg.update({"models": {"active": ""}})
        return models.delete(mid)

    def model_select(self, mid: str):
        if mid not in models.installed_ids():
            return {"ok": False, "error": "Сначала скачайте модель"}
        self.cfg.update({"models": {"active": mid}})
        threading.Thread(target=self._warm, args=(mid,), daemon=True).start()
        return {"ok": True}

    def _warm(self, mid):
        try:
            self._engine().start(mid)
        except Exception as e:
            self.assistant.log("error", str(e))

    def model_test(self, prompt: str = ""):
        """Короткая проверка: модель отвечает на вопрос. Возвращает текст и скорость."""
        def run():
            t0 = time.time()
            try:
                text = self._engine().chat([{"role": "user", "content": prompt or "Скажи по-русски одним предложением, что ты работаешь."}], max_tokens=120)
                ok, out = True, text
            except Exception as e:
                ok, out = False, str(e)
            self.emit({"type": "model_test", "ok": ok, "text": out, "sec": round(time.time() - t0, 1)})
        threading.Thread(target=run, daemon=True, name="model-test").start()
        return True

    def models_settings(self, patch: dict):
        allowed = {k: patch[k] for k in ("gpu", "ctx", "threads") if k in patch}
        if allowed.get("gpu") not in (None, "auto", "cpu"):
            allowed.pop("gpu")
        old = dict(self.cfg["models"])
        self.cfg.update({"models": allowed})
        if {k: self.cfg["models"][k] for k in allowed} != {k: old.get(k) for k in allowed}:
            self._engine().stop()      # новые параметры вступят в силу при следующем запуске
        return True

    # ---- программы ----
    def list_apps(self):
        return [{"name": e["name"], "aliases": e.get("aliases", []), "src": e["src"], "path": e.get("path", ""),
                 "found": bool(e.get("path") or e.get("exec") or e.get("browser") or (e.get("find") and self.assistant.apps.find_shortcut(e["find"], 88)))}
                for e in self.assistant.apps.entries()]

    def add_app(self, name: str, aliases: str, path: str, args: str = ""):
        name = (name or "").strip()
        if not name or not path:
            return {"ok": False, "error": "Нужны название и путь к программе"}
        apps = [a for a in self.cfg.get("apps", []) if a["name"].lower() != name.lower()]
        apps.append({"name": name, "aliases": [a.strip() for a in aliases.split(",") if a.strip()], "path": path.strip().strip('"'),
                     "args": args.strip(), "proc": [os.path.splitext(os.path.basename(path.strip('"')))[0]]})
        self.cfg.data["apps"] = apps
        self.cfg.save()
        return {"ok": True}

    def remove_app(self, name: str):
        self.cfg.data["apps"] = [a for a in self.cfg.get("apps", []) if a["name"] != name]
        self.cfg.save()
        return True

    def test_app(self, name: str):
        r = self.assistant.apps.open(name)
        return r

    def pick_file(self):
        import webview
        w = self._win()
        res = w.create_file_dialog(webview.OPEN_DIALOG, file_types=("Программы (*.exe;*.lnk;*.bat;*.cmd)", "Все файлы (*.*)"))
        return res[0] if res else ""

    # ---- свои команды ----
    def list_custom(self):
        return self.cfg.get("commands", [])

    def add_custom(self, phrases: str, type_: str, value: str, reply: str = ""):
        ph = [p.strip() for p in phrases.replace(";", ",").split(",") if p.strip()]
        if not ph or not value.strip():
            return {"ok": False, "error": "Нужны фраза и действие"}
        if type_ not in ("run", "url", "keys", "shell", "type", "http"):
            return {"ok": False, "error": "Неизвестный тип действия"}
        cmds = list(self.cfg.get("commands", []))
        cmds.append({"phrases": ph, "do": {"type": type_, "value": value.strip()}, "reply": reply.strip() or "Готово."})
        self.cfg.data["commands"] = cmds
        self.cfg.save()
        return {"ok": True}

    def remove_custom(self, index: int):
        cmds = list(self.cfg.get("commands", []))
        if 0 <= index < len(cmds):
            cmds.pop(index)
            self.cfg.data["commands"] = cmds
            self.cfg.save()
        return True

    def open_data_folder(self):
        DATA.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(DATA))
        return True
