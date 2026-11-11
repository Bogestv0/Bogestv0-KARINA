"""Ядро: микрофон → распознавание → имя → команда → голосовой ответ."""
from __future__ import annotations
import queue, threading, time, traceback
from . import actions, chat as chatmod, llm, net
from .brain import Brain
from .coder import CoderService
from .diag import trace
from .engine import Engine
from .actions import Ctx
from .apps import AppIndex
from .audio import Listener, format_status
from .stt import STT
from .tts import Speaker
from .textutil import norm
from .wake import split_wake

# Фиксированные ответы: озвучиваются заранее, чтобы без интернета звучали тем же голосом, что и онлайн.
STATIC_PHRASES = [
    "Готово.", "Хорошо.", "Пожалуйста!", "Слушаю.", "Я здесь!", "Хорошо, отменяю.", "Открываю чат.",
    "Следующий трек.", "Предыдущий трек.", "Свернула.", "Развернула.", "Закрываю окно.", "Переключаю.",
    "Блокирую.", "Выполняю.", "Напечатала.", "Звук выключен.", "Звук включён.", "Погромче.", "Потише.",
    "Скриншот сохранён в папку Изображения, Карина.",
    "Нет интернета, поиск сейчас недоступен.",
    "Ухожу в режим сна. Скажите «проснись», когда понадоблюсь.",
    "Не поняла команду. Скажите «что ты умеешь», чтобы услышать список.",
    "Модель не выбрана. Откройте вкладку «Модели», скачайте и выберите одну.",
    "Я ещё отвечаю на предыдущий вопрос.", "Хорошо, поищу в интернете.", "Хорошо, подумаю.",
    "Открываю папку с файлами.", "Не получилось открыть папку с файлами.",
    "Готово, файл сохранён в папке Карина в документах.", "Готово, файлы сохранены в папке Карина в документах.",
    "Готово, смотрите в окне чата.",
    "Доброе утро! Чем помочь?", "Добрый день! Чем помочь?", "Добрый вечер! Чем помочь?", "Привет! Чем помочь?",
] + [f"Громкость {v}." for v in range(0, 101, 10)] + [f"Громкость {v} процентов." for v in range(0, 101, 10)]


GOOD_CONF = 0.70       # от такой уверенности модели распознавание считаем хорошим и помощь остальных микрофонов не нужна

YES = {"да", "давай", "подтверждаю", "конечно", "ага", "выполняй", "точно", "да да"}
NO = {"нет", "не надо", "отмена", "отменяю", "стоп", "не нужно"}


class Assistant:
    def __init__(self, cfg, emit):
        self.cfg, self.emit = cfg, emit
        self.apps = AppIndex(cfg)
        self.engine = Engine(cfg, emit)
        self.brain = Brain(cfg, self.engine)
        self.coder = CoderService(cfg, self.brain, emit)
        self.chat = chatmod.ChatService(cfg, self.engine, emit, speak=self.say, brain=self.brain, coder=self.coder)
        self.stt, self.tts = STT(cfg), Speaker(cfg)
        self.listener = Listener(cfg, self._on_audio, self._on_level, self._on_mics)
        self.mic_status: list = []
        self._levels: dict = {}
        self._lvl_emit = 0.0
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
        self.warm_voice()

    def phrases_to_cache(self) -> list[str]:
        """Что стоит озвучить заранее: стандартные ответы, ответы своих команд, «Открываю/Запускаю/Закрываю <программа>»."""
        out = list(STATIC_PHRASES) + [f"Привет! Меня зовут {self.cfg['name']}. Я вас слышу."]
        out += [str(c.get("reply")) for c in self.cfg.get("commands", []) if c.get("reply")]
        try:
            from .apps import SITES
            out += [f"Открываю {k}." for k in SITES]
            for e in self.apps.entries():
                n = e.get("name")
                if n:
                    out += [f"Открываю {n}.", f"Запускаю {n}.", f"Закрываю {n}."]
        except Exception:
            pass
        return out

    def warm_voice(self):
        """Фоновая подготовка записей выбранного голоса (если есть интернет); вызывается при старте и смене голоса."""
        try:
            self.tts.prewarm(self.phrases_to_cache())
        except Exception as e:
            trace(f"кэш голоса: {e}")

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
                "name": self.cfg["name"], "plugin_errors": self.plugin_errors, "llm": bool(self.cfg["llm"].get("enabled") and (self.cfg["llm"].get("api_key"))),
                "brain": self.brain.mode, "mics": self.mic_status, "mics_text": format_status(self.mic_status),
                "out_warning": self.tts.out_warning}

    def _worker(self):
        # 1) модель
        t0 = time.time()
        try:
            trace("Распознавание речи: загружаю модель " + str(self.cfg["stt"].get("model")))
            self._set_state("loading", "Загружаю модель распознавания… при первом запуске нужен интернет (один раз)")
            trace("Распознавание речи: окно уведомлено, начинаю проверку модели")
            self.stt.load(lambda mb: self._set_state("loading", f"Скачиваю модель распознавания: {mb} МБ (один раз, около 460 МБ для «Хорошей»)"))
            trace(f"Распознавание речи: модель готова за {time.time() - t0:.1f} с")
        except Exception as e:
            traceback.print_exc()
            self.log("error", f"Не удалось загрузить модель распознавания: {e}")
            self._set_state("error", "Модель не загружена")
        # 2) микрофон
        try:
            self.listener.start()
            trace("Микрофоны: слушаю — " + format_status(self.mic_status))
            try:
                import faulthandler
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
            if self.stt.model is not None:
                self._set_state("idle")
        except Exception as e:
            self.log("error", f"Микрофон недоступен: {e}")
            self._set_state("error", "Нет микрофона")
        # 3) цикл
        while not self._stop.is_set():
            try:
                group = self.q.get(timeout=0.4)
            except queue.Empty:
                continue
            if not self.listening or self.stt.model is None:
                continue
            try:
                self._set_state("thinking")
                text = self._recognize(group)
                if text:
                    self.handle(text)
            except Exception as e:
                traceback.print_exc()
                self.log("error", f"Ошибка обработки: {e}")
            finally:
                self._set_state("idle")

    def _on_audio(self, group):
        """Фраза от микрофонов: список записей одной и той же речи (лучшая первой)."""
        try:
            self.q.put_nowait(group)
        except queue.Full:
            pass

    # ---------- несколько микрофонов ----------
    def _on_level(self, slot: int, v: float):
        self._levels[slot] = v
        now = time.time()
        if now - self._lvl_emit < 0.07:
            return
        self._lvl_emit = now
        self.emit({"type": "level", "v": max(self._levels.values()), "mics": {str(k): round(x, 3) for k, x in self._levels.items()}})

    def _on_mics(self, statuses: list):
        """Состояние микрофонов изменилось (открылся, пропал, вернулся)."""
        old = {s["slot"]: s["state"] for s in self.mic_status}
        self.mic_status = statuses
        self._levels = {k: v for k, v in self._levels.items() if any(s["slot"] == k and s["state"] == "ok" for s in statuses)}
        self.emit({"type": "mics", "mics": statuses})
        for s in statuses:
            if s["state"] in ("error", "missing", "dup", "unset") and old.get(s["slot"]) != s["state"]:
                self.log("error", f"Микрофон {s['slot'] + 1}: {s['text']}")
            elif s["state"] == "ok" and old.get(s["slot"]) in ("error", "missing"):
                self.log("info", f"Микрофон {s['slot'] + 1} снова работает.")
        if self.state == "error" and self.stt.model is not None and any(s["state"] == "ok" for s in statuses):
            self._set_state("idle")

    def _addressed_ok(self, text: str) -> bool:
        """Фраза годится для команды: обращение по имени (или оно не нужно)."""
        if not self.cfg["wake_required"] or time.time() < self.followup_until:
            return True
        return split_wake(text, self.cfg.wake_names())[0]

    def _recognize(self, group) -> str:
        """Лучший текст из записей одной фразы. Один микрофон — просто распознаём. Несколько — режим stt.assist:
        smart: сначала самый чистый микрофон; остальные подключаются, только если он расслышал плохо или пропустил имя;
        all: распознаём со всех и берём лучший; best: самый чистый, остальные — запасные, если он ничего не расслышал."""
        if not isinstance(group, (list, tuple)):          # на случай прямой передачи звука
            return self.stt.transcribe(group)
        if len(group) == 1:
            return self.stt.transcribe(group[0].audio)
        mode = self.cfg["stt"].get("assist", "smart")
        best = None          # (оценка, текст, номер микрофона, уверенность)
        tried = 0
        for u in group:
            text, conf = self.stt.transcribe_ex(u.audio)
            tried += 1
            if not text:
                continue
            ok = self._addressed_ok(text)
            score = conf + (0.25 if ok else 0.0) + min(len(text.split()), 20) * 0.002
            if best is None or score > best[0]:
                best = (score, text, u.slot, conf)
            if mode in ("smart", "best") and (mode == "best" or (conf >= GOOD_CONF and ok)):
                break
        if best is None:
            return ""
        trace(f"Микрофоны: фраза на {len(group)} микр., распознано с {tried}; выбран №{best[2] + 1} (уверенность {best[3]:.2f}): {best[1]}")
        self.emit({"type": "mic_pick", "slot": best[2]})
        return best[1]

    # ---------- управление ----------
    def set_listening(self, on: bool):
        self.listening = on
        self.listener.muted = not on
        self._set_state("idle" if on else "paused")

    def reload_audio(self):
        try:
            self.listener.start()
            if self.state == "error" and self.stt.model is not None:
                self._set_state("idle")
        except Exception as e:
            self.log("error", f"Микрофон недоступен: {e}")
            self._set_state("error", "Нет микрофона")

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
        if chatmod.wants_open_files(rest):
            try:
                self.chat.files.open_folder()
                self.say("Открываю папку с файлами.")
            except Exception as e:
                self.log("error", f"Не удалось открыть папку: {e}")
                self.say("Не получилось открыть папку с файлами.")
            return
        if chatmod.is_chat_request(rest):
            self._to_chat(rest)
            return
        ctx = Ctx(self.cfg, self.apps, self, raw=text)
        handled, reply = actions.dispatch(ctx, rest)
        if not handled and self.brain.usable():
            self._to_chat(rest)          # свободная фраза — отвечает выбранный «мозг»
            return
        if not handled and self.brain.mode == "claude":
            self.say(self.brain.unavailable_reason())       # человек выбрал Claude — говорим сразу, почему он молчит
            return
        if not handled:
            handled, reply = self._try_llm(ctx, rest)
        if not handled:
            reply = "Не поняла команду. Скажите «что ты умеешь», чтобы услышать список."
        self.say(reply)

    def _to_chat(self, text: str):
        """Голосовой вопрос: коротко отвечаем голосом, подробности показываем в окне «Чат»."""
        why = self.chat.preflight(text)
        if why:
            self.say(why)                # выбранный мозг недоступен — говорим об этом сразу
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
