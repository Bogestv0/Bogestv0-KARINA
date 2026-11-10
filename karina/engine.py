"""Встроенный движок моделей: llama.cpp (llama-server) на этом же компьютере, без LM Studio и без интернета.

Две сборки движка: Vulkan (ускорение на видеокартах AMD / NVIDIA / Intel) и CPU (запасная, работает везде).
Сервер слушает только 127.0.0.1 и требует случайный ключ, чтобы им не мог воспользоваться посторонний процесс или сайт.
"""
from __future__ import annotations
import json, os, re, secrets, shutil, socket, subprocess, sys, threading, time, urllib.error, urllib.request, zipfile
from pathlib import Path
from . import models
from .config import DATA, ROOT

IS_WIN = sys.platform == "win32"
EXE = "llama-server.exe" if IS_WIN else "llama-server"
BUNDLED = ROOT / "engine"            # кладётся в сборку на GitHub
LOCAL = DATA / "engine"              # скачивается при запуске из исходников
API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"
# Для каждой сборки — шаблоны имён файлов по убыванию предпочтения (имена в релизах llama.cpp со временем меняются).
FLAVORS = {"vulkan": (r"bin-win-vulkan-x64\.zip$",),
           "cpu": (r"bin-win-cpu-x64\.zip$", r"bin-win-avx2-x64\.zip$")}
_NOOP = {}


class EngineError(Exception):
    pass


def find_server(flavor: str) -> Path | None:
    for base in (BUNDLED, LOCAL):
        d = base / flavor
        if (d / EXE).exists():
            return d / EXE
        hits = list(d.rglob(EXE)) if d.exists() else []
        if hits:
            return hits[0]
    return None


def _asset(rel: dict, flavor: str):
    for pat in FLAVORS[flavor]:
        for a in rel.get("assets", []):
            if re.search(pat, a["name"]) and "cudart" not in a["name"]:
                return a
    return None


def find_release(flavors=("cpu", "vulkan")) -> dict:
    """Самый свежий релиз llama.cpp, в котором есть сборки Windows для всех нужных вариантов (обе из одного релиза).
    «latest» не годится: иногда последним числится релиз без Windows-файлов."""
    h = {"User-Agent": "Bogestv0-KARINA", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]          # в GitHub Actions — без лимита запросов
    seen = []
    for page in (1, 2, 3):
        rels = json.loads(urllib.request.urlopen(urllib.request.Request(f"{API}&page={page}", headers=h), timeout=30).read().decode("utf-8"))
        for rel in rels:
            # Пре-релизы НЕ пропускаем: llama.cpp помечает свои автоматические сборки именно так.
            if rel.get("draft"):
                continue
            if all(_asset(rel, f) for f in flavors):
                return rel
            names = [a["name"] for a in rel.get("assets", [])]
            seen.append(f"{rel.get('tag_name')} (файлы: {', '.join(names[:6]) or 'нет'})")
        if len(rels) < 30:
            break
    raise EngineError(f"Не нашла релиз llama.cpp со сборками Windows {'/'.join(flavors)}. Проверены: {', '.join(seen[:8]) or 'список релизов пуст'}.")


def fetch_engine(flavor: str, dest: Path, progress=None, rel: dict | None = None) -> Path:
    """Скачивает сборку движка с GitHub (ggml-org/llama.cpp) и распаковывает в dest/flavor."""
    rel = rel or find_release((flavor,))
    asset = _asset(rel, flavor)
    if not asset:
        raise EngineError(f"В релизе {rel.get('tag_name')} нет сборки «{flavor}» для Windows.")
    out = dest / flavor
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    zpath = dest / asset["name"]
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "Bogestv0-KARINA"}), timeout=60) as r, open(zpath, "wb") as f:
        total, done, last = int(r.headers.get("Content-Length", 0)), 0, 0.0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress and time.time() - last > 0.4:
                progress(done, total, "engine")
                last = time.time()
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    zpath.unlink(missing_ok=True)
    (out / "VERSION.txt").write_text(f"{rel.get('tag_name')}\n{asset['name']}\n", "utf-8")
    exe = find_server(flavor)
    if not exe:
        raise EngineError("Движок скачан, но в нём нет llama-server.")
    return exe


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # локальные запросы мимо прокси


class Engine:
    """Запускает llama-server под выбранную модель и общается с ним по OpenAI-совместимому API."""

    def __init__(self, cfg, emit=None):
        self.cfg, self.emit = cfg, emit or (lambda ev: None)
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.key = ""
        self.model_id = ""
        self.flavor = ""
        self.state = "stopped"       # stopped | starting | ready | error
        self.detail = ""
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._log: list[str] = []
        self.launcher = None         # для тестов: функция (args) -> Popen

    # ---------- состояние ----------
    def _set(self, state: str, detail: str = ""):
        self.state, self.detail = state, detail
        self.emit({"type": "engine", "state": state, "detail": detail, "model": self.model_id, "flavor": self.flavor})

    def info(self) -> dict:
        return {"state": self.state, "detail": self.detail, "model": self.model_id, "flavor": self.flavor,
                "has_vulkan": bool(find_server("vulkan")), "has_cpu": bool(find_server("cpu"))}

    # ---------- запуск / остановка ----------
    def _pidfile(self) -> Path:
        return DATA / "engine.pid"

    def _kill_stale(self):
        """Если Карина в прошлый раз закрылась аварийно, прибираем оставшийся сервер."""
        try:
            pid = int(self._pidfile().read_text().strip())
        except (OSError, ValueError):
            return
        try:
            if IS_WIN:
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                     creationflags=0x08000000).stdout.lower()
                if "llama-server" in out:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=0x08000000)
        except Exception:
            pass
        self._pidfile().unlink(missing_ok=True)

    def _args(self, model_path: Path, m: dict, flavor: str) -> list[str]:
        mc = self.cfg.get("models", {})
        ctx = int(mc.get("ctx") or m.get("ctx") or 8192)
        gpu = flavor == "vulkan"
        a = ["-m", str(model_path), "--host", "127.0.0.1", "--port", str(self.port), "--api-key", self.key,
             "-c", str(ctx), "-ngl", "99" if gpu else "0", "--no-webui", "-np", "1", "--jinja"]
        if m.get("cpu_moe"):
            a.append("--cpu-moe")
        threads = int(mc.get("threads") or 0)
        if threads > 0:
            a += ["-t", str(threads)]
        return a

    def start(self, model_id: str, wait: bool = True) -> None:
        """Запускает движок. Если Vulkan не стартовал — один раз пробует CPU-сборку."""
        with self._lock:
            if self.state == "ready" and self.model_id == model_id and self._alive():
                return
            self.stop()
            m = models.by_id(model_id)
            path = models.local_path(model_id) if m else None
            if not m or not path:
                self._set("error", "Эта модель не скачана. Откройте «Модели» и нажмите «Скачать».")
                raise EngineError(self.detail)
            self.model_id = model_id
            self._cancel.clear()
            mode = (self.cfg.get("models", {}) or {}).get("gpu", "auto")
            order = ["cpu"] if mode == "cpu" else ["vulkan", "cpu"]
            last_err = ""
            for flavor in order:
                self.flavor = flavor
                try:
                    self._launch(path, m, flavor)
                    return
                except EngineError as e:
                    last_err = str(e)
                    self._kill_proc()
                    if self._cancel.is_set():
                        break
            self._set("error", last_err or "Не удалось запустить модель.")
            raise EngineError(self.detail)

    def _binary(self, flavor: str) -> Path:
        exe = find_server(flavor)
        if exe:
            return exe
        self._set("starting", "Скачиваю движок (один раз)…")
        try:
            return fetch_engine(flavor, LOCAL)
        except Exception as e:
            raise EngineError(f"Не удалось получить движок ({flavor}): {e}")

    def _launch(self, path: Path, m: dict, flavor: str):
        self._kill_stale()
        self.port, self.key = _free_port(), secrets.token_urlsafe(24)
        self._log = []
        self._set("starting", "Загружаю модель в память…" + (" (видеокарта)" if flavor == "vulkan" else " (процессор)"))
        if self.launcher:
            self.proc = self.launcher(self._args(path, m, flavor))
        else:
            exe = self._binary(flavor)
            DATA.mkdir(parents=True, exist_ok=True)
            self.proc = subprocess.Popen([str(exe)] + self._args(path, m, flavor), cwd=str(exe.parent), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                         creationflags=0x08000000 if IS_WIN else 0)
        try:
            self._pidfile().write_text(str(self.proc.pid))
        except OSError:
            pass
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        deadline = time.time() + 600       # большая модель с диска грузится долго
        while time.time() < deadline:
            if self._cancel.is_set():
                raise EngineError("Загрузка отменена.")
            if self.proc.poll() is not None:
                tail = " ".join(self._log[-6:])[-400:]
                raise EngineError(f"Движок ({flavor}) завершился при запуске. {tail}".strip())
            try:
                if self._get("/health").get("status") == "ok":
                    self._set("ready", "Готово")
                    return
            except EngineError:
                pass
            time.sleep(0.5)
        raise EngineError("Модель слишком долго загружается.")

    def _pump(self, proc):
        try:
            for line in proc.stdout:
                self._log.append(line.strip())
                del self._log[:-200]
        except Exception:
            pass

    def _alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _kill_proc(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        try:
            if p and p.stdout:
                p.stdout.close()
        except Exception:
            pass
        self._pidfile().unlink(missing_ok=True)

    def stop(self):
        self._cancel.set()
        self._kill_proc()
        if self.state != "stopped":
            self._set("stopped")

    # ---------- запросы ----------
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(self._url(path), headers={"Authorization": f"Bearer {self.key}"})
        try:
            return json.loads(_OPENER.open(req, timeout=3).read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 503:
                return {"status": "loading"}
            raise EngineError(f"HTTP {e.code}")
        except (OSError, ValueError) as e:
            raise EngineError(str(e))

    def ensure(self) -> None:
        """Гарантирует, что выбранная в настройках модель запущена."""
        mid = (self.cfg.get("models", {}) or {}).get("active", "")
        if not mid:
            raise EngineError("Модель не выбрана. Откройте «Модели», скачайте и выберите одну.")
        if not (self.state == "ready" and self.model_id == mid and self._alive()):
            self.start(mid)

    def chat(self, messages: list[dict], max_tokens: int = 1024, temperature: float = 0.6, on_delta=None,
             stop: threading.Event | None = None, json_mode: bool = False) -> str:
        """Ответ модели. on_delta(текст) вызывается по мере генерации. Блоки <think> отбрасываются."""
        self.ensure()
        body = {"model": self.model_id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "stream": True, "chat_template_kwargs": {"enable_thinking": False}}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self._url("/v1/chat/completions"), data=json.dumps(body).encode("utf-8"), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        out: list[str] = []
        try:
            with _OPENER.open(req, timeout=600) as r:
                for raw in r:
                    if stop is not None and stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        d = json.loads(data)["choices"][0].get("delta", {})
                    except (ValueError, KeyError, IndexError):
                        continue
                    piece = d.get("content") or ""
                    if piece:
                        out.append(piece)
                        if on_delta:
                            on_delta(strip_think("".join(out)))
        except urllib.error.HTTPError as e:
            raise EngineError(f"Модель вернула ошибку {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except OSError as e:
            if not self._alive():
                self._set("error", "Движок остановился. Попробуйте ещё раз.")
            raise EngineError(f"Нет связи с моделью: {e}")
        return strip_think("".join(out)).strip()


def strip_think(text: str) -> str:
    """Убирает рассуждения <think>…</think> (в том числе ещё не закрытые)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return re.sub(r"<think>.*$", "", text, flags=re.S)
