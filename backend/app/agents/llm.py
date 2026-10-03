"""Optional Gemini calls. Every caller has a deterministic fallback, so the
pipeline works without a key; the model only plans and words, never computes.

Check a new key with:  uv run python -m app.agents.llm
"""

import json
import os
import time

import httpx

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
# Tried in order. The lite model answers in about a second; the larger Flash
# models are often overloaded on the free tier, so they are only fallbacks.
DEFAULT_MODELS = ("gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-2.5-flash")
TIMEOUT_SECONDS = 20.0
# Gemini returns these when a model is briefly overloaded or rate limited.
RETRY_STATUSES = {429, 500, 503}
RETRY_DELAYS_SECONDS = (1.0,)

_state: dict = {"model": None, "last_error": None, "calls": 0, "failures": 0}


def _key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None


def available() -> bool:
    return _key() is not None


def _headers() -> dict:
    # The key travels in a header so it never appears in a URL or an error message.
    return {"x-goog-api-key": _key() or "", "Content-Type": "application/json"}


def _gemini_schema(schema: dict) -> dict:
    """JSON Schema to Gemini's schema dialect: upper-case types, no additionalProperties."""
    converted = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type":
            converted[key] = value.upper()
        elif key == "properties":
            converted[key] = {name: _gemini_schema(sub) for name, sub in value.items()}
        elif key == "items":
            converted[key] = _gemini_schema(value)
        else:
            converted[key] = value
    return converted


def _models() -> tuple[str, ...]:
    configured = os.getenv("GEMINI_MODEL")
    return (configured,) if configured else DEFAULT_MODELS


def _generate(model: str, body: dict) -> httpx.Response:
    url = f"{API_ROOT}/models/{model}:generateContent"
    response = httpx.post(url, json=body, headers=_headers(), timeout=TIMEOUT_SECONDS)
    for delay in RETRY_DELAYS_SECONDS:
        if response.status_code not in RETRY_STATUSES:
            break
        time.sleep(delay)
        response = httpx.post(url, json=body, headers=_headers(), timeout=TIMEOUT_SECONDS)
    return response


def _fail(reason: str) -> None:
    _state["failures"] += 1
    _state["last_error"] = reason


def complete(system: str, user: str, schema: dict | None = None) -> str | dict | None:
    """Model reply as text, or as a dict when `schema` is given. None on any
    failure, so callers fall back to their rule-based path."""
    if not available():
        return None
    body: dict = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
    }
    if schema:
        body["generationConfig"] = {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(schema),
        }

    _state["calls"] += 1
    payload = None
    for model in _models():
        try:
            response = _generate(model, body)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            _state["last_error"] = f"{model}: HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            continue
        except (httpx.HTTPError, ValueError) as exc:
            _state["last_error"] = f"{model}: {type(exc).__name__}: {exc}"
            continue
        _state["model"] = model
        break
    if payload is None:
        _state["failures"] += 1
        return None

    candidate = (payload.get("candidates") or [{}])[0]
    if candidate.get("finishReason") not in (None, "STOP"):
        _fail(f'Model stopped early: {candidate.get("finishReason")}')
        return None
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        _fail("Model returned no text")
        return None
    if not schema:
        _state["last_error"] = None
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        _fail("Model returned invalid JSON")
        return None
    _state["last_error"] = None
    return parsed


def status() -> dict:
    return {"provider": "gemini", "configured": available(),
            "model": _state["model"] or _models()[0],
            **{k: _state[k] for k in ("calls", "failures", "last_error")}}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    if not available():
        raise SystemExit("GEMINI_API_KEY is not set in backend/.env")
    reply = complete("Answer in one short sentence.", "Say that the Gemini connection works.")
    print("Model:", status()["model"])
    print("Text reply:", reply)
    structured = complete(
        "Classify the question.", "What if a cyclone hits Gujarat?",
        {"type": "object", "properties": {"topic": {"type": "string", "enum": ["weather", "rates", "other"]}},
         "required": ["topic"], "additionalProperties": False},
    )
    print("Structured reply:", structured)
    if reply is None or structured is None:
        raise SystemExit(f"Failed: {_state['last_error']}")
