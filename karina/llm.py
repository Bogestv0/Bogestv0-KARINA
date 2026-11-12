"""Необязательное понимание свободных фраз через Claude (только онлайн). Без ключа/сети не используется."""
from __future__ import annotations
import json, os, re, urllib.request
from .actions import REGISTRY
from . import profile, prompts


def ask(cfg, text: str, app_names: list[str]) -> dict | None:
    c = cfg["llm"]
    key = c.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not (c.get("enabled") and key):
        return None
    cmds = "\n".join(f'- {x.name}: {x.desc}' + (f' (аргументы: {x.llm_args})' if x.llm_args else '') for x in REGISTRY)
    system = (prompts.fill(prompts.COMMANDS_SYSTEM, commands=cmds, apps=", ".join(app_names[:60]))
              + profile.prompt_section(cfg)
              + prompts.forbidden_block(prompts.FORBIDDEN_ALL, prompts.FORBIDDEN_COMMANDS))
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
