"""Мост между окном (HTML) и ассистентом. Все методы вызываются из JavaScript: pywebview.api.<метод>()."""
from __future__ import annotations
import json, os, subprocess, sys, threading
from . import actions, net
from .audio import Listener
from .config import DATA, DEFAULTS


class Api:
    def __init__(self, cfg, window_getter):
        self.cfg = cfg
        self._win = window_getter
        self.assistant = None

    def bind(self, assistant):
        self.assistant = assistant

    # ---- события в окно ----
    def emit(self, ev: dict):
        w = self._win()
        if not w:
            return
        try:
            w.evaluate_js(f"window.karina && window.karina.event({json.dumps(ev, ensure_ascii=False)})")
        except Exception:
            pass

    # ---- состояние ----
    def get_state(self):
        return {"status": self.assistant.status(), "config": self._safe_cfg(), "mics": self._mics()}

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
