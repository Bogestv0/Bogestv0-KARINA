"""Дерзкая Карина: переключатель в настройках меняет характер только в обычном чате и в простых ответах."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import chat, prompts, actions
from karina.config import Config


class Persona(unittest.TestCase):
    def tearDown(self):
        chat.PERSONA_PROVIDER = lambda: ""

    def test_default_is_off(self):
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.assertFalse(cfg["persona"]["rude"])
        self.assertNotIn("ХАРАКТЕР", chat.system_prompt())

    def test_rude_prompt_only_in_chat(self):
        chat.PERSONA_PROVIDER = lambda: prompts.PERSONA_RUDE
        self.assertIn("ХАРАКТЕР", chat.system_prompt())
        self.assertIn("ХАРАКТЕР", chat.system_prompt(voice=True))
        from karina import coder
        self.assertNotIn("ХАРАКТЕР", coder.system_prompt(False))

    def test_smalltalk_variants(self):
        self.assertEqual(set(actions._SMALL), set(actions._SMALL_RUDE))
        for k, v in actions._SMALL_RUDE.items():
            self.assertTrue(v, k)


if __name__ == "__main__":
    unittest.main()
