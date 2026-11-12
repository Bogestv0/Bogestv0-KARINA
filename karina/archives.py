"""Приложенные архивы (zip, tar, tar.gz, tgz, tar.bz2, tar.xz): безопасная распаковка в папку проекта.

Архив — чужие данные, поэтому:
  * пути из архива очищаются: «..», абсолютные пути и буквы дисков отбрасываются (защита от zip-slip);
  * ссылки (symlink) и специальные файлы пропускаются;
  * есть лимиты на число файлов и общий распакованный размер (защита от «архивной бомбы»); размер считается по факту чтения,
    а не по заголовку, который можно подделать;
  * программы и скрипты запуска (.exe, .bat, .ps1 …) и служебные папки (.git, node_modules …) не распаковываются.
Вложенные архивы не раскрываются. rar и 7z стандартной библиотекой не читаются — об этом говорим пользователю прямо.
"""
from __future__ import annotations
import re, shutil, tarfile, zipfile
from pathlib import Path, PurePosixPath

MAX_FILES = 600
MAX_TOTAL = 80 * 1024 * 1024          # байт после распаковки
MAX_FILE = 10 * 1024 * 1024
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "__MACOSX"}
BLOCKED_EXT = {"exe", "bat", "cmd", "ps1", "vbs", "msi", "dll", "lnk", "scr", "com", "sh", "reg", "jar", "psm1", "hta", "pif"}
TAR_SUFFIXES = (".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
UNSUPPORTED = (".rar", ".7z")


class ArchiveError(Exception):
    pass


def kind(path: Path | str) -> str:
    """"zip" | "tar" | "unsupported" | "" (не архив)."""
    n = Path(path).name.lower()
    if n.endswith(".zip"):
        return "zip"
    if n.endswith(TAR_SUFFIXES):
        return "tar"
    if n.endswith(UNSUPPORTED):
        return "unsupported"
    return ""


def is_archive(path: Path | str) -> bool:
    return kind(path) in ("zip", "tar")


def stem(path: Path | str) -> str:
    n = Path(path).name
    for s in TAR_SUFFIXES + (".zip",) + UNSUPPORTED:
        if n.lower().endswith(s):
            n = n[: -len(s)]
            break
    return re.sub(r'[<>:"|?*\\/]+', "_", n).strip(" .") or "архив"


def clean_member(name: str) -> str | None:
    """Безопасный относительный путь внутри архива или None, если запись нужно пропустить."""
    n = (name or "").replace("\\", "/")
    if "\x00" in n:
        return None
    n = re.sub(r"^[A-Za-z]:", "", n)
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if set(parts[:-1]) & SKIP_DIRS or parts[-1] in SKIP_DIRS:
        return None
    for p in parts:
        if re.search(r'[<>:"|?*\x00-\x1f]', p) or p.rstrip(". ") != p:
            return None
    ext = parts[-1].rpartition(".")[2].lower() if "." in parts[-1] else ""
    if ext in BLOCKED_EXT:
        return None
    return "/".join(parts)


def _common_root(names: list[str]) -> str:
    """Единственная папка верхнего уровня («Проект-main/»), общая для всех файлов, — её отбрасываем."""
    tops = {n.split("/")[0] for n in names if "/" in n}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop()
    return ""


def _members(path: Path):
    """→ (список (имя в архиве, безопасное имя, размер, открыть()), список предупреждений)."""
    k = kind(path)
    out, warns = [], []
    if k == "zip":
        zf = zipfile.ZipFile(path)
        for info in zf.infolist():
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:          # symlink
                continue
            if info.flag_bits & 0x1:
                raise ArchiveError(f"«{path.name}» защищён паролем — распаковать его я не могу. Снимите пароль или приложите файлы без архива.")
            out.append((info.filename, info.file_size, (lambda i=info: zf.open(i))))
        return out, warns, zf
    if k == "tar":
        tf = tarfile.open(path)
        for m in tf.getmembers():
            if not m.isfile():
                continue
            out.append((m.name, m.size, (lambda mm=m: tf.extractfile(mm))))
        return out, warns, tf
    raise ArchiveError(unsupported_text(path))


def unsupported_text(path: Path | str) -> str:
    return f"«{Path(path).name}»: форматы rar и 7z я не читаю — приложите zip (или tar.gz) либо саму папку."


def extract(path: Path | str, dest: Path, *, max_files: int = MAX_FILES, max_total: int = MAX_TOTAL,
            budget_left: int | None = None) -> tuple[list[str], list[str]]:
    """Распаковывает архив в dest. → (относительные пути файлов, предупреждения). Бросает ArchiveError, если архив не открылся."""
    path = Path(path)
    if kind(path) == "unsupported":
        raise ArchiveError(unsupported_text(path))
    try:
        members, warns, handle = _members(path)
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, ValueError) as e:
        raise ArchiveError(f"«{path.name}» не открылся как архив ({e.__class__.__name__}) — возможно, файл повреждён.")
    names = [(raw, clean_member(raw), size, op) for raw, size, op in members]
    good = [(raw, c, size, op) for raw, c, size, op in names if c]
    skipped = len(names) - len(good)
    root = _common_root([c for _, c, _, _ in good])
    cap_total = max_total if budget_left is None else min(max_total, budget_left)
    copied: list[str] = []
    total, big, over_files, over_size, nested = 0, 0, 0, 0, 0
    dest = Path(dest)
    base = dest.resolve()
    try:
        for raw, c, size, op in good:
            rel = c[len(root) + 1:] if root else c
            if not rel:
                continue
            if size > MAX_FILE:
                big += 1
                continue
            if len(copied) >= max_files:
                over_files += 1
                continue
            if total + size > cap_total:
                over_size += 1
                continue
            target = (dest / rel).resolve()
            if base not in target.parents:                      # страховка поверх clean_member
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = op()
            if src is None:
                continue
            written = 0
            with src, open(target, "wb") as f:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_FILE or total + written > cap_total:      # заголовок соврал о размере
                        break
                    f.write(chunk)
            if written > MAX_FILE or total + written > cap_total:
                target.unlink(missing_ok=True)
                over_size += 1
                continue
            total += written
            if is_archive(target):
                nested += 1
            copied.append(rel)
    finally:
        handle.close()
    if skipped:
        warns.append(f"«{path.name}»: пропущено служебных и запрещённых файлов: {skipped} (программы и скрипты запуска, .git, node_modules и т. п.)")
    if big:
        warns.append(f"«{path.name}»: не распаковано файлов больше {MAX_FILE // 2**20} МБ: {big}")
    if over_files:
        warns.append(f"«{path.name}»: распаковано только {max_files} файлов, ещё {over_files} пропущено")
    if over_size:
        warns.append(f"«{path.name}»: не хватило лимита размера ({cap_total // 2**20} МБ), пропущено файлов: {over_size}")
    if nested:
        warns.append(f"«{path.name}»: внутри есть вложенные архивы ({nested}) — они не раскрываются")
    return copied, warns


def extract_temp(path: Path | str, tmp_parent: Path | None = None) -> tuple[Path, list[str], list[str]]:
    """Распаковка во временную папку (для ответа «только прочитать»). → (папка, пути, предупреждения). Папку удаляет вызывающий (rmtree)."""
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="karina-arc-", dir=str(tmp_parent) if tmp_parent else None))
    try:
        files, warns = extract(path, d)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return d, files, warns
