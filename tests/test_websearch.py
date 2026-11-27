"""Поиск и чтение страниц: разбор выдачи, защита адресов, извлечение текста, загрузка с локального сервера."""
import sys, threading, unittest, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from karina import websearch as ws

DDG = """<html><body>
<div class="result results_links web-result"><h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbaikal%3Fa%3D1&amp;rut=abc">Озеро <b>Байкал</b> — глубина</a></h2>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbaikal">Максимальная <b>глубина</b> 1642 м.</a></div>
<div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=x">Реклама</a></div>
<div class="result"><a class="result__a" href="https://ru.wikipedia.org/wiki/Байкал">Байкал — Википедия</a><a class="result__snippet">Озеро в Сибири.</a></div>
</body></html>"""
MOJEEK = """<ul class="results-standard"><li><h2><a class="title" href="https://site.ru/a">Первый</a></h2><p class="s">Описание <b>один</b></p></li>
<li><h2><a class="title" href="https://site.ru/b">Второй</a></h2><p class="s">Описание два</p></li></ul>"""
WIKI = '{"query":{"search":[{"title":"Байкал","snippet":"озеро <span class=\\"searchmatch\\">Байкал</span> в Сибири"}]}}'

ARTICLE = """<html><head><title>Тест &amp; страница</title><meta charset="utf-8"><script>var secret=1;</script></head><body>
<nav>Меню Главная Контакты</nav><header>Шапка сайта</header>
<article><h1>Заголовок</h1><p>%s</p><p>Второй абзац про Байкал и его глубину в метрах, подробный и длинный, чтобы пройти порог.</p></article>
<footer>Подвал © 2026</footer><script>alert(1)</script></body></html>""" % ("Байкал — самое глубокое озеро планеты. " * 12)


class Page(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/cp1251":
            body, ct = ("<html><head><meta charset='windows-1251'><title>Заголовок</title></head><body><p>" + "Русский текст страницы для проверки кодировки. " * 10 + "</p></body></html>").encode("cp1251"), "text/html"
        elif self.path == "/art":
            body, ct = ARTICLE.encode(), "text/html; charset=utf-8"
        elif self.path == "/pdf":
            body, ct = b"%PDF-1.4 " + b"x" * 500, "application/pdf"
        elif self.path == "/big":
            body, ct = b"<html><body><p>" + b"a " * 3_000_000 + b"</p></body></html>", "text/html"
        elif self.path == "/short":
            body, ct = b"<html><body>hi</body></html>", "text/html"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class Quiet(ThreadingHTTPServer):
    def handle_error(self, *a):      # клиент намеренно обрывает загрузку слишком большой страницы
        pass


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = Quiet(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_parse_ddg(self):
        r = ws.parse_ddg(DDG)
        self.assertEqual([x["url"] for x in r], ["https://example.org/baikal?a=1", "https://ru.wikipedia.org/wiki/Байкал"])   # реклама отброшена
        self.assertEqual(r[0]["title"], "Озеро Байкал — глубина"); self.assertEqual(r[0]["snippet"], "Максимальная глубина 1642 м.")
        with self.assertRaises(ws.SearchError):
            ws.parse_ddg("<html><form action='/anomaly.js'>anomaly</form></html>")

    def test_parse_mojeek_and_wiki(self):
        r = ws.parse_mojeek(MOJEEK)
        self.assertEqual([x["title"] for x in r], ["Первый", "Второй"]); self.assertEqual(r[0]["snippet"], "Описание один")
        w = ws.parse_wikipedia(WIKI, "ru")
        self.assertEqual(w[0]["url"], "https://ru.wikipedia.org/wiki/%D0%91%D0%B0%D0%B9%D0%BA%D0%B0%D0%BB"); self.assertEqual(w[0]["snippet"], "озеро Байкал в Сибири")
        with self.assertRaises(ws.SearchError):
            ws.parse_wikipedia("не json", "ru")

    def test_url_guard(self):
        for bad in ("http://127.0.0.1/", "http://localhost:8080/x", "http://10.0.0.5/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
                    "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "http://user:pass@93.184.216.34/"):
            with self.assertRaises(ws.SearchError, msg=bad):
                ws.check_url(bad)
        self.assertEqual(ws.check_url("http://93.184.216.34/x"), "http://93.184.216.34/x")

    def test_redirect_to_private_is_blocked(self):
        h = ws._Redirect(False)
        req = urllib.request.Request("http://93.184.216.34/")
        with self.assertRaises(ws.SearchError):
            h.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:9999/admin")
        with self.assertRaises(ws.SearchError):
            h.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/")

    def test_extract_prefers_article_and_drops_junk(self):
        title, text = ws.extract_text(ARTICLE)
        self.assertEqual(title, "Тест & страница")
        self.assertIn("самое глубокое озеро", text)
        for junk in ("Меню", "Шапка", "Подвал", "secret", "alert"):
            self.assertNotIn(junk, text)

    def test_best_excerpt(self):
        text = "\n".join(["Совсем про другое: рецепт борща со свёклой и капустой, подробно и вкусно." * 2,
                          "Глубина озера Байкал достигает 1642 метров, это максимальная глубина в мире.",
                          "Ещё один абзац про погоду в Москве, он длинный и неинтересный для запроса."])
        ex = ws.best_excerpt(text, "глубина озера Байкал", 120)
        self.assertIn("1642", ex); self.assertLessEqual(len(ex), 120)

    def test_fetch_page_local(self):
        p = ws.fetch_page(self.base + "/art", allow_private=True)
        self.assertIn("глубокое озеро", p["text"])
        p = ws.fetch_page(self.base + "/cp1251", allow_private=True)
        self.assertIn("Русский текст", p["text"]); self.assertEqual(p["title"], "Заголовок")
        for path, why in (("/pdf", "не страница"), ("/short", "мало текста"), ("/404", "404")):
            with self.assertRaises(ws.SearchError, msg=path) as c:
                ws.fetch_page(self.base + path, allow_private=True)
            self.assertIn(why, str(c.exception))
        _, _, body = ws.http_get(self.base + "/big", allow_private=True)
        self.assertLessEqual(len(body), ws.MAX_PAGE_BYTES)
        with self.assertRaises(ws.SearchError):
            ws.fetch_page(self.base + "/art")                          # без разрешения localhost закрыт

    def test_cancel(self):
        ev = threading.Event(); ev.set()
        with self.assertRaises(ws.Cancelled):
            ws.http_get(self.base + "/art", allow_private=True, stop=ev)

    def test_search_falls_back_between_providers(self):
        def bad(q, n, s): raise ws.SearchError("капча")
        def empty(q, n, s): return []
        def good(q, n, s): return [{"url": "https://a.ru/x/", "title": "A", "snippet": ""}, {"url": "https://www.a.ru/x", "title": "A2", "snippet": ""}, {"url": "https://b.ru", "title": "B", "snippet": ""}]
        res, name = ws.search("q", 5, providers=(("P1", bad), ("P2", empty), ("P3", good)))
        self.assertEqual(name, "P3"); self.assertEqual(len(res), 2)       # дубликат a.ru/x убран
        with self.assertRaises(ws.SearchError) as c:
            ws.search("q", 5, providers=(("P1", bad), ("P2", empty)))
        self.assertIn("капча", str(c.exception))


if __name__ == "__main__":
    unittest.main()
