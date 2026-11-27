"""Выбор, сделанный в установщике (права администратора, виджет-остров, профиль).

Установщик не лезет в config.json сам (редактировать JSON из скрипта Inno Setup хрупко): он кладёт рядом с настройками маленький файл
install_options.json, а Карина при первом запуске переносит его в настройки и удаляет. Файл можно написать и вручную:
  {"ui": {"island": true, "admin": true}, "profile": {"name": "Анна", "city": "Омск", "birth_day": 5, "birth_month": 3, "birth_year": 1995}}
Берутся только известные поля и только осмысленные значения: ошибка в файле не мешает запуску."""
from __future__ import annotations
import json
from pathlib import Path

FILE = "install_options.json"
UI_KEYS = {"island": bool, "admin": bool, "background": bool}
PROFILE_KEYS = ("name", "city", "birth_day", "birth_month", "birth_year")


def read(base: Path) -> dict:
    p = Path(base) / FILE
    try:
        raw = json.loads(p.read_text("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def build_patch(raw: dict, current_profile: dict | None = None) -> dict:
    patch: dict = {}
    ui = {k: bool(v) for k, v in (raw.get("ui") or {}).items() if k in UI_KEYS} if isinstance(raw.get("ui"), dict) else {}
    if ui:
        patch["ui"] = ui
    pr = raw.get("profile")
    if isinstance(pr, dict):
        out: dict = {}
        cur = current_profile or {}
        for k in PROFILE_KEYS:
            v = pr.get(k)
            if k in ("name", "city"):
                v = str(v or "").strip()
                if v and not cur.get(k):                      # уже заполненное в программе (например, при переустановке) не затираем
                    out[k] = v[:60]
            else:
                try:
                    n = int(v or 0)
                except (TypeError, ValueError):
                    n = 0
                if n and not cur.get(k):
                    out[k] = n
        if out:
            patch["profile"] = out
    return patch


def apply(cfg) -> bool:
    """Переносит выбор установщика в настройки. True — что-то применили."""
    base = Path(cfg.path).parent
    raw = read(base)
    if not raw:
        (base / FILE).unlink(missing_ok=True)          # пустой или битый файл не оставляем: иначе он разбирался бы при каждом запуске
        return False
    try:
        patch = build_patch(raw, dict(cfg.get("profile") or {}))
        if patch:
            cfg.update(patch)
    finally:
        (base / FILE).unlink(missing_ok=True)
    return bool(patch)
