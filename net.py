"""Проверка интернета (с кэшем, чтобы не тормозить)."""
import socket, time

_cache = (0.0, False)


def is_online(max_age: float = 15.0) -> bool:
    global _cache
    now = time.time()
    if now - _cache[0] < max_age:
        return _cache[1]
    ok = False
    for host, port in (("ya.ru", 443), ("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout=1.2).close()
            ok = True
            break
        except OSError:
            continue
    _cache = (now, ok)
    return ok
