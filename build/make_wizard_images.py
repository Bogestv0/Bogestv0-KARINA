"""Рисует картинки мастера установки (build/wizard_*.bmp) в стиле окна Карины. Запуск: python build/make_wizard_images.py
Готовые BMP лежат в репозитории, этот скрипт нужен только чтобы перерисовать их."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
BG, BG2, ACC, FG, MUTED = (19, 19, 24), (26, 28, 40), (106, 179, 243), (242, 242, 246), (154, 156, 174)


def font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def side(w, h):
    k = w / 164
    img = Image.new("RGB", (w, h), BG)
    px = img.load()
    for y in range(h):                                   # мягкий синий свет снизу
        t = max(0.0, (y / h - 0.45) / 0.55)
        for x in range(w):
            px[x, y] = tuple(int(BG[i] + (BG2[i] - BG[i]) * t + (ACC[i] - BG[i]) * 0.10 * t * t) for i in range(3))
    logo = Image.open(ROOT / "ui" / "logo.png").convert("RGBA").resize((int(92 * k), int(92 * k)), Image.LANCZOS)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((w / 2 - 70 * k, 70 * k, w / 2 + 70 * k, 210 * k), fill=(106, 179, 243, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(26 * k))
    img.paste(glow, (0, 0), glow)
    img.paste(logo, (int((w - logo.width) / 2), int(72 * k)), logo)
    d = ImageDraw.Draw(img)
    for text, f, col, y in (("Bogestv0", font(int(13 * k)), MUTED, 182), ("KARINA", font(int(22 * k), True), ACC, 200)):
        tw = d.textlength(text, font=f)
        d.text(((w - tw) / 2, y * k), text, font=f, fill=col)
    for i, hgt in enumerate((10, 18, 28, 18, 10)):       # столбики звука, как в логотипе
        bx = w / 2 + (i - 2) * 11 * k
        d.rounded_rectangle((bx - 2.2 * k, h - 40 * k - hgt * k / 2, bx + 2.2 * k, h - 40 * k + hgt * k / 2), radius=2 * k, fill=ACC if i == 2 else (46, 112, 175))
    return img


def small(w):
    img = Image.new("RGB", (w, w), BG)
    logo = Image.open(ROOT / "ui" / "logo.png").convert("RGBA").resize((int(w * 0.84), int(w * 0.84)), Image.LANCZOS)
    img.paste(logo, ((w - logo.width) // 2, (w - logo.height) // 2), logo)
    return img


if __name__ == "__main__":
    side(164, 314).save(OUT / "wizard_side.bmp")
    side(328, 628).save(OUT / "wizard_side_2x.bmp")
    small(55).save(OUT / "wizard_small.bmp")
    small(110).save(OUT / "wizard_small_2x.bmp")
    print("ok")
