"""Поиск в интернете и чтение страниц. Только стандартная библиотека.

Поисковики по очереди: DuckDuckGo (HTML) → Mojeek → Википедия (официальный API, без капчи).
Страницы читаются с защитой: только http/https, только публичные адреса (локальная сеть и сам компьютер закрыты,
в том числе при переадресациях), ограничение размера и времени. Текст страниц — данные, команд в нём нет.
"""
from __future__ import annotations
import html, http.client, ipaddress, json, re, socket, ssl, threading, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 Karina/1.3"
MAX_PAGE_BYTES = 1_500_000
PAGE_TIMEOUT = 12
MAX_REDIRECTS = 5


class SearchError(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- безопасность адресов
def check_url(url: str, allow_private: bool = False) -> str:
    """Возвращает url, если его можно открывать; иначе SearchError."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        raise SearchError("Некорректный адрес")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise SearchError("Разрешены только http/https")
    if p.username or p.password:
        raise SearchError("Адреса с логином не открываю")
    if allow_private:
        return url
    host = p.hostname
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise SearchError("Адрес не найден")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise SearchError("Локальные и служебные адреса не открываю")
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, allow_private: bool):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(urllib.parse.urljoin(req.full_url, newurl), self.allow_private)     # каждая переадресация проверяется заново
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, max_bytes: int = MAX_PAGE_BYTES, timeout: float = PAGE_TIMEOUT, stop: threading.Event | None = None,
             allow_private: bool = False, data: bytes | None = None, headers: dict | None = None) -> tuple[str, str, bytes]:
    """→ (итоговый адрес, content-type, тело). Бросает SearchError."""
    check_url(url, allow_private)
    opener = urllib.request.build_opener(_Redirect(allow_private), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
         "Accept-Language": "ru,en;q=0.7", "Accept-Encoding": "identity"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with opener.open(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            chunks, size = [], 0
            while True:
                if stop is not None and stop.is_set():
                    raise Cancelled()
                b = r.read(32768)
                if not b:
                    break
                chunks.append(b)
                size += len(b)
                if size >= max_bytes:
                    break
            return r.geturl(), ctype, b"".join(chunks)[:max_bytes]
    except urllib.error.HTTPError as e:
        raise SearchError(f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        raise SearchError(str(getattr(e, "reason", e)) or e.__class__.__name__)


def decode(body: bytes, ctype: str = "") -> str:
    enc = ""
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore") if isinstance(m.group(1), bytes) else m.group(1)
    for e in (enc, "utf-8", "cp1251"):
        if not e:
            continue
        try:
            return body.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")


# ---------------------------------------------------------------- извлечение текста
_SKIP = {"script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header", "aside", "template", "button", "select", "head"}
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "main", "h1", "h2", "h3", "h4", "h5", "h6",
          "blockquote", "pre", "dd", "dt", "figcaption", "hr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self._in_title = "", False
        self.skip = 0
        self.main_depth = 0
        self.all: list[str] = []
        self.main: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _SKIP and tag != "head":
            self.skip += 1
        if tag in ("article", "main"):
            self.main_depth += 1
        if tag in _BLOCK:
            self._put("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and tag != "head" and self.skip:
            self.skip -= 1
        if tag in ("article", "main") and self.main_depth:
            self.main_depth -= 1
        if tag in _BLOCK:
            self._put("\n")

    def _put(self, s):
        self.all.append(s)
        if self.main_depth:
            self.main.append(s)

    def handle_data(self, d):
        if self._in_title:
            self.title += d
        if self.skip:
            return
        self._put(d)


def _clean(parts: list[str]) -> str:
    t = "".join(parts)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in t.split("\n")]
    out, seen = [], set()
    for ln in lines:
        if not ln or ln in seen and len(ln) < 80:
            continue
        seen.add(ln)
        out.append(ln)
    return "\n".join(out)


def extract_text(page_html: str) -> tuple[str, str]:
    """→ (заголовок, основной текст). Если есть <article>/<main> с достаточным текстом, берём его."""
    p = _Text()
    try:
        p.feed(page_html)
        p.close()
    except Exception:
        pass
    full = _clean(p.all)
    main = _clean(p.main)
    text = main if len(main) >= 600 and len(main) >= len(full) * 0.3 else full
    return html.unescape(re.sub(r"\s+", " ", p.title)).strip(), text


_WORD = re.compile(r"[\w-]{3,}", re.U)


def _stems(s: str) -> set[str]:
    return {w.lower().replace("ё", "е")[:5] for w in _WORD.findall(s)}


def best_excerpt(text: str, query: str, limit: int) -> str:
    """Самые подходящие под запрос абзацы (в исходном порядке), не длиннее limit символов."""
    paras = [p for p in text.split("\n") if len(p) >= 40] or [text]
    terms = _stems(query)
    scored = []
    for i, p in enumerate(paras):
        s = len(terms & _stems(p)) * 3 + min(len(p), 400) / 400 - i * 0.002     # чуть любим начало страницы
        scored.append((s, i))
    chosen, used = [], 0
    for s, i in sorted(scored, reverse=True):
        p = paras[i]
        if used + len(p) > limit:
            p = p[: max(0, limit - used)]
            if len(p) < 80:
                continue
        chosen.append(i)
        used += len(p) + 1
        if used >= limit:
            break
    return "\n".join(paras[i][:limit] for i in sorted(chosen))[:limit]


def fetch_page(url: str, stop: threading.Event | None = None, allow_private: bool = False) -> dict:
    """→ {url, title, text}. Не HTML и слишком короткие страницы — SearchError."""
    final, ctype, body = http_get(url, stop=stop, allow_private=allow_private)
    low = ctype.lower()
    if low and not any(k in low for k in ("html", "text/plain", "xml")):
        raise SearchError("не страница (" + low.split(";")[0] + ")")
    title, text = extract_text(decode(body, ctype))
    if len(text) < 200:
        raise SearchError("мало текста")
    return {"url": final, "title": title or urllib.parse.urlsplit(final).netloc, "text": text}


# ---------------------------------------------------------------- поисковики
def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


class _Results(HTMLParser):
    """Общий разбор выдачи: заголовочные ссылки и сниппеты определяются по CSS-классам."""

    def __init__(self, title_cls: str, snippet_cls: str, snippet_tags: tuple[str, ...]):
        super().__init__(convert_charrefs=True)
        self.tc, self.sc, self.st = title_cls, snippet_cls, snippet_tags
        self.items: list[dict] = []
        self._mode = None          # "t" | "s"
        self._buf: list[str] = []
        self._tag = ""
        self._cur: dict | None = None

    @staticmethod
    def _cls(attrs):
        return (dict(attrs).get("class") or "").split()

    def handle_starttag(self, tag, attrs):
        cls = self._cls(attrs)
        if tag == "a" and self.tc in cls:
            href = dict(attrs).get("href") or ""
            self._cur = {"href": href, "title": "", "snippet": ""}
            self.items.append(self._cur)
            self._mode, self._buf, self._tag = "t", [], "a"
        elif tag in self.st and self.sc in cls and self._cur is not None and not self._cur["snippet"]:
            self._mode, self._buf, self._tag = "s", [], tag

    def handle_data(self, d):
        if self._mode:
            self._buf.append(d)

    def handle_endtag(self, tag):
        if self._mode and tag == self._tag and self._cur is not None:
            txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._cur["title" if self._mode == "t" else "snippet"] = txt
            self._mode = None


def _ddg_url(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.urlsplit(href)
    if q.netloc.endswith("duckduckgo.com") and q.path.startswith("/l/"):
        u = urllib.parse.parse_qs(q.query).get("uddg")
        return u[0] if u else ""
    if q.netloc.endswith("duckduckgo.com"):          # реклама и служебные ссылки
        return ""
    return href


def parse_ddg(page: str) -> list[dict]:
    if "anomaly" in page and "result__a" not in page:
        raise SearchError("DuckDuckGo просит подтвердить, что вы не робот")
    p = _Results("result__a", "result__snippet", ("a", "div", "td"))
    p.feed(page)
    out = []
    for it in p.items:
        u = _ddg_url(it["href"])
        if u.startswith("http") and it["title"]:
            out.append({"url": u, "title": it["title"], "snippet": it["snippet"]})
    return out


def parse_mojeek(page: str) -> list[dict]:
    p = _Results("title", "s", ("p",))
    p.feed(page)
    return [{"url": it["href"], "title": it["title"], "snippet": it["snippet"]} for it in p.items
            if it["href"].startswith("http") and it["title"]]


def parse_wikipedia(data: str, lang: str) -> list[dict]:
    try:
        rows = json.loads(data)["query"]["search"]
    except (ValueError, KeyError, TypeError):
        raise SearchError("Википедия вернула непонятный ответ")
    return [{"url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
             "title": r["title"] + " — Википедия", "snippet": _strip_tags(r.get("snippet", ""))} for r in rows if r.get("title")]


def _ddg(q, n, stop):
    _, ct, body = http_get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": q, "kl": "ru-ru"}).encode(),
                           headers={"Content-Type": "application/x-www-form-urlencoded"}, stop=stop, timeout=15)
    return parse_ddg(decode(body, ct))


def _mojeek(q, n, stop):
    _, ct, body = http_get("https://www.mojeek.com/search?" + urllib.parse.urlencode({"q": q, "lang": "ru"}), stop=stop, timeout=15)
    return parse_mojeek(decode(body, ct))


def _wiki(q, n, stop):
    out = []
    for lang in ("ru", "en"):
        u = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": min(n, 8), "utf8": 1})
        try:
            _, ct, body = http_get(u, stop=stop, timeout=15)
            out += parse_wikipedia(decode(body, ct), lang)
        except SearchError:
            if lang == "ru":
                raise
        if len(out) >= 3:
            break
    return out


PROVIDERS = (("DuckDuckGo", _ddg), ("Mojeek", _mojeek), ("Википедия", _wiki))


def search(query: str, n: int = 8, stop: threading.Event | None = None, providers=None) -> tuple[list[dict], str]:
    """→ (результаты [{url,title,snippet}], имя поисковика). Если все поисковики недоступны — SearchError с причинами."""
    errors = []
    for name, fn in providers or PROVIDERS:
        try:
            res = fn(query, n, stop)
        except Cancelled:
            raise
        except SearchError as e:
            errors.append(f"{name}: {e}")
            continue
        seen, uniq = set(), []
        for r in res:
            key = urllib.parse.urlsplit(r["url"]).netloc.lower().removeprefix("www.") + urllib.parse.urlsplit(r["url"]).path.rstrip("/")
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        if uniq:
            return uniq[:n], name
        errors.append(f"{name}: ничего не найдено")
    raise SearchError("Поиск не сработал. " + "; ".join(errors))
