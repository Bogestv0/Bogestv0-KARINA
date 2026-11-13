"""1.7.0: архивы и большие проекты, команды НАЙТИ / чтение по строкам, перетаскивание файлов (uploads), картинки, предпросмотр шаблонов."""
import base64, http.server, io, json, struct, sys, tarfile, tempfile, threading, unittest, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import archives, coder, files, images
from karina.coder import CoderError, Workspace, first_message, parse, read_attachments
from karina.config import Config
from karina.uploads import Uploads

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def mkzip(entries: dict, folder: Path | None = None, name="proj.zip") -> Path:
    folder = folder or Path(tempfile.mkdtemp())
    p = folder / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return p


class Archives(unittest.TestCase):
    def test_extract_strips_single_top_folder(self):
        z = mkzip({"Proj-main/a.py": "print(1)\n", "Proj-main/pkg/b.py": "x = 2\n", "Proj-main/.git/config": "x", "Proj-main/run.bat": "del"})
        dest = Path(tempfile.mkdtemp())
        files_, warns = archives.extract(z, dest)
        self.assertEqual(sorted(files_), ["a.py", "pkg/b.py"])
        self.assertEqual((dest / "pkg" / "b.py").read_text(), "x = 2\n")
        self.assertTrue(any("пропущено служебных" in w for w in warns))              # .git и .bat не распакованы, и об этом сказано

    def test_zip_slip_blocked(self):
        z = mkzip({"../evil.py": "x", "a/../../evil2.py": "x", "/abs.py": "x", "C:/win.py": "x", "ok.py": "1"})
        base = Path(tempfile.mkdtemp())
        dest = base / "out"
        files_, _ = archives.extract(z, dest)
        self.assertIn("ok.py", files_)
        self.assertFalse((base / "evil.py").exists()); self.assertFalse((base / "evil2.py").exists())
        for rel in files_:
            self.assertTrue((dest / rel).resolve().is_relative_to(dest.resolve()), rel)

    def test_limits_against_bombs(self):
        z = mkzip({f"f{i}.txt": "a" * 100 for i in range(30)})
        files_, warns = archives.extract(z, Path(tempfile.mkdtemp()), max_files=10)
        self.assertEqual(len(files_), 10)
        self.assertTrue(any("ещё 20" in w for w in warns))
        z = mkzip({"big.txt": "0" * 500_000, "small.txt": "1"})
        files_, warns = archives.extract(z, Path(tempfile.mkdtemp()), max_total=1000)
        self.assertEqual(files_, ["small.txt"])

    def test_tar_and_unsupported_and_broken(self):
        d = Path(tempfile.mkdtemp())
        t = d / "p.tar.gz"
        with tarfile.open(t, "w:gz") as tf:
            info = tarfile.TarInfo("p/x.py"); data = b"print(5)\n"; info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("p/link"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"
            tf.addfile(link)
        files_, _ = archives.extract(t, d / "o")
        self.assertEqual(files_, ["x.py"])                                          # ссылка пропущена
        with self.assertRaises(archives.ArchiveError) as cm:
            archives.extract(d / "a.rar", d / "o2")
        self.assertIn("rar", str(cm.exception))
        bad = d / "bad.zip"; bad.write_bytes(b"not a zip")
        with self.assertRaises(archives.ArchiveError):
            archives.extract(bad, d / "o3")

    def test_stem(self):
        self.assertEqual(archives.stem("KARINA-coder.zip"), "KARINA-coder")
        self.assertEqual(archives.stem("x.tar.gz"), "x")


class ArchivesInProject(unittest.TestCase):
    def test_import_paths_unpacks_zip(self):
        """Тот самый случай со скриншота: приложили KARINA-coder.zip."""
        z = mkzip({"KARINA-main/main.py": "print('hi')\n", "KARINA-main/karina/chat.py": "def route(): pass\n", "KARINA-main/ui/logo.png": "\x89PNG"}, name="KARINA-coder.zip")
        ws = Workspace(Path(tempfile.mkdtemp()))
        copied, warns = ws.import_paths([str(z)])
        self.assertEqual(sorted(copied), ["input/KARINA-coder/karina/chat.py", "input/KARINA-coder/main.py", "input/KARINA-coder/ui/logo.png"])
        self.assertFalse(any("не текстов" in w for w in warns))
        self.assertIn("def route", ws.read("input/KARINA-coder/karina/chat.py"))
        # распаковка не затирает вторую такую же
        copied2, _ = ws.import_paths([str(z)])
        self.assertTrue(all(c.startswith("input/KARINA-coder_2/") for c in copied2))

    def test_read_attachments_reads_zip_text(self):
        z = mkzip({"p/a.py": "A = 1\n", "p/b.py": "B = 2\n", "p/img.png": "\x89PNG"})
        out, notes = read_attachments([str(z)], 10_000)
        self.assertEqual(sorted(o["name"] for o in out), ["proj/a.py", "proj/b.py"])
        self.assertEqual(len(notes), 1)                                              # одна сводная заметка про нетекстовое
        out, notes = read_attachments([str(Path(tempfile.mkdtemp()) / "x.rar")], 100)
        self.assertEqual(out, [])

    def test_read_attachments_summarizes_overflow(self):
        z = mkzip({f"f{i}.py": "x" * 3000 for i in range(8)})
        out, notes = read_attachments([str(z)], 7000)
        self.assertEqual(len(out), 2)
        self.assertEqual(len(notes), 1); self.assertIn("6 файл", notes[0])


class Navigation(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(Path(tempfile.mkdtemp()))
        self.ws.write("src/app.py", "\n".join(f"line {i}" for i in range(1, 201)) + "\ndef target():\n    return 42\n")
        self.ws.write("src/other.py", "def helper():\n    return target()\n")

    def test_read_range(self):
        t = self.ws.read("src/app.py:10-12")
        self.assertTrue(t.startswith("10: line 10\n11: line 11\n12: line 12"))
        self.assertIn("строки 10–12 из 203", t)
        self.assertTrue(self.ws.read("src/app.py:5").startswith("5: line 5"))       # без конца — 80 строк
        self.assertIn("line 1\n", self.ws.read("src/app.py"))                        # без диапазона — как раньше
        with self.assertRaises(CoderError):
            self.ws.read("../x.py:1-2")

    def test_read_truncation_tells_how_to_continue(self):
        t = self.ws.read("src/app.py", limit=100)
        self.assertIn("src/app.py:1-80", t)

    def test_find(self):
        r = self.ws.find("target")
        self.assertIn("src/app.py:201: def target():", r); self.assertIn("src/other.py:2:", r)
        self.assertIn("не найдено", self.ws.find("нетакого"))
        with self.assertRaises(CoderError):
            self.ws.find("a")

    def test_parse_find(self):
        p = parse("Ищу.\n[[НАЙТИ: def route]]\n[[ЧИТАТЬ: a.py:1-20]]")
        self.assertEqual([(k, a) for k, a, _ in p["actions"]], [("find", "def route"), ("read", "a.py:1-20")])
        self.assertEqual(p["say"], "Ищу.")

    def test_big_project_first_message_fits_small_context(self):
        """Qwen 3.5 9B: контекст ≈12k токенов. Проект из ста файлов не должен целиком попасть в первое сообщение."""
        ws = Workspace(Path(tempfile.mkdtemp()))
        copied = []
        for i in range(100):
            ws.write(f"input/p/pkg{i % 10}/mod{i}.py", f"def f{i}():\n    return {i}\n" * 300)
            copied.append(f"input/p/pkg{i % 10}/mod{i}.py")
        budget = int((12288 - 4000 - 1200) * 2.0)
        msg = first_message("посмотри код", ws, copied, "", False, budget)
        self.assertLess(len(msg), budget * 0.6)
        self.assertIn("Файлов всего: 100", msg)
        self.assertIn("[[НАЙТИ", msg)
        # маленький проект — по-прежнему целиком
        ws2 = Workspace(Path(tempfile.mkdtemp())); ws2.write("input/a.py", "print('привет')\n")
        self.assertIn("print('привет')", first_message("t", ws2, ["input/a.py"], "", False, budget))


class UploadsTests(unittest.TestCase):
    def test_roundtrip_in_chunks(self):
        up = Uploads(Path(tempfile.mkdtemp()))
        data = bytes(range(256)) * 1000
        r = up.begin("proj/src/a.bin", len(data))
        self.assertTrue(r["ok"])
        for i in range(0, len(data), 70000):
            self.assertTrue(up.chunk(r["token"], base64.b64encode(data[i:i + 70000]).decode())["ok"])
        self.assertTrue(up.end(r["token"])["ok"])
        got = Path(r["top"]) / "src" / "a.bin"
        self.assertEqual(got.read_bytes(), data)
        self.assertEqual(Path(r["top"]).name, "proj")                              # прикладывается папка верхнего уровня

    def test_rejects_bad_paths_and_sizes(self):
        up = Uploads(Path(tempfile.mkdtemp()))
        for bad in ("../x.py", "a/../../b.py", "run.bat", ".git/config", "node_modules/x.js"):
            self.assertFalse(up.begin(bad)["ok"], bad)
        r = up.begin("/etc/passwd")                                                  # абсолютный путь не выходит из папки загрузок, а «приземляется» в ней
        self.assertTrue(r["ok"]); self.assertTrue(Path(r["top"]).resolve().is_relative_to(up.root.resolve()))
        self.assertFalse(up.begin("huge.zip", 10**10)["ok"])

    def test_discard_only_inside(self):
        root = Path(tempfile.mkdtemp())
        up = Uploads(root / "up")
        r = up.begin("a.txt"); up.chunk(r["token"], base64.b64encode(b"x").decode()); up.end(r["token"])
        outside = root / "keep.txt"; outside.write_text("1")
        self.assertFalse(up.discard(str(outside))); self.assertTrue(outside.exists())
        self.assertTrue(up.discard(r["top"])); self.assertFalse(Path(r["top"]).exists())

    def test_oversize_stream_aborted(self):
        up = Uploads(Path(tempfile.mkdtemp()))
        import karina.uploads as u
        old = u.MAX_FILE; u.MAX_FILE = 1000
        try:
            r = up.begin("a.bin")
            self.assertFalse(up.chunk(r["token"], base64.b64encode(b"0" * 2000).decode())["ok"])
            self.assertFalse(up.end(r["token"])["ok"])
        finally:
            u.MAX_FILE = old


class Previews(unittest.TestCase):
    def test_kinds(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.html").write_text("<h1>Привет</h1>", "utf-8")
        (d / "a.png").write_bytes(PNG)
        (d / "a.py").write_text("print(1)", "utf-8")
        (d / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><rect width="5" height="5"/></svg>', "utf-8")
        (d / "a.exe").write_bytes(b"MZ")
        self.assertEqual(files.preview_file(d / "a.html")["type"], "html")
        self.assertTrue(files.preview_file(d / "a.png")["src"].startswith("data:image/png;base64,"))
        self.assertEqual(files.preview_file(d / "a.py"), {"type": "text", "text": "print(1)"})
        svg = base64.b64decode(files.preview_file(d / "a.svg")["src"].split(",")[1]).decode()
        self.assertNotIn("script", svg)                                            # SVG очищается и при показе
        with self.assertRaises(files.FileError):
            files.preview_file(d / "a.exe")

    def test_project_preview_stays_inside_project(self):
        from karina.coder import CoderService
        base = Path(tempfile.mkdtemp())
        (base / "Проекты" / "p1").mkdir(parents=True)
        (base / "Проекты" / "p1" / "index.html").write_text("<p>x</p>", "utf-8")
        (base / "secret.txt").write_text("секрет", "utf-8")
        svc = CoderService(Config(base / "c.json"), None, lambda e: None, projects_dir=base / "Проекты")
        self.assertEqual(svc.preview("p1", "index.html")["type"], "html")
        for proj, rel in (("p1", "../../secret.txt"), ("..", "secret.txt"), ("p1", "/etc/passwd"), ("нет", "a.html")):
            with self.assertRaises(CoderError, msg=(proj, rel)):
                svc.preview(proj, rel)

    def test_images_not_orderable_by_model_but_savable(self):
        svc = files.FileService(Path(tempfile.mkdtemp()))
        with self.assertRaises(files.FileError):
            svc.create("a.png", "текст")
        m = svc.create_bytes("Кот.png", PNG)
        m2 = svc.create_bytes("Кот.png", PNG)
        self.assertEqual((m["name"], m2["name"], m["kind"]), ("Кот.png", "Кот (2).png", "image"))      # не перезаписывает
        self.assertEqual(svc.preview(m["name"])["type"], "image")
        with self.assertRaises(files.FileError):
            svc.create_bytes("a.exe", PNG)
        with self.assertRaises(CoderError):
            Workspace(Path(tempfile.mkdtemp())).safe("a.png", for_write=True)                          # и кодер не пишет «картинки» текстом


class FakeImageServer:
    """Крошечный сервер с API AUTOMATIC1111 и OpenAI-совместимым, плюс «Pollinations»."""
    def __init__(self):
        outer = self
        self.requests = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, code, body, ctype="application/json"):
                self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body, self.headers.get("Authorization")))
                if self.path == "/sdapi/v1/txt2img":
                    self._send(200, json.dumps({"images": [base64.b64encode(PNG).decode()]}).encode())
                elif self.path == "/v1/images/generations":
                    self._send(200, json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode())
                else:
                    self._send(404, b"{}")
            def do_GET(self):
                outer.requests.append((self.path, None, None))
                if self.path.startswith("/prompt/bad"):
                    self._send(200, b"<html>Rate limit</html>", "text/html")
                elif self.path.startswith("/prompt/"):
                    self._send(200, PNG, "image/png")
                else:
                    self._send(403, b"no")
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeImageServer()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def cfg(self, **kw):
        c = Config(Path(tempfile.mkdtemp()) / "c.json")
        c.update({"images": kw})
        return c

    def test_intent(self):
        yes = ["Сделай изображения аниме девушки с белыми волосами и немного в корове", "нарисуй кота на крыше", "Сгенерируй фото заката над морем",
               "создай картинку для обоев", "сделай мне аватар в стиле киберпанк", "Сделай обои с горами"]
        no = ["нарисуй схему архитектуры", "создай документ word про фото", "сделай отчет с картинками", "напиши программу", "сделай сайт с картинкой",
              "создай таблицу excel", "объясни что такое арт", "нарисуй диаграмму", "сделай график продаж", "сделай описание картинки"]
        for t in yes:
            self.assertTrue(images.wants_image(t), t)
        for t in no:
            self.assertFalse(images.wants_image(t), t)

    def test_sd_server(self):
        c = self.cfg(provider="sd", url=self.srv.url)
        data, ext, who = images.generate(c, "anime girl, white hair", (512, 768), seed=5)
        self.assertEqual((ext, data[:4]), ("png", PNG[:4]))
        path, body, _ = [r for r in self.srv.requests if r[0] == "/sdapi/v1/txt2img"][-1]
        self.assertEqual((body["prompt"], body["width"], body["height"], body["seed"]), ("anime girl, white hair", 512, 768, 5))

    def test_auto_uses_own_server_when_url_given(self):
        self.assertEqual(images.provider_name(self.cfg(provider="auto", url=self.srv.url)), "sd")
        self.assertEqual(images.provider_name(self.cfg(provider="auto")), "pollinations")

    def test_openai_compatible_with_key(self):
        c = self.cfg(provider="openai", url=self.srv.url, key="sk-test", model="flux")
        data, ext, _ = images.generate(c, "cat")
        self.assertEqual(ext, "png")
        path, body, auth = [r for r in self.srv.requests if r[0] == "/v1/images/generations"][-1]
        self.assertEqual((auth, body["model"]), ("Bearer sk-test", "flux"))

    def test_pollinations_with_fallback_and_html_rejected(self):
        old = images.POLLINATIONS_URLS
        try:
            images.POLLINATIONS_URLS = (self.srv.url + "/dead/{prompt}", self.srv.url + "/prompt/{prompt}")        # первый адрес не работает — берётся второй
            data, ext, who = images.generate(self.cfg(provider="pollinations"), "a cat")
            self.assertEqual((ext, who), ("png", "Pollinations"))
            images.POLLINATIONS_URLS = (self.srv.url + "/prompt/bad{prompt}",)
            with self.assertRaises(images.ImageError) as cm:
                images.generate(self.cfg(provider="pollinations"), "x")
            self.assertIn("не картинку", str(cm.exception))                                                       # страница с ошибкой не сохраняется как картинка
        finally:
            images.POLLINATIONS_URLS = old

    def test_errors_are_explained(self):
        with self.assertRaises(images.ImageError) as cm:
            images.generate(self.cfg(provider="sd", url=""), "x")
        self.assertIn("не указан адрес", str(cm.exception))
        with self.assertRaises(images.ImageError) as cm:
            images.generate(self.cfg(provider="sd", url="http://127.0.0.1:9"), "x")
        self.assertIn("нет связи", str(cm.exception))
        with self.assertRaises(images.ImageError) as cm:
            images.generate(self.cfg(provider="openai", url=self.srv.url + "/nope"), "x")
        self.assertIn("404", str(cm.exception))

    def test_stop_interrupts_wait(self):
        ev = threading.Event(); ev.set()
        with self.assertRaises(images.ImageError):
            images.generate(self.cfg(provider="sd", url=self.srv.url), "x", stop=ev)

    def test_anime_style_and_model(self):
        self.assertTrue(images.style_prompt("girl, white hair, katana", "нарисуй аниме девочку").startswith("anime screencap, anime coloring, 2D"))
        out = images.style_prompt("anime screencap, 2D, girl", "аниме")                       # уже названные теги стиля не дублируются
        self.assertEqual(out.count("anime screencap"), 1); self.assertEqual(out.count("2D"), 1); self.assertTrue(out.endswith("girl"))
        self.assertTrue(images.style_prompt("girl", "нарисуй девочку", True).startswith("anime screencap"))   # force: стиль всегда
        self.assertEqual(images.style_prompt("a cat", "нарисуй кота"), "a cat")
        self.assertEqual(images.pick_models("фото аниме девочки"), ("flux-anime", "flux"))
        self.assertEqual(images.pick_models("кот"), ("flux",))

    def test_pollinations_falls_back_to_next_model(self):
        old = images.POLLINATIONS_URLS
        try:
            images.POLLINATIONS_URLS = (self.srv.url + "/prompt/{prompt}",)
            seen = len(self.srv.requests)
            images.via_pollinations("x", (64, 64), 1, models=("flux-anime", "flux"))
            self.assertIn("model=flux-anime", self.srv.requests[seen][0])
        finally:
            images.POLLINATIONS_URLS = old

    def test_prompt_helpers(self):
        self.assertEqual(images.clean_prompt("Prompt: `1girl, white hair, anime`\nlol", "x"), "1girl, white hair, anime lol")
        self.assertEqual(images.clean_prompt("", "кот на крыше"), "кот на крыше")
        self.assertEqual(images.sniff(PNG), "png"); self.assertEqual(images.sniff(b"\xff\xd8\xff\xe0"), "jpg"); self.assertEqual(images.sniff(b"<html>"), "")
        self.assertEqual(images.pick_size("обои с горами"), (1024, 576))
        msgs = images.prompt_request("сделай фон лесом", "1girl, white hair")
        self.assertIn("1girl, white hair", msgs[1]["content"])


if __name__ == "__main__":
    unittest.main()


class LiveAndSyntax(unittest.TestCase):
    def _svc(self, replies):
        from test_coder import make
        return make(replies)

    def test_live_view(self):
        v = coder.live_view("Сделаю страницу.\n[[ЗАПИСАТЬ: index.html]]\n<h1>a</h1>\n<p>b</p>\n")
        self.assertEqual(v["say"], "Сделаю страницу.")
        self.assertIn("Пишу index.html", v["doing"]); self.assertIn("строк: 3", v["doing"])
        self.assertEqual(coder.live_view("Так.\n[[ЗАПУСТИТЬ: python a.py]]")["doing"], "▶ python a.py")
        self.assertEqual(coder.live_view("[[ЗАПИСАТЬ: a.py]]\nx\n[[/ЗАПИСАТЬ]]")["doing"], "")        # файл закончен — уже не «пишет»

    def test_think_and_stream_events(self):
        svc, model, ev, tmp = self._svc(["Пишу.\n[[ЗАПИСАТЬ: a.py]]\nprint(1)\n[[/ЗАПИСАТЬ]]", "[[ГОТОВО]]ок[[/ГОТОВО]]"])
        # «модель» присылает ответ кусками
        orig = model.chat
        def chat(messages, max_tokens=0, temperature=0, on_delta=None, stop=None, **kw):
            r = orig(messages, max_tokens, temperature, on_delta, stop)
            if on_delta:
                on_delta(r)
            return r
        model.chat = chat
        svc.start("задача", [], wait=True)
        kinds = [e.get("kind") for e in ev if e["type"] == "coder"]
        self.assertEqual(kinds.count("think"), 2)
        self.assertIn("stream", kinds)
        self.assertLess(kinds.index("think"), kinds.index("write"))

    def test_syntax_error_sent_back_to_model(self):
        svc, model, ev, tmp = self._svc(["[[ЗАПИСАТЬ: a.py]]\ndef f(:\n    pass\n[[/ЗАПИСАТЬ]]\n[[ГОТОВО]]готово[[/ГОТОВО]]",
                                         "[[ЗАПИСАТЬ: a.py]]\ndef f():\n    pass\n[[/ЗАПИСАТЬ]]\n[[ГОТОВО]]исправила[[/ГОТОВО]]"], )
        svc.start("задача", [], wait=True)
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertEqual(done["summary"], "исправила")
        self.assertIn("синтаксические ошибки", model.seen[1][-1]["content"])
        self.assertIn("a.py, строка 1", model.seen[1][-1]["content"])
        self.assertIn("Синтаксис проверен автоматически", done["verdict"])                      # честный итог: не запускали, но синтаксис проверен
        self.assertFalse(done["ok"])

    def test_autocheck_reports_remaining_error(self):
        bad = "[[ЗАПИСАТЬ: a.py]]\ndef f(:\n[[/ЗАПИСАТЬ]]\n[[ГОТОВО]]х[[/ГОТОВО]]"
        svc, model, ev, tmp = self._svc([bad, bad, bad])
        svc.start("задача", [], wait=True)
        done = [e for e in ev if e["type"] == "coder_done"][0]
        self.assertIn("нашла синтаксические ошибки", done["verdict"])
