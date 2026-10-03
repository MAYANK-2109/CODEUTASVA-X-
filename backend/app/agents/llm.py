"""Optional Claude calls. Every caller has a deterministic fallback, so the
pipeline works without a key; the model only plans and words, never computes."""

import json
import os

import anthropic

MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")
TIMEOUT_SECONDS = 40.0

_client: anthropic.Anthropic | None = None


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=1)
    return _client


def _text(response) -> str | None:
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    return next((b.text for b in response.content if b.type == "text"), None)


def complete(system: str, user: str, schema: dict | None = None) -> str | dict | None:
    """Model reply as text, or as a dict when `schema` is given. None on any
    failure, so callers fall back to their rule-based path."""
    if not available():
        return None
    output_config: dict = {"effort": "low"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
    except anthropic.APIError:
        return None
    text = _text(response)
    if text is None or not schema:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
