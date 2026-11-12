"""Режиссёр картинок: текстовая модель пишет описание и выбирает художника, варианты рисуют разные модели, правки идут к подходящей модели или ко всем."""
import random, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from karina import chat, images, imgdirector as d, localimg
from karina.chat import ChatService, ChatStore, route
from karina.config import Config
from karina.files import FileService
from test_chat_coder import FakeBrain
from test_coder import Scripted

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 60
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

    def test_four_variants_cycle_through_styles(self):
        v = d.plan_new(d.Intent(count=4), ["noobai", "ponyxl", "flux2klein"], random.Random(1))
        self.assertEqual([x.model for x in v], ["noobai", "ponyxl", "flux2klein", "noobai"])
        self.assertEqual(len({x.seed for x in v}), 4)                                              # у повторов другой seed
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
        self.assertIn("лица", b["why"])
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


class Studio(unittest.TestCase):
    """Чат целиком: настоящий ChatService, подменены только модель текста и сама рисовалка."""

    def setUp(self):
        self.orig = (localimg.installed_ids, localimg.engine_ready, localimg.engine_supports, localimg.generate, images.generate)
        self.calls, self.broken = [], set()
        localimg.installed_ids = lambda: list(IDS)
        localimg.engine_ready = lambda: True
        localimg.engine_supports = lambda m: True

        def fake(prompt, size, seed, model_id="", negative="", stop=None):
            self.calls.append({"prompt": prompt, "size": size, "seed": seed, "model": model_id, "neg": negative})
            if model_id in self.broken:
                raise localimg.LocalImgError("видеокарте не хватило памяти")
            return PNG, "png"
        localimg.generate = fake

    def tearDown(self):
        localimg.installed_ids, localimg.engine_ready, localimg.engine_supports, localimg.generate, images.generate = self.orig

    def make(self, replies, ask=False):
        tmp = Path(tempfile.mkdtemp())
        cfg = Config(tmp / "c.json")
        cfg.update({"coder": {"run": False}, "search": {"mode": "off"}, "images": {"ask_count": ask}})
        model = Scripted(replies)
        ev = []
        svc = ChatService(cfg, model, ev.append, store=ChatStore(tmp / "chats"), brain=FakeBrain(model), files=FileService(tmp / "Карина"))
        return svc, model, ev, tmp

    def test_four_variants_in_styles_of_different_models(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "Сделай изображения аниме девушки с белыми волосами", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual([c["model"] for c in self.calls], ["noobai", "ponyxl", "flux2klein", "noobai"])        # NoobAI назван лучшим → первый и четвёртый
        self.assertEqual(len(reply["files"]), 4)
        self.assertEqual([f["variant"] for f in reply["files"]], [1, 2, 3, 4])
        self.assertEqual([f["label"] for f in reply["files"]][:3], ["NoobAI XL 1.1", "Pony Diffusion V6 XL", "FLUX.2 Klein 4B"])
        for f in reply["files"]:
            self.assertTrue((tmp / "Карина" / f["name"]).read_bytes().startswith(b"\x89PNG"))
        tags_models = [c for c in self.calls if c["model"] != "flux2klein"]
        self.assertTrue(all("1girl, long white hair" in c["prompt"] for c in tags_models))                       # NoobAI и Pony получили теги
        flux = [c for c in self.calls if c["model"] == "flux2klein"][0]
        self.assertIn("An anime girl with long white hair", flux["prompt"]); self.assertNotIn("1girl", flux["prompt"])    # FLUX — предложения
        self.assertEqual(self.calls[0]["size"], (768, 1024))
        types = [e["type"] for e in ev]
        self.assertEqual(types[0], "chat_start"); self.assertEqual(types[-1], "chat_done")
        self.assertIn("chat_image_plan", types)
        done = [e for e in ev if e["type"] == "chat_image" and e["status"] == "done"]
        self.assertEqual([e["n"] for e in done], [1, 2, 3, 4])                                                   # каждый вариант уходит в окно по готовности
        self.assertIn("4 варианта", reply["text"]); self.assertIn("Описание тегами", reply["text"])
        self.assertEqual(len(reply["image_set"]["variants"]), 4); self.assertTrue(reply["image_prompt"])
        self.assertIn("NoobAI", reply["brain"])

    def test_variant_count_from_request_and_settings(self):
        svc, model, ev, tmp = self.make([BRIEF])
        svc.send(None, "нарисуй аниме девушку, 2 варианта", wait=True)
        self.assertEqual(len(self.calls), 2)
        self.calls.clear()
        svc2, model2, ev2, tmp2 = self.make([BRIEF])
        svc2.cfg.update({"images": {"variants": 3}})
        svc2.send(None, "нарисуй аниме девушку", wait=True)
        self.assertEqual([c["model"] for c in self.calls], ["noobai", "ponyxl", "flux2klein"])

    def test_edit_goes_to_best_model_and_keeps_character(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку с белыми волосами", wait=True)
        first = svc.store.get(cid)["messages"][1]["image_set"]
        pony_seed = next(v["seed"] for v in first["variants"] if v["model"] == "ponyxl")
        self.calls.clear()
        model.replies.append("TAGS: anime illustration, 2D, 1girl, long white hair, blue eyes, school uniform, from side, profile, waving\n"
                             "SCENE: An anime girl with long white hair waves, seen from the side.\nBEST: ponyxl\nWHY: Pony лучше всех справляется с позами")
        svc.send(cid, "поверни её боком и пусть машет рукой", wait=True)
        self.assertIn("Change requested: поверни её боком", model.seen[-1][1]["content"])
        self.assertIn("TAGS: anime illustration, 2D, 1girl, long white hair", model.seen[-1][1]["content"])      # модель видит прежнее описание
        self.assertEqual([(c["model"], c["seed"]) for c in self.calls], [("ponyxl", pony_seed)])                 # одна модель, прежний seed
        self.assertIn("from side", self.calls[0]["prompt"]); self.assertEqual(self.calls[0]["size"], (768, 1024))
        reply = svc.store.get(cid)["messages"][3]
        self.assertEqual(len(reply["files"]), 1); self.assertIn("поправила", reply["text"]); self.assertIn("Pony Diffusion V6 XL", reply["text"])
        self.assertIn("аниме", reply["image_set"]["request"])                                                    # стиль первой просьбы не потерян

    def test_edit_on_chosen_variant_and_on_everyone(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        first = svc.store.get(cid)["messages"][1]["image_set"]
        self.calls.clear()
        model.replies.append("TAGS: 1girl, long white hair, smile\nSCENE: A smiling anime girl.\nBEST: noobai\nWHY: x")
        svc.send(cid, "на третьем варианте пусть улыбается", wait=True)
        self.assertEqual([(c["model"], c["seed"]) for c in self.calls], [("flux2klein", first["variants"][2]["seed"])])
        self.calls.clear()
        model.replies.append("TAGS: 1girl, long white hair, night, moon\nSCENE: An anime girl at night.\nBEST: flux2klein\nWHY: x")
        svc.send(cid, "во всех моделях сделай ночь", wait=True)
        self.assertEqual(sorted(c["model"] for c in self.calls), sorted(IDS))                                    # обратился ко всем
        self.assertEqual(len(svc.store.get(cid)["messages"][-1]["files"]), 3)

    def test_more_reuses_description_without_asking_the_model(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        asked = len(model.seen)
        self.calls.clear()
        svc.send(cid, "ещё вариант", wait=True)
        self.assertEqual(len(model.seen), asked)                                                                 # текстовую модель не трогали
        self.assertEqual([c["model"] for c in self.calls], ["noobai", "ponyxl", "flux2klein", "noobai"])
        first_seeds = {v["seed"] for v in svc.store.get(cid)["messages"][1]["image_set"]["variants"]}
        self.assertFalse(first_seeds & {c["seed"] for c in self.calls})                                          # новые seed — новые картинки

    def test_photo_is_drawn_only_by_flux(self):
        svc, model, ev, tmp = self.make(["TAGS: cat, roof\nSCENE: A photo of a cat on a roof at sunset.\nBEST: flux2klein\nWHY: фото"])
        cid = svc.send(None, "Сделай фотографию кота на крыше", wait=True)
        self.assertEqual({c["model"] for c in self.calls}, {"flux2klein"})
        self.assertEqual(len(self.calls), 4)
        self.assertIn("Для фото подходит только", svc.store.get(cid)["messages"][1]["text"])

    def test_one_model_fails_others_still_draw(self):
        self.broken = {"ponyxl"}
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual([f["variant"] for f in reply["files"]], [1, 3, 4])
        self.assertIn("вариант 2 (Pony Diffusion V6 XL): видеокарте не хватило памяти", reply["text"])
        self.assertTrue(any(e["type"] == "chat_image" and e["status"] == "fail" and e["n"] == 2 for e in ev))
        self.assertFalse(svc.busy)

    def test_all_fail_falls_back_to_online_generator(self):
        self.broken = set(IDS)
        seen = {}

        def online(cfg, prompt, size=(768, 768), seed=None, stop=None, request=""):
            seen["provider"] = cfg.get("images")["provider"]
            return PNG, "png", "Pollinations"
        images.generate = online
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual(seen["provider"], "pollinations")                                                       # не те же модели ещё раз
        self.assertEqual(len(reply["files"]), 1); self.assertIn("запасной генератор", reply["text"])

    def test_all_fail_without_fallback_explains(self):
        self.broken = set(IDS)

        def offline(*a, **k): raise images.ImageError("нет связи")
        images.generate = offline
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertIn("Не получилось нарисовать", reply["text"]); self.assertIn("видеокарте не хватило памяти", reply["text"])
        self.assertNotIn("files", reply); self.assertFalse(svc.busy)

    def test_text_model_down_still_draws(self):
        svc, model, ev, tmp = self.make([])                                                                       # у «модели» нет реплик → ошибка
        model.chat = lambda *a, **k: (_ for _ in ()).throw(chat.EngineError("модель не загружена"))
        cid = svc.send(None, "нарисуй аниме девушку с белыми волосами", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual(len(reply["files"]), 4); self.assertIn("не отвечает", reply["text"])
        self.assertIn("white hair", self.calls[0]["prompt"])                                                      # теги собраны по словам запроса

    def test_external_provider_keeps_old_single_picture_path(self):
        svc, model, ev, tmp = self.make(["anime girl, white hair"])
        svc.cfg.update({"images": {"provider": "pollinations"}})
        images.generate = lambda cfg, prompt, size=(768, 768), seed=None, stop=None, request="": (PNG, "png", "Pollinations")
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        reply = svc.store.get(cid)["messages"][1]
        self.assertEqual(len(reply["files"]), 1); self.assertEqual(self.calls, []); self.assertIn("Готово — нарисовала", reply["text"])

    def test_director_can_be_switched_off(self):
        svc, model, ev, tmp = self.make(["anime girl, white hair"])
        svc.cfg.update({"images": {"director": False}})
        images.generate = lambda cfg, prompt, size=(768, 768), seed=None, stop=None, request="": (PNG, "png", "встроенный генератор")
        svc.send(None, "нарисуй аниме девушку", wait=True)
        self.assertEqual(self.calls, [])                                                                          # режиссёр выключен — прежний путь


class AskCount(Studio):
    """«Сколько вариантов?»: новая просьба ждёт ответа 1–4; число запускает рисование, любое другое сообщение вопрос отменяет."""

    def test_asks_then_draws_chosen_number(self):
        svc, model, ev, tmp = self.make([BRIEF], ask=True)
        cid = svc.send(None, "Сделай изображения аниме девушки с белыми волосами", wait=True)
        c = svc.store.get(cid)
        self.assertEqual(self.calls, [])                                                    # ничего не рисовалось
        self.assertEqual(c["messages"][1]["choices"], ["1", "2", "3", "4"])
        self.assertIn("Сколько вариантов", c["messages"][1]["text"])
        self.assertIn("pending_image", c)
        self.assertEqual([e for e in ev if e["type"] == "chat_done"][-1]["choices"], ["1", "2", "3", "4"])
        svc.send(cid, "2", wait=True)
        c = svc.store.get(cid)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(c["messages"][3]["files"]), 2)
        self.assertNotIn("pending_image", c)
        self.assertIn("white hair", self.calls[0]["prompt"])                                 # описание — по исходной просьбе, а не по «2»

    def test_word_answer_and_other_text_cancels(self):
        svc, model, ev, tmp = self.make([BRIEF], ask=True)
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        svc.send(cid, "три", wait=True)
        self.assertEqual(len(self.calls), 3)
        cid2 = svc.send(None, "нарисуй аниме девушку", wait=True)
        svc.send(cid2, "кстати, как дела?", wait=True)
        self.assertEqual(len(self.calls), 3)                                                # отмена: ничего нового не нарисовано
        self.assertNotIn("pending_image", svc.store.get(cid2))

    def test_no_question_when_count_named_or_edit_or_disabled(self):
        svc, model, ev, tmp = self.make([BRIEF, BRIEF], ask=True)
        cid = svc.send(None, "нарисуй аниме девушку, 2 варианта", wait=True)
        self.assertEqual(len(self.calls), 2)                                                # число названо — не спрашиваем
        svc.send(cid, "сделай волосы длиннее", wait=True)
        self.assertNotIn("pending_image", svc.store.get(cid))                               # правка — не спрашиваем
        svc2, *_ = self.make([BRIEF], ask=False)
        n = len(self.calls)
        svc2.send(None, "нарисуй аниме девушку", wait=True)
        self.assertEqual(len(self.calls) - n, 4)                                            # вопрос отключён — как раньше

    def test_parse_answer(self):
        for t, n in (("3", 3), ("Три", 3), ("2 варианта", 2), (" 1. ", 1), ("одну", 1)):
            self.assertEqual(d.parse_answer(t), n, t)
        for t in ("0", "5", "нарисуй кота", "", "я хочу 3 кота"):
            self.assertIsNone(d.parse_answer(t), t)


if __name__ == "__main__":
    unittest.main()
