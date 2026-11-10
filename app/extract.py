"""Step 2: LLM extraction. JSON only; one repair round for invalid JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app import llm
from app.schema import FIELDS, REPAIR_TEMPLATE, system_prompt, user_prompt


class ExtractionError(RuntimeError):
    """The model did not produce usable JSON, even after the repair round."""


@dataclass
class Extraction:
    data: dict  # parsed JSON after the (at most one) repair round
    reply: str  # raw final reply
    first_reply: str  # raw first reply (the baseline uses only this one)
    first_data: dict | None  # parsed first reply, None if it was not valid JSON
    repaired: bool
    llm_ms: int
    first_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


def parse_json_reply(reply: str) -> dict:
    """Parse a model reply into a JSON object. Tolerates a markdown fence around it. Raises ValueError."""
    s = reply.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{exc.msg} at char {exc.pos}") from exc
    if not isinstance(obj, dict):
        raise ValueError("top level is not a JSON object")
    return obj


def unwrap(obj: dict, doc_type: str) -> dict:
    """Some models wrap the answer ({"invoice": {...}}). Unwrap when no expected key is at the top level."""
    expected = set(FIELDS[doc_type])
    if expected & set(obj):
        return obj
    for v in obj.values():
        if isinstance(v, dict) and expected & set(v):
            return v
    return obj


def extract(doc_type: str, text: str, chat=None) -> Extraction:
    chat = chat or llm.chat
    system = {"role": "system", "content": system_prompt(doc_type)}
    user = {"role": "user", "content": user_prompt(text)}
    r1 = chat([system, user])
    total_ms, ptok, ctok = r1.latency_ms, r1.prompt_tokens, r1.completion_tokens
    try:
        first = unwrap(parse_json_reply(r1.content), doc_type)
        return Extraction(first, r1.content, r1.content, first, False, total_ms, r1.latency_ms, ptok, ctok, r1.model)
    except ValueError as exc:
        err = str(exc)
    repair = {"role": "user", "content": REPAIR_TEMPLATE.format(error=err)}
    r2 = chat([system, user, {"role": "assistant", "content": r1.content}, repair])
    total_ms += r2.latency_ms
    ptok, ctok = ptok + r2.prompt_tokens, ctok + r2.completion_tokens
    try:
        data = unwrap(parse_json_reply(r2.content), doc_type)
    except ValueError as exc:
        raise ExtractionError(f"invalid JSON after one repair round: {exc}") from exc
    return Extraction(data, r2.content, r1.content, None, True, total_ms, r1.latency_ms, ptok, ctok, r2.model)
