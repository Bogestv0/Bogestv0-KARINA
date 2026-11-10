"""Распознавание речи: faster-whisper, полностью офлайн (модель скачивается один раз)."""
from __future__ import annotations
import json, os, re, socket, subprocess, sys, tempfile, threading, time
import numpy as np
from .config import DATA, FROZEN, ROOT
from .textutil import norm

# типичные «галлюцинации» Whisper на тишине и шуме
_JUNK = ("субтитр", "продолжение следует", "спасибо за просмотр", "подписывайтесь", "редактор субтитров",
         "dimatorzok", "amara.org")


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


def _probe_cmd(name: str, root, download: bool, out_file: str) -> list[str]:
    tail = ["--stt-probe", name, str(root), "download" if download else "local", out_file]
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
        from faster_whisper import WhisperModel
        step = "загрузка модели" if mode == "local" else "скачивание и загрузка модели"
        try:
            model = WhisperModel(name, device="auto", compute_type="int8", download_root=str(root),
                                 local_files_only=(mode == "local"))
        except Exception as e:
            if mode == "local":
                res = {"status": "missing", "message": f"{e.__class__.__name__}: {e}"}
                save()
                return _MISSING
            raise
        step = "пробное распознавание"
        import numpy as np
        segs, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="ru", beam_size=1, vad_filter=True)
        list(segs)
        res = {"status": "ok", "message": ""}
        save()
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


class STT:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.loaded_name = None

    def load(self, progress=None):
        """Сначала модель проверяется в ОТДЕЛЬНОМ процессе (загрузка + пробное распознавание тишины). Если нативная
        библиотека роняет процесс — Карина остаётся жива и показывает причину. Если модели нет на диске — проверяется
        huggingface.co и модель скачивается (тоже в отдельном процессе). Только после успешной проверки модель
        загружается в основном процессе."""
        name = self.cfg["stt"].get("model", "small")
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
        self.model = WhisperModel(name, device="auto", compute_type="int8", download_root=str(root), local_files_only=True)
        self.loaded_name = name

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        hint = f"{self.cfg['name']}, открой, закрой, запусти, громкость, найди в интернете, сделай скриншот."
        segs, _ = self.model.transcribe(audio, language="ru", beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False, initial_prompt=hint,
                                        no_speech_threshold=0.6, temperature=0.0)
        parts = [s.text.strip() for s in segs if s.no_speech_prob < 0.7]
        text = " ".join(parts).strip()
        low = text.lower()
        if len(norm(text)) < 2 or any(j in low for j in _JUNK):
            return ""
        return text
