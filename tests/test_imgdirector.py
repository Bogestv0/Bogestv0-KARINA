"""Режиссёр картинок: текстовая модель пишет описание и выбирает художника, варианты рисуют разные модели, правки идут к подходящей модели или ко всем."""
import random, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from karina import chat, images, imgdirector as d, imgpower, localimg
from karina.chat import ChatService, ChatStore, route
from karina.config import Config
from karina.files import FileService
from test_chat_coder import FakeBrain
from test_coder import Scripted

def _png() -> bytes:
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 120, 90)).save(out, "PNG")
    return out.getvalue()


PNG = _png()
IDS = ["noobai", "ponyxl", "flux2klein"]
BRIEF = ("TAGS: anime illustration, 2D, 1girl, long white hair, blue eyes, school uniform, portrait, upper body\n"
         "SCENE: An anime girl with long white hair and blue eyes in a school uniform, upper body portrait.\n"
         "BEST: noobai\nWHY: лучше всего рисует лица и причёски в аниме-стиле")


class Intent(unittest.TestCase):
    def test_what_user_asks(self):
        i = d.parse_intent("нарисуй 2 варианта аниме девушки", IDS, False)
        self.assertEqual((i.kind, i.count), ("new", 2))
        self.assertEqual(d.parse_intent("Сделай фото кота", IDS, False).count, 4)
        i = d.parse_intent("а на втором варианте сделай волосы длиннее", IDS, True)
        self.assertEqual((i.kind, i.refs), ("edit", [2]))
        self.assertTrue(d.parse_intent("во всех моделях пусть она улыбается", IDS, True).everyone)
        self.assertEqual(d.parse_intent("через pony пусть она машет рукой", IDS, True).models, ["ponyxl"])
        self.assertEqual(d.parse_intent("ещё вариант", IDS, True).kind, "more")
        self.assertEqual(d.parse_intent("ещё 3 варианта", IDS, True).kind, "more")
        self.assertEqual(d.parse_intent("нарисуй другую девушку", IDS, True).kind, "new")       # новая просьба — не правка
        self.assertEqual(d.parse_intent("нарисуй пони", IDS, False).models, [])                 # «пони» без предлога — лошадка, а не модель
        self.assertEqual(d.parse_intent("через пони", IDS, True).models, ["ponyxl"])
        self.assertEqual(d.parse_intent("на 3 варианте пусть сидит", IDS, True).refs, [3])

    def test_edits_are_images_only_after_a_picture(self):
        for t in ("поверни её вполоборота", "пусть она машет рукой", "на втором варианте сделай волосы длиннее", "во всех моделях сделай ночь", "сделай через pony"):
            self.assertEqual(route(t, False, False, last_image=True), "image", t)
            self.assertNotEqual(route(t, False, False, last_image=False), "image", t)
        self.assertNotEqual(route("добавь в код тесты", False, False, last_image=True), "image")       # про код — не картинка


class Rank(unittest.TestCase):
    def test_who_fits(self):
        self.assertEqual(d.rank("поверни её", IDS, "аниме девушка")[0], "ponyxl")                 # позы и действия — Pony
        self.assertEqual(d.rank("смени цвет волос", IDS, "аниме девушка")[0], "noobai")           # внешность аниме-персонажа — NoobAI
        self.assertEqual(d.rank("сделай фон закат", IDS, "нарисуй кота")[0], "flux2klein")        # фон, свет — FLUX
        self.assertEqual(d.rank("сделай фото кота", IDS)[0], "flux2klein")
        self.assertEqual(d.rank("нарисуй аниме девушку", IDS)[0], "noobai")

    def test_photo_only_by_flux(self):
        pool, note = d.pool_for("сделай фото кота", IDS)
        self.assertEqual(pool, ["flux2klein"]); self.assertIn("FLUX", note)
        self.assertEqual(d.pool_for("нарисуй аниме", IDS)[0], IDS)
        self.assertEqual(d.pool_for("сделай фото кота", ["noobai"])[0], ["noobai"])               # реалистичной модели нет — рисуют те, что есть

    def test_all_variants_use_one_style(self):
        v = d.plan_new(d.Intent(count=4), ["noobai", "ponyxl", "flux2klein"], random.Random(1))
        self.assertEqual([x.model for x in v], ["noobai"] * 4)                       # фирменный стиль один на все варианты
        self.assertEqual(len({x.seed for x in v}), 4)                                # у повторов другой seed
        v = d.plan_new(d.Intent(count=2, models=["ponyxl", "noobai"]), ["noobai", "ponyxl"], random.Random(1))
        self.assertEqual([x.model for x in v], ["ponyxl", "noobai"])                 # а если модели названы вручную — по кругу по ним
        v = d.plan_new(d.Intent(count=4), ["ponyxl"], random.Random(1))
        self.assertEqual([x.model for x in v], ["ponyxl"] * 4)

    def test_edit_plans(self):
        prev = {"variants": [{"n": 1, "model": "noobai", "seed": 11}, {"n": 2, "model": "ponyxl", "seed": 22}, {"n": 3, "model": "flux2klein", "seed": 33}]}
        order = ["ponyxl", "noobai", "flux2klein"]
        v, why = d.plan_edit(d.Intent(kind="edit"), order, prev)                                    # без указаний — один подходящий, прежний seed
        self.assertEqual([(x.model, x.seed) for x in v], [("ponyxl", 22)])
        v, why = d.plan_edit(d.Intent(kind="edit", refs=[3]), order, prev)                          # выбранный вариант правит тот же художник
        self.assertEqual([(x.model, x.seed) for x in v], [("flux2klein", 33)]); self.assertIn("тот же", why)
        v, why = d.plan_edit(d.Intent(kind="edit", everyone=True), order, prev)                     # ко всем
        self.assertEqual({x.model: x.seed for x in v}, {"ponyxl": 22, "noobai": 11, "flux2klein": 33})
        v, why = d.plan_edit(d.Intent(kind="edit", models=["noobai"]), order, prev)
        self.assertEqual([(x.model, x.seed) for x in v], [("noobai", 11)])


class Brief(unittest.TestCase):
    def test_parse(self):
        b = d.parse_brief(BRIEF, "x", IDS)
        self.assertTrue(b["ok"]); self.assertEqual(b["best"], "noobai")
        self.assertTrue(b["tags"].startswith("anime illustration")); self.assertTrue(b["scene"].startswith("An anime girl"))
        self.assertIn("лица", b["why"]); self.assertEqual(b["ru"], "")
        b = d.parse_brief("TAGS: 1girl\nSCENE: A girl.\nRU: Персонаж: девушка; Поза: лежит\nBEST: noobai\nWHY: x", "x", IDS)
        self.assertEqual(b["ru"], "Персонаж: девушка; Поза: лежит")
        b = d.parse_brief("<think>думаю…</think>\n**TAGS:** a, b, c, d\n**SCENE:** A nice picture.\n**BEST:** Pony XL", "x", IDS)
        self.assertEqual((b["tags"], b["best"]), ("a, b, c, d", "ponyxl"))
        b = d.parse_brief("1girl, white hair, forest", "x", IDS)                                    # без формата — весь ответ как описание
        self.assertEqual((b["ok"], b["tags"], b["scene"]), (False, "1girl, white hair, forest", "1girl, white hair, forest"))
        b = d.parse_brief("<think>очень долго думаю", "нарисуй кота на крыше", IDS)                  # размышление оборвано — берём просьбу
        self.assertEqual(b["tags"], "нарисуй кота на крыше")
        self.assertEqual(d.parse_brief("TAGS: a, b, c, d\nSCENE: x\nBEST: unknown", "x", IDS)["best"], "")

    def test_each_artist_gets_its_own_language(self):
        brief = {"tags": "1girl, white hair", "scene": "A girl with white hair."}
        self.assertIn("1girl, white hair", d.prompt_for("noobai", brief, "аниме"))
        self.assertNotIn("A girl", d.prompt_for("noobai", brief, "аниме"))
        self.assertIn("A girl with white hair", d.prompt_for("flux2klein", brief, "аниме"))
        self.assertNotIn("1girl", d.prompt_for("flux2klein", brief, "аниме"))
        self.assertIn("realistic", d.negative_for("noobai", "аниме"))                               # аниме: против «кукольной» рисовки
        self.assertNotIn("realistic", d.negative_for("noobai", "обычный рисунок"))
        self.assertEqual(d.negative_for("flux2klein", "аниме"), "")                                 # у FLUX негативного промпта нет

    def test_request_to_model(self):
        m = d.brief_request("нарисуй аниме девушку", IDS)
        self.assertIn("flux2klein", m[0]["content"]); self.assertIn("ponyxl", m[0]["content"]); self.assertEqual(m[1]["content"], "Request: нарисуй аниме девушку")
        m = d.brief_request("поверни её", IDS, {"tags": "1girl, white hair", "scene": "A girl."}, True)
        self.assertIn("TAGS: 1girl, white hair", m[1]["content"]); self.assertIn("Change requested: поверни её", m[1]["content"])
        self.assertNotIn("flux2klein", d.brief_request("x", ["noobai"])[0]["content"])             # про нескачанные модели не упоминается


class StudioKit:
    """Чат целиком: настоящий ChatService, подменены только модель текста и сама рисовалка."""

    GPU = {"detected": True, "gpu": "Test GPU 24GB", "vram_gb": 24.0, "integrated": False, "gpus": []}      # тесты не зависят от железа, на котором запущены
    INSTALLED = IDS

    def setUp(self):
        self.orig = (localimg.installed_ids, localimg.engine_ready, localimg.engine_supports, localimg.generate, images.generate, imgpower._HW)
        imgpower._HW = dict(self.GPU)
        self.calls, self.broken = [], set()
        localimg.installed_ids = lambda: list(self.INSTALLED)
        localimg.engine_ready = lambda: True
        localimg.engine_supports = lambda m: True

        def fake(prompt, size, seed, model_id="", negative="", stop=None, vram="", plan=None, progress=None, init_image=None, strength=0.6, exact_size=False, no_cpu=False):
            self.calls.append({"prompt": prompt, "size": size, "seed": seed, "model": model_id, "neg": negative, "init": init_image is not None, "strength": strength,
                               "plan": plan})
            if model_id in self.broken:
                raise localimg.LocalImgError("видеокарте не хватило памяти")
            if progress:
                progress(1, 2, 1.0)
            return PNG, "png"
        localimg.generate = fake

    def tearDown(self):
        localimg.installed_ids, localimg.engine_ready, localimg.engine_supports, localimg.generate, images.generate, imgpower._HW = self.orig

    def make(self, replies, ask=False, **images_cfg):
        tmp = Path(tempfile.mkdtemp())
        cfg = Config(tmp / "c.json")
        cfg.update({"coder": {"run": False}, "search": {"mode": "off"}, "images": dict({"ask_count": ask}, **images_cfg)})
        model = Scripted(replies)
        ev = []
        svc = ChatService(cfg, model, ev.append, store=ChatStore(tmp / "chats"), brain=FakeBrain(model), files=FileService(tmp / "Карина"))
        return svc, model, ev, tmp


class Studio(StudioKit, unittest.TestCase):
    """Путь для фото и реализма (FLUX.2) и запасные пути: внешний сервис, режиссёр выключен, всё упало."""

    def test_photo_is_drawn_only_by_flux(self):
        svc, model, ev, tmp = self.make(["TAGS: cat, roof\nSCENE: A photo of a cat on a roof at sunset.\nBEST: flux2klein\nWHY: фото"])
        cid = svc.send(None, "Сделай фотографию кота на крыше", wait=True)
        self.assertEqual({c["model"] for c in self.calls}, {"flux2klein"})
        self.assertEqual(len(self.calls), 4)
        self.assertIn("Для фото подходит только", svc.store.get(cid)["messages"][1]["text"])
        self.assertNotIn("draft", svc.store.get(cid))                               # фото: прежний путь, подтверждения нет

    def test_all_fail_falls_back_to_online_generator(self):
        self.broken = set(IDS)
        seen = {}

        def online(cfg, prompt, size=(768, 768), seed=None, stop=None, request="", progress=None):
            seen["provider"] = cfg.get("images")["provider"]
            return PNG, "png", "Pollinations"
        images.generate = online
        svc, model, ev, tmp = self.make(["TAGS: cat, roof\nSCENE: A photo of a cat.\nBEST: flux2klein\nWHY: x"])
        cid = svc.send(None, "сделай фото кота", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual(seen["provider"], "pollinations")
        self.assertEqual(len(reply["files"]), 1); self.assertIn("запасной генератор", reply["text"])

    def test_external_provider_keeps_old_single_picture_path(self):
        svc, model, ev, tmp = self.make(["anime girl, white hair"])
        svc.cfg.update({"images": {"provider": "pollinations"}})
        images.generate = lambda cfg, prompt, size=(768, 768), seed=None, stop=None, request="", progress=None: (PNG, "png", "Pollinations")
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual(len(reply["files"]), 1); self.assertEqual(self.calls, []); self.assertIn("Готово — нарисовала", reply["text"])

    def test_director_can_be_switched_off(self):
        svc, model, ev, tmp = self.make(["anime girl, white hair"])
        svc.cfg.update({"images": {"director": False}})
        images.generate = lambda cfg, prompt, size=(768, 768), seed=None, stop=None, request="", progress=None: (PNG, "png", "встроенный генератор")
        svc.send(None, "нарисуй аниме девушку", wait=True)
        self.assertEqual(self.calls, [])                                                                          # режиссёр выключен — прежний путь


class AskCount(StudioKit, unittest.TestCase):
    """Для фото по-прежнему «Сколько вариантов?»: новая просьба ждёт ответа 1–4 (у аниме вместо этого подтверждение — см. test_imgflow)."""

    def test_asks_then_draws_chosen_number(self):
        svc, model, ev, tmp = self.make(["TAGS: cat\nSCENE: A cat photo.\nBEST: flux2klein\nWHY: x"], ask=True)
        cid = svc.send(None, "Сделай фотографию кота", wait=True)
        c = svc.store.get(cid)
        self.assertEqual(self.calls, [])
        self.assertEqual(c["messages"][1]["choices"], ["1", "2", "3", "4"])
        svc.send(cid, "2", wait=True)
        self.assertEqual(len(self.calls), 2)

    def test_parse_answer(self):
        for t, n in (("3", 3), ("Три", 3), ("2 варианта", 2), (" 1. ", 1), ("одну", 1)):
            self.assertEqual(d.parse_answer(t), n, t)
        for t in ("0", "5", "нарисуй кота", "", "я хочу 3 кота"):
            self.assertIsNone(d.parse_answer(t), t)


if __name__ == "__main__":
    unittest.main()
