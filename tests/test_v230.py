"""1.23.0: DeepSeek, расширенный профиль (пол, пояс, партнёр…), синхронизация между компьютерами через Google Drive (шифрование персональным кодом)."""
import json, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import cloud, google_auth, profile as prof, sync as S
from karina.config import Config


class FakeDrive:
    store: dict = {}

    def __init__(self, token):
        pass

    def find(self):
        return "F1" if "F1" in self.store else ""

    def read(self, fid):
        return json.loads(json.dumps(self.store[fid]))

    def write(self, fid, blob):
        self.store["F1"] = json.loads(json.dumps(blob))
        return "F1"


def make(name):
    cfg = Config(Path(tempfile.mkdtemp()) / "config.json")
    cfg.update({"profile": {"google": {"email": "a@b.c"}}, "google": {"client_id": "x"}})
    sy = S.Sync(cfg, drive_factory=FakeDrive)
    sy._set(device_name=name)
    return cfg, sy


class TestDeepseek(unittest.TestCase):
    def test_preset(self):
        p = cloud.PRESETS["deepseek"]
        self.assertEqual(p["url"], "https://api.deepseek.com")
        self.assertIn("deepseek", cloud.DEFAULT_ORDER)

    def test_provider_in_pool(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"cloud": {"providers": {"deepseek": {"enabled": True, "key": "sk-test"}}}})
        pool = cloud.CloudPool(cfg)
        self.assertEqual([p["id"] for p in pool.configured()], ["deepseek"])
        self.assertIn("deepseek", [d["id"] for d in pool.describe()])


class TestProfile(unittest.TestCase):
    def test_new_fields_cleaned(self):
        c = prof.clean({"name": "Миша", "gender": "robot", "timezone": "Asia/Krasnoyarsk", "country": "Россия",
                        "partner": {"kind": "girl", "name": "Катя", "birth_day": 31, "birth_month": 2}})
        self.assertEqual(c["gender"], "")                                    # неизвестное значение отброшено
        self.assertEqual(c["timezone"], "Asia/Krasnoyarsk")
        self.assertEqual(c["partner"]["kind"], "girl")
        self.assertEqual(c["partner"]["birth_day"], 0)                       # 31 февраля — не бывает

    def test_bad_timezone_and_partner_kind(self):
        c = prof.clean({"timezone": "<script>", "partner": {"kind": "x", "name": "Q"}})
        self.assertEqual(c["timezone"], "")
        self.assertEqual(c["partner"], {"kind": "none", "name": "", "birth_day": 0, "birth_month": 0})

    def test_utc_offset_timezone(self):
        self.assertTrue(prof.valid_timezone("UTC+07:00"))
        self.assertFalse(prof.valid_timezone("UTC+25:00"))
        self.assertEqual(prof.now({"timezone": "UTC+07:00"}).utcoffset().total_seconds(), 7 * 3600)

    def test_partner_birthday_greeting_and_prompt(self):
        import datetime
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"profile": prof.clean({"name": "Миша", "gender": "male", "timezone": "UTC+03:00", "occupation": "разработчик",
                                           "partner": {"kind": "girl", "name": "Катя", "birth_day": 8, "birth_month": 3}})})
        self.assertIn("вашей девушки, Катя", prof.due_greeting(cfg, datetime.date(2026, 3, 8)))
        sec = prof.prompt_section(cfg)
        for w in ("Миша", "мужчина", "UTC+03:00", "девушка по имени Катя", "разработчик"):
            self.assertIn(w, sec)

    def test_privacy_switch_hides_everything(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        cfg.update({"profile": prof.clean({"name": "Миша", "about": "секрет", "share_with_llm": False})})
        self.assertEqual(prof.prompt_section(cfg), "")


class TestCrypto(unittest.TestCase):
    def test_roundtrip_and_wrong_code(self):
        code = S.new_code()
        self.assertTrue(S.code_ok(code))
        blob = S.encrypt(code, {"k": "ключ-секрет"})
        self.assertNotIn("секрет", json.dumps(blob))
        self.assertEqual(S.decrypt(code, blob), {"k": "ключ-секрет"})
        with self.assertRaises(S.SyncError):
            S.decrypt(S.new_code(), blob)

    def test_tampering_detected(self):
        code = S.new_code()
        blob = S.encrypt(code, {"a": 1})
        ct = bytearray(S._unb64(blob["ct"]))
        ct[0] ^= 1
        blob["ct"] = S._b64(bytes(ct))
        with self.assertRaises(S.SyncError):
            S.decrypt(code, blob)

    def test_code_is_forgiving(self):
        code = S.new_code()
        self.assertTrue(S.code_ok(code.lower().replace("-", " ")))
        self.assertFalse(S.code_ok("KRN-1234"))


class TestTwoComputers(unittest.TestCase):
    def setUp(self):
        FakeDrive.store = {}
        self._old = google_auth.refresh_access
        google_auth.refresh_access = lambda *a: "tok"

    def tearDown(self):
        google_auth.refresh_access = self._old

    def test_full_scenario(self):
        cfgA, A = make("PC-A")
        cfgB, B = make("PC-B")
        cfgA.update({"profile": {"name": "Миша", "timezone": "Asia/Krasnoyarsk", "partner": {"kind": "girl", "name": "Катя"}},
                     "llm": {"api_key": "sk-ant-SECRET", "enabled": True}, "cloud": {"providers": {"deepseek": {"enabled": True, "key": "ds-KEY"}}}})
        r = A.on_login({"refresh_token": "rt", "drive": True})
        self.assertEqual(r["kind"], "new_code")
        raw = json.dumps(FakeDrive.store)
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("ds-KEY", raw)                                       # в Drive ключи только в зашифрованном виде
        self.assertEqual(B.on_login({"refresh_token": "rt2", "drive": True})["kind"], "need_code")
        with self.assertRaises(S.SyncError):
            B.enter_code("KRN-AAAA-BBBB-CCCC-DDDD")
        self.assertFalse(B.state()["enabled"])
        B.enter_code(r["code"].lower())
        self.assertEqual(prof.get(cfgB)["partner"]["name"], "Катя")
        self.assertEqual(cfgB["llm"]["api_key"], "sk-ant-SECRET")
        self.assertEqual(cfgB["cloud"]["providers"]["deepseek"]["key"], "ds-KEY")
        self.assertEqual(prof.get(cfgB)["google"]["email"], "a@b.c")          # вход Google у каждого компьютера свой
        self.assertEqual(len(B.state()["devices"]), 2)
        A.sync_now()
        self.assertEqual(len(A.state()["devices"]), 2)                        # «авторизовано на 2 устройствах»
        cfgB.update({"profile": {"city": "Красноярск"}})
        B._set(changed=time.time())
        self.assertEqual(B.sync_now()["did"], "push")
        self.assertEqual(A.sync_now()["did"], "pull")
        self.assertEqual(prof.get(cfgA)["city"], "Красноярск")
        A.remove_device(B.device_id())
        self.assertEqual(B.sync_now()["did"], "removed")
        self.assertFalse(B.state()["enabled"])

    def test_no_drive_scope_means_no_sync(self):
        cfg, A = make("PC")
        self.assertEqual(A.on_login({"refresh_token": "", "drive": False})["kind"], "no_access")
        self.assertFalse(A.state()["enabled"])

    def test_reset_replaces_code(self):
        cfgA, A = make("PC-A")
        code = A.on_login({"refresh_token": "rt", "drive": True})["code"]
        cfgB, B = make("PC-B")
        B.on_login({"refresh_token": "rt", "drive": True})
        new = B.reset()
        self.assertNotEqual(new, code)
        self.assertEqual(len(B.state()["devices"]), 1)
        with self.assertRaises(S.SyncError):
            A._load(FakeDrive("t"), "F1", code)                               # старый код больше не подходит

    def test_disconnect_keeps_local_data(self):
        cfg, A = make("PC")
        cfg.update({"profile": {"name": "Миша"}})
        A.on_login({"refresh_token": "rt", "drive": True})
        A.disconnect()
        self.assertFalse(A.state()["enabled"])
        self.assertEqual(cfg["sync"]["code"], "")
        self.assertEqual(prof.get(cfg)["name"], "Миша")


if __name__ == "__main__":
    unittest.main()
