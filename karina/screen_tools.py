"""Инструменты вкладки «Инструменты» острова: снимок области, текст с экрана, QR, пипетка, чистый текст,
запись экрана, раскладка RU/EN и свои инструменты. Окон здесь нет: всё, что не требует Windows, проверяется тестами,
а системные вызовы (снимок, буфер, WinRT, ffmpeg) собраны в отдельных функциях и на других ОС возвращают понятную ошибку."""
from __future__ import annotations
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ROOT

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0
MAX_CUSTOM = 12


class ToolError(Exception):
    """Ошибка с готовым текстом для человека."""


# ---------------------------------------------------------------- папки
def pictures_dir() -> Path:
    return Path.home() / "Pictures" / "Karina"


def videos_dir() -> Path:
    return Path.home() / "Videos" / "Karina"


def _stamp(prefix: str, ext: str, d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d / time.strftime(f"{prefix}-%Y%m%d-%H%M%S.{ext}")


# ---------------------------------------------------------------- чистый текст
_ZERO = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SPACES = {ord(c): " " for c in "          "}


def clean_text(s: str) -> str:
    """Текст без «хвостов» форматирования: неразрывные пробелы, невидимые знаки, лишние пробелы в конце строк и пустые строки."""
    s = str(s or "").translate(_ZERO).translate(_SPACES).replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------------------------------------------------------- цвет
def color_info(r: int, g: int, b: int) -> dict:
    r, g, b = (max(0, min(255, int(v))) for v in (r, g, b))
    return {"r": r, "g": g, "b": b, "hex": f"#{r:02X}{g:02X}{b:02X}", "rgb": f"rgb({r}, {g}, {b})"}


# ---------------------------------------------------------------- QR
def qr_png(text: str, scale: int = 10, border: int = 3) -> bytes:
    """QR-код как PNG (чёрное на белом). Нужен пакет segno."""
    text = str(text or "")
    if not text.strip():
        raise ToolError("Нечего кодировать: текст пустой.")
    if len(text) > 1500:
        raise ToolError("Текст слишком длинный для QR-кода (больше 1500 знаков).")
    try:
        import segno
    except Exception:
        raise ToolError("Не установлен пакет segno (pip install segno).")
    q = segno.make(text, error="m", micro=False)
    out = io.BytesIO()
    q.save(out, kind="png", scale=int(scale), border=int(border), dark="#000000", light="#ffffff")
    return out.getvalue()


def png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def qr_read(img) -> list[str]:
    """Все QR-коды на изображении PIL (на снимке экрана их может быть несколько). Нужен пакет zxing-cpp."""
    try:
        import zxingcpp
    except Exception:
        raise ToolError("Не установлен пакет zxing-cpp (pip install zxing-cpp).")
    out: list[str] = []
    for r in zxingcpp.read_barcodes(img.convert("RGB")):
        t = (r.text or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def is_url(s: str) -> bool:
    return bool(re.match(r"^(https?://|www\.)\S+$", str(s or "").strip(), re.I))


# ---------------------------------------------------------------- снимок экрана
def grab_screen():
    """Весь виртуальный рабочий стол (все мониторы) → (изображение PIL, (x, y) его левого верхнего угла в экранных пикселях)."""
    from PIL import ImageGrab
    ox = oy = 0
    if IS_WIN:
        import ctypes
        u = ctypes.WinDLL("user32")
        ox, oy = int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))       # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        try:
            return ImageGrab.grab(all_screens=True), (ox, oy)
        except TypeError:
            pass
    return ImageGrab.grab(), (ox, oy)


def image_data_url(img, fast: bool = True) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", compress_level=1 if fast else 6)
    return png_data_url(buf.getvalue())


def crop_fraction(img, rect, min_px: int = 4):
    """rect = (x0, y0, x1, y1) в долях 0..1 от размера изображения → вырезанный кусок. Слишком маленькая рамка — ошибка."""
    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
    except Exception:
        raise ToolError("Область не выбрана.")
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    w, h = img.size
    box = (int(round(x0 * w)), int(round(y0 * h)), int(round(x1 * w)), int(round(y1 * h)))
    if box[2] - box[0] < min_px or box[3] - box[1] < min_px:
        raise ToolError("Область слишком маленькая.")
    return img.crop(box)


def save_image(img, prefix: str = "area") -> str:
    p = _stamp(prefix, "png", pictures_dir())
    img.convert("RGB").save(p)
    return str(p)


def pixel_at(img, x: int, y: int) -> dict:
    w, h = img.size
    r, g, b = img.convert("RGB").getpixel((max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))))
    return color_info(r, g, b)


# ---------------------------------------------------------------- буфер обмена
def clip_get_text() -> str:
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception:
        return ""


def clip_set_text(text: str) -> bool:
    try:
        import pyperclip
        pyperclip.copy(str(text))
        return True
    except Exception:
        return False


def clip_set_image(img) -> bool:
    """Картинка в буфер обмена (Windows, формат DIB)."""
    if not IS_WIN:
        return False
    import ctypes
    from ctypes import wintypes
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]                                  # без заголовка файла BMP
    u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HANDLE
    k.GlobalLock.argtypes, k.GlobalLock.restype = [wintypes.HANDLE], ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HANDLE]
    k.GlobalFree.argtypes = [wintypes.HANDLE]
    u.OpenClipboard.argtypes, u.SetClipboardData.argtypes = [wintypes.HWND], [wintypes.UINT, wintypes.HANDLE]
    u.SetClipboardData.restype = wintypes.HANDLE
    h = k.GlobalAlloc(0x0002, len(dib))                        # GMEM_MOVEABLE
    if not h:
        return False
    p = k.GlobalLock(h)
    ctypes.memmove(p, dib, len(dib))
    k.GlobalUnlock(h)
    ok = False
    for _ in range(10):                                        # буфер может быть занят другой программой
        if u.OpenClipboard(None):
            try:
                u.EmptyClipboard()
                ok = bool(u.SetClipboardData(8, h))            # CF_DIB
            finally:
                u.CloseClipboard()
            break
        time.sleep(0.05)
    if not ok:
        k.GlobalFree(h)
    return ok


# ---------------------------------------------------------------- текст с экрана (распознавание средствами Windows)
OCR_LANGS = ("ru", "en-US")


def _word_score(text: str) -> int:
    """Чем больше «чистых» слов (только кириллица или только латиница), тем лучше язык подошёл. Смесь букв в одном слове — признак ошибки."""
    score = 0
    for w in re.findall(r"[^\W\d_]+", text, re.U):
        cyr = len(re.findall(r"[а-яёА-ЯЁ]", w))
        lat = len(re.findall(r"[a-zA-Z]", w))
        if cyr and lat:
            score -= 2 * len(w)
        elif len(w) > 1:
            score += len(w)
    return score


def pick_text(candidates: list[str]) -> str:
    """Из нескольких вариантов распознавания (разные языки) берём тот, что больше похож на настоящий текст."""
    cands = [c for c in candidates if c and c.strip()]
    if not cands:
        return ""
    return max(cands, key=_word_score).strip()


def ocr_image(img) -> str:
    if not IS_WIN:
        raise ToolError("Распознавание текста работает только в Windows.")
    try:
        import asyncio
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except Exception as e:
        raise ToolError("Нет пакетов распознавания (winrt-Windows.Media.Ocr и др.). Обновите Карину или выполните pip install -r requirements.txt. "
                        f"({e.__class__.__name__})")
    engines = []
    for tag in OCR_LANGS:
        try:
            e = OcrEngine.try_create_from_language(Language(tag))
            if e is not None:
                engines.append(e)
        except Exception:
            pass
    if not engines:
        e = OcrEngine.try_create_from_user_profile_languages()
        if e is not None:
            engines.append(e)
    if not engines:
        raise ToolError("В Windows не установлены языковые пакеты для распознавания. Добавьте русский и английский: "
                        "Параметры → Время и язык → Язык и регион.")
    img = img.convert("RGB")
    lim = int(getattr(OcrEngine, "max_image_dimension", 10000) or 10000)
    if max(img.size) > lim:
        k = lim / max(img.size)
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))))
    elif max(img.size) < 600:                                   # мелкий текст читается лучше, если увеличить
        img = img.resize((img.width * 2, img.height * 2))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = buf.getvalue()

    async def go():
        stream = InMemoryRandomAccessStream()
        w = DataWriter(stream)
        w.write_bytes(data)
        await w.store_async()
        w.detach_stream()
        stream.seek(0)
        bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        outs = []
        for eng in engines:
            r = await eng.recognize_async(bmp)
            outs.append("\n".join(line.text for line in r.lines))
        return outs

    return pick_text(asyncio.run(go()))


def ocr_langs_label() -> str:
    return "русский, английский"


# ---------------------------------------------------------------- запись экрана (ffmpeg)
def find_ffmpeg() -> str | None:
    for cand in (ROOT / "engine" / "ffmpeg.exe", ROOT / "ffmpeg.exe"):
        if cand.exists():
            return str(cand)
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_cmd(ff: str, out: str, fps: int = 30, win: bool = True) -> list[str]:
    src = ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1", "-i", "desktop"] if win else ["-f", "x11grab", "-framerate", str(fps), "-i", os.environ.get("DISPLAY", ":0")]
    return [ff, "-y", "-hide_banner", "-loglevel", "error", *src, "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]


def fmt_time(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class Recorder:
    """Запись экрана в Видео\\Karina. Процесс ffmpeg останавливается командой «q» (так файл закрывается корректно)."""

    def __init__(self, popen=subprocess.Popen, ffmpeg=None, out_dir: Path | None = None):
        self._popen, self._ffmpeg, self._dir = popen, ffmpeg, out_dir
        self.proc = None
        self.path = ""
        self.t0 = 0.0

    @property
    def on(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def seconds(self) -> float:
        return time.time() - self.t0 if self.on else 0.0

    def status(self) -> dict:
        return {"on": self.on, "sec": int(self.seconds()), "t0": self.t0 if self.on else 0, "path": self.path}

    def start(self) -> dict:
        if self.on:
            return self.status()
        ff = self._ffmpeg or find_ffmpeg()
        if not ff:
            raise ToolError("Для записи нужен ffmpeg: pip install imageio-ffmpeg или положите ffmpeg.exe рядом с Кариной.")
        out = str(_stamp("record", "mp4", self._dir or videos_dir()))
        self.proc = self._popen(ffmpeg_cmd(ff, out, win=IS_WIN), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=NO_WINDOW if IS_WIN else 0)
        self.path, self.t0 = out, time.time()
        time.sleep(0.6)                                          # если ffmpeg сразу упал, скажем почему
        if self.proc.poll() is not None:
            err = self._err()
            self.proc = None
            raise ToolError("Не получилось начать запись" + (": " + err if err else "."))
        return self.status()

    def _err(self) -> str:
        try:
            raw = self.proc.stderr.read() if self.proc and self.proc.stderr else b""
            return (raw or b"").decode("utf-8", "ignore").strip().splitlines()[-1][:200] if raw else ""
        except Exception:
            return ""

    def stop(self) -> dict:
        p, path, secs = self.proc, self.path, self.seconds()
        self.proc = None
        if p is None:
            return {"on": False, "path": "", "sec": 0}
        try:
            if p.poll() is None:
                try:
                    p.stdin.write(b"q\n")
                    p.stdin.flush()
                except Exception:
                    pass
                try:
                    p.wait(timeout=10)
                except Exception:
                    p.terminate()
        except Exception:
            pass
        ok = Path(path).exists() and Path(path).stat().st_size > 0
        return {"on": False, "path": path if ok else "", "sec": int(secs)}


# ---------------------------------------------------------------- раскладка клавиатуры и окно под островом
LAYOUTS = {"RU": 0x0419, "EN": 0x0409}


def layout_name(lang_id: int) -> str:
    return "RU" if (lang_id & 0xFFFF) == 0x0419 else "EN" if (lang_id & 0xFF) == 0x09 else f"{lang_id & 0xFFFF:04X}"


def next_layout(cur: str) -> str:
    return "EN" if cur == "RU" else "RU"


class Focus:
    """Запоминает последнее чужое окно переднего плана: остров не должен брать фокус, но если взял, вставка и раскладка идут в то окно."""

    def __init__(self):
        self.last = 0

    def _fg(self):
        import ctypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetForegroundWindow.restype = ctypes.c_void_p
        h = u.GetForegroundWindow()
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        tid = u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return u, h, int(pid.value), int(tid)

    def poll(self) -> int:
        if not IS_WIN:
            return 0
        try:
            u, h, pid, _ = self._fg()
            if h and pid != os.getpid():
                self.last = int(h)
        except Exception:
            pass
        return self.last

    def restore(self):
        """Вернуть фокус чужому окну (если остров его забрал)."""
        if not IS_WIN:
            return
        try:
            import ctypes
            u, h, pid, _ = self._fg()
            if pid == os.getpid() and self.last:
                u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                u.SetForegroundWindow(self.last)
                time.sleep(0.12)
        except Exception:
            pass

    def layout_get(self) -> str:
        if not IS_WIN:
            return "RU"
        try:
            import ctypes
            self.poll()
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            u.GetKeyboardLayout.restype = ctypes.c_void_p
            u.GetKeyboardLayout.argtypes = [ctypes.c_ulong]
            tid = u.GetWindowThreadProcessId(self.last, None) if self.last else 0
            return layout_name(int(u.GetKeyboardLayout(tid) or 0))
        except Exception:
            return "RU"

    def toggle_layout(self) -> str:
        """Переключить раскладку окна, где человек работает. Сначала просим окно сообщением (так работают обычные программы), а если раскладка не
        поменялась (браузеры на Chromium это сообщение игнорируют), нажимаем Win+Пробел: Windows сама переключает ввод в окне переднего плана."""
        if not IS_WIN:
            return "RU"
        self.poll()
        cur = self.layout_get()
        want = next_layout(cur)
        self.layout_set(want)
        for _ in range(6):
            time.sleep(0.05)
            if self.layout_get() == want:
                return want
        try:
            self.restore()
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.hotkey("win", "space")
            time.sleep(0.3)
        except Exception:
            pass
        return self.layout_get()

    def layout_set(self, name: str) -> bool:
        if not IS_WIN or name not in LAYOUTS or not self.last:
            return False
        try:
            import ctypes
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.LoadKeyboardLayoutW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
            u.LoadKeyboardLayoutW.restype = ctypes.c_void_p
            u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
            hkl = u.LoadKeyboardLayoutW(f"{LAYOUTS[name]:08X}", 1)           # KLF_ACTIVATE
            return bool(hkl and u.PostMessageW(self.last, 0x0050, 0, hkl))   # WM_INPUTLANGCHANGEREQUEST
        except Exception:
            return False


def paste_clean(focus: Focus | None = None) -> str:
    """Берёт текст из буфера, чистит и вставляет в окно под островом (Ctrl+V). Возвращает вставленный текст."""
    raw = clip_get_text()
    if not raw.strip():
        raise ToolError("В буфере обмена нет текста.")
    text = clean_text(raw)
    if not clip_set_text(text):
        raise ToolError("Не удалось записать в буфер обмена.")
    if focus is not None:
        focus.restore()
    import pyautogui
    pyautogui.FAILSAFE = False
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    return text


# ---------------------------------------------------------------- свои инструменты («Ещё»)
def clean_custom(items) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name, cmd = " ".join(str(it.get("name") or "").split())[:24], str(it.get("cmd") or "").strip()[:300]
        if name and cmd:
            out.append({"name": name, "cmd": cmd})
    return out[:MAX_CUSTOM]


def custom_kind(cmd: str) -> str:
    """url — открыть в браузере, path — открыть файл/папку/программу, say — сказать Карине как команду."""
    c = str(cmd or "").strip().strip('"')
    if is_url(c):
        return "url"
    if re.match(r"^([a-zA-Z]:[\\/]|\\\\|~[\\/])", c) or (c and os.path.exists(os.path.expanduser(c))):
        return "path"
    return "say"
