"""1.10.0: «замолчи» голосом, горячие клавиши, нейросетевой VAD, видеокарта и подсказка города в распознавании, автоустановка голоса Silero."""
import sys, tempfile, threading, time, types, unittest
from pathlib import Path
from unittest import mock
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import assistant as asst, hotkeys, silero_voice, stt, vad
from karina.config import Config


def cfg(**patch):
    c = Config(Path(tempfile.mkdtemp()) / "c.json")
    if patch:
        c.update(patch)
    return c


class StopPhrase(unittest.TestCase):
    names = ["карина"]

    def test_stop_phrases(self):
        for t in ("стоп", "Карина, стоп", "хватит", "ладно хватит", "карина замолчи", "всё, достаточно", "тихо", "стоп стоп"):
            self.assertTrue(asst.is_stop_phrase(t, self.names), t)

    def test_commands_are_not_stop(self):
        for t in ("останови музыку", "хватит ли места на диске", "карина открой миксер", "сделай тише", "стоп игра сейчас", "", "карина"):
            self.assertFalse(asst.is_stop_phrase(t, self.names), t)


class Barge(unittest.TestCase):
    def make(self):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = cfg()
        a._stop = threading.Event()
        a.tts = types.SimpleNamespace(speaking=True, stop=mock.MagicMock())
        a.stt = types.SimpleNamespace(model=object(), transcribe=lambda audio: a.heard)
        a.emit = lambda e: None
        a.logs = []
        a.log = lambda kind, text: a.logs.append((kind, text))
        a.bq = __import__("queue").Queue(maxsize=2)
        return a

    def run_worker(self, a, text):
        a.heard = text
        a.bq.put(types.SimpleNamespace(audio=np.zeros(8000, np.float32)))
        th = threading.Thread(target=a._barge_worker, daemon=True)
        th.start()
        time.sleep(0.7)
        a._stop.set()
        th.join(2)

    def test_stop_word_interrupts_speech(self):
        a = self.make()
        self.run_worker(a, "Карина, стоп")
        a.tts.stop.assert_called_once()

    def test_other_speech_does_not_interrupt(self):
        a = self.make()
        self.run_worker(a, "открой браузер")
        a.tts.stop.assert_not_called()

    def test_push_to_talk_opens_window_without_name(self):
        a = self.make()
        a.sleeping, a.followup_until = True, 0.0
        a._set_state = lambda *x, **k: None
        a.push_to_talk()
        self.assertFalse(a.sleeping)
        self.assertGreater(a.ptt_until, time.time() + 5)
        a.tts.stop.assert_called()             # заодно прерывает речь


class Hotkeys(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(hotkeys.parse("ctrl+alt+a"), (0x2 | 0x1, ord("A")))
        self.assertEqual(hotkeys.parse("Ctrl + Shift + F5"), (0x2 | 0x4, 0x74))
        self.assertEqual(hotkeys.parse("win+space"), (0x8, 0x20))
        self.assertIsNone(hotkeys.parse("a"))                   # без модификатора — перехватили бы обычную клавишу
        self.assertIsNone(hotkeys.parse("ctrl+"))
        self.assertIsNone(hotkeys.parse("ctrl+дд"))
        self.assertIsNone(hotkeys.parse(""))

    def test_start_is_harmless_off_windows(self):
        h = hotkeys.Hotkeys(cfg(), {"talk": lambda: None, "stop": lambda: None})
        with mock.patch.object(hotkeys.sys, "platform", "linux"):
            h.start(); h.stop()
        self.assertEqual(h.errors, [])

    def test_defaults_in_config(self):
        c = cfg()
        self.assertEqual(c["hotkeys"], {"enabled": True, "talk": "ctrl+alt+a", "stop": "ctrl+alt+s", "island": "ctrl+alt+i", "snip": "ctrl+alt+x"})
        self.assertIsNotNone(hotkeys.parse(c["hotkeys"]["talk"]))


class NeuralVad(unittest.TestCase):
    @unittest.skipUnless(vad.available(), "нет faster-whisper / onnxruntime")
    def test_silence_and_noise_are_not_speech(self):
        v = vad.NeuralVAD()
        rng = np.random.default_rng(0)
        for sig in (np.zeros(16000, np.float32), (rng.standard_normal(16000) * 0.02).astype(np.float32)):
            v.reset()
            ps = [v.feed(sig[i:i + 480]) for i in range(0, len(sig) - 480, 480)]
            self.assertLess(max(ps), 0.5)

    def test_unavailable_model_gives_zero_probability(self):
        with mock.patch.dict(vad._state, {"session": None, "failed": True}):
            self.assertEqual(vad.NeuralVAD().feed(np.ones(480, np.float32)), 0.0)
            self.assertFalse(vad.available())


class SttHintAndGpu(unittest.TestCase):
    def test_hint_is_off_by_default(self):
        """Подсказка Whisper выключена: на шуме модель повторяла её как «услышанное» и Карина отвечала сама."""
        self.assertEqual(stt.STT(cfg(profile={"city": "г. Новокузнецк"})).hint(), "")

    def test_hint_contains_profile_city(self):
        s = stt.STT(cfg(profile={"city": "г. Новокузнецк, Кемеровская область"}, stt={"use_hint": True}))
        h = s.hint()
        self.assertIn("Новокузнецк", h)
        self.assertNotIn("Кемеровская", h)
        self.assertIn("Карина", h)
        self.assertNotIn("Какая погода в ", stt.STT(cfg(stt={"use_hint": True})).hint())     # города нет — без хвоста

    def test_gpu_not_used_without_nvidia_in_auto_mode(self):
        s = stt.STT(cfg())
        with mock.patch.object(stt.shutil, "which", return_value=None):
            self.assertFalse(s._gpu_wanted("small"))
        with mock.patch.object(stt.shutil, "which", return_value="nvidia-smi"):
            self.assertTrue(s._gpu_wanted("small"))
        s.cfg.update({"stt": {"gpu_probe": {"model": "small", "ok": False}}})
        with mock.patch.object(stt.shutil, "which", return_value="nvidia-smi"):
            self.assertFalse(s._gpu_wanted("small"))                       # уже проверяли — не работает, не мучаем при каждом запуске
            self.assertTrue(s._gpu_wanted("medium"))                       # другая модель — проверим заново
        s.cfg.update({"stt": {"device": "cpu"}})
        self.assertFalse(s._gpu_wanted("medium"))

    def _fake_fw(self, log):
        mod = types.ModuleType("faster_whisper")

        class WhisperModel:
            def __init__(self, name, **kw):
                self.kw = kw
                log.append(kw.get("device"))

            def transcribe(self, audio, **kw):
                if self.kw.get("device") == "cuda":
                    raise RuntimeError("Library cublas64_12.dll is not found")
                seg = types.SimpleNamespace(text=" открой миксер", no_speech_prob=0.0, avg_logprob=-0.1, start=0.0, end=1.0)
                return [seg], None
        mod.WhisperModel = WhisperModel
        return mod

    def test_gpu_loads_when_probe_passes_and_falls_back_on_runtime_error(self):
        log = []
        s = stt.STT(cfg())
        with mock.patch.dict(sys.modules, {"faster_whisper": self._fake_fw(log)}), mock.patch.object(stt, "run_probe", return_value=("ok", "")), \
             mock.patch.object(stt, "run_gpu_probe", return_value=(True, "")), mock.patch.object(stt.shutil, "which", return_value="nvidia-smi"):
            s.load()
            self.assertEqual(s.device, "cuda")
            text, conf = s.transcribe_ex(np.zeros(16000, np.float32))      # на видеокарте упало → переходим на процессор и повторяем
        self.assertEqual(text, "открой миксер")
        self.assertEqual((s.device, log), ("cpu", ["cuda", "cpu"]))
        self.assertEqual(s.cfg["stt"]["gpu_probe"], {"model": "small", "ok": False})

    def test_failed_gpu_probe_means_cpu(self):
        log = []
        s = stt.STT(cfg())
        with mock.patch.dict(sys.modules, {"faster_whisper": self._fake_fw(log)}), mock.patch.object(stt, "run_probe", return_value=("ok", "")), \
             mock.patch.object(stt, "run_gpu_probe", return_value=(False, "нет cublas")), mock.patch.object(stt.shutil, "which", return_value="nvidia-smi"):
            s.load()
        self.assertEqual((s.device, log, s.gpu_note), ("cpu", ["cpu"], "нет cublas"))


class Prefilter(unittest.TestCase):
    def make(self, tiny_text, state="ok", **stt_cfg):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = cfg(stt={"prefilter": True, **stt_cfg})
        a.followup_until, a._pending, a._choice = 0.0, None, None
        s = stt.STT(a.cfg)
        s._tiny_state = state
        s._tiny = types.SimpleNamespace(transcribe=lambda audio, **kw: ([types.SimpleNamespace(text=tiny_text)], None)) if state == "ok" else None
        a.stt = s
        return a

    audio = [types.SimpleNamespace(audio=np.zeros(16000, np.float32))]

    def test_no_name_is_rejected(self):
        self.assertTrue(self.make("давай посмотрим что там по телевизору сегодня")._prefilter_reject(self.audio))

    def test_name_even_misheard_passes(self):
        for t in ("Карина открой миксер", "корина открой миксер", "открой миксер крина", "Карина"):
            self.assertFalse(self.make(t)._prefilter_reject(self.audio), t)

    def test_empty_tiny_text_is_rejected_but_unavailable_filter_passes(self):
        self.assertTrue(self.make("")._prefilter_reject(self.audio))
        self.assertFalse(self.make("", state="loading")._prefilter_reject(self.audio))
        self.assertFalse(self.make("", state="off")._prefilter_reject(self.audio))

    def test_off_by_default_and_not_in_followup_or_confirmation(self):
        a = self.make("просто разговор")
        a.cfg.update({"stt": {"prefilter": False}})
        self.assertFalse(a._prefilter_reject(self.audio))
        a.cfg.update({"stt": {"prefilter": True}, "wake_strict": False})
        a.followup_until = time.time() + 5
        self.assertFalse(a._prefilter_reject(self.audio))
        a.followup_until, a._pending = 0.0, {"action": lambda: "да", "on_no": None, "until": time.time() + 5, "tries": 0}
        self.assertFalse(a._prefilter_reject(self.audio))
        self.assertEqual(Config(Path(tempfile.mkdtemp()) / "x.json")["stt"]["prefilter"], False)


class SileroAuto(unittest.TestCase):
    def make(self, **tts):
        a = asst.Assistant.__new__(asst.Assistant)
        a.cfg = cfg(tts=tts)
        a._stop = threading.Event()
        a.emit = lambda e: None
        return a

    def run_setup(self, a, torch=True, ready=False, online=True):
        with mock.patch.object(asst.time, "sleep"), mock.patch.object(silero_voice, "torch_available", return_value=torch), \
             mock.patch.object(silero_voice, "is_ready", return_value=ready), mock.patch.object(silero_voice, "preload") as pre, \
             mock.patch.object(silero_voice, "ensure_model") as ens, mock.patch.object(asst.net, "is_online", return_value=online):
            a._voice_setup()
        return pre, ens

    def test_voice_is_karina_and_model_downloaded_when_missing(self):
        a = self.make(voice="karina")
        pre, ens = self.run_setup(a)
        self.assertEqual(a.cfg["tts"]["voice"], "karina")
        ens.assert_called_once()

    def test_no_torch_or_disabled_does_nothing(self):
        a = self.make(voice="karina")
        pre, ens = self.run_setup(a, torch=False)
        ens.assert_not_called()
        b = self.make(voice="karina", auto_silero=False)
        pre, ens = self.run_setup(b)
        ens.assert_not_called()

    def test_ready_model_is_only_preloaded(self):
        a = self.make(voice="karina")
        pre, ens = self.run_setup(a, ready=True)
        pre.assert_called_once()
        ens.assert_not_called()

    def test_torch_is_expected_in_packaged_build(self):
        with mock.patch.object(silero_voice.sys, "frozen", True, create=True), mock.patch.object(silero_voice.importlib.util, "find_spec", return_value=object()):
            self.assertTrue(silero_voice.torch_available())

    def test_ensure_model_retries_then_gives_up(self):
        base = Path(tempfile.mkdtemp())
        events = []
        with mock.patch.object(silero_voice, "torch_available", return_value=True), mock.patch.object(silero_voice, "download", side_effect=OSError("сеть")) as dl:
            cancel = threading.Event()
            with mock.patch.object(threading.Event, "wait", return_value=False):
                ok = silero_voice.ensure_model(base, lambda m: None, events.append, attempts=3, cancel=cancel)
        self.assertFalse(ok)
        self.assertEqual(dl.call_count, 3)
        self.assertEqual(events[-1]["type"], "silero_dl_done")
        self.assertFalse(events[-1]["ready"])


class Packaging(unittest.TestCase):
    root = Path(__file__).resolve().parent.parent

    def test_torch_is_listed_and_not_excluded(self):
        req = (self.root / "requirements.txt").read_text("utf-8")
        self.assertRegex(req, r"(?m)^torch>=")
        spec = (self.root / "build" / "karina.spec").read_text("utf-8")
        excludes = spec[spec.index("excludes="):]
        self.assertIn('"torch"', excludes.split("]")[0])                # torch не входит в установщик: его докачивает Карина (karina/components.py)

    def test_run_bat_installs_new_requirements(self):
        bat = (self.root / "run.bat").read_text("utf-8")
        self.assertIn("requirements.installed.txt", bat)

    def test_run_bat_picks_supported_python(self):
        bat = (self.root / "run.bat").read_text("utf-8")
        self.assertIn("py -%%V", bat)
        self.assertIn("(3,11)<=sys.version_info[:2]<=(3,13)", bat)
        self.assertIn("rmdir /s /q .venv", bat)
        self.assertIn("\r\n", (self.root / "run.bat").read_bytes().decode("utf-8"))      # у .bat должны быть переводы строк Windows


if __name__ == "__main__":
    unittest.main()
