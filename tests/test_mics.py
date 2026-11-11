"""Несколько микрофонов (до 4) и вывод звука: настройки, объединение фраз, выбор лучшего, запасной путь воспроизведения.
Звуковых устройств нет — вместо sounddevice подставляется заглушка."""
import sys, tempfile, threading, types, unittest, wave
from pathlib import Path
from unittest import mock
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import audio, playback, tts
from karina.audio import Fuser, Listener, Utterance
from karina.config import Config


def cfg_with(**stt):
    cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
    if stt:
        cfg.update({"stt": stt})
    return cfg


def utt(slot, t0, t1, snr=10.0, peak=0.3):
    return Utterance(slot, np.zeros(int((t1 - t0) * 16000), dtype=np.float32), t0, t1, snr=snr, peak=peak)


DEVS = [{"id": 1, "name": "USB Mic", "api": "MME"}, {"id": 2, "name": "Realtek", "api": "MME"}, {"id": 3, "name": "BT Headset", "api": "MME"}]


class TestSlots(unittest.TestCase):
    def test_old_config_becomes_slot_one(self):
        s = audio.mic_slots(cfg_with(mic_device=7))
        self.assertEqual(len(s), 4)
        self.assertEqual(s[0], {"enabled": True, "device": 7, "name": ""})
        self.assertTrue(all(not x["enabled"] for x in s[1:]))

    def test_default_is_one_system_mic(self):
        s = audio.mic_slots(cfg_with())
        self.assertTrue(s[0]["enabled"] and s[0]["device"] is None)

    def test_saved_slots_padded_and_capped(self):
        raw = [{"enabled": True, "device": 1}] * 6
        self.assertEqual(len(audio.mic_slots(cfg_with(mics=raw))), 4)
        self.assertEqual(len(audio.mic_slots(cfg_with(mics=[{"enabled": True, "device": 2}]))), 4)

    def test_sanitize_takes_name_from_own_list_and_ignores_junk(self):
        s = audio.sanitize_mics([{"enabled": 1, "device": "2", "name": "подделка"}, {"enabled": True, "device": "abc"}, "мусор"], DEVS)
        self.assertEqual(s[0], {"enabled": True, "device": 2, "name": "Realtek"})
        self.assertEqual(s[1]["device"], None)
        self.assertFalse(s[2]["enabled"])
        self.assertEqual(len(s), 4)


class TestResolve(unittest.TestCase):
    def test_default_when_nothing_saved(self):
        self.assertEqual(audio.resolve_device(None, "", DEVS), (None, True))

    def test_same_id_and_name(self):
        self.assertEqual(audio.resolve_device(2, "Realtek", DEVS), (2, True))

    def test_renumbered_device_found_by_name(self):
        self.assertEqual(audio.resolve_device(9, "BT Headset", DEVS), (3, True))
        self.assertEqual(audio.resolve_device(1, "BT Headset", DEVS), (3, True))     # на старом номере теперь другое устройство

    def test_missing_device(self):
        self.assertEqual(audio.resolve_device(9, "Нет такого", DEVS), (None, False))
        self.assertEqual(audio.resolve_device(9, "", DEVS), (None, False))


class TestFuser(unittest.TestCase):
    def setUp(self):
        self.got = []
        self.f = Fuser(self.got.append)

    def test_same_phrase_from_three_mics_is_one_group_best_first(self):
        self.f.add(utt(0, 10.0, 12.0, snr=5), now=12.0)
        self.f.add(utt(1, 10.05, 12.1, snr=30), now=12.1)
        self.f.add(utt(2, 10.1, 11.9, snr=12), now=12.2)
        self.assertEqual(self.f.ready(now=12.3), [])                  # ещё ждём остальных
        groups = self.f.ready(now=12.8)
        self.assertEqual(len(groups), 1)
        self.assertEqual([u.slot for u in groups[0]], [1, 2, 0])      # лучший SNR первым

    def test_different_phrases_are_not_merged(self):
        self.f.add(utt(0, 10.0, 12.0), now=12.0)
        self.f.add(utt(1, 20.0, 22.0), now=22.0)
        self.assertEqual(len(self.f.ready(now=30.0)), 2)

    def test_late_duplicate_after_flush_is_dropped(self):
        self.f.add(utt(0, 10.0, 12.0), now=12.0)
        self.assertEqual(len(self.f.ready(now=13.0)), 1)
        self.assertFalse(self.f.add(utt(1, 10.1, 12.2), now=13.5))   # тот же голос, пришёл позже — команда не повторится
        self.assertEqual(self.f.ready(now=20.0), [])

    def test_next_command_after_flush_is_accepted(self):
        self.f.add(utt(0, 10.0, 12.0), now=12.0)
        self.f.ready(now=13.0)
        self.assertTrue(self.f.add(utt(0, 15.0, 17.0), now=17.0))
        self.assertEqual(len(self.f.ready(now=18.0)), 1)

    def test_noisy_long_blob_does_not_join_or_repeat(self):
        self.f.add(utt(0, 10.0, 12.0), now=12.0)
        self.assertFalse(self.f.add(utt(1, 3.0, 18.0, snr=1.2), now=18.0))   # 15 секунд «склейки» с шумного микрофона
        groups = self.f.ready(now=19.0)
        self.assertEqual(len(groups), 1)
        self.assertEqual([u.slot for u in groups[0]], [0])

    def test_group_not_held_longer_than_max_wait(self):
        self.f.add(utt(0, 10.0, 12.0), now=12.0)
        for i in range(1, 4):
            self.f.add(utt(i, 10.0, 12.0), now=12.0 + i * 0.45)
        self.assertEqual(len(self.f.ready(now=14.1)), 1)

    def test_thread_emits_group(self):
        done = threading.Event()
        f = Fuser(lambda g: (self.got.append(g), done.set()))
        f.start()
        now = f.clock()
        f.add(utt(0, now - 2, now), now=now)
        f.add(utt(1, now - 2, now), now=now)
        self.assertTrue(done.wait(3))
        self.assertEqual(len(self.got), 1)
        self.assertEqual(len(self.got[0]), 2)
        f.close()


class FakeOwner:
    def __init__(self, cfg):
        self.cfg, self.muted, self.levels, self.heard = cfg, False, [], []

    def on_level(self, slot, v):
        self.levels.append((slot, v))

    def _utterance(self, u):
        self.heard.append(u)


def feed(ch, frames):
    for fr in frames:
        ch._cb(fr.reshape(-1, 1), len(fr), None, None)


def tone(n, amp):
    return [(amp * np.sin(np.arange(480) * 0.3 + i)).astype(np.float32) for i in range(n)]


class TestChannel(unittest.TestCase):
    def run_channel(self, noise_amp, speech_amp, **kw):
        owner = FakeOwner(cfg_with())
        ch = audio._Channel(owner, kw.get("slot", 0), None)
        feed(ch, tone(40, noise_amp) + tone(30, speech_amp) + tone(40, noise_amp))
        return owner, ch

    def test_phrase_is_found_with_times_and_quality(self):
        owner, _ = self.run_channel(0.002, 0.2)
        self.assertEqual(len(owner.heard), 1)
        u = owner.heard[0]
        self.assertGreater(u.snr, 20)
        self.assertGreater(u.t1, u.t0)
        self.assertGreater(len(u.audio) / 16000, 0.45)

    def test_noisier_mic_has_lower_quality(self):
        quiet, _ = self.run_channel(0.002, 0.2)
        # тот же голос, но шум выше и речь тише
        noisy, _ = self.run_channel(0.009, 0.06)
        self.assertEqual(len(noisy.heard), 1)
        self.assertLess(noisy.heard[0].snr, quiet.heard[0].snr)

    def test_overloaded_mic_is_penalised(self):
        a = Utterance(0, np.zeros(10, np.float32), 0, 1, snr=20, peak=0.4)
        b = Utterance(0, np.zeros(10, np.float32), 0, 1, snr=20, peak=1.0)
        self.assertLess(b.quality, a.quality)

    def test_muted_hears_nothing(self):
        owner = FakeOwner(cfg_with())
        owner.muted = True
        ch = audio._Channel(owner, 0, None)
        feed(ch, tone(40, 0.002) + tone(30, 0.2) + tone(40, 0.002))
        self.assertEqual(owner.heard, [])


class FakeStream:
    opened = []
    fail_devices = set()

    def __init__(self, samplerate=None, channels=None, dtype=None, blocksize=None, device=None, callback=None):
        self.device, self.callback, self.active = device, callback, False
        if device in FakeStream.fail_devices:
            raise OSError("устройство занято")

    def start(self):
        self.active = True
        FakeStream.opened.append(self.device)

    def stop(self):
        self.active = False

    def close(self):
        pass


def fake_sd(devs=DEVS):
    """Заглушка sounddevice: номер в списке = id устройства (под номером 0 — «пустое» без входов и выходов)."""
    m = types.ModuleType("sounddevice")
    m.InputStream = FakeStream
    table = [{"name": "-", "max_input_channels": 0, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000}] * (max(d["id"] for d in devs) + 1)
    table = list(table)
    for d in devs:
        table[d["id"]] = {"name": d["name"], "max_input_channels": 1, "max_output_channels": 2, "hostapi": 0, "default_samplerate": 48000}
    m.query_devices = lambda *a, **k: {"index": 1, "name": "USB Mic"} if k.get("kind") else table
    m.query_hostapis = lambda: [{"name": "MME"}]
    return m


class TestListener(unittest.TestCase):
    def setUp(self):
        FakeStream.opened, FakeStream.fail_devices = [], set()
        self.p = mock.patch.dict(sys.modules, {"sounddevice": fake_sd()})
        self.p.start()
        self.addCleanup(self.p.stop)
        self.groups, self.statuses = [], []

    def make(self, mics):
        cfg = cfg_with(mics=mics)
        return Listener(cfg, self.groups.append, lambda s, v: None, self.statuses.append)

    def test_three_mics_open_and_report_ok(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": 2, "name": "Realtek"},
                        {"enabled": True, "device": 3, "name": "BT Headset"}, {"enabled": False}])
        try:
            st = ls.start()
            self.assertEqual([s["state"] for s in st], ["ok", "ok", "ok", "off"])
            self.assertEqual(sorted(FakeStream.opened), [1, 2, 3])
        finally:
            ls.stop()

    def test_one_failed_mic_does_not_stop_the_others(self):
        FakeStream.fail_devices = {2}
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": 2, "name": "Realtek"}])
        try:
            st = ls.start()
            self.assertEqual([s["state"] for s in st[:2]], ["ok", "error"])
            self.assertIn("занято", st[1]["text"])
        finally:
            ls.stop()

    def test_all_failed_raises(self):
        FakeStream.fail_devices = {1}
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}])
        with self.assertRaises(RuntimeError):
            ls.start()
        ls.stop()

    def test_same_device_twice_is_opened_once(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": 1, "name": "USB Mic"}])
        try:
            st = ls.start()
            self.assertEqual([s["state"] for s in st[:2]], ["ok", "dup"])
            self.assertEqual(FakeStream.opened, [1])
        finally:
            ls.stop()

    def test_extra_slot_without_device_and_unplugged_device(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": None},
                        {"enabled": True, "device": 8, "name": "Отключённый"}])
        try:
            st = ls.start()
            self.assertEqual([s["state"] for s in st[:3]], ["ok", "unset", "missing"])
        finally:
            ls.stop()

    def test_disabled_everything_is_not_an_error(self):
        ls = self.make([{"enabled": False}])
        self.assertEqual(ls.start()[0]["state"], "off")
        ls.stop()

    def test_renumbered_device_is_found_by_name(self):
        ls = self.make([{"enabled": True, "device": 7, "name": "BT Headset"}])
        try:
            self.assertEqual(ls.start()[0]["state"], "ok")
            self.assertEqual(FakeStream.opened, [3])
        finally:
            ls.stop()

    def test_single_mic_phrase_goes_straight_through(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}])
        try:
            ls.start()
            u = utt(0, 1, 3)
            ls._utterance(u)
            self.assertEqual(self.groups, [[u]])
        finally:
            ls.stop()

    def test_two_mics_hearing_the_same_phrase_give_one_group_best_first(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": 2, "name": "Realtek"}])
        try:
            ls.start()
            feed(ls.channels[0], tone(40, 0.01) + tone(30, 0.06) + tone(40, 0.01))     # далёкий, шумный
            feed(ls.channels[1], tone(40, 0.002) + tone(30, 0.3) + tone(40, 0.002))    # рядом, чистый
            for _ in range(60):
                if self.groups:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(len(self.groups), 1)
            self.assertEqual([u.slot for u in self.groups[0]], [1, 0])
        finally:
            ls.stop()

    def test_muted_listener_drops_pending_phrase(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": 2, "name": "Realtek"}])
        try:
            ls.start()
            feed(ls.channels[0], tone(40, 0.002) + tone(30, 0.3) + tone(40, 0.002))
            ls.muted = True                                   # Карина заговорила, пока ждали остальные микрофоны
            threading.Event().wait(1.2)
            self.assertEqual(self.groups, [])
        finally:
            ls.stop()

    def test_stalled_mic_is_reported_and_reopened(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}, {"enabled": True, "device": 2, "name": "Realtek"}])
        try:
            ls.start()
            ls.channels[1].last_cb -= 10               # микрофон перестал присылать звук
            ls._check(threading.Event(), retry=False)
            self.assertEqual(ls.statuses[1]["state"], "error")
            self.assertNotIn(1, ls.channels)
            ls._check(threading.Event(), retry=True)   # устройство вернулось
            self.assertEqual(ls.statuses[1]["state"], "ok")
            self.assertIn(1, ls.channels)
        finally:
            ls.stop()

    def test_statuses_published_only_on_change(self):
        ls = self.make([{"enabled": True, "device": 1, "name": "USB Mic"}])
        try:
            ls.start()
            n = len(self.statuses)
            ls._check(threading.Event(), retry=True)
            self.assertEqual(len(self.statuses), n)
        finally:
            ls.stop()


class FakeSTT:
    """Распознавалка по заготовке: номер записи (по числу отсчётов) → (текст, уверенность)."""
    def __init__(self, table):
        self.table, self.calls = table, []

    def transcribe_ex(self, audio):
        self.calls.append(len(audio))
        return self.table[len(audio)]

    def transcribe(self, audio):
        return self.transcribe_ex(audio)[0]


def assistant_with(table, assist="smart", wake=True):
    from karina.assistant import Assistant
    a = Assistant.__new__(Assistant)
    a.cfg = cfg_with(assist=assist)
    a.cfg.update({"wake_required": wake})
    a.stt, a.followup_until, a.events = FakeSTT(table), 0.0, []
    a.emit = a.events.append
    return a


def group(*lens):
    """Записи с разной длиной (по ней FakeSTT понимает, какая это запись); первая — лучшая."""
    return [Utterance(i, np.zeros(n, np.float32), 0, 1, snr=50 - i) for i, n in enumerate(lens)]


class TestRecognize(unittest.TestCase):
    def test_single_mic(self):
        a = assistant_with({100: ("карина открой ютуб", 0.9)})
        self.assertEqual(a._recognize(group(100)), "карина открой ютуб")

    def test_smart_stops_when_best_mic_was_good(self):
        a = assistant_with({100: ("карина открой ютуб", 0.9), 200: ("карина открой", 0.99)})
        self.assertEqual(a._recognize(group(100, 200)), "карина открой ютуб")
        self.assertEqual(a.stt.calls, [100])               # помощники не понадобились

    def test_smart_asks_helpers_when_best_mic_unsure(self):
        a = assistant_with({100: ("карина открой юту", 0.35), 200: ("карина открой ютуб", 0.88), 300: ("кар", 0.2)})
        self.assertEqual(a._recognize(group(100, 200, 300)), "карина открой ютуб")
        self.assertEqual(a.stt.calls, [100, 200])
        self.assertIn({"type": "mic_pick", "slot": 1}, a.events)

    def test_smart_asks_helpers_when_name_was_missed(self):
        a = assistant_with({100: ("открой ютуб", 0.9), 200: ("карина открой ютуб", 0.8)})
        self.assertEqual(a._recognize(group(100, 200)), "карина открой ютуб")

    def test_name_not_needed_in_followup_window(self):
        import time
        a = assistant_with({100: ("открой ютуб", 0.9), 200: ("карина открой ютуб", 0.8)})
        a.followup_until = time.time() + 5
        self.assertEqual(a._recognize(group(100, 200)), "открой ютуб")

    def test_best_mic_empty_helper_rescues(self):
        a = assistant_with({100: ("", 0.0), 200: ("карина громче", 0.8)})
        self.assertEqual(a._recognize(group(100, 200)), "карина громче")

    def test_all_mode_picks_best_among_all(self):
        a = assistant_with({100: ("карина открой юту", 0.75), 200: ("карина открой ютуб", 0.95)}, assist="all")
        self.assertEqual(a._recognize(group(100, 200)), "карина открой ютуб")
        self.assertEqual(a.stt.calls, [100, 200])

    def test_best_mode_uses_only_first_unless_empty(self):
        a = assistant_with({100: ("карина открой юту", 0.3), 200: ("карина открой ютуб", 0.95)}, assist="best")
        self.assertEqual(a._recognize(group(100, 200)), "карина открой юту")
        self.assertEqual(a.stt.calls, [100])

    def test_nothing_heard(self):
        a = assistant_with({100: ("", 0.0), 200: ("", 0.0)})
        self.assertEqual(a._recognize(group(100, 200)), "")


class TestTranscribeEx(unittest.TestCase):
    def test_confidence_from_model_and_junk_dropped(self):
        from karina.stt import STT
        seg = lambda t, lp, nsp=0.1, s=0.0, e=1.0: types.SimpleNamespace(text=t, avg_logprob=lp, no_speech_prob=nsp, start=s, end=e)
        s = STT(cfg_with())
        s.load = lambda *a: None
        s.model = types.SimpleNamespace(transcribe=lambda *a, **k: (iter([seg("карина открой", -0.1), seg("ютуб", -0.1, s=1, e=2), seg("шум", -2.0, nsp=0.9)]), None))
        text, conf = s.transcribe_ex(np.zeros(16000, np.float32))
        self.assertEqual(text, "карина открой ютуб")
        self.assertAlmostEqual(conf, 0.905, places=2)
        s.model = types.SimpleNamespace(transcribe=lambda *a, **k: (iter([seg("Субтитры сделал DimaTorzok", -0.2)]), None))
        self.assertEqual(s.transcribe_ex(np.zeros(16000, np.float32)), ("", 0.0))
        self.assertEqual(s.transcribe(np.zeros(16000, np.float32)), "")


class TestOutput(unittest.TestCase):
    def speaker(self, **out):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"audio_out": out})
        return tts.Speaker(cfg)

    def test_default_device_full_volume_uses_system_player(self):
        sp = self.speaker()
        with mock.patch.object(tts, "_mci_play") as mci, mock.patch.object(playback, "play_file") as pf:
            sp._play("a.mp3")
        mci.assert_called_once()
        pf.assert_not_called()

    def test_chosen_device_goes_through_sounddevice(self):
        sp = self.speaker(device=2, name="Realtek", volume=60)
        with mock.patch.object(playback, "resolve_output", return_value=(2, 0.6, True)), \
             mock.patch.object(tts, "_mci_play") as mci, mock.patch.object(playback, "play_file") as pf:
            sp._play("a.mp3")
        pf.assert_called_once_with("a.mp3", 2, 0.6, sp._stop)
        mci.assert_not_called()
        self.assertEqual(sp.out_warning, "")

    def test_lower_volume_on_default_device_also_uses_sounddevice(self):
        sp = self.speaker(volume=50)
        with mock.patch.object(playback, "play_file") as pf, mock.patch.object(tts, "_mci_play") as mci:
            sp._play("a.mp3")
        self.assertEqual(pf.call_args[0][1:3], (None, 0.5))
        mci.assert_not_called()

    def test_missing_device_falls_back_with_warning(self):
        sp = self.speaker(device=9, name="Пропавшие наушники")
        with mock.patch.object(playback, "resolve_output", return_value=(None, 1.0, False)), mock.patch.object(tts, "_mci_play") as mci:
            sp._play("a.mp3")
        mci.assert_called_once()
        self.assertIn("не найдено", sp.out_warning)

    def test_failing_device_falls_back_with_warning(self):
        sp = self.speaker(device=2, name="Realtek")
        with mock.patch.object(playback, "resolve_output", return_value=(2, 1.0, True)), \
             mock.patch.object(playback, "play_file", side_effect=OSError("занято")), mock.patch.object(tts, "_mci_play") as mci:
            sp._play("a.mp3")
        mci.assert_called_once()
        self.assertIn("занято", sp.out_warning)

    def test_stop_during_failed_playback_does_not_replay(self):
        sp = self.speaker(device=2, name="Realtek")
        sp._stop.set()
        with mock.patch.object(playback, "resolve_output", return_value=(2, 1.0, True)), \
             mock.patch.object(playback, "play_file", side_effect=OSError("x")), mock.patch.object(tts, "_mci_play") as mci:
            sp._play("a.mp3")
        mci.assert_not_called()

    def test_resolve_output_by_name_and_volume_clamp(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"audio_out": {"device": 9, "name": "BT Headset", "volume": 250}})
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd()}):
            self.assertEqual(playback.resolve_output(cfg), (3, 1.0, True))
            cfg.update({"audio_out": {"device": 9, "name": "Нет такого", "volume": -5}})
            self.assertEqual(playback.resolve_output(cfg), (None, 0.0, False))


try:
    import av  # noqa: F401
    HAVE_AV = True
except ImportError:
    HAVE_AV = False


class TestPlaybackFiles(unittest.TestCase):
    @unittest.skipUnless(HAVE_AV, "нет PyAV")
    def test_decode_resamples_to_device_format(self):
        path = str(Path(tempfile.mkdtemp()) / "t.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes((np.sin(np.arange(24000) * 0.1) * 16000).astype("<i2").tobytes())
        for rate, ch in ((48000, 2), (44100, 1)):
            d = playback.decode(path, rate, ch)
            self.assertEqual(d.dtype, np.float32)
            self.assertEqual(d.shape[1], ch)
            self.assertAlmostEqual(len(d) / rate, 1.0, delta=0.05)
            self.assertGreater(float(np.abs(d).max()), 0.3)

    def test_chime_shape(self):
        c = playback.chime(48000, 2)
        self.assertEqual(c.shape[1], 2)
        self.assertLessEqual(float(np.abs(c).max()), 0.4)

    def test_play_array_applies_volume_and_stops(self):
        written = []

        class Out:
            def __init__(self, **k): pass
            def start(self): pass
            def write(self, d): written.append(d.copy())
            def stop(self): written.append("stop")
            def abort(self): written.append("abort")
            def close(self): written.append("close")
        sd = types.ModuleType("sounddevice"); sd.OutputStream = Out
        data = np.full((5000, 2), 0.8, np.float32)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            playback.play_array(data, 48000, None, 0.5, threading.Event())
            self.assertAlmostEqual(float(written[0].max()), 0.4, places=5)
            self.assertEqual(written[-2:], ["stop", "close"])
            written.clear()
            ev = threading.Event(); ev.set()
            playback.play_array(data, 48000, None, 1.0, ev)
            self.assertEqual(written, ["abort", "close"])


class TestApi(unittest.TestCase):
    def api(self):
        from karina.ui_api import Api
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        a = Api(cfg, lambda: None)
        asst = mock.Mock()
        asst.tts.out_warning = ""
        a.bind(asst)
        a._mics = lambda: DEVS
        a._outs = lambda: [{"id": 5, "name": "Колонки", "api": "MME", "label": "Колонки"}]
        return cfg, a, asst

    def test_audio_out_set_validates(self):
        cfg, a, _ = self.api()
        a.audio_out_set(5, 70)
        self.assertEqual(cfg["audio_out"], {"device": 5, "name": "Колонки", "volume": 70})
        a.audio_out_set(99, 500)           # такого устройства нет → по умолчанию; громкость в пределах
        self.assertEqual(cfg["audio_out"], {"device": None, "name": "", "volume": 100})
        a.audio_out_set("мусор", "мусор")
        self.assertEqual(cfg["audio_out"]["volume"], 100)

    def test_audio_out_cannot_be_changed_through_save_settings(self):
        cfg, a, _ = self.api()
        a.save_settings({"audio_out": {"device": 5, "volume": 1}})
        self.assertEqual(cfg["audio_out"]["volume"], 100)

    def test_save_mics_reopens_microphones_and_keeps_old_key_in_sync(self):
        cfg, a, asst = self.api()
        a.save_settings({"stt": {"mics": [{"enabled": True, "device": 2}, {"enabled": True, "device": 3}, {"enabled": False}, {"enabled": False}], "assist": "all"}})
        asst.reload_audio.assert_called_once()
        self.assertEqual(cfg["stt"]["mic_device"], 2)
        self.assertEqual(cfg["stt"]["mics"][1], {"enabled": True, "device": 3, "name": "BT Headset"})
        self.assertEqual(cfg["stt"]["assist"], "all")

    def test_unchanged_mics_do_not_reopen(self):
        cfg, a, asst = self.api()
        a.save_settings({"stt": {"vad_threshold": 0.02}})
        asst.reload_audio.assert_not_called()

    def test_bad_assist_value_ignored(self):
        cfg, a, _ = self.api()
        a.save_settings({"stt": {"assist": "взлом"}})
        self.assertEqual(cfg["stt"]["assist"], "smart")


if __name__ == "__main__":
    unittest.main()
