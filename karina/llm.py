"""Необязательное понимание свободных фраз через Claude (только онлайн). Без ключа/сети не используется."""
from __future__ import annotations
import json, os, re, urllib.request
from .actions import REGISTRY


def ask(cfg, text: str, app_names: list[str]) -> dict | None:
    c = cfg["llm"]
    key = c.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not (c.get("enabled") and key):
        return None
    cmds = "\n".join(f'- {x.name}: {x.desc}' + (f' (аргументы: {x.llm_args})' if x.llm_args else '') for x in REGISTRY)
    system = (
        "Ты — модуль понимания команд голосового ассистента Карина для Windows. Фраза пришла из распознавания речи и может содержать ошибки. "
        "Выбери ОДНО действие из списка либо коротко ответь по-русски (1–2 предложения); о себе говори в женском роде («я открыла», «я не поняла»). Верни ТОЛЬКО JSON без пояснений: "
        '{"action":"имя","args":{...}} или {"say":"ответ"}. Если действие не подходит — отвечай через say.\n'
        f"Действия:\n{cmds}\nИзвестные программы: {', '.join(app_names[:60])}"
    )
    body = json.dumps({"model": c.get("model", "claude-haiku-5-5"), "max_tokens": 300, "system": system,
                       "messages": [{"role": "user", "content": text}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        raw = "".join(b.get("text", "") for b in out.get("content", []))
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None
