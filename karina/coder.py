"""Кодер: пользователь прикладывает файлы и задачу, Карина сама пишет код, запускает проверки, чинит ошибки и отдаёт результат.

Как это устроено (работает с любой моделью — и локальной, и Claude, без «вызова функций»):
  * Для каждой задачи создаётся своя папка проекта «Документы\\Карина\\Проекты\\<имя>». Приложенные файлы копируются туда (оригиналы не трогаются).
  * Модель отвечает текстом с командами-блоками:
        [[ЗАПИСАТЬ: путь]] … [[/ЗАПИСАТЬ]]   — создать или заменить файл проекта
        [[ЧИТАТЬ: путь]] или [[ЧИТАТЬ: путь:10-80]] — прочитать файл проекта (целиком или строки 10–80)
        [[НАЙТИ: текст]]                      — найти текст по всем файлам проекта (номера строк)
        [[ЗАПУСТИТЬ: команда]]                — запустить проверку (python …, pytest …, node …)
        [[ГОТОВО]] итог [[/ГОТОВО]]           — закончить
    Карина выполняет команды сама и возвращает модели результат (вывод программы, ошибки), и так по кругу, пока не получится или не кончатся шаги.
  * Границы: пути только внутри папки проекта (без «..» и абсолютных); запись только известных типов файлов (без .exe/.bat/.ps1 и т. п.);
    запускать можно только python / pytest / node, без оболочки, с таймаутом и ограничением вывода; запуск вообще включается отдельной галочкой.
  * Важно: запуск кода — это НЕ песочница. Программа, которую написала модель, выполняется с правами пользователя. Поэтому запуск выключен по умолчанию,
    а перед запуском файлы проекта просматриваются на явно опасные вещи (сеть, подпроцессы, удаление файлов, реестр) — это страховка, а не гарантия.
"""
from __future__ import annotations
import datetime, os, re, shlex, shutil, subprocess, sys, threading, time, uuid, zipfile
from pathlib import Path
from . import archives, files as filesmod, prompts
from .engine import EngineError
from .textutil import FEMALE

PROJECTS = "Проекты"
MAX_STEPS = 16
MAX_INPUT_FILES = 400
MAX_INPUT_BYTES = 80 * 1024 * 1024
MAX_WRITE_CHARS = 400_000
MAX_PROJECT_FILES = 120
INLINE_BYTES = 24_000          # файл такого размера и меньше отдаём модели целиком в первом сообщении
INLINE_TOTAL = 60_000          # всего в первом сообщении
RUN_TIMEOUT = 60
OUT_LIMIT = 6000
LIMITS = {"local": 4000, "claude": 8000, "cloud": 3000}     # токенов на один шаг

# что можно записывать (код и данные); программы и скрипты запуска Windows — нельзя
WRITE_EXT = (set(filesmod.KINDS) - {"docx", "xlsx", "pdf"} - filesmod.IMAGE_EXT) | {"cfg", "gitignore", "ts", "vue", "in"}
BLOCKED_EXT = set(archives.BLOCKED_EXT)
SKIP_DIRS = set(archives.SKIP_DIRS)
RUNNERS = {"python": "python", "python3": "python", "py": "python", "pytest": "pytest", "node": "node"}

# Явно опасные обращения в коде проекта: запуск в таком случае отклоняется (страховка, не песочница)
DANGEROUS = re.compile(
    r"(?:\bsubprocess\b|\bos\s*\.\s*(?:system|popen|remove|unlink|rmdir|removedirs|rename|startfile|exec\w*|spawn\w*)\b|\bshutil\s*\.\s*(?:rmtree|move|copytree)\b|"
    r"\bsocket\b|\burllib\b|\brequests\b|\bhttpx\b|\baiohttp\b|\bhttp\s*\.\s*client\b|\bftplib\b|\bsmtplib\b|\bctypes\b|\bwinreg\b|\b_winreg\b|"
    r"\bpyautogui\b|\bkeyboard\b|\bpathlib\b[^\n]*\.\s*(?:unlink|rmdir)\b|"
    r"child_process|\brequire\(\s*['\"](?:fs|net|http|https|child_process|os)['\"]\s*\)|\bfrom\s+['\"](?:fs|net|http|https|child_process)['\"]|"
    r"\bfetch\s*\(|\beval\s*\(\s*(?:input|open)|\b__import__\s*\()", re.I)


class CoderError(Exception):
    pass


# ---------------------------------------------------------------- разбор ответа модели
_TAG = r"\[\[\s*{}\s*(?::\s*(?P<arg>[^\]\n]{{0,300}}?))?\s*\]\]"
_WRITE = re.compile(_TAG.format(r"(?:ЗАПИСАТЬ|WRITE)") + r"(?P<body>.*?)(?:\[\[\s*/\s*(?:ЗАПИСАТЬ|WRITE)\s*\]\]|(?=\[\[\s*(?:ЗАПИСАТЬ|WRITE|ЧИТАТЬ|READ|НАЙТИ|FIND|ЗАПУСТИТЬ|RUN|ГОТОВО|DONE)\b)|\Z)", re.S | re.I)
_READ = re.compile(_TAG.format(r"(?:ЧИТАТЬ|READ)"), re.I)
_FIND = re.compile(_TAG.format(r"(?:НАЙТИ|FIND)"), re.I)
_RUN = re.compile(_TAG.format(r"(?:ЗАПУСТИТЬ|RUN)"), re.I)
_DONE = re.compile(r"\[\[\s*(?:ГОТОВО|DONE)\s*\]\](?P<body>.*?)(?:\[\[\s*/\s*(?:ГОТОВО|DONE)\s*\]\]|\Z)", re.S | re.I)
_ANY = re.compile(r"\[\[\s*/?\s*(?:ЗАПИСАТЬ|WRITE|ЧИТАТЬ|READ|НАЙТИ|FIND|ЗАПУСТИТЬ|RUN|ГОТОВО|DONE)\b[^\]]*\]\]", re.I)


def _unfence(body: str) -> str:
    """Модель часто оборачивает содержимое в ```: убираем обёртку, сам код не трогаем."""
    b = body.strip("\n")
    m = re.match(r"^\s*```[\w+-]*\n(.*?)\n?```\s*$", b, re.S)
    return (m.group(1) if m else b).rstrip() + "\n"


def parse(text: str) -> dict:
    """→ {'say': текст без команд, 'actions': [(вид, аргумент, тело)], 'done': итог или None}. Команды — в порядке появления."""
    acts: list[tuple[int, str, str, str]] = []
    done = None
    for m in _WRITE.finditer(text):
        acts.append((m.start(), "write", (m["arg"] or "").strip().strip("\"'«»`"), _unfence(m["body"])))
    for m in _READ.finditer(text):
        acts.append((m.start(), "read", (m["arg"] or "").strip().strip("\"'«»`"), ""))
    for m in _FIND.finditer(text):
        acts.append((m.start(), "find", (m["arg"] or "").strip().strip("\"'«»`"), ""))
    for m in _RUN.finditer(text):
        acts.append((m.start(), "run", (m["arg"] or "").strip().strip("`"), ""))
    d = _DONE.search(text)
    if d:
        done = d["body"].strip() or "Готово."
    acts.sort(key=lambda a: a[0])
    say = _DONE.sub("", _WRITE.sub("", text))
    say = _ANY.sub("", _RUN.sub("", _FIND.sub("", _READ.sub("", say))))
    say = re.sub(r"\n{3,}", "\n\n", say).strip()
    return {"say": say, "actions": [(k, a, b) for _, k, a, b in acts], "done": done}


def live_view(partial: str) -> dict:
    """Что показать в окне, пока модель ещё пишет ответ: её слова и что она пишет прямо сейчас (файл, команда)."""
    say = parse(partial)["say"][-700:]
    doing = ""
    w = list(re.finditer(r"\[\[\s*(?:ЗАПИСАТЬ|WRITE)\s*:\s*([^\]\n]{1,200}?)\s*\]\]", partial, re.I))
    if w and not re.search(r"\[\[\s*/\s*(?:ЗАПИСАТЬ|WRITE)", partial[w[-1].end():], re.I):
        n = partial[w[-1].end():].count("\n")
        doing = f"✏ Пишу {w[-1].group(1)} · строк: {n}"
    else:
        r = list(re.finditer(r"\[\[\s*(?:ЗАПУСТИТЬ|RUN|ЧИТАТЬ|READ|НАЙТИ|FIND)\s*:\s*([^\]\n]{1,120})", partial, re.I))
        if r:
            doing = "▶ " + r[-1].group(1).strip()
    return {"say": say, "doing": doing}


def syntax_errors(ws: "Workspace") -> list[str]:
    """Автопроверка без запуска кода: синтаксис .py и .json в файлах, которые написала Карина (input/ не трогаем)."""
    errs = []
    for rel, size in ws.listing():
        ext = rel.rpartition(".")[2].lower()
        if rel.startswith("input/") or ext not in ("py", "json") or size > 1_000_000:
            continue
        text = read_text(ws.root / rel)
        if text is None:
            continue
        try:
            if ext == "py":
                compile(text, rel, "exec", dont_inherit=True)
            else:
                import json
                json.loads(text)
        except SyntaxError as e:
            errs.append(f"{rel}, строка {e.lineno}: {e.msg}")
        except ValueError as e:
            errs.append(f"{rel}: {e}")
    return errs


# ---------------------------------------------------------------- рабочая папка проекта
def slug(text: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", text or "", flags=re.U).strip().replace(" ", "_")
    return (s[:32] or "проект").strip("_") or "проект"


class Workspace:
    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def create(cls, base: Path, title: str) -> "Workspace":
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        for n in range(1, 100):
            p = base / (f"{slug(title)}_{stamp}" + ("" if n == 1 else f"_{n}"))
            try:
                p.mkdir()
                return cls(p)
            except FileExistsError:
                continue
        raise CoderError("Не удалось создать папку проекта.")

    # ---- пути ----
    def safe(self, rel: str, *, for_write: bool = False) -> Path:
        rel = (rel or "").strip().replace("\\", "/")
        if not rel or rel.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel) or "\x00" in rel:
            raise CoderError(f"недопустимый путь «{rel}»: нужен путь внутри проекта, например src/main.py")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise CoderError(f"недопустимый путь «{rel}»: выходить за папку проекта нельзя")
        for p in parts:
            if re.search(r'[<>:"|?*]', p) or p.rstrip(". ") != p or p.split(".")[0].lower() in filesmod.RESERVED:
                raise CoderError(f"недопустимое имя «{p}»")
        path = (self.root / "/".join(parts)).resolve()
        if self.root.resolve() not in path.parents:
            raise CoderError(f"путь «{rel}» вне папки проекта")
        if for_write:
            ext = parts[-1].rpartition(".")[2].lower() if "." in parts[-1] else parts[-1].lower().lstrip(".")
            if ext in BLOCKED_EXT:
                raise CoderError(f"файлы типа .{ext} (программы и скрипты запуска) Карина не создаёт")
            if "." in parts[-1] and ext not in WRITE_EXT:
                raise CoderError(f"тип файла .{ext} не поддерживается для записи")
        return path

    def listing(self) -> list[tuple[str, int]]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not (set(p.relative_to(self.root).parts) & SKIP_DIRS):
                out.append((p.relative_to(self.root).as_posix(), p.stat().st_size))
        return out

    def tree_text(self, limit: int = 80, max_chars: int = 0) -> str:
        """Список файлов. Если проект большой — по папкам (имя папки и сколько в ней файлов), чтобы не съесть контекст модели."""
        items = self.listing()
        lines = [f"{n} ({s} байт)" for n, s in items[:limit]]
        if len(items) > limit:
            lines.append(f"… и ещё {len(items) - limit}")
        text = "\n".join(lines) or "(пусто)"
        if max_chars and len(text) > max_chars:
            dirs: dict[str, list[int]] = {}
            for n, s in items:
                d = n.rpartition("/")[0] or "."
                e = dirs.setdefault(d, [0, 0])
                e[0] += 1
                e[1] += s
            lines = [f"{d}/ — файлов: {c}, {sz // 1024 + 1} КБ" for d, (c, sz) in sorted(dirs.items())]
            text = f"Файлов всего: {len(items)}. По папкам (содержимое смотри командами [[НАЙТИ: …]] и [[ЧИТАТЬ: папка/файл]]):\n" + "\n".join(lines)
            if len(text) > max_chars:
                text = text[:max_chars].rsplit("\n", 1)[0] + "\n…"
        return text

    # ---- чтение / запись ----
    def read(self, rel: str, limit: int = 20_000) -> str:
        """Файл проекта целиком или частью: «путь» либо «путь:10-80» (строки с 10 по 80)."""
        rng = re.match(r"^(?P<p>.*?):(?P<a>\d+)(?:\s*[-–]\s*(?P<b>\d+))?$", rel.strip())
        a = b = 0
        if rng and not re.match(r"^[A-Za-z]$", rng["p"]):
            rel, a, b = rng["p"], int(rng["a"]), int(rng["b"] or 0)
        p = self.safe(rel)
        if not p.is_file():
            raise CoderError(f"файла «{rel}» нет в проекте")
        text = read_text(p)
        if text is None:
            raise CoderError(f"«{rel}» — не текстовый файл, прочитать его нельзя")
        if a:
            lines = text.split("\n")
            b = b if b >= a else a + 79
            chunk = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines[a - 1:b], start=a))
            tail = f"\n… (показаны строки {a}–{min(b, len(lines))} из {len(lines)})"
            return (chunk if len(chunk) <= limit else chunk[:limit] + "\n… (обрезано)") + tail
        n_lines = text.count("\n") + 1
        return text if len(text) <= limit else text[:limit] + f"\n… (обрезано: показано {limit} из {len(text)} символов, всего строк {n_lines}; читайте частями: [[ЧИТАТЬ: {rel}:1-80]])"

    def find(self, query: str, limit: int = 40) -> str:
        """Поиск текста (без учёта регистра) по текстовым файлам проекта: «путь:строка: текст»."""
        q = (query or "").strip().lower()
        if len(q) < 2:
            raise CoderError("для поиска нужно хотя бы 2 символа")
        hits, files_hit = [], 0
        for rel, size in self.listing():
            if size > 2_000_000:
                continue
            text = read_text(self.root / rel)
            if text is None:
                continue
            found = False
            for i, ln in enumerate(text.split("\n"), start=1):
                if q in ln.lower():
                    found = True
                    hits.append(f"{rel}:{i}: {ln.strip()[:160]}")
                    if len(hits) >= limit:
                        break
            files_hit += found
            if len(hits) >= limit:
                hits.append(f"… (показаны первые {limit} совпадений — уточните запрос)")
                break
        return "\n".join(hits) if hits else f"«{query}» в проекте не найдено"

    def write(self, rel: str, body: str) -> int:
        if len(body) > MAX_WRITE_CHARS:
            raise CoderError("содержимое слишком большое")
        p = self.safe(rel, for_write=True)
        if not p.exists() and len(self.listing()) >= MAX_PROJECT_FILES:
            raise CoderError(f"в проекте уже {MAX_PROJECT_FILES} файлов — больше создавать нельзя")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
        return len(body)

    def import_paths(self, paths: list[str]) -> tuple[list[str], list[str]]:
        """Копирует приложенные файлы и папки в проект, архивы (zip, tar.gz) распаковывает в input/<имя архива>/.
        → (скопированные, предупреждения)."""
        copied, warns, total = [], [], 0
        (self.root / "input").mkdir(exist_ok=True)
        dropped = 0

        def add(src: Path, rel: Path):
            nonlocal total, dropped
            if len(copied) >= MAX_INPUT_FILES:
                dropped += 1
                return
            try:
                size = src.stat().st_size
            except OSError:
                return
            if rel.suffix[1:].lower() in BLOCKED_EXT:
                warns.append(f"«{src.name}» пропущен: программы и скрипты запуска не принимаются")
                return
            if total + size > MAX_INPUT_BYTES:
                warns.append(f"«{src.name}» пропущен: слишком большой общий размер (лимит {MAX_INPUT_BYTES // 2**20} МБ)")
                return
            dst = self.root / "input" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total += size
            copied.append(("input" / rel).as_posix())

        def add_archive(src: Path):
            nonlocal total
            name = archives.stem(src)
            dest = self.root / "input" / name
            n = 2
            while dest.exists():
                dest = self.root / "input" / f"{name}_{n}"
                n += 1
            try:
                files, w = archives.extract(src, dest, max_files=max(0, MAX_INPUT_FILES - len(copied)), budget_left=max(0, MAX_INPUT_BYTES - total))
            except archives.ArchiveError as e:
                warns.append(str(e))
                return
            warns.extend(w)
            for f in files:
                try:
                    total += (dest / f).stat().st_size
                except OSError:
                    pass
                copied.append(("input/" + dest.name + "/" + f))
            if not files:
                warns.append(f"«{src.name}»: в архиве не нашлось подходящих файлов")

        for raw in paths:
            src = Path(raw)
            if src.is_file():
                if archives.kind(src) == "unsupported":
                    warns.append(archives.unsupported_text(src))
                elif archives.is_archive(src):
                    add_archive(src)
                else:
                    add(src, Path(src.name))
            elif src.is_dir():
                for f in sorted(src.rglob("*")):
                    if f.is_file() and not (set(f.relative_to(src).parts) & SKIP_DIRS):
                        add(f, Path(src.name) / f.relative_to(src))
            else:
                warns.append(f"«{raw}» не найден")
        if dropped:
            warns.append(f"Принято не больше {MAX_INPUT_FILES} файлов — остальных ({dropped}) нет в проекте.")
        return copied, warns

    def zip(self) -> Path:
        z = self.root.with_suffix(".zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
            for rel, _ in self.listing():
                f.write(self.root / rel, rel)
        return z


def read_text(p: Path) -> str | None:
    """Текст файла или None, если файл бинарный. docx и xlsx разбираются в текст."""
    ext = p.suffix[1:].lower()
    try:
        if ext == "docx":
            import docx
            d = docx.Document(str(p))
            parts = [x.text for x in d.paragraphs if x.text.strip()]
            for t in d.tables:
                parts += [" | ".join(c.text.strip() for c in r.cells) for r in t.rows]
            return "\n".join(parts)
        if ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets:
                out.append(f"# Лист: {ws.title}")
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 200:
                        out.append("…")
                        break
                    out.append(";".join("" if v is None else str(v) for v in row))
            return "\n".join(out)
        if ext in ("pdf", "png", "jpg", "jpeg", "gif", "zip", "7z", "rar", "db", "sqlite", "pyc", "bin", "ico", "mp3", "mp4", "wav"):
            return None
        data = p.read_bytes()
        if b"\x00" in data[:4096]:
            return None
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
    except Exception:
        return None
    return None


def read_attachments(paths: list[str], limit: int) -> tuple[list[dict], list[str]]:
    """Для ответа «только прочитать»: тексты приложенных файлов, папок и архивов (без копирования и без проекта).
    → ([{'name', 'text'}], замечания). Всего не больше limit символов; что не поместилось или не текст — в замечаниях."""
    out, notes, used, count = [], [], 0, 0
    skipped_big, skipped_bin = [], 0

    def add(src: Path, name: str):
        nonlocal used, count, skipped_bin
        count += 1
        if count > MAX_INPUT_FILES:
            if count == MAX_INPUT_FILES + 1:
                notes.append(f"Прочитано не больше {MAX_INPUT_FILES} файлов — остальные пропущены.")
            return
        text = read_text(src)
        if text is None:
            if len(paths or []) == 1 and not src.is_dir() and "/" not in name:
                notes.append(f"«{name}» — не текстовый файл, содержимое не показано")
            else:
                skipped_bin += 1
        elif used + len(text) > limit:
            skipped_big.append(name)
        else:
            used += len(text)
            out.append({"name": name, "text": text})

    for raw in paths or []:
        src = Path(raw)
        if src.is_file() and archives.kind(src) == "unsupported":
            notes.append(archives.unsupported_text(src))
        elif src.is_file() and archives.is_archive(src):
            try:
                d, files, w = archives.extract_temp(src)
            except archives.ArchiveError as e:
                notes.append(str(e))
                continue
            try:
                notes.extend(w)
                for rel in sorted(files):
                    add(d / rel, f"{archives.stem(src)}/{rel}")
            finally:
                shutil.rmtree(d, ignore_errors=True)
        elif src.is_file():
            add(src, src.name)
        elif src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_file() and not (set(f.relative_to(src).parts) & SKIP_DIRS):
                    add(f, (Path(src.name) / f.relative_to(src)).as_posix())
        else:
            notes.append(f"«{raw}» не найден")
    if skipped_big:
        shown = ", ".join(f"«{x}»" for x in skipped_big[:4]) + (f" и ещё {len(skipped_big) - 4}" if len(skipped_big) > 4 else "")
        notes.append(f"Не поместилось в запрос ({len(skipped_big)} файл.): {shown} — для больших проектов используется режим «Код» (читаю файлы по частям)")
    if skipped_bin:
        notes.append(f"Нетекстовых файлов (картинки, шрифты, базы) пропущено: {skipped_bin}")
    return out, notes


# ---------------------------------------------------------------- запуск проверок
def find_interpreter(kind: str) -> str | None:
    names = {"python": ("python", "python3", "py"), "pytest": ("pytest",), "node": ("node",)}[kind]
    for n in names:
        p = shutil.which(n)
        if not p:
            continue
        if "WindowsApps" in p and n != "py":          # заглушка Microsoft Store, а не настоящий Python
            continue
        return p
    return None


def python_cmd() -> list[str] | None:
    """Команда запуска Python. Если запущены из исходников — тот же интерпретатор; в собранном exe — установленный в системе."""
    if not getattr(sys, "frozen", False) and sys.executable:
        return [sys.executable]
    p = find_interpreter("python")
    return [p] if p else None


class Runner:
    def __init__(self, ws: Workspace, timeout: int = RUN_TIMEOUT):
        self.ws, self.timeout = ws, timeout

    def check_code(self) -> str:
        """Страховка перед запуском: если в проекте есть явно опасные вещи — запуск отклоняется с объяснением."""
        for rel, size in self.ws.listing():
            if rel.startswith("input/") or rel.rpartition(".")[2].lower() not in ("py", "js", "mjs", "ts"):
                continue
            text = read_text(self.ws.root / rel) or ""
            m = DANGEROUS.search(text)
            if m:
                return f"в «{rel}» найдено «{m.group(0).strip()[:40]}» — запуск таких программ из Карины запрещён (сеть, подпроцессы, удаление файлов, реестр). Перепишите без этого и проверяйте логику на встроенных данных."
        return ""

    def build(self, cmdline: str) -> list[str]:
        try:
            parts = shlex.split(cmdline, posix=(sys.platform != "win32"))
        except ValueError as e:
            raise CoderError(f"не разобрала команду: {e}")
        parts = [p.strip('"') for p in parts]
        if not parts:
            raise CoderError("пустая команда")
        head = Path(parts[0]).name.lower().removesuffix(".exe")
        if head not in RUNNERS or parts[0] != Path(parts[0]).name:
            raise CoderError("можно запускать только python, pytest и node (например: python main.py, pytest -q, node app.js)")
        kind, args = RUNNERS[head], parts[1:]
        if kind == "python" and any(a in ("-c", "-m") for a in args[:1]) and not (args[0] == "-m" and len(args) > 1 and args[1] in ("pytest", "unittest", "doctest")):
            raise CoderError("python -c и python -m разрешены только для pytest / unittest / doctest; запускайте файл: python файл.py")
        if kind == "node" and any(a in ("-e", "--eval", "-p", "--print") for a in args):
            raise CoderError("node -e запрещён; запускайте файл: node файл.js")
        for a in args:
            if a.startswith("-"):
                continue
            if re.match(r"^[A-Za-z]:|^[/\\~]|(^|[/\\])\.\.([/\\]|$)", a):
                raise CoderError(f"аргумент «{a}» указывает за пределы проекта")
        if kind == "python":
            base = python_cmd()
            if not base:
                raise CoderError("на компьютере не найден Python — проверить код запуском нельзя. Установите Python (python.org) или проверьте вручную.")
            return base + args
        if kind == "pytest":
            base = python_cmd()
            if not base:
                raise CoderError("на компьютере не найден Python, а pytest работает на нём")
            return base + ["-m", "pytest", "-p", "no:cacheprovider"] + args
        exe = find_interpreter("node")
        if not exe:
            raise CoderError("на компьютере не найден Node.js")
        return [exe] + args

    def run(self, cmdline: str, stop: threading.Event | None = None) -> tuple[int, str]:
        cmd = self.build(cmdline)
        bad = self.check_code()
        if bad:
            raise CoderError(bad)
        env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATHEXT", "WINDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1", "CI": "1"})
        try:
            proc = subprocess.Popen(cmd, cwd=str(self.ws.root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", creationflags=0x08000000 if sys.platform == "win32" else 0)
        except OSError as e:
            raise CoderError(f"не удалось запустить: {e}")
        buf: list[str] = []

        def pump():
            for line in proc.stdout:
                buf.append(line)
        t = threading.Thread(target=pump, daemon=True)
        t.start()
        t0, timed_out = time.time(), False
        while proc.poll() is None:
            if time.time() - t0 > self.timeout:
                timed_out = True
            if timed_out or (stop is not None and stop.is_set()):
                proc.kill()
                break
            time.sleep(0.05)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        t.join(2)
        try:
            proc.stdout.close()
        except Exception:
            pass
        out = "".join(buf)
        if len(out) > OUT_LIMIT:
            out = out[:OUT_LIMIT // 2] + "\n… (вывод обрезан) …\n" + out[-OUT_LIMIT // 2:]
        if timed_out:
            return 124, out + f"\n[остановлено: программа работала дольше {self.timeout} с]"
        return proc.returncode if proc.returncode is not None else 1, out


# ---------------------------------------------------------------- подсказка для модели
def system_prompt(can_run: bool, run_note: str = "") -> str:
    run = prompts.CODER_RUN_ON if can_run else prompts.fill(prompts.CODER_RUN_OFF, note=(" (" + run_note + ")") if run_note else "")
    return (prompts.fill(prompts.CODER_HEAD, female=FEMALE, run=run) + prompts.CODER_RULES
            + prompts.forbidden_block(prompts.FORBIDDEN_ALL, prompts.FORBIDDEN_CODER))


def first_message(task: str, ws: Workspace, copied: list[str], context: str = "", resumed: bool = False, budget: int = 60_000) -> str:
    """Первое сообщение модели: задача, список файлов и содержимое небольших файлов. budget — сколько символов контекста есть у модели:
    у локальной модели он мал, поэтому большие проекты отдаются списком, а читаются по частям командами."""
    parts = []
    if context:
        parts += ["Предыдущая переписка с пользователем (для контекста):\n" + context[-int(budget * 0.1):], ""]
    if resumed:
        parts += ["Это ПРОДОЛЖЕНИЕ работы над уже существующим проектом: файлы лежат в папке проекта, прочитай нужные командой [[ЧИТАТЬ: путь]] и вноси правки в них.", ""]
    parts += [f"Задача:\n{task.strip()[:int(budget * 0.25)]}", "", "Файлы проекта:\n" + ws.tree_text(max_chars=max(600, int(budget * 0.18)))]
    inline_total, inline_each = min(INLINE_TOTAL, int(budget * 0.35)), min(INLINE_BYTES, int(budget * 0.15))
    used = 0
    few = len(copied) <= 12                 # много файлов (проект, архив) целиком не показываем: список + чтение по частям
    for rel in copied if few else []:
        p = ws.root / rel
        try:
            size = p.stat().st_size
        except OSError:
            continue
        text = read_text(p)
        if text is None:
            continue                        # картинки, шрифты и т. п. в списке уже есть; модели они не нужны
        elif size <= inline_each and used + len(text) <= inline_total:
            used += len(text)
            parts.append(f"\n=== {rel} ===\n{text}")
        else:
            parts.append(f"\n[{rel}: {len(text)} символов, не помещается — читай по частям: [[ЧИТАТЬ: {rel}:1-80]] или ищи: [[НАЙТИ: …]]]")
    if not few:
        parts.append("\nФайлов много, содержимое сюда не вставлено: читай нужное командами [[ЧИТАТЬ: путь]], [[ЧИТАТЬ: путь:строки]] и [[НАЙТИ: текст]].")
    return "\n".join(parts)


# ---------------------------------------------------------------- сам цикл
class CoderService:
    def __init__(self, cfg, brain, emit, projects_dir: Path | None = None, runner_cls=Runner):
        self.cfg, self.brain, self.emit = cfg, brain, emit
        self._dir = projects_dir
        self._runner_cls = runner_cls
        self._sink = emit
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self.last: dict = {}

    def project_dir(self, project: str) -> Path | None:
        """Папка существующего проекта по имени или None. Принимается только имя папки прямо внутри «Проекты» (без «..», «.» и путей)."""
        if not isinstance(project, str) or not project or project in (".", "..") or project != Path(project).name or "\\" in project or "/" in project:
            return None
        d = self.base / project
        try:
            if d.is_dir() and d.resolve().parent == self.base.resolve():
                return d
        except OSError:
            pass
        return None

    @property
    def base(self) -> Path:
        return self._dir or filesmod.documents_dir() / filesmod.FOLDER_NAME / PROJECTS

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def stop(self):
        self._stop.set()

    def settings(self) -> dict:
        c = self.cfg.get("coder", {}) or {}
        return {"run": bool(c.get("run", True)), "max_steps": max(1, min(30, int(c.get("max_steps") or MAX_STEPS)))}

    def start(self, task: str, paths: list[str], wait: bool = False, sink=None, project: str = "", context: str = "") -> dict:
        """Запускает задачу в фоне (wait=True — дождаться конца).
        sink — куда слать события хода работы (по умолчанию в окно как «coder» / «coder_done»; чат подставляет свой приёмник).
        project — имя существующей папки проекта: продолжаем работу в ней, а не создаём новую. context — краткая прежняя переписка."""
        task = (task or "").strip()
        if not task:
            return {"ok": False, "error": "Опишите, что нужно сделать."}
        if self.busy:
            return {"ok": False, "error": "Карина уже работает над задачей. Дождитесь конца или нажмите «Стоп»."}
        try:
            choice = self.brain.choose(task, want_files=True)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        resumed = False
        if project and self.project_dir(project):
            ws, resumed = Workspace(self.project_dir(project)), True
        else:
            ws = Workspace.create(self.base, task)
        copied, warns = ws.import_paths([p for p in (paths or []) if isinstance(p, str)][:200]) if paths else ([], [])
        t = threading.Thread(target=self._run, args=(task, ws, copied, warns, choice, sink or self.emit, resumed, context), daemon=True, name="karina-coder")
        t.start()
        if wait:
            t.join()
        return {"ok": True, "project": ws.root.name, "files": copied, "warnings": warns, "brain": choice.label, "resumed": resumed}

    # ---- шаги ----
    def _say(self, kind: str, text: str, **kw):
        self._sink({"type": "coder", "kind": kind, "text": text, **kw})

    def _trim(self, msgs: list[dict], budget: int) -> list[dict]:
        """Бюджет контекста: первое сообщение (задача и файлы) остаётся, из середины вычёркиваются самые старые обмены."""
        def size(ms): return sum(len(m["content"]) + 20 for m in ms)
        out = list(msgs)
        while size(out) > budget and len(out) > 3:
            del out[1:3]
            if out[1]["role"] != "user":
                out.insert(1, {"role": "user", "content": "[Часть прежней переписки удалена для экономии памяти. Актуальные файлы смотри командой [[ЧИТАТЬ: путь]].]"})
        return out

    def _budget(self, claude: bool, max_tokens: int, ctx: int = 0) -> int:
        ctx = ctx or (32768 if claude else int((self.cfg.get("models", {}) or {}).get("ctx") or 0))
        if not ctx:
            from . import models
            m = models.by_id((self.cfg.get("models", {}) or {}).get("active", ""))
            ctx = int(m["ctx"]) if m else 8192
        return int(max(1000, ctx - max_tokens - 1200) * 2.0)

    def _run(self, task: str, ws: Workspace, copied: list[str], warns: list[str], choice, sink=None, resumed: bool = False, context: str = ""):
        if not self._busy.acquire(blocking=False):
            return
        self._sink = sink or self.emit
        self._stop.clear()
        st = self.settings()
        backend, is_claude = choice.backend, choice.name == "claude"
        max_tokens = LIMITS[choice.name]
        runner = self._runner_cls(ws)
        run_note = ""
        can_run = st["run"]
        if can_run and not python_cmd() and not find_interpreter("node"):
            can_run, run_note = False, "на компьютере не найден Python или Node.js"
        elif not can_run:
            run_note = "запуск выключен в настройках Кодера"
        summary, error = "", ""
        stats = {"writes": 0, "runs": 0, "last_run": None, "dirty": False}      # last_run: код возврата последнего запуска; dirty: после него файлы менялись
        self._say("start", f"Проект: {ws.root.name}" + (f" · отвечает: {choice.label}" if choice.label else ""), project=ws.root.name)
        for w in warns:
            self._say("warn", w)
        try:
            budget = self._budget(is_claude, max_tokens, backend.ctx_tokens if choice.name == "cloud" else 0)
            read_limit = max(2000, min(20_000, int(budget * 0.3)))                 # сколько символов файла отдаём за одно чтение
            msgs = [{"role": "user", "content": first_message(task, ws, copied, context, resumed, budget)}]
            sysmsg = {"role": "system", "content": system_prompt(can_run, run_note)}
            nudged, fixes = False, 0
            if backend.state != "ready":
                self._say("step", "Загружаю модель в память…")
            for step in range(1, st["max_steps"] + 1):
                if self._stop.is_set():
                    break
                self._say("think", f"Шаг {step} из {st['max_steps']}: думаю…", step=step, total=st["max_steps"])
                msgs = self._trim(msgs, budget)
                last_emit = [0.0]

                def on_delta(t: str):
                    now = time.time()
                    if now - last_emit[0] >= 0.35:
                        last_emit[0] = now
                        self._say("stream", "", **live_view(t))
                reply = backend.chat([sysmsg] + msgs, max_tokens=max_tokens, temperature=0.3, on_delta=on_delta, stop=self._stop)
                if self._stop.is_set():
                    break
                p = parse(reply)
                if p["say"]:
                    self._say("say", p["say"])
                results: list[str] = []
                for kind, arg, body in p["actions"]:
                    if self._stop.is_set():
                        break
                    try:
                        if kind == "write":
                            n = ws.write(arg, body)
                            stats["writes"] += 1
                            stats["dirty"] = True
                            self._say("write", f"Записала {arg}", path=arg, chars=n, lines=body.count("\n") + 1, preview=body[:1500])
                            results.append(f"✔ {arg} записан ({n} символов)")
                        elif kind == "read":
                            text = ws.read(arg, read_limit)
                            self._say("read", f"Читаю {arg}", path=arg)
                            results.append(f"=== {arg} ===\n{text}")
                        elif kind == "find":
                            found = ws.find(arg)
                            self._say("find", f"Ищу «{arg}»", query=arg, output=found[:1500])
                            results.append(f"Поиск «{arg}»:\n{found}")
                        elif kind == "run":
                            if not can_run:
                                raise CoderError("запуск недоступен" + (f" ({run_note})" if run_note else ""))
                            self._say("run", f"Запускаю: {arg}", cmd=arg)
                            code, out = runner.run(arg, self._stop)
                            stats["runs"] += 1
                            stats["last_run"], stats["dirty"] = code, False
                            self._say("result", "Проверка прошла" if code == 0 else f"Проверка не прошла (код {code})", ok=(code == 0), code=code, output=out[-1500:])
                            results.append(f"$ {arg}\n[код возврата: {code}]\n{out.strip() or '(вывода нет)'}")
                    except CoderError as e:
                        self._say("warn", f"{kind}: {e}")
                        results.append(f"✖ Ошибка: {e}")
                if p["done"] is not None:
                    bad = syntax_errors(ws) if stats["writes"] else []
                    if bad and fixes < 2 and step < st["max_steps"]:
                        fixes += 1
                        self._say("warn", "Автопроверка нашла синтаксические ошибки: " + "; ".join(bad[:3]))
                        msgs.append({"role": "assistant", "content": reply.strip() or "(пусто)"})
                        msgs.append({"role": "user", "content": prompts.fill(prompts.CODER_SYNTAX_ERRORS, errors="\n".join(bad[:8]))})
                        continue
                    summary = p["done"]
                    break
                msgs.append({"role": "assistant", "content": reply.strip() or "(пусто)"})
                if not p["actions"]:
                    if not nudged and not p["say"].strip().endswith("?"):
                        nudged = True
                        msgs.append({"role": "user", "content": prompts.CODER_NUDGE_NO_ACTIONS})
                        continue
                    summary = p["say"]
                    break
                msgs.append({"role": "user", "content": prompts.fill(prompts.CODER_RESULTS, results="\n\n".join(results))})
            else:
                summary = "Закончились шаги, не все проверки пройдены. Посмотрите файлы проекта — часть работы сделана."
        except EngineError as e:
            error = str(e)
        except Exception as e:
            error = f"{e.__class__.__name__}: {e}"
        if self._stop.is_set():
            summary = (summary + "\n\n*(остановлено)*").strip()
        # итог проверки считаем сами, а не со слов модели
        syntax_note = ""
        if stats["writes"] and not error:
            try:
                bad = syntax_errors(ws)
                self._say("run", "Автопроверка синтаксиса (без запуска)", cmd="проверка синтаксиса Python/JSON")
                self._say("result", "Синтаксис в порядке" if not bad else "Есть синтаксические ошибки", ok=not bad, code=0 if not bad else 1, output="\n".join(bad))
                syntax_note = " Синтаксис проверен автоматически — ошибок нет." if not bad else " Автопроверка нашла синтаксические ошибки: " + "; ".join(bad[:3]) + "."
            except Exception:
                pass
        if stats["runs"] == 0:
            verdict = ("Код не запускался, работа не проверена." + syntax_note) if stats["writes"] else "Файлы не создавались."
        elif stats["dirty"]:
            verdict = "После последней проверки файлы менялись — итоговая версия не проверена."
        elif stats["last_run"] == 0:
            verdict = f"Последняя проверка прошла (код 0), всего запусков: {stats['runs']}."
        else:
            verdict = f"Последняя проверка не прошла (код {stats['last_run']})."
        files = [{"path": r, "size": s} for r, s in ws.listing() if not r.startswith("input/")]
        zip_name = ""
        if files:
            try:
                zip_name = ws.zip().name
            except Exception:
                pass
        done = {"type": "coder_done", "project": ws.root.name, "summary": summary, "error": error, "verdict": verdict,
                "files": files, "zip": zip_name, "ok": (not error) and stats["last_run"] == 0 and not stats["dirty"]}
        self.last = {"project": ws.root.name, "folder": str(ws.root), "result": done}
        sink_ = self._sink
        self._busy.release()
        sink_(done)

    # ---- доступ из окна ----
    def _project_file(self, project: str, rel: str) -> Path:
        d = self.project_dir(project)
        if d is None:
            raise CoderError("Проект не найден.")
        p = Workspace(d).safe(rel)
        if not p.is_file():
            raise CoderError("Файл не найден — возможно, его удалили.")
        return p

    def preview(self, project: str, rel: str) -> dict:
        """Предпросмотр файла проекта в окне: картинка / HTML-шаблон / текст (см. files.preview_file)."""
        return filesmod.preview_file(self._project_file(project, rel))

    def open_file(self, project: str, rel: str) -> None:
        """Открыть файл проекта: картинки и SVG — программой по умолчанию, код, текст и HTML — только в Блокноте (не запускаем)."""
        p = self._project_file(project, rel)
        if sys.platform != "win32":
            subprocess.Popen(["xdg-open", str(p)])
        elif p.suffix[1:].lower() in filesmod.SHELL_OPEN:
            os.startfile(str(p))                                   # noqa: S606 — путь проверен выше
        else:
            subprocess.Popen(["notepad.exe", str(p)])

    def open_folder(self, project: str = "") -> None:
        p = self.base
        if project:
            p = self.project_dir(project)
            if p is None:
                raise CoderError("Проект не найден.")
        p.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(p))                                   # noqa: S606 — путь проверен выше
        else:
            subprocess.Popen(["xdg-open", str(p)])
