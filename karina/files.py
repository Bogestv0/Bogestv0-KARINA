"""Файлы: Word, Excel, PDF, текст, код и схемы SVG.

Модель только ПРОСИТ создать файл — оформляет содержимое в блок
    [[ФАЙЛ: имя.расширение]] … [[/ФАЙЛ]]
а файл создаёт сама Карина, и только в «Документы\\Карина»:
  * имя файла очищается от пути и запрещённых символов, расширение — только из белого списка;
  * чужие и уже существующие файлы не перезаписываются (к имени добавляется « (2)»);
  * SVG очищается по белому списку (без скриптов, внешних ссылок и картинок);
  * «Открыть» работает только для файлов из этой папки; код и текст открываются в Блокноте, а не запускаются.
"""
from __future__ import annotations
import csv, datetime, io, json, os, re, subprocess, sys, uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

FOLDER_NAME = "Карина"
MAX_FILES = 6                      # файлов за один ответ
MAX_CHARS = 1_500_000              # размер содержимого одного файла

# расширение → вид файла
KINDS: dict[str, str] = {"docx": "word", "xlsx": "excel", "pdf": "pdf", "svg": "svg"}
for _e in ("txt", "md", "csv", "json", "xml", "yaml", "yml", "log", "ini", "toml"):
    KINDS[_e] = "text"
for _e in ("py", "js", "mjs", "ts", "tsx", "jsx", "html", "css", "sql", "java", "c", "cpp", "h", "cs", "go", "rs", "php", "rb", "kt", "swift", "lua"):
    KINDS[_e] = "code"
KIND_LABEL = {"word": "Документ Word", "excel": "Таблица Excel", "pdf": "PDF", "svg": "Схема SVG", "text": "Текстовый файл", "code": "Код"}
# Этими программами Windows открывает файл «как есть»; всё остальное (код, текст) открываем в Блокноте — его нельзя «запустить».
SHELL_OPEN = {"docx", "xlsx", "pdf", "svg", "csv"}
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class FileError(Exception):
    """Понятная пользователю причина, по которой файл не создан или не открыт."""


# ---------------------------------------------------------------- папка
def documents_dir() -> Path:
    """«Документы» пользователя (с учётом переноса в OneDrive); запасной вариант — ~/Documents."""
    if sys.platform == "win32":
        try:
            import ctypes
            guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)   # FOLDERID_Documents
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf)) == 0 and buf.value:
                p = Path(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)
                return p
        except Exception:
            pass
    return Path.home() / "Documents"


# ---------------------------------------------------------------- запрос на файл в ответе модели
_BLOCK = re.compile(r"\[\[\s*(?:ФАЙЛ|FILE)\s*:\s*(?P<name>[^\]\n]{1,200}?)\s*\]\](?P<body>.*?)(?:(?P<end>\[\[\s*/\s*(?:ФАЙЛ|FILE)\s*\]\])|\Z)", re.S | re.I)
FILE_HINT = re.compile(
    r"(?:файл|документ|\bворд|\bword|docx|эксел|excel|xlsx|\bpdf|пдф|\btxt|схем|диаграмм|svg|иллюстрац|нарисуй|рисунок|логотип|сохрани|скрипт|\.py\b|\.js\b|\bhtml|\bcss\b|\bjson|\bcsv|\bsql)")


def wants_file(text: str) -> bool:
    """text — нормализованная фраза. Описание формата файлов даём модели, только если просьба об этом говорит:
    так текст из интернета не может «попросить» создать файл, а обычные вопросы не превращаются в файлы."""
    return bool(FILE_HINT.search(text or ""))


class Request:
    def __init__(self, name: str, body: str, complete: bool):
        self.name, self.body, self.complete = name.strip().strip("\"'«»"), body, complete


def extract(text: str) -> tuple[str, list[Request]]:
    """→ (текст ответа без блоков, запросы на файлы)."""
    reqs = [Request(m["name"], m["body"], bool(m["end"])) for m in _BLOCK.finditer(text or "")]
    clean_ = re.sub(r"\n{3,}", "\n\n", _BLOCK.sub("", text or "")).strip()
    return clean_, reqs


def display_text(text: str) -> str:
    """Для потока ответа в окне: вместо содержимого файла — «Готовлю файл…»."""
    return re.sub(r"\n{3,}", "\n\n", _BLOCK.sub(lambda m: f"\n\n⏳ Готовлю файл «{m['name'].strip()}»…\n\n", text or "")).strip()


# ---------------------------------------------------------------- имя файла
_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*\u200b-\u200f\u202a-\u202e]')


def safe_name(raw: str) -> tuple[str, str]:
    """→ (имя без расширения, расширение в нижнем регистре). Путь отбрасывается, символы очищаются."""
    base = (raw or "").replace("\\", "/").split("/")[-1].strip()
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem:
        raise FileError(f"У файла «{base or raw}» не указан тип (например, .docx или .txt).")
    ext = ext.lower().strip()
    if ext not in KINDS:
        raise FileError(f"тип «.{ext}» Карина не создаёт. Доступно: {', '.join('.' + e for e in sorted(KINDS))}.")
    stem = re.sub(r"\s+", " ", _BAD.sub("", stem)).strip(" .")[:80].strip(" .")
    if not stem:
        stem = "файл"
    if stem.lower() in RESERVED:
        stem = "_" + stem
    return stem, ext


# ---------------------------------------------------------------- разбор Markdown
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_INLINE = re.compile(r"\*\*(?P<b>.+?)\*\*|(?<![\w*])\*(?P<i>[^*\s][^*]*?)\*(?![\w*])|`(?P<c>[^`]+)`|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)\s]+)\)", re.S)
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST = re.compile(r"^(\s*)([-*•]|\d+[.)])\s+(.*)$")


def clean(text: str) -> str:
    return _CTRL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))


def spans(text: str) -> list[tuple[str, str]]:
    """Строка → [(текст, стиль)], стиль: '' | 'b' | 'i' | 'c' (код)."""
    out, last = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > last:
            out.append((text[last:m.start()], ""))
        if m["b"] is not None:
            out.append((m["b"], "b"))
        elif m["i"] is not None:
            out.append((m["i"], "i"))
        elif m["c"] is not None:
            out.append((m["c"], "c"))
        else:
            out.append((m["lt"] + (f" ({m['lu']})" if m["lu"].lower().startswith(("http://", "https://")) else ""), ""))
        last = m.end()
    if last < len(text):
        out.append((text[last:], ""))
    return out


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_md(text: str) -> list[tuple]:
    """('h', уровень, текст) ('p', текст) ('list', нумерованный, [(отступ, текст)]) ('code', текст) ('table', строки) ('hr',)"""
    lines = clean(text).split("\n")
    out: list[tuple] = []
    para: list[str] = []
    i = 0

    def flush():
        if para:
            out.append(("p", "\n".join(para)))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if re.match(r"^\s*```", ln):
            flush()
            code = []
            i += 1
            while i < len(lines) and not re.match(r"^\s*```", lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(("code", "\n".join(code)))
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
        if m:
            flush()
            out.append(("h", len(m[1]), m[2]))
        elif re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", ln):
            flush()
            out.append(("hr",))
        elif "|" in ln and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]) and "|" in lines[i + 1]:
            flush()
            rows = [_cells(ln)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            out.append(("table", rows))
            continue
        elif _LIST.match(ln):
            flush()
            items, ordered = [], bool(re.match(r"^\s*\d", ln))
            while i < len(lines) and _LIST.match(lines[i]):
                mm = _LIST.match(lines[i])
                items.append((min(2, len(mm[1].replace("\t", "    ")) // 2), mm[3]))
                i += 1
            out.append(("list", ordered, items))
            continue
        elif not ln.strip():
            flush()
        else:
            para.append(re.sub(r"^\s*>\s?", "", ln).strip())
        i += 1
    flush()
    return out


# ---------------------------------------------------------------- Word
def render_docx(content: str, title: str) -> bytes:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = Document()
    doc.core_properties.author = "Карина"
    doc.core_properties.title = title
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    st.paragraph_format.space_after = Pt(6)

    def mono(run, size):
        run.font.name, run.font.size = "Consolas", Pt(size)
        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Consolas")

    def runs(par, text, bold=False):
        for t, s in spans(text):
            for k, part in enumerate(t.split("\n")):
                if k:
                    par.add_run().add_break()
                r = par.add_run(part)
                r.bold = bold or s == "b"
                r.italic = s == "i"
                if s == "c":
                    mono(r, 10)

    def shade(el_pr, fill):
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        el_pr.append(shd)

    blocks = parse_md(content)
    for b in blocks:
        kind = b[0]
        if kind == "h":
            h = doc.add_heading(level=min(b[1] - 1, 3) if b[1] > 1 else 0)
            runs(h, b[2])
        elif kind == "p":
            runs(doc.add_paragraph(), b[1])
        elif kind == "list":
            for n, (lvl, text) in enumerate(b[2], 1):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.9 + 0.8 * lvl)
                p.paragraph_format.first_line_indent = Cm(-0.6)
                p.paragraph_format.space_after = Pt(2)
                p.add_run(f"{n}.\u00a0" if b[1] else "•\u00a0\u00a0")
                runs(p, text)
        elif kind == "code":
            for line in (b[1] or " ").split("\n"):
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.left_indent = Cm(0.4)
                shade(p._p.get_or_add_pPr(), "F2F2F2")
                mono(p.add_run(line or " "), 9.5)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == "table":
            rows = b[1]
            width = max(len(r) for r in rows)
            t = doc.add_table(rows=len(rows), cols=width)
            t.style = "Table Grid"
            for ri, row in enumerate(rows):
                for ci in range(width):
                    cell = t.cell(ri, ci)
                    runs(cell.paragraphs[0], row[ci] if ci < len(row) else "", bold=ri == 0)
                    if ri == 0:
                        shade(cell._tc.get_or_add_tcPr(), "E7EBF3")
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == "hr":
            p = doc.add_paragraph()
            pb = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            for k, v in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "999999")):
                bottom.set(qn("w:" + k), v)
            pb.append(bottom)
            p._p.get_or_add_pPr().append(pb)
    if not blocks:
        doc.add_paragraph("")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF
_FONT_SETS = [
    # (обычный, жирный, курсив, жирный курсив, моноширинный) — все с русскими буквами
    ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf", "consola.ttf"),
    ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Arial Bold Italic.ttf", "Courier New.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf", "DejaVuSansMono.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf", "LiberationMono-Regular.ttf"),
]


def _font_dirs() -> list[Path]:
    d = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        d.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    d += [Path(p) for p in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/truetype/liberation",
                            "/usr/share/fonts/liberation", "/Library/Fonts", "/System/Library/Fonts/Supplemental", str(Path.home() / ".fonts"))]
    return d


def _find_fonts() -> dict[str, Path]:
    """Первый набор шрифтов, у которого есть хотя бы обычное начертание."""
    for s in _FONT_SETS:
        for d in _font_dirs():
            if (d / s[0]).exists():
                return {k: d / n for k, n in zip(("r", "b", "i", "bi", "m"), s) if (d / n).exists()}
    raise FileError("для PDF не нашлось шрифта с русскими буквами (Arial или DejaVu).")


def render_pdf(content: str, title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    f = _find_fonts()
    for key, name in (("r", "KSans"), ("b", "KSans-B"), ("i", "KSans-I"), ("bi", "KSans-BI"), ("m", "KMono")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(f.get(key) or f["r"])))
    pdfmetrics.registerFontFamily("KSans", normal="KSans", bold="KSans-B", italic="KSans-I", boldItalic="KSans-BI")

    ink = colors.HexColor("#1b1b22")
    base = ParagraphStyle("b", fontName="KSans", fontSize=10.5, leading=15, textColor=ink, alignment=TA_LEFT, spaceAfter=6)
    heads = {n: ParagraphStyle(f"h{n}", parent=base, fontName="KSans-B", fontSize=s, leading=s * 1.25, spaceBefore=s * 0.6, spaceAfter=s * 0.4, keepWithNext=1)
             for n, s in ((1, 22), (2, 16), (3, 13), (4, 11.5))}
    code_st = ParagraphStyle("c", parent=base, fontName="KMono", fontSize=8.8, leading=12, backColor=colors.HexColor("#f2f2f5"), borderPadding=6, spaceBefore=4, spaceAfter=10)
    cell_st = ParagraphStyle("t", parent=base, fontSize=9, leading=12, spaceAfter=0)
    cell_b = ParagraphStyle("tb", parent=cell_st, fontName="KSans-B")

    def pdf_text(s: str) -> str:
        return re.sub("[\U00010000-\U0010ffff\u200d\ufe0f]", "", s)        # эмодзи в Arial/DejaVu нет — были бы пустые квадраты

    def mk(text: str) -> str:
        out = []
        for t, s in spans(pdf_text(text)):
            t = xml_escape(t).replace("\n", "<br/>")
            out.append({"b": f"<b>{t}</b>", "i": f"<i>{t}</i>", "c": f'<font name="KMono">{t}</font>'}.get(s, t))
        return "".join(out)

    story: list = []
    for b in parse_md(content):
        k = b[0]
        if k == "h":
            story.append(Paragraph(mk(b[2]), heads[min(b[1], 4)]))
        elif k == "p":
            story.append(Paragraph(mk(b[1]), base))
        elif k == "list":
            for n, (lvl, text) in enumerate(b[2], 1):
                st = ParagraphStyle("l", parent=base, leftIndent=18 + 16 * lvl, bulletIndent=4 + 16 * lvl, spaceAfter=2)
                story.append(Paragraph(mk(text), st, bulletText=f"{n}." if b[1] else "•"))
            story.append(Spacer(1, 4))
        elif k == "code":
            t = xml_escape(pdf_text(b[1])).replace("\n", "<br/>")
            t = re.sub(r"(?<=<br/>) +| {2,}", lambda m: "&nbsp;" * len(m.group(0)), t)
            story.append(Paragraph(t or "&nbsp;", code_st))
        elif k == "table":
            rows = b[1]
            width = max(len(r) for r in rows)
            data = [[Paragraph(mk(r[c] if c < len(r) else ""), cell_b if ri == 0 else cell_st) for c in range(width)] for ri, r in enumerate(rows)]
            weights = [min(40, max(6, max(len(r[c]) if c < len(r) else 0 for r in rows))) for c in range(width)]
            avail = A4[0] - 4 * cm
            tb = Table(data, colWidths=[avail * w / sum(weights) for w in weights], repeatRows=1)
            tb.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8bcc8")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7ebf3")),
                                    ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
            story += [tb, Spacer(1, 10)]
        elif k == "hr":
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#b8bcc8"), spaceBefore=6, spaceAfter=8))
    if not story:
        story.append(Spacer(1, 1))

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("KSans", 8)
        canvas.setFillColor(colors.HexColor("#888894"))
        canvas.drawCentredString(A4[0] / 2, 1.1 * cm, str(doc_.page))
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                      title=title, author="Карина").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ---------------------------------------------------------------- Excel
_SHEET_HEAD = re.compile(r"^\s*#{1,3}\s*(?:лист|sheet)\s*[:\-–]?\s*(.+?)\s*$", re.I | re.M)
_NUM = re.compile(r"^[+-]?\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?$|^[+-]?\d+(?:[.,]\d+)?$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_FORMULA_OK = re.compile(r"^=[A-Za-zА-Яа-я0-9_+\-*/^()., :;$<>=!\"'%&\[\]]+$")
_FORMULA_BAD = re.compile(r"HYPERLINK|WEBSERVICE|FILTERXML|\bCALL\b|REGISTER|EXEC|DDE|SHELL|\.\./|\\\\|https?:", re.I)


def _sniff(lines: list[str]) -> str:
    best, score = ",", 0.0
    for d in (";", "\t", ",", "|"):
        try:
            counts = [len(r) for r in csv.reader(lines[:15], delimiter=d) if r]
        except csv.Error:
            continue
        if counts and max(counts) > 1:
            modal = max(set(counts), key=counts.count)
            s = modal * (counts.count(modal) / len(counts)) if modal > 1 else 0
            if s > score + 1e-9:
                best, score = d, s
    return best


def _grid(text: str) -> list[list[str]]:
    lines = [l for l in clean(text).split("\n") if l.strip() and not re.match(r"^\s*```", l)]
    if not lines:
        return []
    if sum("|" in l for l in lines) >= max(2, len(lines) // 2):
        return [_cells(l) for l in lines if not _TABLE_SEP.match(l)]
    return [r for r in csv.reader(lines, delimiter=_sniff(lines), skipinitialspace=True)]


def _value(s: str):
    """Строка ячейки → (значение, формат). Числа и даты — числами и датами; опасные формулы — просто текстом."""
    s = s.strip()
    if s.startswith("=") and len(s) > 1:
        return (s, None) if _FORMULA_OK.match(s) and not _FORMULA_BAD.search(s) else (s, "text")
    if s[:1] in ("+", "-", "@") and not _NUM.match(s) and re.match(r"^[+\-@]\s*(?:cmd|powershell|\||@|=)", s, re.I):
        return s, "text"
    if _NUM.match(s) and not (len(s) > 1 and s[0] == "0" and s[1].isdigit()):
        n = s.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        return (float(n) if "." in n else int(n)), None
    if re.match(r"^[+-]?\d+(?:[.,]\d+)?\s*%$", s):
        return float(s.rstrip("% ").replace(",", ".")) / 100, "0.##%"
    m = _DATE.match(s)
    if m:
        try:
            return datetime.date(int(m[1]), int(m[2]), int(m[3])), "DD.MM.YYYY"
        except ValueError:
            pass
    return s, None


def render_xlsx(content: str, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    text = clean(content)
    heads = list(_SHEET_HEAD.finditer(text))
    parts: list[tuple[str, str]] = []
    if heads:
        if text[:heads[0].start()].strip():
            parts.append(("Лист1", text[:heads[0].start()]))
        for k, m in enumerate(heads):
            parts.append((m[1], text[m.end():heads[k + 1].start() if k + 1 < len(heads) else len(text)]))
    else:
        parts.append(("Лист1", text))

    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for name, body in parts[:20]:
        nm = re.sub(r"[\[\]:*?/\\]", " ", name).strip("' ")[:31] or "Лист"
        base_nm, k = nm, 2
        while nm.lower() in used:
            nm = f"{base_nm[:28]} {k}"
            k += 1
        used.add(nm.lower())
        ws = wb.create_sheet(nm)
        rows = _grid(body)[:20000]
        widths: dict[int, int] = {}
        for ri, row in enumerate(rows, 1):
            for ci, raw in enumerate(row[:100], 1):
                if not raw.strip():
                    continue
                v, fmt = _value(raw)
                c = ws.cell(row=ri, column=ci)
                if fmt == "text":
                    c.value, c.data_type = v, "s"
                else:
                    c.value = v
                    if fmt:
                        c.number_format = fmt
                if isinstance(v, str) and not v.startswith("="):
                    widths[ci] = max(widths.get(ci, 0), min(60, max(len(x) for x in v.split("\n"))))
                else:
                    widths[ci] = max(widths.get(ci, 0), min(14, len(str(raw))))
                if ri == 1:
                    c.font = Font(bold=True)
                    c.fill = PatternFill("solid", fgColor="E7EBF3")
                    c.alignment = Alignment(wrap_text=True, vertical="center")
                elif isinstance(v, str) and len(v) > 40:
                    c.alignment = Alignment(wrap_text=True, vertical="top")
        for ci, w in widths.items():
            ws.column_dimensions[get_column_letter(ci)].width = max(8, w + 2)
        if len(rows) > 1:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    wb.properties.creator, wb.properties.title = "Карина", title
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- SVG (очистка по белому списку)
SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
_SVG_TAGS = {"svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
             "lineargradient", "radialgradient", "stop", "clippath", "marker", "pattern", "symbol"}
_SVG_ATTRS = set("""x y x1 y1 x2 y2 cx cy r rx ry fx fy width height d points transform viewbox preserveaspectratio version id offset
fill fill-opacity fill-rule stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray stroke-dashoffset stroke-opacity stroke-miterlimit
opacity font-family font-size font-weight font-style text-anchor text-decoration dominant-baseline alignment-baseline letter-spacing dx dy rotate
textlength lengthadjust stop-color stop-opacity gradientunits gradienttransform spreadmethod markerwidth markerheight markerunits refx refy orient
clip-path clippathunits marker-start marker-mid marker-end style patternunits patterntransform visibility display""".split())
_STYLE_OK = re.compile(r"^[\w\s:;#.,%()\-'\"/!]*$")
_CAMEL = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio", "gradientunits": "gradientUnits", "gradienttransform": "gradientTransform",
          "spreadmethod": "spreadMethod", "markerwidth": "markerWidth", "markerheight": "markerHeight", "markerunits": "markerUnits", "refx": "refX", "refy": "refY",
          "textlength": "textLength", "lengthadjust": "lengthAdjust", "clippathunits": "clipPathUnits", "patternunits": "patternUnits", "patterntransform": "patternTransform"}
_CAMEL_TAG = {"lineargradient": "linearGradient", "radialgradient": "radialGradient", "clippath": "clipPath"}


def _attr_ok(name: str, value: str) -> bool:
    v = value.strip().lower()
    if "javascript:" in v or "data:" in v or "@import" in v or "expression(" in v or "&#" in v:
        return False
    for m in re.finditer(r"url\(\s*['\"]?([^)'\"]*)", v):
        if not m.group(1).startswith("#"):
            return False
    return bool(_STYLE_OK.match(value)) if name == "style" else True


def sanitize_svg(src: str) -> str:
    s = clean(src).strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    s = re.sub(r"<\?xml[^>]*\?>", "", s, count=1)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", s, re.I) or "<?" in s:         # внешние сущности и «бомбы» из сущностей
        raise FileError("SVG содержит недопустимые объявления.")
    a, b = s.lower().find("<svg"), s.lower().rfind("</svg>")
    if a < 0 or b < 0:
        raise FileError("в SVG нет элемента <svg>…</svg>.")
    s = s[a:b + 6]
    s = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", s)
    try:
        root = ET.fromstring(s)
    except ET.ParseError as e:
        raise FileError(f"SVG записан с ошибкой ({e}).")

    def local(t: str) -> str:
        if t.startswith("{"):
            ns, _, n = t[1:].partition("}")
            if ns != SVG_NS:
                raise FileError("SVG содержит чужие элементы.")
            return n
        return t

    def walk(el, depth=0):
        if depth > 40:
            raise FileError("SVG слишком глубоко вложен.")
        tag = local(el.tag).lower()
        if tag not in _SVG_TAGS:
            return None
        new = ET.Element(_CAMEL_TAG.get(tag, tag))
        for k, v in el.attrib.items():
            if k.startswith("{"):
                if k == f"{{{XML_NS}}}space" and v in ("preserve", "default"):
                    new.set(f"{{{XML_NS}}}space", v)
                elif k == f"{{{XLINK}}}href" and v.startswith("#"):
                    new.set("href", v)
                continue
            nm = k.lower()
            if nm == "href":
                if v.startswith("#"):
                    new.set("href", v)
                continue
            if nm in _SVG_ATTRS and _attr_ok(nm, v):
                new.set(_CAMEL.get(nm, nm), v)
        if tag in ("text", "tspan", "title", "desc"):
            new.text = el.text
        last = None
        for ch in el:
            c = walk(ch, depth + 1)
            if c is not None:
                new.append(c)
                last = c
            if tag in ("text", "tspan") and ch.tail:
                if last is not None:
                    last.tail = (last.tail or "") + ch.tail
                else:
                    new.text = (new.text or "") + ch.tail
        return new

    if local(root.tag).lower() != "svg":
        raise FileError("корневой элемент должен быть <svg>.")
    out = walk(root)
    if out.get("viewBox") is None and not (out.get("width") and out.get("height")):
        out.set("viewBox", "0 0 800 600")
    ET.register_namespace("", SVG_NS)
    body = ET.tostring(out, encoding="unicode")
    body = body.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1) if "xmlns=" not in body.split(">", 1)[0] else body
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


# ---------------------------------------------------------------- текст и код
def _unfence(body: str) -> str:
    s = clean(body).strip("\n")
    m = re.match(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", s, re.S)
    return m.group(1) if m else s


def _check_text(ext: str, text: str) -> str:
    """Предупреждение о явной ошибке в файле (файл всё равно создаётся). Код не выполняется, только проверяется."""
    try:
        if ext == "json":
            json.loads(text)
        elif ext == "py":
            compile(text, "file.py", "exec", dont_inherit=True)
    except (ValueError, SyntaxError) as e:
        line = getattr(e, "lineno", None)
        return f"в файле есть синтаксическая ошибка{f' (строка {line})' if line else ''}"
    return ""


def render(ext: str, body: str, title: str) -> tuple[bytes, str]:
    """→ (байты файла, предупреждение)."""
    if ext == "docx":
        return render_docx(body, title), ""
    if ext == "pdf":
        return render_pdf(body, title), ""
    if ext == "xlsx":
        return render_xlsx(body, title), ""
    if ext == "svg":
        return sanitize_svg(body).encode("utf-8"), ""
    text = _unfence(body)
    if not text.strip():
        raise FileError("файл получился пустым.")
    text = text.rstrip("\n") + "\n"
    return text.encode("utf-8-sig" if ext == "csv" else "utf-8"), _check_text(ext, text)


# ---------------------------------------------------------------- сервис
def human_size(n: int) -> str:
    return f"{n} Б" if n < 1024 else f"{n / 1024:.0f} КБ" if n < 1 << 20 else f"{n / (1 << 20):.1f} МБ"


class FileService:
    def __init__(self, folder: Path | None = None, opener=None):
        self._folder = folder
        self._opener = opener          # для проверок: вместо запуска программ получает команду

    @property
    def folder(self) -> Path:
        return self._folder or documents_dir() / FOLDER_NAME

    # ---------- создание ----------
    def create(self, name: str, body: str, complete: bool = True) -> dict:
        stem, ext = safe_name(name)
        if len(body) > MAX_CHARS:
            raise FileError("содержимое слишком большое.")
        data, warn = render(ext, body, stem)
        if not complete:
            warn = (warn + "; " if warn else "") + "ответ оборвался, файл может быть неполным"
        folder = self.folder
        folder.mkdir(parents=True, exist_ok=True)
        root = folder.resolve()
        for n in range(1, 1000):
            fname = f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}"
            path = folder / fname
            if path.resolve().parent != root:
                raise FileError("недопустимое имя файла.")
            try:
                with open(path, "xb") as f:               # «x»: существующий файл не перезаписывается ни при каких условиях
                    f.write(data)
                break
            except FileExistsError:
                continue
        else:
            raise FileError("слишком много файлов с таким именем.")
        return {"name": fname, "kind": KINDS[ext], "size": len(data), "warning": warn}

    def create_all(self, reqs: list[Request]) -> tuple[list[dict], list[str]]:
        made, errors = [], []
        for r in reqs[:MAX_FILES]:
            try:
                made.append(self.create(r.name, r.body, r.complete))
            except FileError as e:
                errors.append(f"Файл «{r.name}» не создан: {e}")
            except Exception as e:                    # сломанная библиотека или диск — понятным текстом, без падения чата
                errors.append(f"Файл «{r.name}» не создан: {e.__class__.__name__}: {e}")
        if len(reqs) > MAX_FILES:
            errors.append(f"За один раз создаётся не больше {MAX_FILES} файлов — остальные пропущены.")
        return made, errors

    # ---------- доступ из окна ----------
    def resolve(self, name: str) -> Path:
        """Путь к файлу из папки Карины. Принимает только имя файла: пути, «..» и чужие типы отвергаются."""
        if not isinstance(name, str) or not name or name != name.replace("\\", "/").split("/")[-1] or name in (".", ".."):
            raise FileError("Недопустимое имя файла.")
        if name.rpartition(".")[2].lower() not in KINDS:
            raise FileError("Этот тип файла Карина не открывает.")
        p = self.folder / name
        if not p.is_file() or p.resolve().parent != self.folder.resolve():
            raise FileError("Файл не найден — возможно, его переместили или удалили.")
        return p

    def _launch(self, cmd):
        if self._opener:
            return self._opener(cmd)
        if isinstance(cmd, str):
            if sys.platform == "win32":
                os.startfile(cmd)                          # noqa: S606 — путь уже проверен в resolve()
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", cmd])
        else:
            subprocess.Popen(cmd)

    def open(self, name: str):
        p = self.resolve(name)
        if p.suffix[1:].lower() in SHELL_OPEN or not (sys.platform == "win32" or self._opener):
            self._launch(str(p))
        else:
            self._launch(["notepad.exe", str(p)])          # код и текст только показываем, не запускаем

    def reveal(self, name: str):
        p = self.resolve(name)
        self._launch(["explorer", f"/select,{p}"] if sys.platform == "win32" or self._opener else str(p.parent))

    def open_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self._launch(str(self.folder))

    def preview_svg(self, name: str) -> str:
        """SVG для показа в чате: data-URI (картинка в <img> не выполняет скрипты, а файл уже очищен)."""
        import base64
        p = self.resolve(name)
        if p.suffix.lower() != ".svg" or p.stat().st_size > 2_000_000:
            raise FileError("Предпросмотр только для SVG.")
        return "data:image/svg+xml;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


# ---------------------------------------------------------------- описание формата для модели
def prompt_section() -> str:
    return (
        "\nСоздание файлов. Сама ты файлы не создаёшь: оформи содержимое в блок, а программа Карина создаст файл в папке «Документы\\Карина»:\n"
        "[[ФАЙЛ: имя.расширение]]\nсодержимое\n[[/ФАЙЛ]]\n"
        "• docx и pdf — содержимое в Markdown: # название, ## раздел, списки, **жирный**, таблицы | колонка | колонка |.\n"
        "• xlsx — таблица в CSV с разделителем «;», первая строка — заголовки; числа без единиц в ячейке; несколько листов — строка «# Лист: Название» перед каждым; формула начинается с =.\n"
        "• txt, md, csv, json и код (py, js, html, css, sql и т. п.) — просто текст файла, без обёртки ```.\n"
        "• svg — векторная схема или иллюстрация: один элемент <svg> с viewBox, русские подписи (font-family=\"sans-serif\"), блоки не налезают друг на друга; "
        "без скриптов, стилей-классов, внешних ссылок и вставленных картинок.\n"
        "Правила: создавай файл только если просят именно файл, документ, схему или «сохрани»; обычные ответы, таблицы и короткие фрагменты кода пиши в чат. "
        "Имя короткое и осмысленное, без папок. Один блок — один файл, не больше 4. Вне блока — одно-два предложения о результате, без повторения содержимого. "
        "Не говори, что файл создан, если не оформила блок."
    )
