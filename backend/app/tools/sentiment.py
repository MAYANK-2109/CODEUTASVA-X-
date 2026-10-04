"""Headline sentiment. FinBERT (ProsusAI/finbert, int8 ONNX build) when its
model files are present, otherwise the VADER lexicon with finance terms.

Fetch the model with:  uv run python -m app.tools.sentiment --download
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from app.tools import memory

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "finbert"
MODEL_FILE = MODEL_DIR / "model_quantized.onnx"
TOKENIZER_FILE = MODEL_DIR / "tokenizer.json"
MODEL_REPO = "https://huggingface.co/Xenova/finbert/resolve/main"
MODEL_MIN_BYTES = 100_000_000
MAX_TOKENS = 64
INFERENCE_THREADS = max(1, min(2, os.cpu_count() or 1))
SCORE_CACHE_SIZE = 5000   # headlines repeat between requests, so each is scored once
BATCH_SIZE = 16           # headlines per inference call: memory grows with the batch, speed does not
MODEL_MEMORY_MB = 230     # what the loaded model occupies
SHED_SECONDS = 600        # after unloading for memory, score with the lexicon for this long
POSITIVE, NEGATIVE = 0, 1  # label order in the model's config

FINBERT = "FinBERT"
VADER = "VADER lexicon"

# VADER is a general-purpose lexicon; these market terms are missing from it or
# carry the wrong polarity for financial headlines.
FINANCE_TERMS = {
    "surge": 2.0, "surges": 2.0, "rally": 2.0, "rallies": 2.0, "soar": 2.5, "soars": 2.5,
    "jump": 1.5, "jumps": 1.5, "gain": 1.5, "gains": 1.5, "upgrade": 2.0, "upgrades": 2.0,
    "outperform": 2.0, "beat": 1.5, "beats": 1.5, "bullish": 2.5, "record high": 2.5,
    "plunge": -3.0, "plunges": -3.0, "slump": -2.5, "slumps": -2.5, "tumble": -2.5,
    "tumbles": -2.5, "selloff": -2.5, "sell-off": -2.5, "crash": -3.0, "downgrade": -2.0,
    "downgrades": -2.0, "underperform": -2.0, "miss": -1.5, "misses": -1.5,
    "bearish": -2.5, "default": -2.5, "shutdown": -2.0, "disruption": -1.5,
    "shortfall": -1.5, "probe": -1.5, "penalty": -1.5,
}
POSITIVE_CUTOFF = 0.05
NEGATIVE_CUTOFF = -0.05
EVAL_FILE = MODEL_DIR.parent.parent / "trained" / "sentiment_eval.json"

_vader = SentimentIntensityAnalyzer()
_vader.lexicon.update(FINANCE_TERMS)

_lock = threading.Lock()
_scores: dict[str, float] = {}
_inference = threading.Lock()   # one inference at a time: concurrent runs each take their own buffers
_finbert: dict = {"session": None, "tokenizer": None, "tried": False, "error": None, "shed_until": 0.0}


def _load_finbert() -> bool:
    """Load the model once. False if the files or libraries are missing."""
    with _lock:
        if _finbert["tried"]:
            return _finbert["session"] is not None
        _finbert["tried"] = True
        if not MODEL_FILE.exists() or MODEL_FILE.stat().st_size < MODEL_MIN_BYTES or not TOKENIZER_FILE.exists():
            _finbert["error"] = "model files not downloaded"
            return False
        if not memory.room_for(MODEL_MEMORY_MB):
            _finbert.update(tried=False, error="not enough free memory to load the model",
                            shed_until=time.time() + SHED_SECONDS)
            return False
        try:
            import onnxruntime
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(TOKENIZER_FILE))
            tokenizer.enable_truncation(max_length=MAX_TOKENS)
            tokenizer.enable_padding()
            # On a shared fraction of a CPU, the default of one thread per host
            # core makes inference slower, not faster.
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = INFERENCE_THREADS
            options.inter_op_num_threads = 1
            _finbert["session"] = onnxruntime.InferenceSession(
                str(MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"]
            )
            _finbert["tokenizer"] = tokenizer
        except Exception as exc:  # optional model: any failure means "use VADER"
            _finbert["error"] = f"{type(exc).__name__}: {exc}"
        return _finbert["session"] is not None


def lexicon_only() -> bool:
    """SENTIMENT_MODEL=vader forces the lexicon; any other value uses FinBERT
    when it loads. Unset, the lexicon is used on Render, whose 512 MB plan
    cannot hold the model alongside the rest of the server, and FinBERT elsewhere."""
    return os.getenv("SENTIMENT_MODEL", "vader" if os.getenv("RENDER") else "auto").lower() == "vader"


def backend() -> str:
    """Which scorer is in use."""
    if lexicon_only():
        return VADER
    if time.time() < _finbert["shed_until"]:
        return VADER
    return FINBERT if _load_finbert() else VADER


def shed() -> None:
    """Unload the model to free its memory. Headlines are scored with the
    lexicon, and labelled so, until it is loaded again."""
    with _inference, _lock:
        if _finbert["session"] is None:
            return
        _finbert.update(session=None, tokenizer=None, tried=False, shed_until=time.time() + SHED_SECONDS,
                        error="unloaded to stay inside the memory limit")
        _scores.clear()


def source_label() -> str:
    if backend() == FINBERT:
        return "Google News RSS, scored with FinBERT (ProsusAI/finbert)"
    return "Google News RSS, scored with VADER + finance lexicon"


def _finbert_scores(texts: list[str]) -> list[float]:
    import numpy as np

    out: list[float] = []
    with _inference:
        session, tokenizer = _finbert["session"], _finbert["tokenizer"]
        if session is None:
            raise RuntimeError("model not loaded")
        wanted = {i.name for i in session.get_inputs()}
        for start in range(0, len(texts), BATCH_SIZE):
            encoded = tokenizer.encode_batch(texts[start:start + BATCH_SIZE])
            feeds = {
                "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
            }
            logits = session.run(None, {k: v for k, v in feeds.items() if k in wanted})[0]
            exp = np.exp(logits - logits.max(axis=1, keepdims=True))
            probs = exp / exp.sum(axis=1, keepdims=True)
            out.extend(float(p[POSITIVE] - p[NEGATIVE]) for p in probs)
    return out


def cutoffs() -> tuple[float, float]:
    """(positive, negative) score cut-offs for the scorer in use. They come from
    the calibration in data/trained/sentiment_eval.json when it exists."""
    key = "finbert" if backend() == FINBERT else "vader"
    try:
        calibrated = json.loads(EVAL_FILE.read_text())[key]
        return calibrated["positive_cutoff"], calibrated["negative_cutoff"]
    except (OSError, ValueError, KeyError):
        return POSITIVE_CUTOFF, NEGATIVE_CUTOFF


def score_many(texts: list[str]) -> list[float]:
    """Sentiment per text in [-1, 1]: P(positive) - P(negative) for FinBERT,
    the compound score for VADER."""
    if not texts:
        return []
    if backend() == FINBERT:
        try:
            fresh = [text for text in dict.fromkeys(texts) if text not in _scores]
            if fresh:
                if len(_scores) + len(fresh) > SCORE_CACHE_SIZE:
                    # Emptying the cache also drops this batch's earlier scores, so all of it is scored again.
                    _scores.clear()
                    fresh = list(dict.fromkeys(texts))
                _scores.update(zip(fresh, _finbert_scores(fresh)))
            return [_scores[text] for text in texts]
        except Exception as exc:
            _finbert.update(session=None, error=f"{type(exc).__name__}: {exc}")
    return [_vader.polarity_scores(text)["compound"] for text in texts]


def score(text: str) -> float:
    return score_many([text])[0]


def score_headlines(items: list[dict]) -> dict | None:
    """Aggregate sentiment over news items. None when there is nothing to score."""
    if not items:
        return None
    values = score_many([item["title"] for item in items])
    scored = [{**item, "score": round(value, 2)} for item, value in zip(items, values)]
    scores = [s["score"] for s in scored]
    positive_cutoff, negative_cutoff = cutoffs()
    return {
        "mean": round(sum(scores) / len(scores), 2),
        "count": len(scores),
        "positive": sum(s >= positive_cutoff for s in scores),
        "negative": sum(s <= negative_cutoff for s in scores),
        "items": sorted(scored, key=lambda s: s["score"]),
    }


def label(mean: float) -> str:
    if mean >= 0.15:
        return "positive"
    if mean <= -0.15:
        return "negative"
    return "neutral"


def status() -> dict:
    return {"backend": backend(), "lexicon_only": lexicon_only(), "model_downloaded": MODEL_FILE.exists(),
            "error": _finbert["error"]}


def download_model() -> None:
    """Fetch the tokenizer and the quantized model, resuming a partial download."""
    import httpx

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, target in (("tokenizer.json", TOKENIZER_FILE), ("onnx/model_quantized.onnx", MODEL_FILE)):
        if target.exists() and (target != MODEL_FILE or target.stat().st_size >= MODEL_MIN_BYTES):
            print(f"{target.name}: already present")
            continue
        part = target.with_suffix(target.suffix + ".part")
        for _ in range(60):
            done = part.stat().st_size if part.exists() else 0
            try:
                with httpx.stream("GET", f"{MODEL_REPO}/{name}", headers={"Range": f"bytes={done}-"},
                                  follow_redirects=True, timeout=60) as response:
                    if response.status_code == 416:
                        break
                    response.raise_for_status()
                    with part.open("ab" if response.status_code == 206 else "wb") as out:
                        for chunk in response.iter_bytes(1 << 16):
                            out.write(chunk)
                break
            except httpx.HTTPError as exc:
                print(f"{target.name}: {type(exc).__name__} at {part.stat().st_size if part.exists() else 0} bytes, resuming")
        part.replace(target)
        print(f"{target.name}: {target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    if "--download" in sys.argv:
        download_model()
    print(status())
    for sample in ("Refiner posts record profit and raises dividend",
                   "Shares plunge after regulator opens fraud probe",
                   "Company to hold annual general meeting on Friday"):
        print(f"{score(sample):+.2f}  {sample}")
