"""Ядро: микрофон → распознавание → имя → команда → голосовой ответ."""
from __future__ import annotations
import queue, re, sys, threading, time, traceback
from . import actions, chat as chatmod, llm, net
from . import integrations, music, personal, profile as prof  # noqa: F401  (регистрируют команды микшера, VPN, музыки, погоды и профиля)
from . import memory as memorymod
from . import dialog
from . import rude as rudemod
from .mixer_remote import MixerRemote
from .vpn_link import VpnLink
from .tunnel import Tunnel
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
from .hotkeys import Hotkeys

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
    "Готово, картинка сохранена в папке Карина в документах.", "Готово, нарисовала несколько вариантов, смотрите в окне чата.",
    "Готово, смотрите в окне чата.",
    "Включила впн.", "Выключила впн.", "Впн уже включён.", "Впн уже выключен.", "Впн включён.", "Впн выключен.",
    "Доброе утро! Чем помочь?", "Добрый день! Чем помочь?", "Добрый вечер! Чем помочь?", "Привет! Чем помочь?",
] + [f"Громкость {v}." for v in range(0, 101, 10)] + [f"Громкость {v} процентов." for v in range(0, 101, 10)]


GOOD_CONF = 0.70       # от такой уверенности модели распознавание считаем хорошим и помощь остальных микрофонов не нужна

YES, NO = dialog.YES, dialog.NO | dialog.STOP
DIALOG_WAIT = 30.0     # сколько секунд ждём «да/нет» (или ответ на уточнение) ПОСЛЕ того, как Карина договорила вопрос
REPROMPTS = 2          # сколько раз переспросить, если ответ не похож ни на «да», ни на «нет», ни на «стоп»


# «Замолчи» посреди её речи: фраза целиком из этих слов, и хотя бы одно — из первой группы
STOP_CORE = {"стоп", "хватит", "замолчи", "помолчи", "молчи", "тихо", "достаточно", "стой", "перестань", "прекрати", "довольно", "отмена"}
STOP_FILL = {"ладно", "все", "уже", "ну", "пожалуйста", "хорош", "хорошо", "карина", "эй", "так", "давай"}


def is_stop_phrase(text: str, names: list[str]) -> bool:
    """«Стоп», «Карина, хватит», «ладно, замолчи» — да; «останови музыку», «хватит ли места» — нет (команды не трогаем, только её речь)."""
    _, rest = split_wake(text, names)
    words = norm(rest or text).split()
    return 0 < len(words) <= 3 and any(w in STOP_CORE for w in words) and all(w in STOP_CORE | STOP_FILL for w in words)


class Assistant:
    _say_lock = threading.Lock()        # одна речь за раз (общий на процесс: Карина одна)
    _say_gen = 0
    _repeats = None
    _last_talk = 0.0

    def __init__(self, cfg, emit):
        self.cfg, self.emit = cfg, emit
        self.apps = AppIndex(cfg)
        self.engine = Engine(cfg, emit)
        self.brain = Brain(cfg, self.engine)
        self.coder = CoderService(cfg, self.brain, emit)
        self.chat = chatmod.ChatService(cfg, self.engine, emit, speak=self.say, brain=self.brain, coder=self.coder)
        if cfg["tts"].get("voice") != "karina":
            cfg.update({"tts": {"voice": "karina"}})     # единственный голос — Карина (Silero «Байя»); старые значения вроде silero_xenia приводим к нему
        self.stt, self.tts = STT(cfg), Speaker(cfg)
        self.mixer = MixerRemote(cfg)                       # «сделай тише музыку»: связь с Bogestv0 MIXER
        self.vpn = VpnLink(cfg, log=lambda m: trace(m))     # «включи впн»: расширение Bogestv0 VPN подключается к нам
        self.tunnel = Tunnel(cfg, cfg.path.parent, log=lambda m: trace(m))     # свой VPN/прокси на sing-box: «включи впн», «включи прокси»
        self.listener = Listener(cfg, self._on_audio, self._on_level, self._on_mics)
        self.listener.on_barge = self._on_barge
        self.memory = memorymod.get(cfg)                    # чему Карина научилась: выбранные приложения, узнаваемые сразу фразы
        self.bq: queue.Queue = queue.Queue(maxsize=2)       # короткие фразы поверх речи Карины («стоп»)
        self.hotkeys = Hotkeys(cfg, {"talk": self.push_to_talk, "stop": self.hush}, trace)
        self.mic_status: list = []
        self._levels: dict = {}
        self._lvl_emit = 0.0
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self.state = "loading"
        self.sleeping = False
        self.listening = True
        self.followup_until = 0.0
        self._conf = None
        self._pending = None            # {"action", "on_no", "until", "tries"}: ждём «да/нет/стоп»; until=None, пока вопрос ещё звучит (отсчёт — после речи)
        self._choice = None             # {"fn", "until", "tries", "max"}: ждём ответ на уточнение: «что сделать тише?» → «музыку», шаги мастера и т. д.
        self._repeats = rudemod.RepeatGuard()
        self._last_talk = 0.0
        self._say_gen = 0                   # растёт при «замолчи»: реплики, стоявшие в очереди на озвучку, пропадают
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.plugin_errors = actions.load_plugins()

    # ---------- жизненный цикл ----------
    def start(self):
        self._threads = [threading.Thread(target=self._worker, daemon=True, name="karina-worker")]
        for t in self._threads:
            t.start()
        self.vpn.start()
        self.tunnel.recover()
        chatmod.PROFILE_PROVIDER = lambda: prof.prompt_section(self.cfg)
        chatmod.PERSONA_PROVIDER = lambda: (chatmod.prompts.PERSONA_RUDE if (self.cfg.get("persona") or {}).get("rude") else "")
        t = threading.Thread(target=self._birthday_loop, daemon=True, name="karina-birthday")
        t.start()
        self._threads.append(t)
        self.warm_voice()
        t = threading.Thread(target=self._voice_setup, daemon=True, name="karina-silero-setup")
        t.start()
        self._threads.append(t)
        t = threading.Thread(target=self._barge_worker, daemon=True, name="karina-barge")
        t.start()
        self._threads.append(t)
        self.hotkeys.start()

    def _voice_setup(self):
        """Естественный голос без возни: если есть torch — один раз переключаем старую настройку на Silero и докачиваем модель в фоне."""
        try:
            time.sleep(3)
            from . import silero_voice
            silero_voice.apply_pending(self.cfg.path.parent)        # обновление голоса, которое не удалось подставить при работе, подставляем при запуске
            t = self.cfg["tts"]
            if not t.get("auto_silero", True) or not silero_voice.torch_available():
                if t.get("auto_silero", True):
                    trace("Silero: torch не найден — " + silero_voice.torch_hint())
                return
            if silero_voice.is_ready(self.cfg.path.parent):
                silero_voice.preload(self.cfg.path.parent)
                return
            while not self._stop.is_set() and not net.is_online():
                self._stop.wait(20)
            if not self._stop.is_set():
                silero_voice.ensure_model(self.cfg.path.parent, trace, self.emit, cancel=self._stop)
        except Exception as e:
            trace(f"Silero: автонастройка не удалась: {e}")

    def _birthday_loop(self):
        """Раз в минуту проверяет, не пора ли поздравить (день рождения владельца или питомца). Один раз за день."""
        time.sleep(8)                                       # дать приложению запуститься
        while not self._stop.is_set():
            try:
                g = prof.due_greeting(self.cfg)
                if g and self.cfg["tts"].get("enabled", True) and not self.sleeping:
                    prof.mark_greeted(self.cfg)
                    self.say(g)
                elif g:
                    prof.mark_greeted(self.cfg)
                    self.log("karina", g)
            except Exception as e:
                trace(f"поздравление: {e}")
            self._stop.wait(60)

    def warm_voice(self):
        """Заранее загружает модель голоса, чтобы первая фраза не ждала; вызывается при старте и смене скорости."""
        try:
            from . import silero_voice
            if silero_voice.is_ready(self.cfg.path.parent):
                silero_voice.preload(self.cfg.path.parent)
        except Exception as e:
            trace(f"голос: {e}")

    def stop(self):
        self._stop.set()
        self.hotkeys.stop()
        self.tts.stop()
        self.listener.stop()
        self.chat.stop()
        self.engine.stop()
        self.vpn.stop()
        self.tunnel.stop()
        self.mixer.close()

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
            trace(f"Распознавание речи: модель готова за {time.time() - t0:.1f} с ({'видеокарта' if self.stt.device == 'cuda' else 'процессор'})")
            self.stt.ensure_prefilter()
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
                self._expire_dialogs()
                continue
            if not self.listening or self.stt.model is None:
                continue
            try:
                if self._prefilter_reject(group):
                    continue
                self._set_state("thinking")
                text = self._recognize(group)
                if text and not self._is_background(text):
                    self.handle(text)
            except Exception as e:
                traceback.print_exc()
                self.log("error", f"Ошибка обработки: {e}")
            finally:
                self._set_state("idle")

    def _prefilter_reject(self, group) -> bool:
        """Быстрая проверка (stt.prefilter): в записи точно нет обращения по имени → полное распознавание не запускаем. Без имени команды не нужны,
        поэтому не мешаем ответам «да/нет» и фразам в окне после обращения."""
        c = self.cfg
        if not c["stt"].get("prefilter") or not c["wake_required"] or time.time() < self.followup_until or self._pending or self._choice:
            return False
        audio = group[0].audio if isinstance(group, (list, tuple)) and group and hasattr(group[0], "audio") else group
        try:
            return self.stt.has_name(audio, c.wake_names()) is False
        except Exception:
            return False

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

    def _is_background(self, text: str) -> bool:
        """True — это, скорее всего, фон (телевизор, чужой разговор, шум), а не команда: фразу пропускаем.
        • неуверенное распознавание: с именем нужна уверенность stt.min_conf, без имени (окно «слушаю дальше») — stt.followup_conf;
        • без имени принимаем только короткие фразы (followup_max_words): длинная речь из фона — не команда."""
        st = self.cfg["stt"]
        now = time.time()
        addressed, rest = split_wake(text, self.cfg.wake_names(), int(self.cfg.get("wake_tail_words", 7)))
        answering = bool(self._pending or self._choice)           # ответ «да/нет» или выбор на мой вопрос — короткий, имя не нужно
        free = (not addressed) and (now < self.followup_until or not self.cfg["wake_required"] or answering)
        if not addressed and not free:
            return False                                           # обращения нет — handle() сам запишет «слышала, но не мне»
        need = float(st.get("followup_conf", 0.55)) if (free and not answering) else float(st.get("min_conf", 0.42))
        conf = getattr(self, "_conf", None)
        if conf is not None and conf < need:
            self.log("ignored", f"{text} (нечётко, уверенность {conf:.2f})")
            return True
        if free and not answering and self.cfg["wake_required"] and len(rest.split()) > int(self.cfg.get("followup_max_words", 10)):
            self.log("ignored", f"{text} (длинная фраза без обращения)")
            return True
        return False

    def _addressed_ok(self, text: str) -> bool:
        """Фраза годится для команды: обращение по имени (или оно не нужно)."""
        if not self.cfg["wake_required"] or time.time() < self.followup_until:
            return True
        return split_wake(text, self.cfg.wake_names(), int(self.cfg.get("wake_tail_words", 7)))[0]

    def _recognize(self, group) -> str:
        """Лучший текст из записей одной фразы. Один микрофон — просто распознаём. Несколько — режим stt.assist:
        smart: сначала самый чистый микрофон; остальные подключаются, только если он расслышал плохо или пропустил имя;
        all: распознаём со всех и берём лучший; best: самый чистый, остальные — запасные, если он ничего не расслышал."""
        self._conf = None
        if not isinstance(group, (list, tuple)):          # на случай прямой передачи звука
            text, self._conf = self.stt.transcribe_ex(group)
            return text
        if len(group) == 1:
            text, self._conf = self.stt.transcribe_ex(group[0].audio)
            return text
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
        self._conf = best[3]
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

    # ---------- вопросы к человеку ----------
    @property
    def rude(self) -> bool:
        return bool((self.cfg.get("persona") or {}).get("rude"))

    def ask_confirm(self, action, on_no=None):
        """Задаёт вопрос «да или нет»: слушаем (имя «Карина» не нужно), пока не услышим «да», «нет» или «стоп» — отсчёт времени начинается,
        когда Карина договорила вопрос. «Да» → action() (возвращает фразу для озвучки; может задать следующий вопрос), «нет» и «стоп» → on_no() или «Хорошо, отменяю»."""
        self._pending = {"action": action, "on_no": on_no, "until": None, "tries": 0}

    def ask_choice(self, handler, tries: int = REPROMPTS):
        """Следующая фраза (имя «Карина» не нужно) — ответ на мой вопрос. handler(текст) → (ответ, дальше): дальше=True — спросить то же ещё раз
        (не больше tries раз подряд), дальше=функция — следующий шаг диалога, иначе (False) — диалог окончен. Отсчёт времени — после речи."""
        self._choice = {"fn": handler, "until": None, "tries": 0, "max": tries}

    @staticmethod
    def _alive(d) -> bool:
        return bool(d) and (d["until"] is None or time.time() < d["until"])

    def _arm_dialogs(self):
        """Карина договорила: теперь у ответа есть DIALOG_WAIT секунд, имя говорить не нужно."""
        now = time.time()
        for d in (self._pending, self._choice):
            if d and d["until"] is None:
                d["until"] = now + DIALOG_WAIT
        if self._pending or self._choice:
            self.followup_until = max(self.followup_until, now + DIALOG_WAIT)

    def _expire_dialogs(self):
        """Не дождались ответа: забываем вопрос и говорим об этом, чтобы человек не гадал, что происходит."""
        dead = [n for n, d in (("_pending", self._pending), ("_choice", self._choice)) if d and not self._alive(d)]
        if not dead:
            return
        for n in dead:
            setattr(self, n, None)
        if not (self._pending or self._choice):
            self.say(rudemod.pick("no_answer") if self.rude else "Не услышала ответа, отменяю.")

    def cancel_dialogs(self):
        self._pending = self._choice = None

    # ---------- обработка фразы ----------
    def handle(self, text: str, direct: bool = False):
        """direct=True — введено вручную в окне: имя не требуется."""
        now = time.time()
        names = self.cfg.wake_names()
        addressed, rest = (True, norm(text)) if direct else split_wake(text, names, int(self.cfg.get("wake_tail_words", 7)))

        if self._pending and not self._alive(self._pending):
            self._pending = None
        if self._pending:
            d = self._pending
            kind = dialog.classify(rest)
            if kind:
                self._pending = None
                self.log("you", text)
                if kind == "yes":
                    self.say(self._run_dialog_action(d["action"]))
                else:
                    # «нет» → on_no (например, ответить без интернета); «стоп» — всегда просто отмена
                    self.say(d["on_no"]() if (d.get("on_no") and kind == "no") else (rudemod.pick("cancel") if self.rude else "Хорошо, отменяю."))
                return
            if addressed and rest:
                self._pending = None            # обратились по имени с другой командой — вопрос больше не нужен, разбираем новую команду
            elif len(rest.split()) > dialog.MAX_WORDS:
                self.log("ignored", text + " (длинная фраза, это не ответ)")
                return
            else:
                d["tries"] += 1
                self.log("you", text)
                if d["tries"] <= REPROMPTS:
                    self.say(rudemod.pick("reprompt") if self.rude else "Не поняла. Скажите «да» или «нет». Чтобы отменить, скажите «стоп».")
                else:
                    self.log("info", "Жду «да» или «нет» (или «стоп»).")
                return

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
        prev_talk, self._last_talk = self._last_talk, now
        self.followup_until = now + float(self.cfg["followup_seconds"])
        if not rest:
            self.say(self._wake_reply(now, prev_talk))
            return
        if self._choice and not self._alive(self._choice):
            self._choice = None
        if self._choice:
            c = self._choice
            self._choice = None
            if dialog.classify(rest) == "stop":
                self.say(rudemod.pick("cancel") if self.rude else "Хорошо, отменяю.")
                return
            try:
                reply, again = c["fn"](rest)
            except Exception as e:
                traceback.print_exc()
                self.log("error", f"Ответ на уточнение не обработан: {e}")
                reply, again = "Не получилось.", False
            if reply is None:
                pass                                                   # это не ответ на мой вопрос, а новая команда — разбираем как обычно (ниже)
            else:
                if callable(again):
                    self.ask_choice(again, c["max"])                   # следующий шаг диалога
                elif again and c["tries"] < c["max"] and not self._choice:
                    self._choice = {"fn": c["fn"], "until": None, "tries": c["tries"] + 1, "max": c["max"]}     # переспрашиваем то же самое
                self.say(reply)
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
        handled, reply = self._recall(ctx, rest)         # знакомая фраза — выполняем сразу, без разбора
        if not handled:
            before = (self._pending, self._choice)
            handled, reply = actions.dispatch(ctx, rest)
            self._learn(ctx, rest, handled, reply, before)
        if not handled and self.brain.usable():
            self._to_chat(rest)          # свободная фраза — отвечает выбранный «мозг»
            return
        if not handled and self.brain.mode == "claude":
            self.say(self.brain.unavailable_reason())       # человек выбрал Claude — говорим сразу, почему он молчит
            return
        if not handled:
            handled, reply = self._try_llm(ctx, rest)
        if not handled:
            reply = rudemod.pick("not_understood") if self.rude else "Не поняла команду. Скажите «что ты умеешь», чтобы услышать список."
        elif self.rude and not (self._pending or self._choice):
            reply = rudemod.flavor(reply)                  # дерзкий режим: к обычному ответу добавляем недовольство
        self.say(reply)

    # ---------- дерзкий режим: болтовня и повторные вопросы ----------
    def _wake_reply(self, now: float, prev_talk: float) -> str:
        """Одно имя без команды: «Слушаю.» (в дерзком режиме после долгого молчания — сама спрашивает, как дела, и разговор продолжается)."""
        if not self.rude:
            return "Слушаю."
        if now - prev_talk > 1200:
            self.start_rude_chat()
            return rudemod.pick("wake_greet", name=(self.cfg.get("profile") or {}).get("name") or "дорогой")
        return rudemod.pick("listening")

    def _rude_handler(self, depth: int):
        def handler(text: str):
            reply, cont = rudemod.chat_turn(text, depth)
            return reply, (self._rude_handler(depth + 1) if cont else False)
        return handler

    def start_rude_chat(self):
        """Дерзкая Карина сама ведёт разговор: после её вопроса «как дела» слушаем ответ без имени и отвечаем по смыслу (не больше трёх реплик)."""
        self.ask_choice(self._rude_handler(0), tries=0)

    def repeat_gate(self, text: str):
        """Дерзкий режим: тот же вопрос в который раз. → ("ok"|"again"|"refuse", реплика). Первый раз — отвечаем, второй — с руганью «ещё раз и всё»,
        третий и дальше — отказ отвечать."""
        if not self.rude:
            return "ok", ""
        if self._repeats is None:
            self._repeats = rudemod.RepeatGuard()
        n = self._repeats.hit(text)
        r = rudemod.repeat_reply(n)
        return ("ok", "") if r is None else ("again", r) if n == 2 else ("refuse", r)

    def _to_chat(self, text: str):
        """Голосовой вопрос: коротко отвечаем голосом, подробности показываем в окне «Чат»."""
        why = self.chat.preflight(text)
        if why:
            self.say(why)                # выбранный мозг недоступен — говорим об этом сразу
            return
        if self.chat.busy:
            self.say(rudemod.pick("busy") if self.rude else "Я ещё отвечаю на предыдущий вопрос.")
            return
        gate, said = self.repeat_gate(text)            # дерзкий режим: тот же вопрос по третьему разу — не отвечаем
        if gate == "refuse":
            self.say(said)
            return
        if gate == "again":
            self.say(said)
        if self.chat.will_search(text) and (self.cfg.get("search") or {}).get("confirm", True):
            short = re.sub(r"\s+", " ", text).strip()[:90]
            self.ask_confirm(lambda: self._chat_go(text, True), on_no=lambda: self._chat_go(text, False))       # в интернет — только после «да»
            self.say(rudemod.pick("search_ask", short=short) if self.rude else f"Хотите, посмотрю в интернете: {short}? Скажите «да» или «нет».")
            return
        self.say(self._chat_go(text, False))

    def _run_dialog_action(self, action) -> str:
        try:
            return action()
        except Exception as e:
            traceback.print_exc()
            self.log("error", f"Действие после «да» не выполнено: {e}")
            return "Не получилось."

    def _chat_go(self, text: str, search: bool) -> str:
        """Отправляет вопрос в чат. search=True — человек подтвердил поиск в интернете; иначе отвечаем без интернета (после «нет» — всё равно отвечаем, из головы)."""
        if self.chat.busy:
            return rudemod.pick("busy") if self.rude else "Я ещё отвечаю на предыдущий вопрос."
        cid = self.chat.voice_chat_id()
        self.emit({"type": "chat_open", "chat": cid})
        self.chat.send(cid, text, voice=True, search=True if search else None)
        if self.rude:
            return rudemod.pick("searching" if search else "thinking")
        return "Хорошо, поищу в интернете." if search else "Хорошо, подумаю."

    _FAIL = ("не нашла", "не получилось", "ошибка", "не поняла", "не знаю", "нет интернета", "не выбрано", "не удалось", "окно «")

    def _recall(self, ctx: Ctx, phrase: str):
        """Фраза, которую я уже понимала и выполняла, выполняется сразу. Не вышло — забываю её и разбираю заново."""
        hit = self.memory.recall(phrase)
        if not hit:
            return False, ""
        before = (self._pending, self._choice)
        handled, reply = actions.run_command(ctx, hit[0], hit[1])
        if handled and not str(reply).lower().startswith(self._FAIL) and (self._pending, self._choice) == before:
            self.memory.remember(phrase, hit[0], hit[1])
            return True, reply
        self.memory.forget(phrase)
        return False, ""

    def _learn(self, ctx: Ctx, phrase: str, handled: bool, reply, before):
        """Запоминает удачно выполненную команду. Не запоминаем, если я задала вопрос (ждём «да» или выбор) или ответила отказом."""
        try:
            if handled and ctx.matched and (self._pending, self._choice) == before and not str(reply or "").lower().startswith(self._FAIL):
                if self.memory.remember(phrase, ctx.matched[0], ctx.matched[1]):
                    trace(f"Память: запомнила «{phrase}» → {ctx.matched[0]}")
        except Exception as e:
            trace(f"Память: {e}")

    def _try_llm(self, ctx: Ctx, text: str):
        if not net.is_online():
            return False, ""
        names = [e["name"] for e in self.apps.entries()]
        self._set_state("thinking", "Думаю…")
        r = llm.ask(self.cfg, text, names)
        if not r:
            return False, ""
        if r.get("action"):
            before = (self._pending, self._choice)
            handled, reply = actions.run_by_name(ctx, str(r["action"]), r.get("args") or {})
            self._learn(ctx, text, handled, reply, before)          # поняла с помощью языковой модели — в следующий раз узнаю сама
            return handled, reply
        if r.get("say"):
            return True, str(r["say"])
        return False, ""

    def say(self, text: str, shown: str | None = None):
        """Говорит вслух. Всё, что произносится, встаёт в очередь: две реплики из разных потоков (например, «Хорошо, подумаю» и ошибка чата)
        больше не звучат одновременно. shown — что показать в окне вместо озвученного (длинная ошибка: вслух коротко, в журнале полностью)."""
        if not text:
            self._arm_dialogs()
            return
        gen = self._say_gen
        self.log("karina", shown or text)
        with self._say_lock:
            if gen != self._say_gen:         # пока ждала очереди, сказали «замолчи» — эту реплику не произносим
                self._arm_dialogs()
                return
            barge = bool(self.cfg["tts"].get("barge_in", True)) and self.stt.model is not None
            if barge:
                self.listener.speaking = True    # слушаем только короткое «стоп» — её можно перебить
            else:
                self.listener.muted = True       # не слушаем самих себя
            self._set_state("speaking")
            try:
                self.tts.speak(text)
            except Exception as e:
                self.log("error", f"Не удалось озвучить: {e}")
            finally:
                self.listener.speaking = False
                self.listener.muted = True       # на время «хвоста» эха после речи
                time.sleep(0.2)
                self.listener.muted = not self.listening
                self.followup_until = max(self.followup_until, time.time() + float(self.cfg["followup_seconds"]))
                self._arm_dialogs()              # ответ на вопрос ждём ОТ ЭТОГО момента, а не от начала речи
                self._set_state("idle")

    # ---------- «замолчи» и «говорить без имени» ----------
    def hush(self):
        """Прервать речь (горячая клавиша или фраза «стоп»)."""
        self._say_gen += 1                   # всё, что стояло в очереди на озвучку, отменяется
        if self.tts.speaking:
            self.tts.stop()
            self.log("info", "Замолчала.")

    def push_to_talk(self):
        """Горячая клавиша: следующая фраза принимается без обращения по имени."""
        self.hush()
        self.sleeping = False
        self.followup_until = time.time() + 10
        self._set_state("idle", "Слушаю — говорите без имени")
        self.log("info", "Слушаю: можно говорить без имени.")
        if sys.platform == "win32":
            try:
                import winsound
                threading.Thread(target=winsound.Beep, args=(880, 90), daemon=True).start()
            except Exception:
                pass

    def _on_barge(self, u):
        try:
            self.bq.put_nowait(u)
        except queue.Full:
            pass

    def _barge_worker(self):
        while not self._stop.is_set():
            try:
                u = self.bq.get(timeout=0.4)
            except queue.Empty:
                continue
            if not self.tts.speaking or self.stt.model is None:
                continue
            try:
                text = self.stt.transcribe(u.audio)
                if text and is_stop_phrase(text, self.cfg.wake_names()):
                    self.hush()
            except Exception as e:
                trace(f"барж-ин: {e}")
