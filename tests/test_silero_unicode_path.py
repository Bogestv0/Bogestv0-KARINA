"""Голос Silero не грузился, если имя пользователя Windows кириллицей: torch открывал путь через fopen. Теперь модель читается Python-ом."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import silero_voice as sv


class UnicodePath(unittest.TestCase):
    def test_stream_reads_cyrillic_path(self):
        with tempfile.TemporaryDirectory(prefix="Алексей_") as d:
            p = Path(d) / "voices" / "silero_v5_6_ru.pt"
            p.parent.mkdir()
            p.write_bytes(b"abc" * 1000)
            self.assertEqual(sv.model_stream(p).read(), b"abc" * 1000)

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            sv.model_stream(Path(tempfile.gettempdir()) / "нет_такого.pt")


if __name__ == "__main__":
    unittest.main()
