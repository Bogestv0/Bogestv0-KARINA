"""Связь с расширением Bogestv0 VPN: «Карина, включи впн».

Расширение браузера не умеет принимать входящие соединения, поэтому наоборот: Карина слушает на 127.0.0.1:<порт> (по умолчанию 47835),
а расширение само подключается к ней по WebSocket и ждёт команды. Пока Карина не запущена, расширение раз в 30 секунд пробует снова.

Защита: сервер слушает только локальный адрес, а соединение принимает только от расширения Bogestv0 VPN (браузер сам ставит заголовок
Origin: chrome-extension://<id>, страница сайта подделать его не может; id расширения постоянен — задан ключом в manifest.json).

Протокол (JSON):
  расширение → Карина: {"t":"hello","connected":bool,"hasProfile":bool,"v":"1.4.0"} · {"t":"ping"} · {"t":"result","id":N,"ok":bool,"connected":bool,"error":"…"}
  Карина → расширение: {"t":"cmd","id":N,"cmd":"on"|"off"|"toggle"|"status"} · {"t":"pong"}
"""
from __future__ import annotations
import itertools, json, threading
from http import HTTPStatus

EXTENSION_ID = "cfilndkhacojgcfnepiiljkpaikemkgc"     # из поля "key" в extension/manifest.json
ALLOWED_ORIGINS = {f"chrome-extension://{EXTENSION_ID}"}
DEFAULT_PORT = 47835


class VpnError(Exception):
    """Сообщение можно сразу произнести голосом."""


class VpnLink:
    def __init__(self, cfg, log=None):
        self.cfg, self._log = cfg, log or (lambda *_: None)
        self._server = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._conns: dict = {}                      # соединение → последнее известное состояние {"connected","hasProfile"}
        self._pending: dict[int, dict] = {}         # id команды → {"ev": Event, "res": ответ}
        self._ids = itertools.count(1)
        self.error = ""

    # ---------- сервер ----------
    @property
    def port(self) -> int:
        try:
            p = int((self.cfg.get("vpn") or {}).get("port") or DEFAULT_PORT)
            return p if 1024 <= p <= 65535 else DEFAULT_PORT
        except (TypeError, ValueError):
            return DEFAULT_PORT

    def enabled(self) -> bool:
        return bool((self.cfg.get("vpn") or {}).get("enabled", True))

    def start(self):
        if self._thread or not self.enabled():
            return
        try:
            from websockets.sync.server import serve
        except ImportError:
            self.error = "не установлен пакет websockets"
            return

        def check(conn, request):
            origin = request.headers.get("Origin", "")
            if origin not in ALLOWED_ORIGINS:
                return conn.respond(HTTPStatus.FORBIDDEN, "Только расширение Bogestv0 VPN.\n")
            return None

        try:
            self._server = serve(self._handler, "127.0.0.1", self.port, process_request=check, max_size=64 * 1024,
                                 ping_interval=None)
        except OSError as e:
            self.error = f"порт {self.port} занят ({e.__class__.__name__})"
            self._log(f"Связь с VPN: {self.error}")
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="vpn-link")
        self._thread.start()

    def stop(self):
        srv, self._server, self._thread = self._server, None, None
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass

    def _handler(self, conn):
        with self._lock:
            self._conns[conn] = {}
        try:
            for raw in conn:
                try:
                    m = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                t = m.get("t")
                if t == "ping":
                    conn.send('{"t":"pong"}')
                elif t == "hello":
                    with self._lock:
                        self._conns[conn] = {"connected": bool(m.get("connected")), "hasProfile": bool(m.get("hasProfile"))}
                elif t == "result":
                    with self._lock:
                        p = self._pending.get(m.get("id"))
                        if "connected" in m and conn in self._conns:
                            self._conns[conn]["connected"] = bool(m["connected"])
                    if p is not None:
                        p["res"].append(m)
                        if len(p["res"]) >= p["need"]:
                            p["ev"].set()
        except Exception:
            pass
        finally:
            with self._lock:
                self._conns.pop(conn, None)

    # ---------- команды ----------
    def browsers(self) -> int:
        with self._lock:
            return len(self._conns)

    def command(self, cmd: str, timeout: float = 25.0) -> list[dict]:
        """Отправляет команду всем подключённым расширениям (у вас может быть Chrome и Edge сразу) и ждёт ответы."""
        with self._lock:
            conns = list(self._conns)
        if not conns:
            if not self.enabled():
                raise VpnError("Управление VPN выключено в настройках Карины.")
            if self.error:
                raise VpnError(f"Связь с VPN не работает: {self.error}.")
            raise VpnError("Расширение Bogestv0 VPN не подключено к Карине. Откройте браузер с расширением; если оно старой версии — обновите его.")
        cid = next(self._ids)
        p = {"ev": threading.Event(), "res": [], "need": len(conns)}
        with self._lock:
            self._pending[cid] = p
        try:
            msg = json.dumps({"t": "cmd", "id": cid, "cmd": cmd})
            sent = 0
            for c in conns:
                try:
                    c.send(msg)
                    sent += 1
                except Exception:
                    pass
            p["need"] = sent
            if not sent:
                raise VpnError("Связь с расширением потеряна. Попробуйте ещё раз через полминуты.")
            p["ev"].wait(timeout)
            return list(p["res"])
        finally:
            with self._lock:
                self._pending.pop(cid, None)
