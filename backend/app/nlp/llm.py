"""LLM provider abstraction. OpenAI first (Chat Completions + Structured Outputs, strict JSON
schema); model identifiers come from configuration only."""

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


class LLMError(Exception):
    def __init__(self, reason: str, detail: str = "", *, fatal: bool = False) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.fatal = fatal     # stop calling the provider for the rest of the job


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    name: str

    def complete_json(self, *, model: str, system: str, user: str, schema: dict[str, Any],
                      schema_name: str) -> LLMResult: ...


@lru_cache
def openai_client() -> httpx.Client:
    return httpx.Client(timeout=60.0)


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.openai_api_key.get_secret_value()
        self.base = s.openai_base_url.rstrip("/")

    def complete_json(self, *, model: str, system: str, user: str, schema: dict[str, Any],
                      schema_name: str) -> LLMResult:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema_name, "strict": True, "schema": schema}},
        }
        try:
            r = openai_client().post(f"{self.base}/chat/completions", json=body,
                                     headers={"authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise LLMError("network_error", type(e).__name__) from e
        if r.status_code in (401, 403):
            raise LLMError("auth_failed", "OpenAI rejected the API key", fatal=True)
        if r.status_code == 429:
            raise LLMError("rate_limited", "OpenAI rate limit or quota reached", fatal=True)
        if r.status_code == 404:
            raise LLMError("model_not_found", model, fatal=True)
        if r.status_code >= 400:
            raise LLMError("http_error", f"OpenAI returned {r.status_code}")
        try:
            payload = r.json()
            msg = payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError("bad_response", "unexpected response shape") from e
        if msg.get("refusal"):
            raise LLMError("refusal", str(msg["refusal"])[:200])
        try:
            data = json.loads(msg.get("content") or "")
        except ValueError as e:
            raise LLMError("bad_json", "model output is not JSON") from e
        usage = payload.get("usage") or {}
        return LLMResult(data, payload.get("model", model), int(usage.get("prompt_tokens", 0)),
                         int(usage.get("completion_tokens", 0)))


def get_provider() -> LLMProvider:
    return OpenAIProvider()
