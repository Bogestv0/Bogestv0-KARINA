"""Ядро: микрофон → распознавание → имя → команда → голосовой ответ."""
from __future__ import annotations
import queue, threading, time, traceback
from . import actions, chat as chatmod, llm, models, net
from .diag import trace
from .engine import Engine
from .actions import Ctx
from .apps import AppIndex
from .audio import Listener
from .stt import STT
from .tts import Speaker
from .textutil import norm
from .wake import split_wake

YES = {"да", "давай", "подтверждаю", "конечно", "ага", "выполняй", "точно", "да да"}
NO = {"нет", "не надо", "отмена", "отменяю", "стоп", "не нужно"}


class Assistant:
    def __init__(self, cfg, emit):
        self.cfg, self.emit = cfg, emit
        self.apps = AppIndex(cfg)
        self.engine = Engine(cfg, emit)
        self.chat = chatmod.ChatService(cfg, self.engine, emit, speak=self.say)
        self.stt, self.tts = STT(cfg), Speaker(cfg)
        self.listener = Listener(cfg, self._on_audio, lambda v: self.emit({"type": "level", "v": v}))
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self.state = "loading"
        self.sleeping = False
        self.listening = True
        self.followup_until = 0.0
        self._pending = None            # (действие, до какого времени ждём «да»)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.plugin_errors = actions.load_plugins()

    # ---------- жизненный цикл ----------
    def start(self):
        self._threads = [threading.Thread(target=self._worker, daemon=True, name="karina-worker")]
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop.set()
        self.tts.stop()
        self.listener.stop()
        self.chat.stop()
        self.engine.stop()

    def _set_state(self, s: str, detail: str = ""):
        self.state = s
        self.emit({"type": "state", "state": s, "detail": detail, "sleeping": self.sleeping, "listening": self.listening})

    def log(self, kind: str, text: str):
        if kind == "error":
            trace("ОШИБКА: " + text)
        self.emit({"type": "log", "kind": kind, "text": text, "ts": time.strftime("%H:%M:%S")})

    def status(self) -> dict:
        return {"online": net.is_online(), "model": self.cfg["stt"]["model"], "voice": self.tts.engine_used or self.cfg["tts"]["engine"],
                "tts_warning": self.tts.warning, "state": self.state, "sleeping": self.sleeping, "listening": self.listening,
                "name": self.cfg["name"], "plugin_errors": self.plugin_errors, "llm": bool(self.cfg["llm"].get("enabled") and (self.cfg["llm"].get("api_key"))) }

    def _worker(self):
        # 1) модель
        t0 = time.time()
        try:
            trace("Распознавание речи: загружаю модель " + str(self.cfg["stt"].get("model")))
            self._set_state("loading", "Загружаю модель распознавания… при первом запуске нужен интернет (один раз)")
            self.stt.load(lambda mb: self._set_state("loading", f"Скачиваю модель распознавания: {mb} МБ (один раз, около 460 МБ для «Хорошей»)"))
            trace(f"Распознавание речи: модель готова за {time.time() - t0:.1f} с")
        except Exception as e:
            traceback.print_exc()
            self.log("error", f"Не удалось загрузить модель распознавания: {e}")
            self._set_state("error", "Модель не загружена")
        # 2) микрофон
        try:
            self.listener.start()
            trace("Микрофон: слушаю")
            if self.stt.model is not None:
                self._set_state("idle")
        except Exception as e:
            self.log("error", f"Микрофон недоступен: {e}")
            self._set_state("error", "Нет микрофона")
        # 3) цикл
        while not self._stop.is_set():
            try:
                audio = self.q.get(timeout=0.4)
            except queue.Empty:
                continue
            if not self.listening or self.stt.model is None:
                continue
            try:
                self._set_state("thinking")
                text = self.stt.transcribe(audio)
                if text:
                    self.handle(text)
            except Exception as e:
                traceback.print_exc()
                self.log("error", f"Ошибка обработки: {e}")
            finally:
                self._set_state("idle")

    def _on_audio(self, audio):
        try:
            self.q.put_nowait(audio)
        except queue.Full:
            pass

    # ---------- управление ----------
    def set_listening(self, on: bool):
        self.listening = on
        self.listener.muted = not on
        self._set_state("idle" if on else "paused")

    def reload_audio(self):
        try:
            self.listener.start()
        except Exception as e:
            self.log("error", f"Микрофон недоступен: {e}")

    def reload_model(self):
        def run():
            self._set_state("loading", "Загружаю модель…")
            try:
                self.stt.load()
                self._set_state("idle")
            except Exception as e:
                self.log("error", f"Не удалось загрузить модель: {e}")
                self._set_state("error", "Модель не загружена")
        threading.Thread(target=run, daemon=True).start()

    def ask_confirm(self, action):
        self._pending = (action, time.time() + 12)

    # ---------- обработка фразы ----------
    def handle(self, text: str, direct: bool = False):
        """direct=True — введено вручную в окне: имя не требуется."""
        now = time.time()
        names = self.cfg.wake_names()
        addressed, rest = (True, norm(text)) if direct else split_wake(text, names)

        if self._pending:
            action, until = self._pending
            if now < until and (addressed or direct or now < self.followup_until):
                w = rest.strip()
                if w in YES or w.startswith("да "):
                    self._pending = None
                    self.log("you", text)
                    self.say(action())
                    return
                if w in NO:
                    self._pending = None
                    self.log("you", text)
                    self.say("Хорошо, отменяю.")
                    return
            elif now >= until:
                self._pending = None

        if self.sleeping:
            if addressed and rest.startswith(("проснись", "просыпайся", "вернись", "подъем")):
                self.sleeping = False
                self.log("you", text)
                self.followup_until = now + float(self.cfg["followup_seconds"])
                self.say("Я здесь!")
            else:
                self.log("ignored", text)
            return

        in_followup = now < self.followup_until
        if not (addressed or direct or in_followup or not self.cfg["wake_required"]):
            self.log("ignored", text)     # слышала, но обращались не ко мне
            return

        self.log("you", text)
        self.followup_until = now + float(self.cfg["followup_seconds"])
        if not rest:
            self.say("Слушаю.")
            return
        if chatmod.wants_open_chat(rest):
            self.emit({"type": "goto", "tab": "chat"})
            self.say("Открываю чат.")
            return
        if chatmod.is_chat_request(rest):
            self._to_chat(rest)
            return
        ctx = Ctx(self.cfg, self.apps, self, raw=text)
        handled, reply = actions.dispatch(ctx, rest)
        if not handled and self._local_ready():
            self._to_chat(rest)          # свободная фраза — отвечает встроенная модель
            return
        if not handled:
            handled, reply = self._try_llm(ctx, rest)
        if not handled:
            reply = "Не поняла команду. Скажите «что ты умеешь», чтобы услышать список."
        self.say(reply)

    def _local_ready(self) -> bool:
        mid = (self.cfg.get("models", {}) or {}).get("active", "")
        return bool(mid and models.local_path(mid))

    def _to_chat(self, text: str):
        """Голосовой вопрос: коротко отвечаем голосом, подробности показываем в окне «Чат»."""
        if not self._local_ready():
            self.say("Модель не выбрана. Откройте вкладку «Модели», скачайте и выберите одну.")
            return
        if self.chat.busy:
            self.say("Я ещё отвечаю на предыдущий вопрос.")
            return
        cid = self.chat.voice_chat_id()
        self.emit({"type": "chat_open", "chat": cid})
        self.say("Хорошо, поищу в интернете." if self.chat.will_search(text) else "Хорошо, подумаю.")
        self.chat.send(cid, text, voice=True)

    def _try_llm(self, ctx: Ctx, text: str):
        if not net.is_online():
            return False, ""
        names = [e["name"] for e in self.apps.entries()]
        self._set_state("thinking", "Думаю…")
        r = llm.ask(self.cfg, text, names)
        if not r:
            return False, ""
        if r.get("action"):
            return actions.run_by_name(ctx, str(r["action"]), r.get("args") or {})
        if r.get("say"):
            return True, str(r["say"])
        return False, ""

    def say(self, text: str):
        if not text:
            return
        self.log("karina", text)
        self.listener.muted = True       # не слушаем самих себя
        self._set_state("speaking")
        try:
            self.tts.speak(text)
        except Exception as e:
            self.log("error", f"Не удалось озвучить: {e}")
        finally:
            time.sleep(0.25)
            self.listener.muted = not self.listening
            self.followup_until = max(self.followup_until, time.time() + float(self.cfg["followup_seconds"]))
            self._set_state("idle")
