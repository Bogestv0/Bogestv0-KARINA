"""Сеть для облачных моделей (Gemini, «свой сервер», Claude): куда отправлять запрос — напрямую или через VPN-сервер из «Настроек → VPN и прокси».

Зачем. Часть облачных сервисов не пускает из некоторых стран (ошибка 403 / нет связи). Включать для этого VPN на весь компьютер не нужно:
перед облачным запросом Карина сама поднимает отдельный маленький прокси sing-box (tunnel.Tunnel.cloud_start) и отправляет через него ТОЛЬКО этот запрос.
Ни сетевой адаптер, ни системный прокси Windows не меняются, права администратора не нужны, остальной трафик компьютера идёт как обычно.

Правила (см. Tunnel.cloud_route):
  • выключено в настройках, нет сервера или движка — запрос идёт напрямую (а в тексте ошибки Карина подсказывает, что добавить);
  • VPN/прокси на весь компьютер уже включён — запрос идёт обычным путём: он и так под VPN, второй прокси не нужен, ничего не ломается;
  • иначе — через облачный прокси; он сам выключается, когда им давно не пользовались.
Свои адреса (localhost, 192.168.*, 10.*, *.local — Ollama, LM Studio) всегда идут напрямую."""
from __future__ import annotations
import re, urllib.parse, urllib.request

_tunnel = None          # Tunnel, подставляется в Assistant; без него (тесты, запуск модуля отдельно) всё идёт как раньше


def bind(tunnel) -> None:
    global _tunnel
    _tunnel = tunnel


def is_local(host: str) -> bool:
    h = (host or "").lower().strip("[]")
    return (h in ("localhost", "::1") or h.endswith((".local", ".lan", ".internal")) or "." not in h and ":" not in h
            or h.startswith(("127.", "10.", "192.168.", "169.254.")) or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", h) is not None)


def opener_for(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlsplit(url).hostname or ""
    if is_local(host):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))     # свой сервер — напрямую, мимо системного прокси
    addr = None
    if _tunnel is not None:
        try:
            addr = _tunnel.cloud_route()
        except Exception:
            addr = None
    if addr:
        p = f"http://{addr}"
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener()


def open(req, timeout: float = 90):             # noqa: A001 — замена urllib.request.urlopen для облачных запросов
    url = req if isinstance(req, str) else req.full_url
    return opener_for(url).open(req, timeout=timeout)


def note() -> str:
    """Подсказка для текста ошибки: почему VPN для облака не включился (пусто, если всё в порядке)."""
    try:
        p = _tunnel.cloud_problem if _tunnel is not None else ""
    except Exception:
        p = ""
    return f" {p}" if p else ""
