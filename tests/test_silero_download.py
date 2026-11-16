"""Скачивание голоса Silero: обычный путь, запасной через curl при ошибке сертификата, обновление без потери старого голоса."""
import io, ssl, sys, tempfile, threading, unittest, urllib.error
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import silero_voice as sv


class Resp(io.BytesIO):
    status = 200
    def __init__(self, data):
        super().__init__(data); self.headers = {"Content-Length": str(len(data))}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def cert_error():
    return urllib.error.URLError(ssl.SSLCertVerificationError("certificate has expired"))


class Download(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_normal_download(self):
        with mock.patch.object(sv.urllib.request, "urlopen", return_value=Resp(b"x" * 1_500_000)):
            p = sv.download(self.tmp)
        self.assertEqual(p.stat().st_size, 1_500_000)
        self.assertTrue(sv.is_ready(self.tmp))

    def test_certificate_error_falls_back_to_curl(self):
        def fake_curl(part, prog, cancel):
            part.write_bytes(b"y" * 1_200_000)
        with mock.patch.object(sv.urllib.request, "urlopen", side_effect=cert_error()), mock.patch.object(sv, "_download_curl", side_effect=fake_curl) as c:
            sv.download(self.tmp)
        c.assert_called_once()
        self.assertTrue(sv.is_ready(self.tmp))

    def test_curl_missing_gives_manual_instructions(self):
        with mock.patch.object(sv.urllib.request, "urlopen", side_effect=cert_error()), mock.patch.object(sv, "_download_curl", side_effect=FileNotFoundError("curl не найден")):
            with self.assertRaises(RuntimeError) as c:
                sv.download(self.tmp)
        self.assertIn("сертификат", str(c.exception)); self.assertIn("silero_v5_6_ru.pt", str(c.exception))
        self.assertFalse(sv.is_ready(self.tmp))

    def test_update_keeps_old_voice_if_it_fails(self):
        sv.model_path(self.tmp).parent.mkdir(parents=True)
        sv.model_path(self.tmp).write_bytes(b"old" * 500_000)
        with mock.patch.object(sv.urllib.request, "urlopen", side_effect=urllib.error.URLError("нет сети")):
            with self.assertRaises(urllib.error.URLError):
                sv.download(self.tmp, fresh=True)
        self.assertEqual(sv.model_path(self.tmp).read_bytes()[:3], b"old")

    def test_update_replaces_old_voice_and_unloads_model(self):
        sv.model_path(self.tmp).parent.mkdir(parents=True)
        sv.model_path(self.tmp).write_bytes(b"old" * 500_000)
        with mock.patch.object(sv.urllib.request, "urlopen", return_value=Resp(b"n" * 1_600_000)), mock.patch.object(sv, "unload") as un:
            sv.download(self.tmp, fresh=True)
        self.assertEqual(sv.model_path(self.tmp).read_bytes()[:1], b"n")
        un.assert_called()
        for suf in (".new", ".new.part"):
            self.assertFalse(sv.model_path(self.tmp).with_name(sv.model_path(self.tmp).name + suf).exists())

    def test_update_when_file_is_locked_is_applied_on_next_start(self):
        d = sv.model_path(self.tmp); d.parent.mkdir(parents=True)
        d.write_bytes(b"old" * 500_000)
        real = Path.replace
        def locked(self_, target):
            if Path(target) == d and self_.name.endswith(".new"):
                raise PermissionError(5, "Отказано в доступе")
            return real(self_, target)
        with mock.patch.object(sv.urllib.request, "urlopen", return_value=Resp(b"n" * 1_600_000)), mock.patch.object(Path, "replace", locked), mock.patch.object(sv.time, "sleep"):
            with self.assertRaises(RuntimeError) as c:
                sv.download(self.tmp, fresh=True)
        self.assertIn("следующем запуске", str(c.exception))
        self.assertEqual(d.read_bytes()[:3], b"old")
        self.assertTrue(sv.apply_pending(self.tmp))
        self.assertEqual(d.read_bytes()[:1], b"n")

    def test_unfinished_update_is_never_applied(self):
        d = sv.model_path(self.tmp); d.parent.mkdir(parents=True)
        d.write_bytes(b"old" * 500_000)
        d.with_name(d.name + ".new.part").write_bytes(b"n" * 2_000_000)
        self.assertFalse(sv.apply_pending(self.tmp))
        self.assertEqual(d.read_bytes()[:3], b"old")

    def test_delete_removes_all_parts(self):
        d = sv.model_path(self.tmp); d.parent.mkdir(parents=True)
        for suf in ("", ".part", ".new", ".new.part"):
            d.with_name(d.name + suf).write_bytes(b"1")
        sv.delete(self.tmp)
        self.assertEqual(list(d.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
