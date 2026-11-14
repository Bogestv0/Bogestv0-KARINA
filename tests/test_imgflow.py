"""Фирменный стиль и поток рисования: режим «рисую» с кнопками, подтверждение перед рисованием, цепочка «стиль → поза → проверка», правка присланных картинок, обои."""
import io, random, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from karina import imgcheck, imgdirector as d, imgflow, localimg, prompts
from test_imgdirector import PNG, StudioKit

IDS = ["messillustrious", "noobai", "ponyxl", "flux2klein"]
BRIEF = ("LOOK: девушка с длинными белыми волосами и синими глазами | 1girl, long white hair, blue eyes\n"
         "OUTFIT: повседневная одежда | casual clothes\n"
         "POSE: лежит на диване | lying, on couch\n"
         "SCENE: гостиная с окном, солнечный свет | indoors, living room, window, sunlight\n"
         "EXTRA: -")
REVISED = ("LOOK: очень длинные белые волосы | very long white hair\n"
           "OUTFIT: -\nPOSE: -\nSCENE: -\nEXTRA: -")
TURN = ("LOOK: -\nOUTFIT: -\nPOSE: сидит боком | sitting, from side, profile\nSCENE: -\nEXTRA: -")


class Pure(unittest.TestCase):
    def test_buttons_and_phrases(self):
        c = imgflow.classify
        self.assertEqual(c(imgflow.BTN_DRAW, True), "confirm"); self.assertEqual(c("да", True), "confirm"); self.assertEqual(c("Рисуй, пожалуйста", True), "confirm")
        self.assertEqual(c("да, но волосы длиннее", True), "")                              # «да, но …» — это правка, а не подтверждение
        self.assertEqual(c(imgflow.BTN_CANCEL, True), "cancel"); self.assertEqual(c(imgflow.BTN_EDIT, True), "edit_ask")
        self.assertEqual(c(imgflow.BTN_CLOSE, False), "close"); self.assertEqual(c("закрой рисование", False), "close"); self.assertEqual(c("хватит рисовать", False), "close")
        self.assertEqual(c(imgflow.BTN_CONTINUE, False), "continue"); self.assertEqual(c(imgflow.BTN_NEW, False), "new")
        self.assertEqual(c("да", False), "")                                                  # без черновика «да» ничего не значит
        self.assertEqual(c("поверни её вполоборота", True), "")

    def test_schedule_and_roles(self):
        self.assertEqual([r for r, _ in imgflow.schedule("new")], ["style", "pose", "style"])
        self.assertEqual(len(imgflow.schedule("new", 1)), 1); self.assertEqual(len(imgflow.schedule("new", 9)), 3)
        self.assertEqual(imgflow.roles(IDS[:3]), {"style": "messillustrious", "pose": "noobai"})
        self.assertEqual(imgflow.roles(["ponyxl"]), {"style": "ponyxl", "pose": "ponyxl"})
        self.assertEqual(imgflow.roles([]), {})

    def test_boost_and_strength_and_count(self):
        self.assertEqual(imgflow.boost("1girl, white hair, lying", ["lying", "on_couch"]), "1girl, (lying:1.4), (on couch:1.4), white hair")
        self.assertEqual(imgflow.boost("1girl, lying", []), "1girl, lying")
        self.assertLess(imgflow.strength_for("сделай чуть светлее"), 0.5); self.assertGreater(imgflow.strength_for("поверни её вполоборота"), 0.7)
        self.assertEqual(imgflow.count_from("нужно 3 варианта", 4), (3, True)); self.assertEqual(imgflow.count_from("два варианта", 4), (2, True))
        self.assertEqual(imgflow.count_from("просто так", 4), (4, False)); self.assertEqual(imgflow.count_from("9 вариантов", 4)[0], 6)

    def test_run_variant_rounds(self):
        """Круг 1 не прошёл проверку → поза от второй модели → поза верна → последний круг возвращает стиль; описание второго круга усилено недостающими тегами."""
        log = []
        verdicts = iter([{"ok": False, "missing": ["lying"], "coverage": 0.5}, {"ok": True, "missing": [], "coverage": 1.0}, {"ok": True, "missing": [], "coverage": 1.0}])

        def draw(model, prompt, neg, init, strength, rn, role):
            log.append((model, role, init is not None, strength, prompt)); return b"x%d" % rn
        passes = imgflow.run_variant("new", {"style": "S", "pose": "P"}, 3, "1girl, lying", lambda m, t: (t, ""), draw, lambda data, tags: next(verdicts))
        self.assertEqual([(a, b, c) for a, b, c, *_ in log], [("S", "style", False), ("P", "pose", True), ("S", "style", True)])
        self.assertIn("(lying:1.4)", log[1][4]); self.assertNotIn("(lying:1.4)", log[0][4])
        self.assertEqual(len(passes), 3); self.assertEqual(imgflow.pick_best(passes), 2)
        # без проверки — один круг; всё сразу хорошо — один круг
        self.assertEqual(len(imgflow.run_variant("new", {"style": "S", "pose": "P"}, 3, "a", lambda m, t: (t, ""), draw, None)), 1)
        ok = lambda data, tags: {"ok": True, "missing": [], "coverage": 1.0}
        self.assertEqual(len(imgflow.run_variant("new", {"style": "S", "pose": "P"}, 3, "a", lambda m, t: (t, ""), draw, ok)), 1)
        # три неудачи — ровно три круга и лучший по совпадению
        vs = iter([{"ok": False, "missing": ["a"], "coverage": 0.4}, {"ok": False, "missing": ["a"], "coverage": 0.7}, {"ok": False, "missing": ["a"], "coverage": 0.5}])
        ps = imgflow.run_variant("new", {"style": "S", "pose": "P"}, 3, "a", lambda m, t: (t, ""), draw, lambda data, tags: next(vs))
        self.assertEqual((len(ps), imgflow.pick_best(ps)), (3, 1))

    def test_partial_passes_survive_error(self):
        passes = []

        def draw(model, prompt, neg, init, strength, rn, role):
            if rn == 2:
                raise RuntimeError("нет памяти")
            return b"x"
        with self.assertRaises(RuntimeError):
            imgflow.run_variant("new", {"style": "S", "pose": "P"}, 3, "a", lambda m, t: (t, ""), draw, lambda data, tags: {"ok": False, "missing": ["a"], "coverage": 0.1}, passes=passes)
        self.assertEqual(len(passes), 1)

    def test_images(self):
        big = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (1500, 1000), (10, 20, 30)).save(big, "PNG")
        data, (w, h) = imgflow.prepare_init(big.getvalue())
        self.assertTrue(w % 64 == 0 and h % 64 == 0 and w * h <= 1.06e6); self.assertEqual(imgflow.size_of(data), (w, h))
        wall = imgflow.to_wallpaper(big.getvalue(), (1920, 1080)); self.assertEqual(imgflow.size_of(wall), (1920, 1080))
        self.assertEqual(imgflow.size_of(imgflow.cover(big.getvalue(), 1536, 896)), (1536, 896))
        self.assertEqual(imgflow.image_paths(["a.png", "b.txt", "C.JPG"]), ["a.png", "C.JPG"]); self.assertTrue(imgflow.only_images(["a.webp"])); self.assertFalse(imgflow.only_images(["a.png", "x.py"]))


class Style(unittest.TestCase):
    def test_house_style_and_pose_first(self):
        p = d.prompt_for("messillustrious", {"tags": "1girl, white hair, lying, on couch, blue eyes", "scene": "x"}, "нарисуй аниме", True)
        for t in prompts.STYLE_CORE:
            self.assertIn(t, p)
        self.assertIn("(lying:1.25)", p); self.assertIn("(on couch:1.25)", p)
        self.assertLess(p.index("1girl"), p.index("(lying:1.25)")); self.assertLess(p.index("(lying:1.25)"), p.index("white hair"))        # поза сразу после «1girl»
        self.assertEqual(p.count("anime screencap"), 1)
        self.assertIn("semi-realistic", d.negative_for("messillustrious", "нарисуй аниме", True))
        flux = d.prompt_for("flux2klein", {"tags": "1girl", "scene": "A girl."}, "аниме", True)
        self.assertNotIn("1girl", flux)

    def test_catalog_has_mess(self):
        m = localimg.by_id("messillustrious")
        self.assertEqual(m["kind"], "sdxl"); self.assertTrue(m["parts"]); self.assertEqual({p["role"] for p in m["parts"]}, {"model", "vae", "clip_l", "clip_g"})
        self.assertEqual(len({p["save_as"] for p in m["parts"]}), 4)                       # файлы частей не перезаписывают друг друга
        self.assertIn("messillustrious", d.ORDER_ANIME); self.assertEqual(d.ORDER_ANIME[0], "messillustrious")

    def test_split_model_command_and_img2img(self):
        m = localimg.by_id("messillustrious")
        paths = {"model": Path("/m/u"), "vae": Path("/m/v"), "clip_l": Path("/m/l"), "clip_g": Path("/m/g")}
        cmd = localimg._command(Path("/x/sd"), m, paths, "p", "n", 832, 1216, 28, 1, Path("/o.png"), "cpu", "low", 0, Path("/i.png"), 0.7)
        self.assertIn("--diffusion-model", cmd); self.assertIn("--clip_l", cmd); self.assertNotIn("-m", cmd)
        self.assertIn("--strength", cmd); self.assertEqual(cmd[cmd.index("--strength") + 1], "0.70")
        one = localimg._command(Path("/x/sd"), m, {"model": Path("/m/all.safetensors")}, "p", "n", 832, 1216, 28, 1, Path("/o.png"), "cpu", "low", 0)
        self.assertIn("-m", one); self.assertNotIn("--init-img", one)                       # одним файлом и без картинки-основы

    def test_local_file_is_found(self):
        tmp = Path(tempfile.mkdtemp())
        old = localimg.MODELS_DIR
        localimg.MODELS_DIR = tmp
        try:
            self.assertIsNone(localimg.model_paths("messillustrious"))
            f = tmp / "Mess_Illustrious_Anime_Mix.safetensors"
            f.write_bytes(b"0" * 11_000_000)
            self.assertEqual(localimg.model_paths("messillustrious"), {"model": f})
        finally:
            localimg.MODELS_DIR = old


class Check(unittest.TestCase):
    def test_required_tags_and_verdict(self):
        vocab = {"1girl", "lying", "on_couch", "white_hair", "blue_eyes", "indoors", "long_hair", "window"}
        req = imgcheck.required_tags("anime screencap, 2D, 1girl, (lying:1.25), (on couch:1.25), white hair, blue eyes, detailed background, unknown tag", vocab)
        self.assertEqual(req[:2], ["lying", "on_couch"])                                     # поза — первой
        self.assertNotIn("anime_screencap", req); self.assertNotIn("1girl", req); self.assertNotIn("unknown_tag", req)
        v = imgcheck.verdict(req, {"lying": 0.9, "on_couch": 0.2, "white_hair": 0.8, "blue_eyes": 0.7})
        self.assertFalse(v["ok"]); self.assertEqual(v["missing"], ["on_couch"]); self.assertAlmostEqual(v["coverage"], 0.75)
        self.assertTrue(imgcheck.verdict(req, {t: 0.9 for t in req})["ok"]); self.assertTrue(imgcheck.verdict([], {})["ok"])


class Flow(StudioKit, unittest.TestCase):
    INSTALLED = IDS

    def setUp(self):
        super().setUp()
        self.orig_check = (imgcheck.importable, imgcheck.installed, imgcheck.ready, imgcheck.check)
        self.verdicts = []
        imgcheck.importable = lambda: True
        imgcheck.installed = lambda: True
        imgcheck.ready = lambda: True
        imgcheck.check = lambda data, tags: self.verdicts.pop(0) if self.verdicts else {"ok": True, "missing": [], "coverage": 1.0, "required": []}

    def tearDown(self):
        imgcheck.importable, imgcheck.installed, imgcheck.ready, imgcheck.check = self.orig_check
        super().tearDown()

    def draw_first(self, svc, text="нарисуй аниме девушку с белыми волосами, лежит на диване, 1 вариант"):
        cid = svc.send(None, text, wait=True)
        self.assertEqual(self.calls, [])                                                     # до «Рисовать» ничего не рисуется
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        return cid

    def test_confirmation_then_drawing_in_house_style(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку с белыми волосами, лежит на диване, 1 вариант", wait=True)
        c = svc.store.get(cid)
        msg = c["messages"][1]
        self.assertEqual(self.calls, [])
        self.assertEqual(msg["choices"], imgflow.DRAFT_BUTTONS)
        for part in ("Вот что я нарисую", "Поза:** лежит на диване", "Фон:** гостиная", "Mess Illustrious Anime Mix", "Вариантов:** 1", "до 3 кругов"):
            self.assertIn(part, msg["text"])
        self.assertIn("draft", c)
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        c = svc.store.get(cid)
        self.assertEqual([x["model"] for x in self.calls], ["messillustrious"])               # стилист — Mess; проверка прошла с первого раза — один круг
        self.assertFalse(self.calls[0]["init"])
        p = self.calls[0]["prompt"]
        self.assertTrue(p.startswith("anime screencap")); self.assertIn("(lying:1.25)", p); self.assertIn("detailed background", p)
        self.assertEqual(self.calls[0]["size"], (832, 1216))
        reply = c["messages"][3]
        self.assertEqual(reply["choices"], imgflow.DONE_BUTTONS); self.assertEqual(len(reply["files"]), 1)
        self.assertTrue(c["draw"]["active"]); self.assertNotIn("draft", c)
        self.assertIn("Готово", reply["text"]); self.assertEqual(reply["image_set"]["variants"][0]["model"], "messillustrious")

    def test_all_variants_one_style_different_seeds(self):
        svc, model, ev, tmp = self.make([BRIEF])
        self.draw_first(svc, "нарисуй аниме девушку, лежит на диване, 3 варианта")
        self.assertEqual([c["model"] for c in self.calls], ["messillustrious"] * 3)
        self.assertEqual(len({c["seed"] for c in self.calls}), 3)

    def test_revise_draft_before_drawing(self):
        svc, model, ev, tmp = self.make([BRIEF, REVISED])
        cid = svc.send(None, "нарисуй аниме девушку, лежит на диване", wait=True)
        svc.send(cid, imgflow.BTN_EDIT, wait=True)
        self.assertIn("Напишите, что поменять", svc.store.get(cid)["messages"][3]["text"]); self.assertIn("draft", svc.store.get(cid))
        svc.send(cid, "сделай волосы очень длинными, 2 варианта", wait=True)
        c = svc.store.get(cid)
        self.assertEqual(self.calls, [])
        self.assertIn("Change requested: сделай волосы очень длинными", model.seen[-1][1]["content"]); self.assertIn("Одежда: повседневная одежда", c["messages"][5]["text"].replace("**", ""))
        self.assertIn("очень длинные", c["messages"][5]["text"]); self.assertIn("Вариантов:** 2", c["messages"][5]["text"])
        svc.send(cid, "да", wait=True)
        self.assertEqual(len(self.calls), 2); self.assertIn("very long white hair", self.calls[0]["prompt"])

    def test_pick_models_by_buttons_single_model(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку, лежит на диване, 1 вариант", wait=True)
        svc.send(cid, imgflow.BTN_MODELS, wait=True)
        ch = svc.store.get(cid)["messages"][3]["choices"]
        self.assertTrue(all(x.startswith(imgflow.PICK_BASE) for x in ch)); self.assertIn(imgflow.PICK_BASE + "NoobAI XL 1.1", ch)
        svc.send(cid, imgflow.PICK_BASE + "NoobAI XL 1.1", wait=True)
        ch = svc.store.get(cid)["messages"][5]["choices"]
        self.assertIn(imgflow.PICK_POSE + "та же модель", ch)
        svc.send(cid, imgflow.PICK_POSE + "та же модель", wait=True)
        m = svc.store.get(cid)["messages"][7]
        self.assertIn("одна модель, без цепочки", m["text"]); self.assertEqual(m["choices"], imgflow.DRAFT_BUTTONS)
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        self.assertEqual([x["model"] for x in self.calls], ["noobai"])
        self.assertEqual(svc.store.get(cid)["draw"]["models"]["base"], "noobai")           # выбор запоминается и для следующих правок

    def test_live_state_for_returning_to_tab(self):
        svc, model, ev, tmp = self.make([BRIEF])
        svc.emit({"type": "chat_start", "chat": "c1", "reply_id": "r1", "user": {}, "title": "t"})
        svc.emit({"type": "chat_image_plan", "chat": "c1", "id": "r1", "items": [{"n": 1, "label": "Mess"}]})
        svc.emit({"type": "chat_image", "chat": "c1", "id": "r1", "n": 1, "status": "progress", "step": 3, "steps": 20})
        svc._busy.acquire()
        try:
            lv = svc.live_state("c1")
            self.assertEqual(lv["plan"][0]["n"], 1); self.assertEqual(lv["tiles"]["1"]["step"], 3); self.assertIsNone(svc.live_state("other"))
        finally:
            svc._busy.release()
        svc.emit({"type": "chat_done", "chat": "c1"})
        self.assertIsNone(svc.live)

    def test_cancel_draft(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку", wait=True)
        svc.send(cid, imgflow.BTN_CANCEL, wait=True)
        c = svc.store.get(cid)
        self.assertEqual(self.calls, []); self.assertNotIn("draft", c); self.assertFalse(c["draw"]["active"])

    def test_after_picture_any_message_edits_the_picture_not_search(self):
        """Жалоба №1: «Хочу чтобы нарисовал в аниме стиле 2025 года» после картинки раньше уходило в чат/поиск — теперь это правка этой картинки."""
        svc, model, ev, tmp = self.make([BRIEF, BRIEF])
        cid = self.draw_first(svc)
        self.calls.clear()
        svc.send(cid, "Хочу чтобы нарисовал в аниме стиле 2025 года", wait=True)
        c = svc.store.get(cid)
        self.assertEqual(self.calls, [])                                                     # сначала подтверждение
        self.assertIn("Вот как я поправлю картинку", c["messages"][-1]["text"])
        self.assertIn("Change requested: Хочу чтобы нарисовал в аниме стиле", model.seen[-1][1]["content"])          # модель видит прежнее описание
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        self.assertTrue(self.calls and all(x["init"] for x in self.calls[:1]))                # правка идёт по прежней картинке (img2img)
        self.assertEqual(self.calls[0]["model"], "messillustrious")                           # «в стиле» — сначала круг стилиста

    def test_pose_edit_uses_pose_model_with_strong_strength(self):
        svc, model, ev, tmp = self.make([BRIEF, TURN])
        cid = self.draw_first(svc)
        self.calls.clear()
        svc.send(cid, "поверни её боком и пусть сидит", wait=True)
        svc.send(cid, "рисуй", wait=True)
        self.assertEqual([(c["model"], c["init"]) for c in self.calls], [("noobai", True), ("messillustrious", True)])     # поза от NoobAI, затем возврат стиля
        self.assertGreaterEqual(self.calls[0]["strength"], 0.7); self.assertLess(self.calls[1]["strength"], 0.5)
        self.assertIn("(sitting:1.25)", self.calls[0]["prompt"])

    def test_chain_style_pose_style_with_checker(self):
        """Круг 1 (Mess) не нарисовал позу → круг 2 (NoobAI) правит позу с усиленными тегами → круг 3 (Mess) возвращает стиль."""
        self.verdicts = [{"ok": False, "missing": ["lying", "on_couch"], "coverage": 0.4, "required": []},
                         {"ok": True, "missing": [], "coverage": 1.0, "required": []}]
        svc, model, ev, tmp = self.make([BRIEF])
        cid = self.draw_first(svc)
        self.assertEqual([(c["model"], c["init"]) for c in self.calls], [("messillustrious", False), ("noobai", True), ("messillustrious", True)])
        self.assertIn("(lying:1.4)", self.calls[1]["prompt"]); self.assertNotIn("(lying:1.4)", self.calls[0]["prompt"])
        self.assertLess(self.calls[2]["strength"], self.calls[1]["strength"])                 # возврат стиля слабее, чтобы не сломать позу
        text = svc.store.get(cid)["messages"][3]["text"]
        self.assertIn("Как рисовала", text); self.assertIn("круг 1 (стиль) — не хватало: lying, on couch", text)

    def test_no_more_than_three_rounds(self):
        self.verdicts = [{"ok": False, "missing": ["lying"], "coverage": 0.3, "required": []}] * 6
        svc, model, ev, tmp = self.make([BRIEF])
        self.draw_first(svc)
        self.assertEqual(len(self.calls), 3)

    def test_without_checker_one_round_and_honest_note(self):
        imgcheck.ready = lambda: False
        imgcheck.installed = lambda: False
        imgcheck.importable = lambda: False
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку, лежит на диване, 1 вариант", wait=True)
        self.assertIn("onnxruntime", svc.store.get(cid)["messages"][1]["text"])
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Автопроверки не было", svc.store.get(cid)["messages"][3]["text"])

    def test_close_drawing_returns_to_normal_assistant(self):
        svc, model, ev, tmp = self.make([BRIEF, "Привет! Чем помочь?"])
        cid = self.draw_first(svc)
        svc.send(cid, imgflow.BTN_CLOSE, wait=True)
        c = svc.store.get(cid)
        self.assertFalse(c["draw"]["active"]); self.assertIn("Закрыла режим рисования", c["messages"][-1]["text"])
        self.calls.clear()
        svc.send(cid, "привет, как дела", wait=True)
        self.assertEqual(self.calls, []); self.assertNotIn("draft", svc.store.get(cid))        # обычный ответ, а не рисование

    def test_continue_and_new_buttons(self):
        svc, model, ev, tmp = self.make([BRIEF, "LOOK: девушка с чёрными волосами | 1girl, black hair\nOUTFIT: -\nPOSE: стоит | standing\nSCENE: -\nEXTRA: -"])
        cid = self.draw_first(svc)
        svc.send(cid, imgflow.BTN_CONTINUE, wait=True)
        self.assertIn("Что поменять", svc.store.get(cid)["messages"][-1]["text"]); self.assertTrue(svc.store.get(cid)["draw"]["active"])
        svc.send(cid, imgflow.BTN_NEW, wait=True)
        self.assertTrue(svc.store.get(cid)["draw"]["fresh"])
        svc.send(cid, "девушка с чёрными волосами стоит", wait=True)
        c = svc.store.get(cid)
        self.assertIn("Вот что я нарисую", c["messages"][-1]["text"])                         # новая картинка, не правка
        self.assertNotIn("fresh", c["draw"])

    def test_user_image_is_edited(self):
        svc, model, ev, tmp = self.make(["TAGS: 1girl, night, moon\nSCENE: A girl at night.\nRU: Персонаж: девушка; Фон: ночь\nBEST: noobai\nWHY: x"])
        up = tmp / "mine.png"
        up.write_bytes(PNG)
        cid = svc.send(None, "перерисуй мою картинку в аниме, ночью", paths=[str(up)], wait=True)
        c = svc.store.get(cid)
        self.assertIn("переделаю вашу картинку", c["messages"][1]["text"]); self.assertEqual(self.calls, [])
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        self.assertTrue(self.calls and self.calls[0]["init"]); self.assertEqual(self.calls[0]["model"], "messillustrious")
        self.assertEqual(len(svc.store.get(cid)["messages"][3]["files"]), 1)

    def test_wallpaper_is_drawn_wide_and_upscaled(self):
        svc, model, ev, tmp = self.make([BRIEF])
        cid = svc.send(None, "нарисуй аниме девушку, лежит на диване, обои на рабочий стол, 1 вариант", wait=True)
        text = svc.store.get(cid)["messages"][1]["text"]
        self.assertIn("обои 1920×1080", text); self.assertIn("1344×768", text)
        svc.send(cid, imgflow.BTN_DRAW, wait=True)
        self.assertEqual(self.calls[0]["size"], (1344, 768))
        self.assertTrue(any(c["init"] and c["size"] == (1536, 896) for c in self.calls))       # проход дорисовки деталей
        f = svc.store.get(cid)["messages"][3]["files"][0]
        self.assertEqual(imgflow.size_of((tmp / "Карина" / f["name"]).read_bytes()), (1920, 1080))

    def test_mess_fails_next_model_draws_same_style(self):
        self.broken = {"messillustrious"}
        svc, model, ev, tmp = self.make([BRIEF])
        cid = self.draw_first(svc)
        reply = svc.store.get(cid)["messages"][3]
        self.assertIn("Mess Illustrious Anime Mix не запустилась", reply["text"])
        self.assertEqual(reply["image_set"]["variants"][0]["model"], "noobai")
        self.assertTrue(self.calls[-1]["prompt"].startswith("anime screencap"))              # тот же фирменный стиль

    def test_confirmation_can_be_switched_off(self):
        svc, model, ev, tmp = self.make([BRIEF], confirm=False)
        cid = svc.send(None, "нарисуй аниме девушку, лежит на диване, 1 вариант", wait=True)
        self.assertEqual(len(self.calls), 1); self.assertNotIn("draft", svc.store.get(cid))

    def test_only_one_anime_model_does_both_jobs(self):
        self.INSTALLED = ["ponyxl"]
        localimg.installed_ids = lambda: ["ponyxl"]
        self.verdicts = [{"ok": False, "missing": ["lying"], "coverage": 0.4, "required": []}, {"ok": True, "missing": [], "coverage": 1.0, "required": []}]
        svc, model, ev, tmp = self.make([BRIEF])
        self.draw_first(svc)
        self.assertEqual([c["model"] for c in self.calls], ["ponyxl", "ponyxl"])


if __name__ == "__main__":
    unittest.main()


class CountPhrases(unittest.TestCase):
    def test_variants(self):
        from karina import imgflow
        for t, n in [("Вариантов: 1", 1), ("вариантов 2", 2), ("один эскиз", 1), ("только один", 1), ("3 варианта", 3), ("Вариантов = три", 3)]:
            self.assertEqual(imgflow.count_from(t, 4), (n, True), t)
        self.assertEqual(imgflow.count_from("нарисуй кошку", 1), (1, False))
        self.assertEqual(imgflow.count_from("1", 4), (4, False))
        self.assertEqual(imgflow.count_from("1", 4, bare=True), (1, True))


class EditDraft(unittest.TestCase):
    def test_edit_shows_change_and_count(self):
        from karina import imgflow
        t = imgflow.draft_text({"ru": "", "tags": "1girl"}, "кошка", "edit", "Mess", (832, 1216), 1, 3, "on", change="сделай волосы длиннее")
        self.assertIn("Что меняю:** сделай волосы длиннее", t)
        self.assertIn("Вариантов:** 1", t)
        self.assertIn("один лучший результат", t)


class Slots(unittest.TestCase):
    def test_edit_keeps_untouched_slots(self):
        from karina import imgslots
        card = imgslots.merge({}, imgslots.parse(BRIEF), "x", False)
        card = imgslots.merge(card, imgslots.parse("LOOK: -\nSCENE: комната в фиолетовых тонах, компьютер с двумя мониторами | purple_theme, 2monitors, computer, indoors\nOUTFIT: -"),
                              "поменяй фон: комната в фиолетовых тонах, компьютер с двумя мониторами", True)
        self.assertEqual(card["outfit"]["tags"], "casual clothes")                         # одежда не сбросилась
        self.assertIn("2monitors", imgslots.compose(card))
        self.assertIn("lying", imgslots.compose(card))
        self.assertNotIn("living room", imgslots.compose(card))

    def test_unmentioned_slot_ignored(self):
        from karina import imgslots
        card = imgslots.merge({}, imgslots.parse(BRIEF), "x", False)
        card = imgslots.merge(card, imgslots.parse("OUTFIT: красное платье | red dress\nSCENE: ночной город | night, city"), "поменяй фон на ночной город", True)
        self.assertEqual(card["outfit"]["tags"], "casual clothes")
        self.assertIn("night", card["scene"]["tags"])

    def test_look_keeps_count_tag(self):
        from karina import imgslots
        card = imgslots.merge({}, imgslots.parse(BRIEF), "x", False)
        card = imgslots.merge(card, imgslots.parse("LOOK: рыжие волосы | red hair"), "сделай рыжие волосы", True)
        self.assertTrue(imgslots.compose(card).startswith("1girl"))

    def test_models_from_text_and_buttons(self):
        ids = ["messillustrious", "noobai", "ponyxl"]
        self.assertEqual(imgflow.models_from_text("основу рисует Mess, позу — Pony", ids), {"base": "messillustrious", "pose": "ponyxl"})
        self.assertEqual(imgflow.models_from_text("рисуй только моделью NoobAI", ids), {"base": "noobai", "pose": "same"})
        self.assertEqual(imgflow.models_from_text("сделай волосы длиннее", ids), {})
        self.assertEqual(imgflow.classify("🧩 Модели", True), "models")
        self.assertEqual(imgflow.classify("Рисует основу: Mess Illustrious Anime Mix", True), "pick_base:mess illustrious anime mix")
        self.assertEqual(imgflow.model_by_name("Mess Illustrious Anime Mix", ids), "messillustrious")
