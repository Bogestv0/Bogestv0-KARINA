"""Распознавание речи: faster-whisper, полностью офлайн (модель скачивается один раз)."""
from __future__ import annotations
import json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time
import numpy as np
from .config import DATA, FROZEN, ROOT
from .diag import trace
from .textutil import norm, is_loop

# типичные «галлюцинации» Whisper на тишине и шуме
_JUNK = ("субтитр", "продолжение следует", "спасибо за просмотр", "подписывайтесь", "редактор субтитров",
         "dimatorzok", "amara.org", "ставьте лайк", "до новых встреч", "всем пока", "спасибо за внимание", "на этом всё", "корректор",
         "переведено", "перевод:", "музыка играет", "[музыка]", "(музыка)", "аплодисменты")


def hf_reachable(timeout: float = 6.0) -> tuple[bool, str]:
    try:
        socket.create_connection(("huggingface.co", 443), timeout=timeout).close()
        return True, ""
    except OSError as e:
        return False, str(e) or e.__class__.__name__


def _poll_size(root, progress, done: threading.Event):
    """Раз в пару секунд сообщает, сколько мегабайт модели уже скачано."""
    while not done.wait(2.0):
        try:
            total = sum(f.stat().st_size for d in root.glob("models--*") for f in d.rglob("*") if f.is_file())
            progress(int(total / 1048576))
        except OSError:
            pass


# Коды завершения дочернего процесса-проверки
_OK, _MISSING, _ERROR = 0, 3, 4

# Типичные коды аварийного падения Windows → понятная причина
_CRASH_HINTS = {
    0xC000001D: "процессор не поддерживает нужные инструкции (нужен AVX2; бывает на очень старых процессорах и в некоторых виртуальных машинах)",
    0xC0000005: "нарушение доступа к памяти в библиотеке распознавания (часто виноваты антивирус или повреждённая установка — переустановите Карину)",
    0xC0000135: "не найдена системная библиотека. Установите «Microsoft Visual C++ Redistributable 2015–2022 (x64)» и запустите Карину снова",
    0xC0000139: "не найдена функция в системной библиотеке. Установите «Microsoft Visual C++ Redistributable 2015–2022 (x64)»",
    0xC0000142: "библиотека не смогла инициализироваться (антивирус мог заблокировать файл) — добавьте папку Карины в исключения",
    0xC00000FD: "переполнение стека в библиотеке распознавания",
    0xC0000409: "библиотека распознавания аварийно завершилась (повреждённая память)",
}


def _explain_exit(code: int) -> str:
    u = code & 0xFFFFFFFF
    if u in _CRASH_HINTS:
        return f"{_CRASH_HINTS[u]} [код 0x{u:08X}]"
    if code < 0:
        return f"процесс завершён сигналом {-code}"
    return f"процесс завершился с кодом {code} (0x{u:08X})"


def _probe_cmd(name: str, root, download: bool, out_file: str, gpu: bool = False) -> list[str]:
    tail = ["--stt-gpu-probe", name, str(root), out_file] if gpu else ["--stt-probe", name, str(root), "download" if download else "local", out_file]
    return [sys.executable] + tail if FROZEN else [sys.executable, str(ROOT / "main.py")] + tail


def probe_main(argv: list[str]) -> int:
    """Тело дочернего процесса. Здесь, и только здесь, первый раз загружается нативный код распознавания
    (faster-whisper → ctranslate2 / onnxruntime). Если он уронит процесс — упадёт только эта проверка.
    argv: имя_модели папка local|download файл_результата."""
    name, root, mode, out_file = argv[:4]
    res: dict = {"status": "error", "message": "проверка не завершилась"}

    def save():
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
        except OSError:
            pass

    save()   # заранее: если процесс упадёт, файл скажет «не завершилась» и родитель найдёт причину по коду выхода
    step = "импорт faster_whisper"
    try:
        trace("Проба: импорт faster_whisper")
        from faster_whisper import WhisperModel
        step = "загрузка модели" if mode == "local" else "скачивание и загрузка модели"
        trace("Проба: " + step)
        try:
            model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(root),
                                 local_files_only=(mode == "local"))
        except Exception as e:
            if mode == "local":
                res = {"status": "missing", "message": f"{e.__class__.__name__}: {e}"}
                save()
                return _MISSING
            raise
        step = "пробное распознавание"
        trace("Проба: пробное распознавание тишины")
        import numpy as np
        segs, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="ru", beam_size=1, vad_filter=True)
        list(segs)
        res = {"status": "ok", "message": ""}
        save()
        trace("Проба: всё хорошо")
        return _OK
    except BaseException as e:
        res = {"status": "error", "message": f"{step}: {e.__class__.__name__}: {e}"}
        save()
        return _ERROR


def run_probe(name: str, root, download: bool, progress=None, timeout: float | None = None) -> tuple[str, str]:
    """Запускает проверку в отдельном процессе. Возвращает (статус, пояснение):
    ok | missing | error | crash | timeout."""
    if timeout is None:
        timeout = 3600 if download else 180
    fd, out_file = tempfile.mkstemp(prefix="karina-probe-", suffix=".json")
    os.close(fd)
    flags = 0x08000000 if sys.platform == "win32" else 0
    done = threading.Event()
    trace(f"Проверка модели «{name}» в отдельном процессе ({'скачивание' if download else 'из кэша'})")
    try:
        if download and progress:
            threading.Thread(target=_poll_size, args=(DATA / "models", progress, done), daemon=True, name="stt-download-progress").start()
        try:
            r = subprocess.run(_probe_cmd(name, root, download, out_file), stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=timeout, creationflags=flags, cwd=str(ROOT))
        except subprocess.TimeoutExpired:
            return "timeout", f"проверка не уложилась в {int(timeout)} с (процесс остановлен)"
        except OSError as e:
            return "error", f"не удалось запустить проверку: {e}"
        try:
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
        except (OSError, ValueError):
            res = {}
        trace(f"Проверка модели: процесс завершился, код {r.returncode} (0x{r.returncode & 0xFFFFFFFF:08X}), результат: {res.get('status', 'нет файла результата')}")
        tail = (r.stderr or b"").decode("utf-8", "replace").strip()[-300:]
        if r.returncode == _OK and res.get("status") == "ok":
            return "ok", ""
        if r.returncode == _MISSING and res.get("status") == "missing":
            return "missing", res.get("message", "")
        if r.returncode == _ERROR and res.get("status") == "error":
            return "error", res.get("message", "")
        # ни одного «нормального» исхода: процесс убит системой / упал в нативном коде
        return "crash", _explain_exit(r.returncode) + (f". Вывод: {tail}" if tail else "")
    finally:
        done.set()
        try:
            os.unlink(out_file)
        except OSError:
            pass


def gpu_probe_main(argv: list[str]) -> int:
    """Дочерний процесс: работает ли распознавание на видеокарте NVIDIA (нужны CUDA 12 и cuDNN 9 в системе). Проверка настоящая:
    модель загружается на видеокарту и расшифровывает секунду шума без фильтра тишины, иначе нехватку библиотек видно только на первой фразе.
    argv: имя_модели папка файл_результата. Код 0 — работает."""
    name, root, out_file = argv[:3]
    res = {"status": "error", "message": "проверка не завершилась"}

    def save():
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
        except OSError:
            pass
    save()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            res = {"status": "no", "message": "видеокарта CUDA не найдена"}
            save()
            return 2
        from faster_whisper import WhisperModel
        import numpy as np
        model = WhisperModel(name, device="cuda", compute_type="float16", download_root=str(root), local_files_only=True)
        noise = (np.random.default_rng(1).standard_normal(16000) * 0.01).astype(np.float32)
        list(model.transcribe(noise, language="ru", beam_size=1, vad_filter=False)[0])
        res = {"status": "ok", "message": ""}
        save()
        return 0
    except BaseException as e:
        res = {"status": "error", "message": f"{e.__class__.__name__}: {e}"}
        save()
        return _ERROR


def run_gpu_probe(name: str, root, timeout: float = 150.0) -> tuple[bool, str]:
    """(работает ли видеокарта, пояснение). Запускается в отдельном процессе: нехватка библиотек CUDA иногда роняет процесс."""
    fd, out_file = tempfile.mkstemp(prefix="karina-gpuprobe-", suffix=".json")
    os.close(fd)
    flags = 0x08000000 if sys.platform == "win32" else 0
    try:
        try:
            r = subprocess.run(_probe_cmd(name, root, False, out_file, gpu=True), stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=timeout, creationflags=flags, cwd=str(ROOT))
        except subprocess.TimeoutExpired:
            return False, "проверка видеокарты не уложилась во время"
        except OSError as e:
            return False, f"не удалось запустить проверку: {e}"
        try:
            with open(out_file, encoding="utf-8") as f:
                res = json.load(f)
        except (OSError, ValueError):
            res = {}
        if r.returncode == 0 and res.get("status") == "ok":
            return True, ""
        return False, res.get("message") or _explain_exit(r.returncode)
    finally:
        try:
            os.unlink(out_file)
        except OSError:
            pass


class STT:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.loaded_name = None
        self.device = "cpu"
        self.gpu_note = ""            # почему не видеокарта (для журнала и настроек)
        self._lock = threading.RLock()    # модель одна: фразу и короткое «стоп» поверх речи Карины распознаём по очереди
        self._tiny = None                 # маленькая модель для быстрой проверки имени (stt.prefilter)
        self._tiny_state = None           # None — не начинали | "loading" | "ok" | "off"
        self._tiny_lock = threading.Lock()

    # ---------- видеокарта ----------
    def _gpu_wanted(self, name: str) -> bool:
        st = self.cfg["stt"]
        pref = str(st.get("device", "auto")).lower()
        if pref == "cpu":
            return False
        known = st.get("gpu_probe") or {}
        if pref == "auto" and (shutil.which("nvidia-smi") is None or (known.get("model") == name and known.get("ok") is False)):
            return False
        return True

    def _remember_gpu(self, name: str, ok: bool):
        try:
            self.cfg.update({"stt": {"gpu_probe": {"model": name, "ok": ok}}})
        except Exception:
            pass

    def load(self, progress=None):
        """Сначала модель проверяется в ОТДЕЛЬНОМ процессе (загрузка + пробное распознавание тишины). Если нативная
        библиотека роняет процесс — Карина остаётся жива и показывает причину. Если модели нет на диске — проверяется
        huggingface.co и модель скачивается (тоже в отдельном процессе). Только после успешной проверки модель
        загружается в основном процессе. Видеокарта NVIDIA используется, если отдельная проверка её работы прошла; иначе процессор."""
        name = self.cfg["stt"].get("model", "small")
        with self._lock:
            if self.model is not None and self.loaded_name == name:
                return
            root = DATA / "models"
            status, why = run_probe(name, root, download=False)
            if status == "missing":
                ok, net_why = hf_reachable()
                if not ok:
                    raise RuntimeError(f"модель распознавания ещё не скачана, а сайт huggingface.co недоступен ({net_why}). "
                                       "Проверьте интернет; если сайт у вас закрыт — включите VPN на время первого запуска "
                                       f"(скачивается один раз, ~0,5 ГБ). Исходная ошибка: {why}")
                status, why = run_probe(name, root, download=True, progress=progress)
            if status == "crash":
                raise RuntimeError("библиотека распознавания речи аварийно завершила работу при загрузке модели «%s»: %s" % (name, why))
            if status == "timeout":
                raise RuntimeError(f"загрузка модели «{name}» зависла: {why}")
            if status != "ok":
                raise RuntimeError(f"не удалось загрузить модель «{name}»: {why}")
            from faster_whisper import WhisperModel      # проверка прошла — теперь безопасно загружать здесь
            self.device, self.gpu_note = "cpu", ""
            if self._gpu_wanted(name):
                ok, why_gpu = run_gpu_probe(name, root)
                self._remember_gpu(name, ok)
                if ok:
                    try:
                        self.model = WhisperModel(name, device="cuda", compute_type="float16", download_root=str(root), local_files_only=True)
                        self.device = "cuda"
                        trace(f"Распознавание речи: работает на видеокарте ({name})")
                    except Exception as e:
                        self.gpu_note = f"{e.__class__.__name__}: {e}"
                else:
                    self.gpu_note = why_gpu
                if self.device == "cpu":
                    trace("Распознавание речи: видеокарта не используется — " + (self.gpu_note or "нет"))
            if self.model is None or self.device == "cpu":
                self.model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(root), local_files_only=True)
            self.loaded_name = name

    def _fall_back_to_cpu(self, why: str):
        """Видеокарта подвела посреди работы — переходим на процессор и больше её не трогаем для этой модели."""
        name = self.loaded_name or self.cfg["stt"].get("model", "small")
        trace("Распознавание речи: ошибка на видеокарте, перехожу на процессор — " + why)
        self._remember_gpu(name, False)
        self.gpu_note = why
        from faster_whisper import WhisperModel
        self.model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(DATA / "models"), local_files_only=True)
        self.device = "cpu"

    def transcribe(self, audio: np.ndarray) -> str:
        return self.transcribe_ex(audio)[0]

    def hint(self) -> str:
        """Подсказка Whisper (по умолчанию выключена — stt.use_hint): на шуме и тишине модель повторяет подсказку как «услышанное»
        («Карина, закрой, закрой, закрой…»), и ассистент начинает отвечать сам. Включайте, только если имя стабильно не узнаётся.
        Подсказка: имя, привычные слова команд и город из профиля — так названия распознаются точнее («погода в Новокузнецке»)."""
        if not self.cfg["stt"].get("use_hint", False):
            return ""
        h = f"{self.cfg['name']}, открой, закрой, запусти, громкость, найди в интернете, сделай скриншот."
        try:
            from .weather import clean_city
            city = clean_city((self.cfg.get("profile") or {}).get("city", ""))
            if city:
                h += f" Какая погода в {city}? Погода в моём городе."
        except Exception:
            pass
        return h

    def transcribe_ex(self, audio: np.ndarray) -> tuple[str, float]:
        """(текст, уверенность 0…1). Уверенность — по оценке самой модели; по ней несколько микрофонов выбирают лучшую запись."""
        with self._lock:
            self.load()
            kw = dict(language="ru", beam_size=1, vad_filter=True, condition_on_previous_text=False, initial_prompt=self.hint() or None,
                      no_speech_threshold=0.6, temperature=0.0)
            try:
                segs, _ = self.model.transcribe(audio, **kw)
                segs = list(segs)
            except Exception as e:
                if self.device != "cuda":
                    raise
                self._fall_back_to_cpu(f"{e.__class__.__name__}: {e}")
                segs, _ = self.model.transcribe(audio, **kw)
                segs = list(segs)
        kept = [s for s in segs if s.no_speech_prob < 0.7]
        text = " ".join(s.text.strip() for s in kept).strip()
        low = text.lower()
        if len(norm(text)) < 2 or any(j in low for j in _JUNK):
            return "", 0.0
        if is_loop(text):                  # «закрой закрой закрой…» — зацикливание модели на шуме, не речь
            trace("Распознавание: отброшено зацикливание — " + text[:80])
            return "", 0.0
        num = den = 0.0
        for s in kept:
            lp = getattr(s, "avg_logprob", None)
            if lp is None:
                continue
            w = max(0.05, float(getattr(s, "end", 0.0)) - float(getattr(s, "start", 0.0)))
            num, den = num + w * max(-3.0, min(0.0, float(lp))), den + w
        import math
        return text, (math.exp(num / den) if den else 0.5)

    # ---------- быстрая проверка имени (необязательная) ----------
    def ensure_prefilter(self):
        """Если включено stt.prefilter — в фоне готовит маленькую модель «tiny» (скачивает при необходимости). Пока не готова, проверка пропускается."""
        if not self.cfg["stt"].get("prefilter") or self._tiny_state is not None:
            return
        self._tiny_state = "loading"
        threading.Thread(target=self._load_tiny, daemon=True, name="stt-prefilter").start()

    def _load_tiny(self):
        try:
            root = DATA / "models"
            status, _ = run_probe("tiny", root, download=False)
            if status == "missing" and hf_reachable()[0]:
                status, _ = run_probe("tiny", root, download=True)
            if status != "ok":
                self._tiny_state = "off"
                return
            from faster_whisper import WhisperModel
            self._tiny = WhisperModel("tiny", device="cpu", compute_type="int8", download_root=str(root), local_files_only=True)
            self._tiny_state = "ok"
            trace("Распознавание речи: быстрая проверка имени готова")
        except Exception as e:
            self._tiny_state = "off"
            trace(f"Распознавание речи: быстрая проверка имени недоступна: {e}")

    def has_name(self, audio: np.ndarray, names: list[str]) -> bool | None:
        """Есть ли в записи обращение по имени (проверка нестрогая, чтобы не потерять команду). None — проверка недоступна: распознаём как обычно."""
        if self._tiny_state != "ok" or self._tiny is None:
            return None
        from rapidfuzz import fuzz
        try:
            with self._tiny_lock:
                segs, _ = self._tiny.transcribe(audio, language="ru", beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                                 initial_prompt=None, temperature=0.0)
                text = " ".join(s.text.strip() for s in segs)
        except Exception:
            return None
        words = norm(text).split()
        if not words:
            return False
        edge = words[:3] + words[-3:]
        return any(fuzz.ratio(w, n) >= 62 for w in edge for n in names)
