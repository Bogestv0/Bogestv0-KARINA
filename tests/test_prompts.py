"""prompts.py: запреты попадают в подсказки, пустые списки ничего не меняют, негативы склеиваются без дублей."""
from karina import prompts, chat, coder, images


def test_empty_lists_add_nothing():
    assert prompts.forbidden_block([], []) == ""
    assert prompts.forbidden_inline([]) == ""


def test_forbidden_reaches_chat_and_coder(monkeypatch):
    monkeypatch.setattr(prompts, "FORBIDDEN_CHAT", ["Не обсуждай политику."])
    monkeypatch.setattr(prompts, "FORBIDDEN_CODER", ["Не добавляй зависимости."])
    assert "Не обсуждай политику." in chat.system_prompt()
    assert "Не обсуждай политику." not in coder.system_prompt(True)
    assert "Не добавляй зависимости." in coder.system_prompt(True)


def test_image_writer_rules(monkeypatch):
    monkeypatch.setattr(prompts, "FORBIDDEN_IMAGE_WRITER", ["do not add text"])
    assert "do not add text" in images.prompt_request("нарисуй кота")[0]["content"]


def test_tags_join_and_unique():
    assert prompts.join_tags("a, b", ["c", ""], None) == "a, b, c"
    assert prompts.unique_tags("nsfw, blurry, NSFW, hands") == "nsfw, blurry, hands"


def test_fill_keeps_json_braces_and_unknown_keys():
    assert prompts.fill('{"a": {x}} {y}', x="1") == '{"a": 1} {y}'
