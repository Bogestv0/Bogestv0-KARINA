"""Профиль, погода по городу из профиля, поздравления, питомцы и вход через Google — на поддельных серверах (без интернета)."""
import datetime, http.server, json, sys, tempfile, threading, unittest, urllib.parse, urllib.request
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from karina import actions, google_auth, personal, profile as prof, weather
from karina.actions import Ctx, dispatch
from karina.config import Config
from karina.wake import split_wake

CITIES = {"красноярск": ("Красноярск", 56.0, 92.8), "москва": ("Москва", 55.7, 37.6), "казань": ("Казань", 55.8, 49.1)}


class FakeWeatherHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == "/geo":
            hit = CITIES.get(q["name"][0].lower())
            out = {"results": [{"name": hit[0], "latitude": hit[1], "longitude": hit[2]}]} if hit else {}
        else:
            rain = abs(float(q["latitude"][0]) - 55.8) < 0.01      # в Казани дождь
            out = {"current": {"temperature_2m": -3.4, "weather_code": 61 if rain else 3, "wind_speed_10m": 4.2},
                   "daily": {"weather_code": [61 if rain else 3, 0], "temperature_2m_max": [1.0, 5.2], "temperature_2m_min": [-6.0, -1.0], "precipitation_probability_max": [70 if rain else 10, 20]}}
        body = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeWeatherHandler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.p = [mock.patch.object(weather, "GEO", base + "/geo"), mock.patch.object(weather, "FORECAST", base + "/forecast")]
        for x in cls.p: x.start()

    @classmethod
    def tearDownClass(cls):
        for x in cls.p: x.stop()
        cls.srv.shutdown()

    def setUp(self):
        self.cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        self.names = self.cfg.wake_names()

    def set_profile(self, **kw):
        self.cfg.update({"profile": prof.clean({**prof.default(), **kw})})

    def say(self, phrase):
        _, rest = split_wake(phrase, self.names)
        return dispatch(Ctx(self.cfg, mock.MagicMock(), None, raw=phrase), rest)[1]


class ProfileTests(Base):
    def test_clean(self):
        c = prof.clean({"name": " Миша " * 20, "birth_day": 31, "birth_month": 2, "birth_year": 1800, "city": "X" * 99,
                        "pets": [{"name": "Шарик", "kind": "dog"}, {"name": "", "kind": "cat"}, {"name": "Муся", "kind": "dragon", "birth_day": 5, "birth_month": 6}]})
        self.assertEqual((c["birth_day"], c["birth_month"], c["birth_year"]), (0, 0, 0))     # 31 февраля и 1800 год отброшены
        self.assertLessEqual(len(c["name"]), 40); self.assertEqual(len(c["city"]), 60)
        self.assertEqual([x["name"] for x in c["pets"]], ["Шарик", "Муся"]); self.assertEqual(c["pets"][1]["kind"], "other")

    def test_age_and_birthday_greeting_once(self):
        self.set_profile(name="Михаил Иванов", birth_day=12, birth_month=11, birth_year=1996)
        d = datetime.date(2026, 11, 12)
        self.assertEqual(prof.age(prof.get(self.cfg), d), 30)
        g = prof.due_greeting(self.cfg, d)
        self.assertIn("С днём рождения, Михаил!", g); self.assertIn("30 лет", g)
        prof.mark_greeted(self.cfg, d)
        self.assertEqual(prof.due_greeting(self.cfg, d), "")                                  # второй раз в тот же день — нет
        self.assertEqual(prof.due_greeting(self.cfg, datetime.date(2026, 11, 13)), "")

    def test_pet_birthday_and_no_year(self):
        self.set_profile(pets=[{"name": "Барсик", "kind": "cat", "birth_day": 1, "birth_month": 3}])
        self.assertIn("Барсик", prof.due_greeting(self.cfg, datetime.date(2026, 3, 1)))
        self.set_profile(name="Аня", birth_day=1, birth_month=3)
        self.assertNotIn("исполняется", prof.due_greeting(self.cfg, datetime.date(2026, 3, 1)))

    def test_hello_uses_name_and_congratulates(self):
        self.set_profile(name="Михаил")
        self.assertRegex(self.say("Карина, привет"), r"^(Доброе утро|Добрый день|Добрый вечер|Привет), Михаил! Чем помочь\?$")
        t = datetime.date.today()
        self.set_profile(name="Михаил", birth_day=t.day, birth_month=t.month)
        r = self.say("привет"); self.assertTrue(r.startswith("С днём рождения, Михаил!"))
        self.assertNotIn("С днём рождения", self.say("привет"))

    def test_leap_day(self):
        self.set_profile(birth_day=29, birth_month=2)
        self.assertIsNotNone(prof.days_to_birthday(prof.get(self.cfg), datetime.date(2026, 1, 1)))

    def test_prompt_section(self):
        self.set_profile(name="Миша", city="Красноярск", birth_year=2000, birth_day=1, birth_month=1, pets=[{"name": "Шарик", "kind": "dog"}])
        s = prof.prompt_section(self.cfg)
        for w in ("Миша", "Красноярск", "собака по имени Шарик"): self.assertIn(w, s)
        self.cfg.update({"profile": {"share_with_llm": False}})
        self.assertEqual(prof.prompt_section(self.cfg), "")
        self.set_profile()
        self.assertEqual(prof.prompt_section(self.cfg), "")

    def test_whoami_and_pets(self):
        self.set_profile(name="Миша", city="Казань", birth_day=1, birth_month=1, birth_year=2000,
                         pets=[{"name": "Шарик", "kind": "dog"}, {"name": "Муся", "kind": "cat"}])
        self.assertEqual(self.say("как меня зовут"), "Вас зовут Миша.")
        self.assertEqual(self.say("как зовут мою собаку"), "Зовут Шарик.")
        self.assertEqual(self.say("как зовут мою кошку"), "Зовут Муся.")
        self.assertIn("собака по имени Шарик, кошка по имени Муся", self.say("кто у меня дома"))
        self.assertIn("Казань", self.say("в каком я городе"))
        self.assertIn("первого января", self.say("когда у меня день рождения"))
        self.set_profile()
        self.assertIn("Профиль", self.say("сколько мне лет"))
        self.assertIn("не добавили питомцев", self.say("кто у меня дома"))


class WeatherTests(Base):
    def test_city_from_profile(self):
        self.set_profile(city="Красноярск")
        r = self.say("Карина, скажи погоду")
        self.assertTrue(r.startswith("Красноярск, сейчас минус 3 градуса, пасмурно, ветер 4 м/с. Днём от минус 6 градусов до плюс 1 градус."), r)

    def test_named_city_with_case(self):
        self.set_profile(city="Красноярск")
        self.assertTrue(self.say("какая погода в Москве").startswith("Москва, сейчас"))
        self.assertTrue(self.say("погода в Казани").startswith("Казань, сейчас"))

    def test_tomorrow(self):
        self.set_profile(city="Москва")
        r = self.say("погода на завтра"); self.assertIn("завтра: ясно", r)
        self.assertIn("завтра", self.say("какая погода завтра"))

    def test_no_city_and_unknown_city(self):
        self.assertIn("Не знаю, где вы живёте", self.say("скажи погоду"))
        self.assertIn("Не нашла город", self.say("погода в Зюзюкино"))

    def test_dog_raincoat_only_for_profile_city(self):
        self.set_profile(city="Казань", pets=[{"name": "Шарик", "kind": "dog"}])
        self.assertIn("дождевик", self.say("какая погода"))
        self.assertNotIn("дождевик", self.say("погода в Москве"))
        self.assertIn("Зонт лучше взять", self.say("нужен ли зонт"))

    def test_web_search_still_searches(self):
        with mock.patch.object(actions.webbrowser, "open") as op:
            self.say("найди в интернете погоду")
        self.assertTrue(op.called)

    def test_offline(self):
        self.set_profile(city="Москва")
        with mock.patch.object(weather, "GEO", "http://127.0.0.1:1/x"):
            self.assertIn("Проверьте интернет", self.say("погода"))


class FakeGoogle(http.server.BaseHTTPRequestHandler):
    seen = {}
    def log_message(self, *a): pass
    def _json(self, o, code=200):
        b = json.dumps(o).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        FakeGoogle.seen = {k: v[0] for k, v in form.items()}
        if form.get("client_id", [""])[0] == "bad": return self._json({"error": "invalid_client"}, 401)
        self._json({"access_token": "tok"})
    def do_GET(self):
        self._json({"sub": "1", "name": "Михаил Иванов", "given_name": "Михаил", "email": "m@example.com", "picture": "http://x/p.png"}
                   if self.headers.get("Authorization") == "Bearer tok" else {}, 200)


class GoogleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGoogle)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.p = [mock.patch.object(google_auth, "TOKEN_URL", b + "/token"), mock.patch.object(google_auth, "USERINFO_URL", b + "/userinfo")]
        for x in cls.p: x.start()

    @classmethod
    def tearDownClass(cls):
        for x in cls.p: x.stop()
        cls.srv.shutdown()

    @staticmethod
    def browser(extra=None, state_ok=True, error=None):
        """Притворяется браузером: открывает «страницу Google» и сразу возвращает пользователя на адрес Карины."""
        def go(url):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            go.q = {k: v[0] for k, v in q.items()}
            params = {"state": q["state"][0] if state_ok else "wrong"}
            params.update({"error": error} if error else {"code": "abc"})
            threading.Thread(target=lambda: urllib.request.urlopen(q["redirect_uri"][0] + "?" + urllib.parse.urlencode(params), timeout=5).read(), daemon=True).start()
        return go

    def test_success_with_pkce(self):
        b = self.browser()
        info = google_auth.sign_in("cid", "sec", open_browser=b, timeout=10)
        self.assertEqual((info["given_name"], info["email"]), ("Михаил", "m@example.com"))
        self.assertEqual(b.q["code_challenge_method"], "S256"); self.assertIn("openid", b.q["scope"]); self.assertTrue(b.q["redirect_uri"].startswith("http://127.0.0.1:"))
        self.assertEqual(FakeGoogle.seen["client_secret"], "sec")
        import base64, hashlib
        want = base64.urlsafe_b64encode(hashlib.sha256(FakeGoogle.seen["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(want, b.q["code_challenge"])                      # проверка PKCE: verifier соответствует challenge

    def test_errors(self):
        with self.assertRaises(google_auth.GoogleError) as e: google_auth.sign_in("", open_browser=self.browser())
        self.assertIn("Client ID", str(e.exception))
        with self.assertRaises(google_auth.GoogleError) as e: google_auth.sign_in("cid", open_browser=self.browser(state_ok=False), timeout=10)
        self.assertIn("state", str(e.exception))
        with self.assertRaises(google_auth.GoogleError) as e: google_auth.sign_in("cid", open_browser=self.browser(error="access_denied"), timeout=10)
        self.assertEqual(str(e.exception), "Вход отменён.")
        with self.assertRaises(google_auth.GoogleError) as e: google_auth.sign_in("bad", open_browser=self.browser(), timeout=10)
        self.assertIn("Client ID", str(e.exception))

    def test_timeout_and_cancel(self):
        with self.assertRaises(google_auth.GoogleError) as e: google_auth.sign_in("cid", open_browser=lambda u: None, timeout=0.5)
        self.assertIn("Не дождалась", str(e.exception))
        ev = threading.Event(); threading.Timer(0.3, ev.set).start()
        with self.assertRaises(google_auth.GoogleError) as e: google_auth.sign_in("cid", open_browser=lambda u: None, cancel=ev, timeout=5)
        self.assertEqual(str(e.exception), "Вход отменён.")


if __name__ == "__main__":
    unittest.main()
