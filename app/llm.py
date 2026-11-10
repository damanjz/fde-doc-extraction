"""Ollama chat client (copied from the support-agent project, trimmed to what extraction needs).

One system message per request: the Qwen chat template rejects a second one with HTTP 500.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.config import settings


class LLMError(RuntimeError):
    """Raised when the model server cannot be reached or returns something unusable."""


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


def _post(url: str, payload: dict) -> dict:
    """POST JSON with a timeout and one retry on transport errors and 5xx."""
    last: Exception | None = None
    for _ in range(2):
        try:
            r = httpx.post(url, json=payload, timeout=settings.llm_timeout_s)
            if r.status_code >= 500:
                raise httpx.HTTPStatusError("server error", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            last = exc
    raise LLMError(f"request to {url} failed: {last!r}") from last


def chat(messages: list[dict]) -> LLMResponse:
    """Send a chat turn expecting a JSON reply. Messages use the Ollama shape."""
    if sum(1 for m in messages if m["role"] == "system") > 1:
        raise ValueError("only one system message is allowed per request")
    start = time.monotonic()
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": settings.num_ctx, "num_predict": settings.num_predict},
    }
    data = _post(f"{settings.ollama_url}/api/chat", payload)
    msg = data.get("message") or {}
    return LLMResponse(
        content=(msg.get("content") or "").strip(),
        prompt_tokens=data.get("prompt_eval_count", 0),
        completion_tokens=data.get("eval_count", 0),
        latency_ms=int((time.monotonic() - start) * 1000),
        model=data.get("model", settings.ollama_model),
    )


def ping() -> bool:
    try:
        return httpx.get(f"{settings.ollama_url}/api/tags", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False
