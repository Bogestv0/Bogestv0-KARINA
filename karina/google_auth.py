"""Вход через Google в браузере (OAuth 2.0 для приложений на компьютере, с PKCE): открываем обычный браузер, вы выбираете аккаунт,
Google возвращает вас на локальную страницу Карины (127.0.0.1), мы забираем имя, почту и фото. Пароль Google Карина не видит,
токены не сохраняются (нужны только на секунду, чтобы спросить имя).

Требуется один раз создать «Идентификатор клиента OAuth» типа «Приложение для компьютера» в Google Cloud Console и вписать Client ID
(и Client secret, если Google его выдал) в Карине. Дату рождения и город Google без особой проверки приложения не отдаёт — их заполняете сами.
"""
from __future__ import annotations
import base64, hashlib, http.server, json, secrets, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = "openid email profile"

_PAGE = """<!doctype html><meta charset="utf-8"><title>Карина</title>
<body style="font:18px system-ui;background:#10131a;color:#e8ecf4;display:grid;place-items:center;height:100vh;margin:0"><div style="text-align:center">
<h2>{title}</h2><p style="opacity:.7">{text}</p></div></body>"""


class GoogleError(Exception):
    """Текст можно показать пользователю."""


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
        except Exception:
            err = {}
        if err.get("error") == "invalid_client":
            raise GoogleError("Google не принял Client ID или Client secret. Проверьте их в настройках профиля.") from None
        raise GoogleError(f"Google отклонил вход: {err.get('error_description') or err.get('error') or e.code}.") from None
    except Exception:
        raise GoogleError("Не получилось связаться с Google. Проверьте интернет.") from None


def sign_in(client_id: str, client_secret: str = "", open_browser=None, timeout: float = 180.0, cancel: threading.Event | None = None) -> dict:
    """Возвращает {sub, name, given_name, email, picture}. Бросает GoogleError с понятным текстом."""
    if not client_id.strip():
        raise GoogleError("Не указан Client ID. Создайте его в Google Cloud Console (инструкция в профиле) и впишите в поле.")
    open_browser = open_browser or webbrowser.open
    cancel = cancel or threading.Event()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    result: dict = {}
    done = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404); self.end_headers(); return          # favicon и прочее
            ok = q.get("state", [""])[0] == state
            if not ok:
                result["error"] = "Ответ Google не прошёл проверку (state). Попробуйте войти ещё раз."
            elif "error" in q:
                result["error"] = "Вход отменён." if q["error"][0] == "access_denied" else f"Google вернул ошибку: {q['error'][0]}."
            else:
                result["code"] = q["code"][0]
            page = _PAGE.format(title="Готово" if "code" in result else "Не получилось", text="Можно закрыть вкладку и вернуться в Карину." if "code" in result else result["error"])
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    redirect = f"http://127.0.0.1:{port}/"
    threading.Thread(target=srv.serve_forever, daemon=True, name="google-oauth").start()
    try:
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id.strip(), "redirect_uri": redirect, "response_type": "code", "scope": SCOPES, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"})
        open_browser(url)
        end = time.time() + timeout
        while not done.wait(0.25):
            if cancel.is_set():
                raise GoogleError("Вход отменён.")
            if time.time() > end:
                raise GoogleError("Не дождалась входа: вкладка в браузере так и не вернулась. Попробуйте ещё раз.")
        if "error" in result:
            raise GoogleError(result["error"])
        tok = _post(TOKEN_URL, {"code": result["code"], "client_id": client_id.strip(), "redirect_uri": redirect, "grant_type": "authorization_code",
                                "code_verifier": verifier, **({"client_secret": client_secret.strip()} if client_secret.strip() else {})})
        access = tok.get("access_token")
        if not access:
            raise GoogleError("Google не выдал доступ. Попробуйте ещё раз.")
        req = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        try:
            info = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        except Exception:
            raise GoogleError("Вход прошёл, но данные профиля получить не удалось.") from None
        return {"sub": str(info.get("sub") or ""), "name": info.get("name") or "", "given_name": info.get("given_name") or "",
                "email": info.get("email") or "", "picture": info.get("picture") or ""}
    finally:
        srv.shutdown()
        srv.server_close()
