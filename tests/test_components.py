"""Компоненты (torch, голос, модели) идут не в установщике, а отдельным архивом, который докачивает Карина."""
import json, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from karina import components


def make_zip(path: Path, with_torch=True):
    with zipfile.ZipFile(path, "w") as z:
        if with_torch:
            z.writestr("pylibs/torch/__init__.py", "")
        z.writestr("voices/silero_v5_6_ru.pt", "x" * 10)
        z.writestr("models/models--Systran--faster-whisper-small/refs/main", "abc")


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = [mock.patch.object(components, "DATA", self.tmp), mock.patch.object(components, "COMP", self.tmp / "components"),
                  mock.patch.object(components, "PYLIBS", self.tmp / "components" / "pylibs"),
                  mock.patch.object(components, "MARK", self.tmp / "components" / "installed.json")]
        for x in self.p:
            x.start()
            self.addCleanup(x.stop)

    def test_install_zip_puts_files_in_place(self):
        z = self.tmp / "pack.zip"
        make_zip(z)
        self.assertFalse(components.is_installed())
        components.install_zip(z)
        self.assertTrue(components.is_installed())
        self.assertTrue((self.tmp / "voices" / "silero_v5_6_ru.pt").exists())
        self.assertTrue((self.tmp / "models" / "models--Systran--faster-whisper-small" / "refs" / "main").exists())
        self.assertEqual(json.loads((self.tmp / "components" / "installed.json").read_text())["version"], components.COMPONENTS_VERSION)

    def test_broken_pack_is_rejected(self):
        z = self.tmp / "bad.zip"
        make_zip(z, with_torch=False)
        with self.assertRaises(components.ComponentsError):
            components.install_zip(z)
        self.assertFalse(components.is_installed())

    def test_ensure_does_nothing_from_sources(self):
        with mock.patch.object(components, "FROZEN", False):
            self.assertFalse(components.ensure())

    def test_names_follow_version(self):
        self.assertIn(components.__version__, components.pack_name())
        self.assertTrue(components.release_base().endswith(f"/v{components.__version__}/"))

    def test_build_scripts_exist_and_installer_is_light(self):
        self.assertTrue((ROOT / "build" / "make_pack.py").exists())
        spec = (ROOT / "build" / "karina.spec").read_text("utf-8")
        self.assertIn('"torch"', spec.split("excludes")[1])
        iss = (ROOT / "build" / "installer.iss").read_text("utf-8-sig")
        self.assertNotIn("whisper_models", iss)
        wf = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
        self.assertNotIn("fetch_engine.py", wf)


if __name__ == "__main__":
    unittest.main()
