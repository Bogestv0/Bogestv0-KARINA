"""Офлайн-голос Piper убран в 1.12.1: у Карины один голос (Silero «Байя»). Этот файл заменяет старые тесты Piper; его можно просто удалить."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import tts


class OneVoiceOnly(unittest.TestCase):
    def test_single_voice(self):
        self.assertEqual(list(tts.VOICES), ["karina"])


if __name__ == "__main__":
    unittest.main()
