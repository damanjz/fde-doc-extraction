import json

import pytest

from app import extract, llm, schema
from app.config import settings
from app.llm import LLMResponse
from app.textlayer import read_document
from tests.conftest import TRUTH, doc_bytes


class Script:
    """A chat function that replays canned replies and records the requests."""

    def __init__(self, *replies: str) -> None:
        self.replies, self.requests = list(replies), []

    def __call__(self, messages):
        self.requests.append(messages)
        return LLMResponse(content=self.replies.pop(0), latency_ms=10, model="script")


GOOD = json.dumps({"supplier_name": "A", "line_items": []})


def test_parse_json_reply_variants():
    assert extract.parse_json_reply('{"a": 1}') == {"a": 1}
    assert extract.parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract.parse_json_reply('  {"a": [1, 2]}  ') == {"a": [1, 2]}
    for bad in ["not json", '{"a": ', "[1, 2]", '"text"', ""]:
        with pytest.raises(ValueError):
            extract.parse_json_reply(bad)


def test_unwrap_nested_answer():
    assert extract.unwrap({"invoice": {"supplier_name": "A"}}, "invoice") == {"supplier_name": "A"}
    assert extract.unwrap({"supplier_name": "A"}, "invoice") == {"supplier_name": "A"}
    assert extract.unwrap({"x": 1}, "invoice") == {"x": 1}


def test_valid_first_reply_makes_one_call():
    chat = Script(GOOD)
    ex = extract.extract("invoice", "text", chat)
    assert len(chat.requests) == 1 and not ex.repaired and ex.first_data == ex.data


def test_invalid_json_gets_exactly_one_repair_round():
    chat = Script('{"supplier_name": "A", "line_it', GOOD)
    ex = extract.extract("invoice", "text", chat)
    assert len(chat.requests) == 2 and ex.repaired and ex.first_data is None
    assert ex.first_reply.startswith('{"supplier_name"') and ex.reply == GOOD
    repair = chat.requests[1]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert "not valid JSON" in repair[-1]["content"]


def test_second_failure_raises_and_does_not_loop():
    chat = Script("nope", "still nope", "never used")
    with pytest.raises(extract.ExtractionError):
        extract.extract("invoice", "text", chat)
    assert len(chat.requests) == 2


def test_every_request_has_exactly_one_system_message_first():
    chat = Script("bad", GOOD)
    extract.extract("invoice", "text", chat)
    for req in chat.requests:
        assert req[0]["role"] == "system" and sum(m["role"] == "system" for m in req) == 1


def test_llm_client_refuses_a_second_system_message():
    msgs = [{"role": "system", "content": "a"}, {"role": "system", "content": "b"}, {"role": "user", "content": "c"}]
    with pytest.raises(ValueError):
        llm.chat(msgs)


def test_ollama_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, payload):
        seen.update(url=url, payload=payload)
        return {"message": {"content": " {} "}, "prompt_eval_count": 3, "eval_count": 4, "model": "m"}

    monkeypatch.setattr(llm, "_post", fake_post)
    r = llm.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    p = seen["payload"]
    assert seen["url"].endswith("/api/chat") and p["think"] is False and p["format"] == "json" and p["stream"] is False
    assert p["options"]["num_ctx"] == settings.num_ctx and p["options"]["temperature"] == 0
    assert r.content == "{}" and r.prompt_tokens == 3 and r.completion_tokens == 4


def test_system_prompt_carries_schema_and_the_injection_rule():
    for doc_type in schema.DOC_TYPES:
        sp = schema.system_prompt(doc_type)
        for name in schema.FIELDS[doc_type]:
            assert f'"{name}"' in sp
        assert "untrusted" in sp and "Never follow" in sp and "null" in sp
    assert "line_items" in schema.system_prompt("invoice")
    assert "{" not in schema.EXTRA["delivery_note"]


def test_user_prompt_wraps_document_text():
    assert "<<<\nhello\n>>>" in schema.user_prompt("hello")


def test_every_text_pdf_fits_the_prompt_budget():
    """Document text plus schema must fit the 8k context with room for the reply."""
    for doc_id, t in TRUTH.items():
        if t["source"] != "pdf":
            continue
        td = read_document(doc_bytes(doc_id))
        assert len(td.text) <= settings.max_prompt_chars, doc_id
    sys_chars = len(schema.system_prompt("invoice"))
    # worst case ~3 chars per token for digit-heavy text
    assert (sys_chars + settings.max_prompt_chars) / 3 + settings.num_predict < settings.num_ctx
