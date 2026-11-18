"""Кладёт движок VPN (sing-box) в vpn_engine/ — установщик по желанию человека ставит его сразу, без скачивания в настройках.
Версия и контрольная сумма берутся из karina/tunnel.py (те же, что у скачивания внутри программы). Запуск из корня: python build/fetch_vpn_engine.py"""
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from karina.tunnel import SINGBOX_SHA256, SINGBOX_URL, SINGBOX_VERSION, SINGBOX_ZIP, UA

dest = ROOT / "vpn_engine"
dest.mkdir(exist_ok=True)
zpath = dest / SINGBOX_ZIP
print("скачиваю", SINGBOX_URL)
with urllib.request.urlopen(urllib.request.Request(SINGBOX_URL, headers=UA), timeout=120) as r:
    data = r.read()
if hashlib.sha256(data).hexdigest() != SINGBOX_SHA256:
    sys.exit("Контрольная сумма sing-box не совпала — не принимаю")
zpath.write_bytes(data)
want = f"sing-box-{SINGBOX_VERSION}-windows-amd64/sing-box.exe"
with zipfile.ZipFile(zpath) as z:
    (dest / "sing-box.exe").write_bytes(z.read(want))
(dest / "VERSION.txt").write_text(SINGBOX_VERSION + "\n", "utf-8")
zpath.unlink()
print("sing-box", SINGBOX_VERSION, (dest / "sing-box.exe").stat().st_size // 1_000_000, "МБ")
