"""Мост между окном (HTML) и ассистентом. Все методы вызываются из JavaScript: pywebview.api.<метод>()."""
from __future__ import annotations
import json, os, queue, subprocess, sys, threading, time
from .diag import trace
from . import actions, claude as claudemod, files as filesmod, images as imagesmod, imgdirector, imgpower, localimg, net, models, updater, __version__
from .uploads import Uploads
from . import audio as audiomod
from .audio import Listener
from .config import DATA, DEFAULTS


class Api:
    def __init__(self, cfg, window_getter):
        self.cfg = cfg
        self._win = window_getter
        self.assistant = None
        self.uploads = Uploads()
        self._downloads: dict = {}
        self.frame = None               # FrameControl безрамочного окна (None — обычное окно с рамкой Windows)
        self.quitting = False           # True — окну можно закрыться по-настоящему (выход из трея, обновление)
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
        return {"status": self.assistant.status(), "config": self._safe_cfg(), "mics": self._mics(), "outs": self._outs(),
                "version": __version__,
                "frameless": self.frame is not None, "maximized": bool(self.frame and self.frame.maximized)}

    def get_status(self):
        return self.assistant.status()

    def _safe_cfg(self):
        d = json.loads(json.dumps(self.cfg.data))
        d["llm"]["has_key"] = bool(d["llm"].get("api_key"))
        d["llm"]["api_key"] = ""       # ключ в окно не отдаём
        d.setdefault("images", {})["has_key"] = bool((d.get("images") or {}).get("key"))
        d["images"]["key"] = ""        # ключ сервиса картинок в окно не отдаём
        for pv in ((d.get("cloud") or {}).get("providers") or {}).values():
            pv["has_key"] = bool(pv.get("key"))
            pv["key"] = ""             # ключи облачных провайдеров тоже не отдаём
        return d

    def _mics(self):
        try:
            return Listener.devices()
        except Exception:
            return []

    def _outs(self):
        try:
            return audiomod.list_devices("output")
        except Exception:
            return []

    def audio_devices(self):
        """Обновить списки устройств (подключили наушники или микрофон, не перезапуская Карину)."""
        return {"mics": self._mics(), "outs": self._outs()}

    # ---- вывод звука (вкладка «Звук») ----
    def audio_out_set(self, device, volume):
        """Выбрать устройство вывода (None — по умолчанию) и громкость голоса 0–100. Имя устройства берём из своего списка."""
        try:
            dev = None if device in (None, "") else int(device)
        except (TypeError, ValueError):
            dev = None
        try:
            vol = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            vol = 100
        name = ""
        if dev is not None:
            name = next((d["name"] for d in self._outs() if d["id"] == dev), "")
            if not name:
                dev = None
        self.cfg.update({"audio_out": {"device": dev, "name": name, "volume": vol}})
        return self.audio_out_state()

    def audio_out_state(self):
        from . import playback
        o = self.cfg["audio_out"]
        _, _, found = playback.resolve_output(self.cfg)
        return {"device": o.get("device"), "name": o.get("name", ""), "volume": o.get("volume", 100), "found": found,
                "warning": self.assistant.tts.out_warning if self.assistant else ""}

    def audio_out_test(self):
        """Короткий сигнал на выбранное устройство. Результат придёт событием out_test."""
        from . import playback

        def run():
            err = ""
            try:
                dev, vol, found = playback.resolve_output(self.cfg)
                if not found:
                    err = "Выбранное устройство не найдено. Подключите его или обновите список."
                else:
                    playback.play_chime(dev, vol, threading.Event())
            except Exception as e:
                err = f"{e.__class__.__name__}: {e}"
            self.emit({"type": "out_test", "error": err})
        threading.Thread(target=run, daemon=True, name="audio-out-test").start()
        return True

    def get_commands(self):
        return [{"name": c.name, "desc": c.desc, "examples": c.examples} for c in actions.REGISTRY]

    # ---- окно без рамки (кнопки заголовка, перетаскивание, края) ----
    def win_minimize(self):
        return bool(self.frame and self.frame.minimize())

    def win_toggle_maximize(self):
        return bool(self.frame and self.frame.toggle_maximize())

    def win_close(self):
        return bool(self.frame and self.frame.close())

    def win_drag(self, edge: str = "move"):
        if edge not in ("move", "n", "s", "e", "w", "ne", "nw", "se", "sw"):
            return False
        return bool(self.frame and self.frame.begin_drag(edge))

    # ---- права администратора ----
    def admin_state(self):
        from . import elevate
        ui = self.cfg["ui"]
        return {"supported": sys.platform == "win32", "admin": elevate.is_admin(), "enabled": bool(ui.get("admin")),
                "task": elevate.task_exists() if sys.platform == "win32" else False, "asked": bool(ui.get("admin_asked"))}

    def admin_set(self, on: bool):
        """Включить/выключить «Запускать от имени администратора». Включение показывает окно UAC один раз."""
        from . import elevate
        r = elevate.enable() if on else elevate.disable()
        if r.get("ok"):
            self.cfg.update({"ui": {"admin": bool(on), "admin_asked": True}})
        r["state"] = self.admin_state()
        return r

    def admin_dismiss(self):
        self.cfg.update({"ui": {"admin_asked": True}})
        return True

    def restart_app(self):
        """Перезапуск Карины (например, чтобы права администратора вступили в силу)."""
        from . import elevate
        want = bool(self.cfg["ui"].get("admin"))
        ok = False
        if want and not elevate.is_admin():
            ok = elevate.relaunch_as_admin(["--restart"], trace)
        elif not want or elevate.is_admin():
            exe, base = elevate._command()
            try:
                subprocess.Popen([exe] + base + ["--restart"] + (["--elevated"] if elevate.is_admin() and want else []), close_fds=True,
                                 creationflags=0x08000000 if sys.platform == "win32" else 0)
                ok = True
            except Exception as e:
                trace(f"Перезапуск не удался: {e}")
        if not ok:
            return {"ok": False, "error": "Не удалось перезапустить. Закройте и откройте Карину вручную."}
        self.quitting = True
        w = self._win()
        if w:
            threading.Timer(0.3, w.destroy).start()
        return {"ok": True}

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

    # ---- офлайн-голос (Piper «Ирина») ----
    def voice_offline_status(self):
        from . import offline_voice
        base = self.cfg.path.parent
        onnx, _ = offline_voice.paths(base)
        size = onnx.stat().st_size if offline_voice.is_ready(base) else 0
        return {"ready": offline_voice.is_ready(base), "size": size, "downloading": "voice" in self._downloads}

    def voice_offline_download(self):
        from . import offline_voice
        if "voice" in self._downloads or offline_voice.is_ready(self.cfg.path.parent):
            return False
        cancel = threading.Event()
        self._downloads["voice"] = {"cancel": cancel}

        def run():
            err = ""
            try:
                offline_voice.download(self.cfg.path.parent,
                                       lambda d, t, st: self.emit({"type": "voice_dl", "done": d, "total": t, "stage": st}), cancel)
            except offline_voice.Cancelled:
                err = "cancelled"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            finally:
                self._downloads.pop("voice", None)
                self.emit({"type": "voice_dl_done", "error": err, "ready": offline_voice.is_ready(self.cfg.path.parent)})
        threading.Thread(target=run, daemon=True, name="voice-download").start()
        return True

    def voice_offline_delete(self):
        from . import offline_voice
        if "voice" not in self._downloads:
            offline_voice.delete(self.cfg.path.parent)
        return self.voice_offline_status()

    # ---- голос Silero ----
    def silero_status(self):
        from . import silero_voice
        base = self.cfg.path.parent
        size = silero_voice.model_path(base).stat().st_size if silero_voice.is_ready(base) else 0
        return {"ready": silero_voice.is_ready(base), "size": size, "downloading": "silero" in self._downloads, "torch": silero_voice.torch_available(), "hint": silero_voice.torch_hint()}

    def silero_download(self):
        from . import silero_voice
        if "silero" in self._downloads or silero_voice.is_ready(self.cfg.path.parent):
            return False
        cancel = threading.Event()
        self._downloads["silero"] = {"cancel": cancel}

        def run():
            err = ""
            try:
                silero_voice.download(self.cfg.path.parent, lambda d, t, st: self.emit({"type": "silero_dl", "done": d, "total": t, "stage": st}), cancel)
            except silero_voice.Cancelled:
                err = "cancelled"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            finally:
                self._downloads.pop("silero", None)
                self.emit({"type": "silero_dl_done", "error": err, "ready": silero_voice.is_ready(self.cfg.path.parent)})
        threading.Thread(target=run, daemon=True, name="silero-download").start()
        return True

    def silero_delete(self):
        from . import silero_voice
        if "silero" not in self._downloads:
            silero_voice.delete(self.cfg.path.parent)
        return self.silero_status()

    # ---- профиль и вход через Google ----
    def get_profile(self):
        from . import profile as prof
        g = self.cfg.get("google") or {}
        return {"profile": prof.get(self.cfg), "kinds": prof.PET_KINDS, "google": {"client_id": g.get("client_id", ""), "has_secret": bool(g.get("client_secret"))},
                "signing_in": "google" in self._downloads}

    def save_profile(self, p: dict):
        from . import profile as prof
        cur = prof.get(self.cfg)
        new = prof.clean({**p, "google": cur.get("google") or {}, "last_greeted": cur.get("last_greeted", "")})
        if new["birth_day"] != cur["birth_day"] or new["birth_month"] != cur["birth_month"]:
            new["last_greeted"] = ""            # дату поменяли: сегодняшнее поздравление снова возможно
        self.cfg.update({"profile": new})
        return self.get_profile()

    def save_google_client(self, client_id: str, client_secret: str = ""):
        g = {"client_id": str(client_id or "").strip()[:200]}
        if client_secret:
            g["client_secret"] = str(client_secret).strip()[:200]
        self.cfg.update({"google": g})
        return self.get_profile()

    def google_login(self):
        from . import google_auth, profile as prof
        if "google" in self._downloads:
            return False
        cancel = threading.Event()
        self._downloads["google"] = {"cancel": cancel}
        g = dict(self.cfg.get("google") or {})

        def run():
            err, info = "", None
            try:
                info = google_auth.sign_in(g.get("client_id", ""), g.get("client_secret", ""), cancel=cancel)
                cur = prof.get(self.cfg)
                patch = {"google": {k: info[k] for k in ("sub", "name", "email", "picture")}}
                if not cur["name"]:
                    patch["name"] = info.get("given_name") or info.get("name") or ""      # имя подставляем, только если своё не вписано
                self.cfg.update({"profile": patch})
            except google_auth.GoogleError as e:
                err = str(e)
            except Exception as e:
                err = f"Не получилось войти: {e}"
            finally:
                self._downloads.pop("google", None)
                self.emit({"type": "google_done", "error": err})
        threading.Thread(target=run, daemon=True, name="google-login").start()
        return True

    def google_cancel(self):
        d = self._downloads.get("google")
        if d:
            d["cancel"].set()
        return True

    def google_logout(self):
        self.cfg.update({"profile": {"google": {}}})
        c = self.cfg.data["profile"]
        c["google"] = {}                      # _merge не умеет «обнулять» вложенные словари, поэтому чистим напрямую
        self.cfg.save()
        return self.get_profile()

    # ---- микшер и VPN ----
    def integrations_status(self):
        v = self.assistant.vpn
        return {"vpn": {"enabled": v.enabled(), "browsers": v.browsers(), "error": v.error}}

    def mixer_test(self):
        from .mixer_remote import MixerError
        m = self.assistant.mixer
        m.close()                                   # заново, с текущими PIN и портом
        try:
            strips = m.strips()
            names = ", ".join(s["name"] for s in strips if not s["hidden"])
            host, port, pin = m.target()
            return {"ok": True, "text": f"Связь есть ({host}:{port}). Дорожки: {names}."}
        except MixerError as e:
            return {"ok": False, "text": str(e)}
        except Exception as e:
            return {"ok": False, "text": f"Ошибка: {e}"}

    def test_voice(self):
        threading.Thread(target=self.assistant.say, args=(f"Привет! Меня зовут {self.cfg['name']}. Я вас слышу.",), daemon=True).start()
        return True

    def save_settings(self, patch: dict):
        old_model = self.cfg["stt"]["model"]
        old_mics = audiomod.mic_slots(self.cfg)
        old_tts = dict(self.cfg["tts"])
        llm = patch.get("llm") or {}
        if "api_key" in llm and not llm["api_key"]:
            llm.pop("api_key")          # пустое поле = «оставить как было»
        patch.pop("apps", None)
        patch.pop("commands", None)
        patch.pop("brain", None)        # «мозг» меняется только через brain_set (с проверкой значений)
        patch.pop("cloud", None)        # облачные модели меняются только через cloud_* (с проверкой значений)
        patch.pop("images", None)       # картинки меняются только через images_set
        patch.pop("audio_out", None)    # вывод звука меняется только через audio_out_set
        mx, vp = patch.get("mixer"), patch.get("vpn")
        if isinstance(mx, dict):                    # проверяем значения: в config.json не должно попасть мусора
            clean = {"enabled": bool(mx.get("enabled", True)), "pin": "".join(ch for ch in str(mx.get("pin") or "") if ch.isdigit())[:8]}
            try:
                port = int(mx.get("port") or 0)
                clean["port"] = port if port == 0 or 1024 <= port <= 65535 else 0
                clean["step_db"] = max(0.5, min(12.0, float(mx.get("step_db") or 3.0)))
            except (TypeError, ValueError):
                clean.update(port=0, step_db=3.0)
            patch["mixer"] = clean
        if isinstance(vp, dict):
            patch["vpn"] = {"enabled": bool(vp.get("enabled", True))}
        old_vpn = bool((self.cfg.get("vpn") or {}).get("enabled", True))
        stt = patch.get("stt") or {}
        if "mics" in stt:
            stt["mics"] = audiomod.sanitize_mics(stt["mics"], self._mics())
            stt["mic_device"] = stt["mics"][0]["device"]          # старый ключ держим в согласии с первым микрофоном
        if "assist" in stt and stt["assist"] not in ("smart", "all", "best"):
            stt.pop("assist")
        self.cfg.update(patch)
        if bool(self.cfg["vpn"].get("enabled", True)) != old_vpn:
            self.assistant.vpn.stop()
            self.assistant.vpn.start()
        if isinstance(mx, dict):
            self.assistant.mixer.close()            # новые PIN и порт применятся при следующей команде
        if self.cfg["stt"]["model"] != old_model:
            self.assistant.reload_model()
        if audiomod.mic_slots(self.cfg) != old_mics:
            self.assistant.reload_audio()
        if self.cfg["tts"] != old_tts:
            self.assistant.warm_voice()      # другой голос/скорость — заранее готовим записи под него
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

    def chat_send(self, cid: str, text: str, search=None, paths=None, mode: str = "auto"):
        """paths — приложенные к сообщению файлы и папки; mode: auto | code | ask (см. ChatService.send)."""
        r = self.assistant.chat.send(cid or None, text, search=None if search is None else bool(search),
                                     paths=paths if isinstance(paths, list) else None, mode=mode if isinstance(mode, str) else "auto")
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

    # ---- файлы (только из папки «Документы\\Карина», по имени файла) ----
    def _file_op(self, fn, *a):
        try:
            return {"ok": True, "result": fn(*a)}
        except filesmod.FileError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}

    def file_open(self, name: str):
        return self._file_op(self.assistant.chat.files.open, name)

    def file_reveal(self, name: str):
        return self._file_op(self.assistant.chat.files.reveal, name)

    def file_preview(self, name: str):
        return self._file_op(self.assistant.chat.files.preview_svg, name)

    def files_open_folder(self):
        return self._file_op(self.assistant.chat.files.open_folder)

    # ---- «мозг» ----
    def brain_state(self):
        return self.assistant.brain.describe()

    def brain_set(self, mode: str = "", claude_model: str = ""):
        from .brain import MODES
        patch = {}
        if mode in MODES:
            patch["mode"] = mode
        if claude_model in claudemod.MODELS:
            patch["claude_model"] = claude_model
        if patch:
            self.cfg.update({"brain": patch})
        return self.assistant.brain.describe()

    def brain_test_claude(self):
        """Проверка ключа и выбранной модели Claude коротким запросом. Результат придёт событием brain_test."""
        def run():
            ok, text, sec = self.assistant.brain.claude.ping()
            self.emit({"type": "brain_test", "ok": ok, "text": text, "sec": sec})
        threading.Thread(target=run, daemon=True, name="claude-test").start()
        return True

    # ---- бесплатные облачные модели ----
    def cloud_state(self):
        c = self.assistant.brain.cloud
        return {"providers": c.describe(), "strategy": c.strategy}

    def cloud_save(self, pid: str, data: dict):
        """Настройки одного провайдера. Пустой ключ = «оставить как было» (удаляется через cloud_clear_key)."""
        from . import cloud as cloudmod
        if pid not in cloudmod.PRESETS or not isinstance(data, dict):
            return self.cloud_state()
        patch: dict = {}
        if "enabled" in data:
            patch["enabled"] = bool(data["enabled"])
        key = str(data.get("key") or "").strip()
        if key and len(key) <= 400 and not any(ch.isspace() for ch in key):
            patch["key"] = key
        if "model" in data:
            patch["model"] = str(data["model"] or "").strip()[:120]
        if pid == "custom":
            if "url" in data:
                url = str(data["url"] or "").strip().rstrip("/")
                patch["url"] = url if url.lower().startswith(("http://", "https://")) else ""
            if "name" in data:
                patch["name"] = str(data["name"] or "").strip()[:40]
        if patch:
            self.cfg.update({"cloud": {"providers": {pid: patch}}})
            if "model" in patch or "url" in patch or "key" in patch:
                self.assistant.brain.cloud._auto.pop(pid, None)        # сменили модель/адрес/ключ — подбор заново
                self.assistant.brain.cloud._st.pop(pid, None)          # и забываем прежний отказ
        return self.cloud_state()

    def cloud_clear_key(self, pid: str):
        from . import cloud as cloudmod
        if pid in cloudmod.PRESETS:
            self.cfg.update({"cloud": {"providers": {pid: {"key": "", "enabled": False}}}})
        return self.cloud_state()

    def cloud_move(self, pid: str, delta: int):
        """Сдвинуть провайдера в очереди (−1 выше, +1 ниже)."""
        c = self.assistant.brain.cloud
        order = c.order()
        if pid in order:
            i = order.index(pid)
            j = max(0, min(len(order) - 1, i + (1 if int(delta) > 0 else -1)))
            order[i], order[j] = order[j], order[i]
            self.cfg.update({"cloud": {"order": order}})
        return self.cloud_state()

    def cloud_strategy(self, strategy: str):
        from . import cloud as cloudmod
        if strategy in cloudmod.STRATEGIES:
            self.cfg.update({"cloud": {"strategy": strategy}})
        return self.cloud_state()

    def cloud_models(self, pid: str):
        """Список моделей провайдера для выбора (нужен ключ)."""
        from . import cloud as cloudmod
        if pid not in cloudmod.PRESETS:
            return {"ok": False, "models": [], "error": "Неизвестный провайдер"}
        try:
            return {"ok": True, "models": self.assistant.brain.cloud.list_models(pid), "error": ""}
        except Exception as e:
            return {"ok": False, "models": [], "error": str(e)}

    def cloud_test(self, pid: str):
        """Проверка ключа и модели провайдера. Результат придёт событием cloud_test."""
        def run():
            ok, text, sec = self.assistant.brain.cloud.ping(pid)
            self.emit({"type": "cloud_test", "pid": pid, "ok": ok, "text": text, "sec": sec})
        threading.Thread(target=run, daemon=True, name="cloud-test").start()
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
                self.quitting = True            # окно не должно «сворачиваться в трей» вместо закрытия
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

    # ---- Кодер (работает внутри чата: см. ChatService) ----
    def coder_state(self):
        c = self.assistant.coder
        return {"busy": c.busy, "settings": c.settings(), "last": c.last}

    def coder_pick(self, folder: bool = False):
        """Диалог выбора файлов (или папки), прикладываемых к сообщению в чате. → список путей."""
        import webview
        w = self._win()
        if not w:
            return []
        if folder:
            res = w.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            res = w.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        return [str(x) for x in (res or [])]

    def coder_stop(self):
        self.assistant.coder.stop()
        return True

    def coder_set(self, run: bool = False, max_steps: int = 0):
        patch = {"run": bool(run)}
        if isinstance(max_steps, int) and 1 <= max_steps <= 30:
            patch["max_steps"] = max_steps
        self.cfg.update({"coder": patch})
        return self.assistant.coder.settings()

    def clipboard_set(self, text: str):
        """Копирование в буфер обмена Windows из окна (надёжнее, чем navigator.clipboard в окне с file://)."""
        try:
            import pyperclip
            pyperclip.copy(str(text)[:2_000_000])
            return True
        except Exception:
            return False

    # ---- файлы, перетащенные в окно (приходят кусками; см. uploads.py) ----
    def upload_begin(self, rel: str, size: int = 0, batch: str = ""):
        return self.uploads.begin(str(rel or ""), int(size or 0), str(batch or ""))

    def upload_chunk(self, token: str, data: str):
        return self.uploads.chunk(str(token), str(data))

    def upload_end(self, token: str):
        return self.uploads.end(str(token))

    def upload_discard(self, path: str):
        return self.uploads.discard(str(path))

    # ---- предпросмотр файлов проекта ----
    def project_preview(self, project: str, rel: str):
        try:
            return {"ok": True, "result": self.assistant.coder.preview(str(project), str(rel))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def project_open_file(self, project: str, rel: str):
        try:
            self.assistant.coder.open_file(str(project), str(rel))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def file_preview_full(self, name: str):
        return self._file_op(self.assistant.chat.files.preview, name)

    # ---- картинки ----
    def images_state(self):
        s = imagesmod.settings(self.cfg)
        dl = self._downloads.get("img:dl")
        inst = set(localimg.installed_ids())
        c = self.cfg.get("images", {}) or {}
        pl = imgpower.plan(self.cfg)
        hw = pl["hw"]
        local = []
        for m in localimg.CATALOG:
            f = imgpower.fit(m, hw)
            local.append({"id": m["id"], "title": m["title"], "size_gb": m["size_gb"], "desc": m["desc"], "installed": m["id"] in inst,
                          "selected": m["id"] == (s["local_model"] or localimg.DEFAULT_ID), "fit": f["level"], "fit_label": f["label"],
                          "eta": f["eta"], "recommended": f["recommended"], "plan": imgpower.describe(imgpower.plan(self.cfg, m), m)})
        return {"provider": s["provider"], "url": s["url"], "model": s["model"], "has_key": bool(s["key"]), "active": imagesmod.provider_name(self.cfg, "аниме"),
                "local": local, "local_ready": localimg.ready(), "local_model": s["local_model"] or localimg.DEFAULT_ID,
                "director": imgdirector.settings(self.cfg)["director"], "variants": imgdirector.settings(self.cfg)["variants"], "ask_count": imgdirector.settings(self.cfg)["ask_count"],
                "power": {"power": pl["power"], "quality": c.get("quality") if c.get("quality") in imgpower.QUALITIES else "auto", "vram_gb": float(c.get("vram_gb") or 0),
                          "threads": int(c.get("threads") or 0), "unload_llm": c.get("unload_llm") if c.get("unload_llm") in imgpower.UNLOAD_MODES else "auto",
                          "now": pl["label"], "auto": pl["auto"], "tier": pl["tier"], "unload_now": pl["unload_llm"]},
                "hw": {"gpu": hw.get("gpu", ""), "vram_gb": hw.get("vram_gb", 0), "detected": hw.get("detected", False), "integrated": hw.get("integrated", False),
                       "source": hw.get("source", "auto"), "ram_gb": hw.get("ram_gb", 0), "cpu": hw.get("cpu", 0), "gpus": hw.get("gpus", [])},
                "dl": dl and {"done": dl["done"], "total": dl["total"], "stage": dl["stage"], "id": dl["id"]}}

    def images_hw_refresh(self):
        """Заново определить видеокарту (например, после установки драйвера или другой карты) и забыть запомненные неудачи."""
        imgpower.hardware(self.cfg, refresh=True)
        localimg.forget_tuning()
        return self.images_state()

    def localimg_download(self, mid: str):
        if "img:dl" in self._downloads or not localimg.by_id(mid):
            return False
        cancel = threading.Event()
        self._downloads["img:dl"] = {"done": 0, "total": 0, "stage": "search", "cancel": cancel, "id": mid}

        def run():
            def prog(done, total, stage):
                self._downloads["img:dl"].update(done=done, total=total, stage=stage)
                self.emit({"type": "limg_progress", "id": mid, "done": done, "total": total, "stage": stage})
            err = ""
            try:
                localimg.download(mid, prog, cancel)
                self.cfg.update({"images": {"local_model": mid}})
            except localimg.Cancelled:
                err = "cancelled"
            except Exception as e:
                err = str(e) or e.__class__.__name__
            finally:
                self._downloads.pop("img:dl", None)
                self.emit({"type": "limg_done", "id": mid, "error": err})
        threading.Thread(target=run, daemon=True, name="localimg-download").start()
        return True

    def localimg_cancel(self):
        d = self._downloads.get("img:dl")
        if d:
            d["cancel"].set()
        return True

    def localimg_delete(self, mid: str):
        return localimg.delete(mid)

    def localimg_select(self, mid: str):
        if mid in localimg.installed_ids():
            self.cfg.update({"images": {"local_model": mid}})
        return self.images_state()

    def images_set(self, provider: str = "auto", url: str = "", model: str = "", key: str = "", director: bool = True, variants: int = 4, ask_count: bool = True,
                   power: str = "auto", quality: str = "auto", vram_gb: float = 0, threads: int = 0, unload_llm: str = "auto"):
        try:
            n = max(1, min(imgdirector.MAX_VARIANTS, int(variants)))
        except (TypeError, ValueError):
            n = imgdirector.DEFAULT_VARIANTS
        try:
            gb = max(0.0, min(256.0, float(vram_gb or 0)))
        except (TypeError, ValueError):
            gb = 0.0
        try:
            th = max(0, min(256, int(threads or 0)))
        except (TypeError, ValueError):
            th = 0
        patch = {"provider": provider if provider in imagesmod.PROVIDERS else "auto", "url": (url or "").strip()[:300], "model": (model or "").strip()[:120],
                 "director": bool(director), "variants": n, "ask_count": bool(ask_count),
                 "power": power if power in imgpower.POWER_MODES else "auto", "quality": quality if quality in imgpower.QUALITIES else "auto",
                 "vram_gb": gb, "threads": th, "unload_llm": unload_llm if unload_llm in imgpower.UNLOAD_MODES else "auto"}
        if key:
            patch["key"] = key.strip()[:300]          # пустое поле = «оставить ключ как был»
        old = self.cfg.get("images", {}) or {}
        if any(old.get(k) != patch[k] for k in ("power", "quality", "vram_gb")):
            localimg.forget_tuning()                  # настройки мощности поменялись — запомненные неудачи уже не актуальны
        self.cfg.update({"images": patch})
        return self.images_state()

    def images_clear_key(self):
        self.cfg.update({"images": {"key": ""}})
        return self.images_state()

    def images_test(self):
        """Проверка: нарисовать маленькую картинку. Результат придёт событием images_test."""
        def run():
            try:
                data, ext, who = imagesmod.generate(self.cfg, "a small red apple on a white table, simple, high quality", (256, 256))
                self.emit({"type": "images_test", "ok": True, "text": f"✔ {who}: картинка получена ({len(data) // 1024} КБ)"})
            except imagesmod.ImageError as e:
                self.emit({"type": "images_test", "ok": False, "text": f"✖ {e}"})
            except Exception as e:
                self.emit({"type": "images_test", "ok": False, "text": f"✖ {e.__class__.__name__}: {e}"})
        threading.Thread(target=run, daemon=True, name="images-test").start()
        return True

    def coder_open(self, project: str = ""):
        try:
            self.assistant.coder.open_folder(project)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
