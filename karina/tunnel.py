"""Свой VPN и прокси Карины: «включи впн», «включи прокси», «включи впн только для хрома».

Как это устроено. Карина сама ничего не шифрует: она скачивает один раз движок sing-box (открытый, SagerNet), сама собирает для него
настройки из ваших ссылок (vless://, vmess://, trojan://, ss://, hysteria2:// или подписка — адрес со списком таких ссылок) и запускает.
  • режим «прокси»: sing-box слушает 127.0.0.1:порт, а Карина прописывает этот адрес системным прокси Windows (права администратора не нужны);
    при выключении прежние настройки прокси возвращаются;
  • режим «VPN» (TUN): создаётся виртуальный сетевой адаптер, через него идёт весь трафик (нужны права администратора);
  • «только для приложений»: тоже TUN, но через сервер идёт трафик лишь выбранных программ (по имени exe), всё остальное — напрямую.
Ссылки лежат в config.json (в них пароли), в окно они не отдаются: там только названия серверов.
"""
from __future__ import annotations
import base64, hashlib, json, os, re, shutil, socket, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path

from .models import Cancelled

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0

# Версия движка закреплена вместе с контрольной суммой: формат настроек sing-box от версии к версии меняется, а подмена файла на сервере не пройдёт проверку.
SINGBOX_VERSION = "1.11.15"
SINGBOX_ZIP = f"sing-box-{SINGBOX_VERSION}-windows-amd64.zip"
SINGBOX_URL = f"https://github.com/SagerNet/sing-box/releases/download/v{SINGBOX_VERSION}/{SINGBOX_ZIP}"
SINGBOX_SHA256 = "9acbda81d7a9a93d8d0616990cf8b804523adfcd0215d11a2dd9370746d2be28"
UA = {"User-Agent": "Bogestv0-KARINA"}

DEFAULTS = {
    "servers": [],          # [{"id","name","link","kind"}]
    "sub_url": "",          # адрес подписки (если сервера добавлены из неё)
    "active": "",           # id выбранного сервера
    "mode": "vpn",          # vpn (TUN, нужны права администратора) | proxy (системный прокси)
    "scope": "all",         # all — весь компьютер | apps — только приложения из списка apps (всегда через TUN)
    "apps": [],             # имена exe: ["chrome.exe", "Discord.exe"]
    "confirm": True,        # переспрашивать «да/нет» перед включением голосом
    "direct_ru": False,     # сайты .ru / .su / .рф открывать напрямую, мимо сервера
    "port": 10808,          # порт локального прокси в режиме «прокси»
}
MODES = ("vpn", "proxy")
SCOPES = ("all", "apps")
KINDS = {"vless": "VLESS", "vmess": "VMess", "trojan": "Trojan", "ss": "Shadowsocks", "hysteria2": "Hysteria2", "hy2": "Hysteria2"}
LINK_RE = re.compile(r"\b(?:vless|vmess|trojan|ss|hysteria2|hy2)://[^\s\"'<>]+", re.I)


class TunnelError(Exception):
    """Сообщение можно сразу произнести голосом."""


class LinkError(TunnelError):
    pass


# ====================================================================== разбор ссылок
def _b64d(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8", "replace")


def _flat(query: str) -> dict:
    return {k: v[0] for k, v in urllib.parse.parse_qs(query, keep_blank_values=True).items()}


def _port(v) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        raise LinkError("в ссылке не указан порт") from None
    if not 1 <= p <= 65535:
        raise LinkError("в ссылке неверный порт")
    return p


def _uport(u, default=None) -> int:
    try:
        p = u.port
    except ValueError:
        raise LinkError("в ссылке неверный порт") from None
    return _port(p if p is not None else default)


def _truthy(v) -> bool:
    return str(v or "").lower() in ("1", "true", "yes")


def _tls(q: dict, host: str, sec: str) -> dict | None:
    sec = (sec or "").lower()
    if sec == "xtls":
        sec = "tls"
    if sec not in ("tls", "reality"):
        return None
    t = {"enabled": True, "server_name": q.get("sni") or q.get("peer") or q.get("host") or host}
    if _truthy(q.get("allowInsecure")) or _truthy(q.get("insecure")):
        t["insecure"] = True
    if q.get("alpn"):
        t["alpn"] = [a for a in q["alpn"].split(",") if a]
    if q.get("fp"):
        t["utls"] = {"enabled": True, "fingerprint": q["fp"]}
    if sec == "reality":
        if not q.get("pbk"):
            raise LinkError("в ссылке Reality нет ключа pbk")
        t["reality"] = {"enabled": True, "public_key": q["pbk"], "short_id": q.get("sid", "")}
        t.setdefault("utls", {"enabled": True, "fingerprint": "chrome"})
    return t


def _transport(net: str, host: str, path: str, service: str) -> dict | None:
    net = (net or "tcp").lower()
    if net in ("ws", "websocket"):
        t = {"type": "ws", "path": path or "/"}
        if host:
            t["headers"] = {"Host": host}
        return t
    if net == "grpc":
        return {"type": "grpc", "service_name": service or path or ""}
    if net in ("h2", "http"):
        t = {"type": "http", "path": path or "/"}
        if host:
            t["host"] = [h for h in host.split(",") if h]
        return t
    if net == "httpupgrade":
        t = {"type": "httpupgrade", "path": path or "/"}
        if host:
            t["host"] = host
        return t
    if net in ("tcp", "raw", "none", ""):
        return None
    raise LinkError(f"транспорт «{net}» пока не поддерживается")


def _name(u_fragment: str, host: str, port: int) -> str:
    return urllib.parse.unquote(u_fragment).strip() or f"{host}:{port}"


def _parse_vless(link: str):
    u = urllib.parse.urlsplit(link)
    q = _flat(u.query)
    if not (u.username and u.hostname):
        raise LinkError("ссылка vless:// неполная")
    port = _uport(u)
    o = {"type": "vless", "server": u.hostname, "server_port": port, "uuid": urllib.parse.unquote(u.username)}
    if q.get("flow"):
        o["flow"] = q["flow"]
    o["packet_encoding"] = "xudp"
    tls = _tls(q, u.hostname, q.get("security", ""))
    if tls:
        o["tls"] = tls
    tr = _transport(q.get("type", "tcp"), q.get("host", ""), urllib.parse.unquote(q.get("path", "")), q.get("serviceName", ""))
    if tr:
        o["transport"] = tr
    return _name(u.fragment, u.hostname, port), o


def _parse_vmess(link: str):
    try:
        d = json.loads(_b64d(link[len("vmess://"):]))
    except (ValueError, json.JSONDecodeError):
        raise LinkError("ссылку vmess:// не удалось прочитать") from None
    if not (d.get("add") and d.get("id")):
        raise LinkError("ссылка vmess:// неполная")
    port = _port(d.get("port"))
    o = {"type": "vmess", "server": d["add"], "server_port": port, "uuid": d["id"], "security": d.get("scy") or "auto"}
    try:
        o["alter_id"] = int(d.get("aid") or 0)
    except (TypeError, ValueError):
        o["alter_id"] = 0
    q = {"sni": d.get("sni", ""), "host": d.get("host", ""), "alpn": d.get("alpn", ""), "fp": d.get("fp", "")}
    tls = _tls(q, d["add"], "tls" if str(d.get("tls", "")).lower() == "tls" else "")
    if tls:
        o["tls"] = tls
    tr = _transport(d.get("net", "tcp"), d.get("host", ""), d.get("path", ""), d.get("path", ""))
    if tr:
        o["transport"] = tr
    return str(d.get("ps") or f"{d['add']}:{port}").strip(), o


def _parse_trojan(link: str):
    u = urllib.parse.urlsplit(link)
    q = _flat(u.query)
    if not (u.username and u.hostname):
        raise LinkError("ссылка trojan:// неполная")
    port = _uport(u)
    o = {"type": "trojan", "server": u.hostname, "server_port": port, "password": urllib.parse.unquote(u.username)}
    tls = _tls(q, u.hostname, q.get("security", "tls"))
    if tls:
        o["tls"] = tls
    tr = _transport(q.get("type", "tcp"), q.get("host", ""), urllib.parse.unquote(q.get("path", "")), q.get("serviceName", ""))
    if tr:
        o["transport"] = tr
    return _name(u.fragment, u.hostname, port), o


def _parse_ss(link: str):
    rest = link[len("ss://"):]
    rest, _, frag = rest.partition("#")
    rest, _, query = rest.partition("?")
    rest = rest.rstrip("/")
    try:
        if "@" in rest:
            userinfo, _, hostport = rest.rpartition("@")
            userinfo = urllib.parse.unquote(userinfo)
            if ":" not in userinfo:
                userinfo = _b64d(userinfo)
        else:
            decoded = _b64d(urllib.parse.unquote(rest))
            userinfo, _, hostport = decoded.rpartition("@")
        method, _, password = userinfo.partition(":")
        host, _, port_s = hostport.rpartition(":")
    except (ValueError, UnicodeError):
        raise LinkError("ссылку ss:// не удалось прочитать") from None
    host = host.strip("[]")
    if not (method and host):
        raise LinkError("ссылка ss:// неполная")
    port = _port(port_s)
    o = {"type": "shadowsocks", "server": host, "server_port": port, "method": method, "password": password}
    plug = _flat(query).get("plugin")
    if plug:
        pname, _, popts = plug.partition(";")
        o["plugin"] = pname
        if popts:
            o["plugin_opts"] = popts
    return _name(frag, host, port), o


def _parse_hy2(link: str):
    u = urllib.parse.urlsplit(link)
    q = _flat(u.query)
    if not (u.hostname and (u.username or u.password)):
        raise LinkError("ссылка hysteria2:// неполная")
    port = _uport(u, 443)
    pw = urllib.parse.unquote(u.username or "") + (":" + urllib.parse.unquote(u.password) if u.password else "")
    o = {"type": "hysteria2", "server": u.hostname, "server_port": port, "password": pw,
         "tls": {"enabled": True, "server_name": q.get("sni") or u.hostname}}
    if _truthy(q.get("insecure")):
        o["tls"]["insecure"] = True
    if q.get("obfs"):
        o["obfs"] = {"type": q["obfs"], "password": q.get("obfs-password", "")}
    return _name(u.fragment, u.hostname, port), o


def parse_link(link: str) -> tuple[str, dict]:
    """→ (название сервера, outbound для sing-box без tag). Бросает LinkError."""
    link = link.strip()
    scheme = link.split("://", 1)[0].lower()
    fn = {"vless": _parse_vless, "vmess": _parse_vmess, "trojan": _parse_trojan, "ss": _parse_ss, "hysteria2": _parse_hy2, "hy2": _parse_hy2}.get(scheme)
    if not fn:
        raise LinkError("такой тип ссылки не поддерживается")
    return fn(link)


def kind_of(link: str) -> str:
    return KINDS.get(link.split("://", 1)[0].lower(), "?")


def server_id(link: str) -> str:
    return hashlib.sha1(link.strip().encode("utf-8")).hexdigest()[:8]


def parse_text(text: str) -> tuple[list[dict], list[str]]:
    """Текст со ссылками (по одной в строке или как попало) → (серверы, ошибки). Подписку (base64 со ссылками) тоже понимает."""
    text = text or ""
    if not LINK_RE.search(text):
        try:
            text = _b64d(re.sub(r"\s+", "", text))
        except Exception:
            pass
    out, errs, seen = [], [], set()
    for m in LINK_RE.finditer(text):
        link = m.group(0).rstrip(",;")
        try:
            name, _ = parse_link(link)
        except LinkError as e:
            errs.append(f"{link[:24]}…: {e}")
            continue
        sid = server_id(link)
        if sid not in seen:
            seen.add(sid)
            out.append({"id": sid, "name": name[:80], "link": link, "kind": kind_of(link)})
    return out, errs


# Панели подписок (Marzban, Remnawave, 3x-ui…) отдают разное в зависимости от «User-Agent»: незнакомому клиенту — страницу сайта, ошибку или заглушку.
# Поэтому пробуем несколько привычных клиентов; нужный список ссылок нужен именно в виде vless:// … (или base64 из них).
SUB_AGENTS = ("v2rayN/6.45", "Hiddify/2.0.5", "Happ/2.0.0", "Streisand", "Bogestv0-KARINA")


def _fetch_curl(url: str, agent: str) -> tuple[int, str]:
    """Запасной путь: curl.exe из Windows проверяет сертификат средствами системы (как браузер) и ходит через системный прокси/VPN."""
    import shutil
    exe = shutil.which("curl.exe" if IS_WIN else "curl")
    if not exe:
        raise OSError("curl не найден")
    r = subprocess.run([exe, "-sSL", "--max-time", "25", "-A", agent, "-w", "\n%{http_code}", url], capture_output=True, creationflags=NO_WINDOW, timeout=35)
    if r.returncode:
        raise OSError((r.stderr.decode("utf-8", "replace").strip().splitlines() or ["ошибка curl"])[-1])
    out = r.stdout.decode("utf-8", "replace")
    body, _, code = out.rpartition("\n")
    return int(code or 0), body[:2_000_000]


def _fetch(url: str, agent: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError) as e:
        try:
            return _fetch_curl(url, agent)      # встроенная проверка сертификата не справилась (бывает у собственных серверов) — пробуем средствами системы
        except Exception:
            raise e from None


def fetch_subscription(url: str) -> str:
    """Скачивает подписку и возвращает текст, в котором есть ссылки на серверы. Перебирает «User-Agent» разных клиентов; если ни один не подошёл — понятная причина."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise TunnelError("Адрес подписки должен начинаться с http:// или https://")
    last = ""
    net_err = None
    for agent in SUB_AGENTS:
        try:
            code, body = _fetch(url, agent)
        except (urllib.error.URLError, OSError) as e:
            net_err = e
            continue
        if code >= 400:
            last = f"сервер ответил ошибкой {code}"
            continue
        found, _ = parse_text(body)
        if found:
            return body
        head = re.sub(r"\s+", " ", body).strip()[:60]
        if body.lstrip().startswith("{") or body.lstrip().startswith("["):
            last = "сервер отдал настройки в формате JSON, а не список ссылок (vless://…): включите в панели подписки обычный (base64) формат"
        elif re.search(r"<html|<!doctype", body[:300], re.I):
            last = "по этому адресу открывается веб-страница, а не список серверов: проверьте, что ссылка скопирована целиком"
        elif not body.strip():
            last = "сервер вернул пустой ответ (возможно, срок подписки истёк или в ней нет серверов)"
        else:
            last = f"в ответе нет ссылок на серверы (начало ответа: «{head}»)"
    if not last and net_err is not None:
        raise TunnelError(f"Не удалось скачать подписку: {getattr(net_err, 'reason', net_err)}")
    raise TunnelError(f"Подписка не подошла: {last or 'нет ответа'}.")


# ====================================================================== настройки sing-box
def _proc_regex(exe: str) -> str:
    return r"(?i)(^|[\\/])" + re.escape(exe) + "$"


def clean_apps(apps) -> list[str]:
    out = []
    for a in apps or []:
        a = str(a).strip()
        if not a:
            continue
        if not a.lower().endswith(".exe"):
            a += ".exe"
        if re.fullmatch(r"[\w .+()\-]{1,64}\.exe", a, re.I | re.U) and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out


def build_config(outbound: dict, mode: str, scope: str, apps: list[str], port: int = 10808, direct_ru: bool = False) -> dict:
    """Настройки sing-box. mode: proxy | vpn; scope: all | apps (apps ⇒ только vpn)."""
    apps = clean_apps(apps)
    only_apps = scope == "apps"
    if only_apps and not apps:
        raise TunnelError("Не выбраны приложения: добавьте хотя бы одно в настройках VPN.")
    if only_apps:
        mode = "vpn"
    proxy_out = dict(outbound, tag="proxy")
    rules: list[dict] = [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}, {"ip_is_private": True, "outbound": "direct"}]
    dns_rules: list[dict] = [{"outbound": "any", "server": "dns-local"}]
    if direct_ru:
        ru = ["ru", "su", "xn--p1ai"]
        rules.append({"domain_suffix": ru, "outbound": "direct"})
        dns_rules.append({"domain_suffix": ru, "server": "dns-local"})
    if only_apps:
        rx = [_proc_regex(a) for a in apps]
        rules.append({"process_path_regex": rx, "outbound": "proxy"})
        dns_rules.append({"process_path_regex": rx, "server": "dns-remote"})
        final, dns_final = "direct", "dns-local"
    else:
        final, dns_final = "proxy", "dns-remote"
    if mode == "proxy":
        inbound = {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": int(port)}
    else:
        inbound = {"type": "tun", "tag": "tun-in", "interface_name": "KarinaTun", "address": ["172.19.0.1/30"], "auto_route": True, "strict_route": False, "stack": "mixed"}
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {"servers": [{"tag": "dns-remote", "address": "https://1.1.1.1/dns-query", "detour": "proxy"},
                            {"tag": "dns-local", "address": "local", "detour": "direct"}],
                "rules": dns_rules, "final": dns_final, "strategy": "ipv4_only"},
        "inbounds": [inbound],
        "outbounds": [proxy_out, {"type": "direct", "tag": "direct"}],
        "route": {"rules": rules, "final": final, "auto_detect_interface": True, "find_process": True},
    }


# ====================================================================== системный прокси Windows
_INET = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_SYS_VALUES = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")


def _notify_inet():
    try:
        import ctypes
        w = ctypes.windll.Wininet
        w.InternetSetOptionW(0, 39, 0, 0)      # INTERNET_OPTION_SETTINGS_CHANGED
        w.InternetSetOptionW(0, 37, 0, 0)      # INTERNET_OPTION_REFRESH
    except Exception:
        pass


def set_system_proxy(addr: str, backup: Path) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if not backup.exists():                # прежние настройки запоминаем один раз: после сбоя не затираем оригинал своими
            old = {}
            for n in _SYS_VALUES:
                try:
                    v, t = winreg.QueryValueEx(k, n)
                    old[n] = [v, t]
                except OSError:
                    old[n] = None
            backup.write_text(json.dumps(old), "utf-8")
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, addr)
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.2*;172.30.*;172.31.*;192.168.*;<local>")
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        try:
            winreg.DeleteValue(k, "AutoConfigURL")     # сценарий автонастройки перебил бы наш адрес
        except OSError:
            pass
    _notify_inet()


def restore_system_proxy(backup: Path) -> bool:
    """Возвращает прежние настройки прокси Windows. True — что-то восстановили."""
    if not backup.exists():
        return False
    import winreg
    try:
        old = json.loads(backup.read_text("utf-8"))
    except (OSError, ValueError):
        old = {"ProxyEnable": [0, winreg.REG_DWORD]}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET, 0, winreg.KEY_SET_VALUE) as k:
        for n in _SYS_VALUES:
            v = old.get(n)
            if v is None:
                try:
                    winreg.DeleteValue(k, n)
                except OSError:
                    pass
            else:
                winreg.SetValueEx(k, n, 0, v[1], v[0])
    backup.unlink(missing_ok=True)
    _notify_inet()
    return True


def _free_port(want: int) -> int:
    for p in [want, *range(want + 1, want + 40)]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ====================================================================== движок и управление
class Tunnel:
    def __init__(self, cfg, base: Path, log=None):
        self.cfg, self._log = cfg, log or (lambda *_: None)
        self.dir = Path(base) / "tunnel"
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._info: dict = {}

    # ---------- настройки ----------
    def settings(self) -> dict:
        d = json.loads(json.dumps(DEFAULTS))
        d.update({k: v for k, v in (self.cfg.get("tunnel") or {}).items() if k in DEFAULTS})
        return d

    def save(self, **patch) -> None:
        self.cfg.update({"tunnel": patch})

    def configured(self) -> bool:
        return bool(self.settings()["servers"])

    def servers(self) -> list[dict]:
        return self.settings()["servers"]

    def active_server(self, sid: str = "") -> dict | None:
        s = self.settings()
        sid = sid or s["active"]
        for x in s["servers"]:
            if x["id"] == sid:
                return x
        return s["servers"][0] if s["servers"] else None

    def add_text(self, text: str) -> dict:
        """Вставка из окна: ссылки или адрес подписки. → {"added": n, "errors": [...]}"""
        text = (text or "").strip()
        if not text:
            raise TunnelError("Вставьте ссылку на сервер или адрес подписки.")
        sub = ""
        if re.match(r"^https?://\S+$", text, re.I):
            sub, text = text, fetch_subscription(text)
        found, errs = parse_text(text)
        if not found:
            raise TunnelError("Не нашла ни одной подходящей ссылки (vless://, vmess://, trojan://, ss://, hysteria2://)." + (f" Ошибки: {'; '.join(errs[:3])}" if errs else ""))
        s = self.settings()
        have = {x["id"] for x in s["servers"]}
        servers = s["servers"] + [x for x in found if x["id"] not in have]
        patch = {"servers": servers, "active": s["active"] or servers[0]["id"]}
        if sub:
            patch["sub_url"] = sub
        self.save(**patch)
        return {"added": len([x for x in found if x["id"] not in have]), "errors": errs}

    def refresh_subscription(self) -> dict:
        url = self.settings()["sub_url"]
        if not url:
            raise TunnelError("Подписка не задана.")
        found, errs = parse_text(fetch_subscription(url))
        if not found:
            raise TunnelError("В подписке нет подходящих ссылок.")
        s = self.settings()
        ids = {x["id"] for x in found}
        self.save(servers=found, active=s["active"] if s["active"] in ids else found[0]["id"])    # список подписки заменяет прежний: серверы в ней меняются
        return {"added": len(found), "errors": errs}

    def remove(self, sid: str) -> None:
        s = self.settings()
        servers = [x for x in s["servers"] if x["id"] != sid]
        self.save(servers=servers, active=(s["active"] if s["active"] != sid else (servers[0]["id"] if servers else "")))
        if not servers:
            self.save(sub_url="")

    def clear(self) -> None:
        self.save(servers=[], active="", sub_url="")

    # ---------- движок ----------
    @property
    def exe(self) -> Path:
        return self.dir / "sing-box.exe"

    def engine_ready(self) -> bool:
        try:
            return self.exe.stat().st_size > 1_000_000
        except OSError:
            return False

    def download_engine(self, progress=None, cancel: threading.Event | None = None) -> Path:
        if not IS_WIN:
            raise TunnelError("Движок VPN нужен только в Windows.")
        import platform
        if platform.machine().lower() not in ("amd64", "x86_64"):
            raise TunnelError("Встроенный VPN пока работает только на 64-разрядных Windows с процессором Intel/AMD.")
        prog = progress or (lambda *a: None)
        cancel = cancel or threading.Event()
        self.dir.mkdir(parents=True, exist_ok=True)
        part = self.dir / (SINGBOX_ZIP + ".part")
        prog(0, 0, "search")
        try:
            r = urllib.request.urlopen(urllib.request.Request(SINGBOX_URL, headers=UA), timeout=60)
        except urllib.error.HTTPError as e:
            raise TunnelError(f"GitHub ответил ошибкой {e.code}. Попробуйте позже.") from None
        except urllib.error.URLError as e:
            import ssl
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                raise TunnelError("Не удалось проверить сертификат GitHub. Проверьте дату и время на компьютере и обновите Windows.") from None
            raise TunnelError(f"Нет связи с GitHub: {getattr(e, 'reason', e)}") from None
        h = hashlib.sha256()
        with r, open(part, "wb") as f:
            total, done, last = int(r.headers.get("Content-Length", 0)), 0, 0.0
            while True:
                if cancel.is_set():
                    part.unlink(missing_ok=True)
                    raise Cancelled()
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                h.update(b)
                done += len(b)
                if time.time() - last > 0.4:
                    prog(done, total, "download")
                    last = time.time()
        if h.hexdigest() != SINGBOX_SHA256:
            part.unlink(missing_ok=True)
            raise TunnelError("Контрольная сумма скачанного движка не совпала: файл не принят. Попробуйте ещё раз позже.")
        want = f"sing-box-{SINGBOX_VERSION}-windows-amd64/sing-box.exe"
        with zipfile.ZipFile(part) as z:
            if want not in z.namelist():
                part.unlink(missing_ok=True)
                raise TunnelError("В архиве нет sing-box.exe.")
            tmp = self.dir / "sing-box.exe.new"
            with z.open(want) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        part.unlink(missing_ok=True)
        self.stop()
        tmp.replace(self.exe)
        (self.dir / "VERSION.txt").write_text(SINGBOX_VERSION + "\n", "utf-8")
        prog(total, total, "done")
        return self.exe

    def delete_engine(self) -> None:
        self.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------- запуск и остановка ----------
    @property
    def _backup(self) -> Path:
        return self.dir / "sysproxy_backup.json"

    @property
    def _pidfile(self) -> Path:
        return self.dir / "sing-box.pid"

    def running(self) -> bool:
        p = self._proc
        return bool(p and p.poll() is None)

    def status(self) -> dict:
        run = self.running()
        if not run:
            self._info = {}
        return {"running": run, **self._info}

    def describe(self) -> str:
        i = self.status()
        if not i["running"]:
            return "Выключен."
        what = ("для приложений: " + ", ".join(i.get("apps", []))) if i.get("scope") == "apps" else "для всего компьютера"
        how = "прокси" if i.get("mode") == "proxy" else "VPN"
        return f"Включён {how}, сервер «{i.get('server', '?')}», {what}."

    def start(self, mode: str | None = None, scope: str | None = None, apps: list[str] | None = None, sid: str = "") -> str:
        """Запускает. Параметры не заданы — берёт из настроек. Возвращает фразу об итоге; ошибки — TunnelError."""
        with self._lock:
            s = self.settings()
            mode = mode if mode in MODES else s["mode"]
            scope = scope if scope in SCOPES else s["scope"]
            apps = clean_apps(apps if apps is not None else s["apps"])
            srv = self.active_server(sid)
            if not srv:
                raise TunnelError("Сначала добавьте сервер: в настройках, в разделе «VPN и прокси», вставьте ссылку или адрес подписки.")
            if scope == "apps":
                mode = "vpn"
            if not IS_WIN:
                raise TunnelError("Встроенный VPN работает только в Windows.")
            if not self.engine_ready():
                raise TunnelError("Движок VPN ещё не скачан. Откройте настройки, раздел «VPN и прокси», и нажмите «Скачать движок».")
            if mode == "vpn":
                from . import elevate
                if not elevate.is_admin():
                    raise TunnelError("Для режима VPN нужны права администратора. Включите в настройках «Запускать от имени администратора» и перезапустите Карину. "
                                      "Режим «прокси» работает и без них.")
            try:
                _, outbound = parse_link(srv["link"])
            except LinkError as e:
                raise TunnelError(f"Сервер «{srv['name']}» не получилось прочитать: {e}.") from None
            self.stop()
            port = _free_port(int(s["port"] or 10808))
            conf = build_config(outbound, mode, scope, apps, port, bool(s["direct_ru"]))
            self.dir.mkdir(parents=True, exist_ok=True)
            cfile, logf = self.dir / "config.json", self.dir / "sing-box.log"
            cfile.write_text(json.dumps(conf, ensure_ascii=False, indent=1), "utf-8")
            chk = subprocess.run([str(self.exe), "check", "-c", str(cfile)], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
            if chk.returncode != 0:
                raise TunnelError("Движок не принял настройки: " + _last_line(chk.stderr or chk.stdout))
            out = open(logf, "wb")
            try:
                self._proc = subprocess.Popen([str(self.exe), "run", "-c", str(cfile), "-D", str(self.dir)], stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            finally:
                out.close()
            self._pidfile.write_text(str(self._proc.pid), "utf-8")
            self._wait_started(mode, port, logf)
            if mode == "proxy":
                try:
                    set_system_proxy(f"127.0.0.1:{port}", self._backup)
                except OSError as e:
                    self.stop()
                    raise TunnelError(f"Не удалось прописать системный прокси: {e}") from None
            self._info = {"mode": mode, "scope": scope, "apps": apps if scope == "apps" else [], "server": srv["name"], "port": port, "since": time.time()}
            self._log(f"Туннель: {self.describe()}")
            return self.describe()

    def _wait_started(self, mode: str, port: int, logf: Path):
        end = time.time() + (8 if mode == "proxy" else 4)
        while time.time() < end:
            if self._proc.poll() is not None:
                break
            if mode == "proxy":
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                    return
                except OSError:
                    pass
            time.sleep(0.25)
        if self._proc.poll() is not None:
            self._proc = None
            self._pidfile.unlink(missing_ok=True)
            try:
                tail = _last_line(logf.read_text("utf-8", "replace"))
            except OSError:
                tail = ""
            raise TunnelError("Движок сразу остановился" + (f": {tail}" if tail else "."))
        if mode == "proxy":
            self.stop()
            raise TunnelError("Движок запустился, но не открыл порт прокси.")

    def stop(self) -> bool:
        """Выключает всё и возвращает прежние настройки прокси Windows. True — что-то было включено."""
        with self._lock:
            was = self.running()
            p, self._proc = self._proc, None
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(6)
                except subprocess.TimeoutExpired:
                    p.kill()
            if IS_WIN:
                try:
                    restore_system_proxy(self._backup)
                except Exception as e:
                    self._log(f"Туннель: не удалось вернуть системный прокси: {e}")
            self._pidfile.unlink(missing_ok=True)
            self._info = {}
            if was:
                self._log("Туннель выключен")
            return was

    def recover(self) -> None:
        """При старте Карины: после аварийного выхода мог остаться чужой sing-box и наш адрес в системном прокси."""
        if not IS_WIN:
            return
        try:
            if self._pidfile.exists():
                pid = self._pidfile.read_text("utf-8").strip()
                if pid.isdigit():
                    t = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
                    if "sing-box" in t.lower():
                        subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True, creationflags=NO_WINDOW)
                self._pidfile.unlink(missing_ok=True)
            if restore_system_proxy(self._backup):
                self._log("Туннель: после прошлого запуска вернула настройки прокси Windows")
        except Exception as e:
            self._log(f"Туннель: очистка после сбоя не удалась: {e}")


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return re.sub(r"\x1b\[[0-9;]*m", "", lines[-1])[:300] if lines else "неизвестная ошибка"
