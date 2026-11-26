"""Vision model call: screenshot(s) in, strict JSON out (OpenAI Chat Completions + Structured
Outputs). The model id comes from configuration (`REPINTEL_SCANNER_EXTRACTION_MODEL`)."""

import base64
import io
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import httpx
from PIL import Image

from app.core.config import get_settings
from app.nlp.llm import LLMError
from app.scanner.extraction.schema import SCHEMA, SYSTEM_PROMPT, USER_PROMPT, USER_PROMPT_PARTS

TALL_RATIO = 2.6         # taller than this (height / width) is sent as overlapping parts
PART_RATIO = 1.8
OVERLAP = 0.12
MAX_PARTS = 6


@dataclass
class VisionResult:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    parts: int


class VisionProvider(Protocol):
    def read(self, images: list[bytes], *, captured: str) -> VisionResult: ...


def prepare(png: bytes) -> list[bytes]:
    """PNG bytes → one or more PNG parts. Very tall screenshots (full-page captures) are cut into
    overlapping parts so the model keeps enough resolution to read small comment text."""
    with Image.open(io.BytesIO(png)) as im:
        im.load()
        w, h = im.size
        if h / w <= TALL_RATIO:
            return [png]
        part_h = int(w * PART_RATIO)
        step = int(part_h * (1 - OVERLAP))
        tops = list(range(0, max(1, h - part_h) + 1, step))
        if tops[-1] + part_h < h:
            tops.append(h - part_h)
        if len(tops) > MAX_PARTS:            # extremely long: fewer, taller parts
            part_h = int(h / MAX_PARTS / (1 - OVERLAP)) + 1
            step = int(part_h * (1 - OVERLAP))
            tops = [min(i * step, h - part_h) for i in range(MAX_PARTS)]
        parts = []
        for top in tops:
            buf = io.BytesIO()
            im.crop((0, top, w, min(h, top + part_h))).save(buf, "PNG", optimize=True)
            parts.append(buf.getvalue())
        return parts


@lru_cache
def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0))


class OpenAIVision:
    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.api_key = s.openai_api_key.get_secret_value()
        self.base = s.openai_base_url.rstrip("/")
        self.model = model or s.scanner_extraction_model
        self.detail = s.scanner_extraction_detail
        self.max_output = s.scanner_extraction_max_output_tokens

    def read(self, images: list[bytes], *, captured: str) -> VisionResult:
        if not self.api_key or not self.model:
            raise LLMError("not_configured", "OpenAI key or extraction model missing", fatal=True)
        prompt = (USER_PROMPT_PARTS.format(n=len(images), captured=captured) if len(images) > 1
                  else USER_PROMPT.format(captured=captured))
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            url = "data:image/png;base64," + base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": url, "detail": self.detail}})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "screenshot_transcription", "strict": True, "schema": SCHEMA}},
            "max_completion_tokens": self.max_output,
        }
        try:
            r = _client().post(f"{self.base}/chat/completions", json=body,
                               headers={"authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise LLMError("network_error", type(e).__name__) from e
        if r.status_code in (401, 403):
            raise LLMError("auth_failed", "OpenAI rejected the API key", fatal=True)
        if r.status_code == 404:
            raise LLMError("model_not_found", self.model, fatal=True)
        if r.status_code == 429:
            quota = "insufficient_quota" in r.text
            raise LLMError("quota_exceeded" if quota else "rate_limited", "OpenAI 429",
                           fatal=quota)
        if r.status_code == 400:
            raise LLMError("bad_request", r.text[:300], fatal=True)
        if r.status_code >= 400:
            raise LLMError("http_error", f"OpenAI returned {r.status_code}")
        try:
            payload = r.json()
            choice = payload["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError("bad_response", "unexpected response shape") from e
        if msg.get("refusal"):
            raise LLMError("refusal", str(msg["refusal"])[:200], fatal=True)
        if choice.get("finish_reason") == "length":
            raise LLMError("output_too_long", "raise REPINTEL_SCANNER_EXTRACTION_MAX_OUTPUT_TOKENS",
                           fatal=True)
        try:
            data = json.loads(msg.get("content") or "")
        except ValueError as e:
            raise LLMError("bad_json", "model output is not JSON") from e
        usage = payload.get("usage") or {}
        return VisionResult(data, payload.get("model", self.model),
                            int(usage.get("prompt_tokens", 0)),
                            int(usage.get("completion_tokens", 0)), len(images))


_override: VisionProvider | None = None


def set_provider(p: VisionProvider | None) -> None:
    """Tests replace the provider with a fake."""
    global _override
    _override = p


def get_provider() -> VisionProvider:
    return _override or OpenAIVision()


def configured() -> bool:
    s = get_settings()
    return bool(_override) or bool(s.scanner_extraction_enabled and s.scanner_extraction_model
                                   and s.openai_api_key.get_secret_value())
