"""Data for the dashboard's risk strip: portfolio risk and exposure, and the
state of every live feed and model the terminal depends on."""

import math
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents import llm
from app.agents.nodes import GENERAL_NEWS_QUERY, HORIZON_SESSIONS
from app.ingestion.news import get_news
from app.risk import exposure
from app.tools import gdelt, health, sentiment, vector_store
from app.tools.cache import cached
from app.tools.market import get_history, get_indices, get_macro, normalise_holdings
from app.tools.weather import get_weather_outlook

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

PROBE_SECONDS = 9
# A feed that last answered longer ago than this is asked again before it is reported.
RECHECK_SECONDS = {"indices": 300, "macro": 600, "news": 300, "weather": 1800}
LIVE, DEGRADED, DOWN, IDLE = "live", "degraded", "down", "idle"


class OverviewRequest(BaseModel):
    holdings: list[dict] | None = None


def _round(value, digits: int = 2) -> float | None:
    return None if value is None or math.isnan(value) else round(float(value), digits)


@router.post("/overview")
def overview(request: OverviewRequest) -> dict:
    """VaR, beta and concentration for the holdings at the latest close."""
    return _overview(request.holdings)


@cached(60)
def _overview(raw_holdings: list[dict] | None) -> dict:
    holdings, source = normalise_holdings(raw_holdings)
    closes, price_source = get_history([h["ticker"] for h in holdings])
    if closes is None:
        raise HTTPException(status_code=503, detail="Price history is unavailable right now.")
    view = exposure.snapshot(holdings, closes)
    if view is None:
        raise HTTPException(status_code=422, detail="None of the holdings could be priced.")

    total, var, largest = view["total"], view["var"], view["positions"][0]
    return {
        "portfolio_source": source,
        "price_source": price_source,
        "as_of": closes.index[-1].date().isoformat(),
        "risk": {
            "total": _round(total),
            "holdings": len(view["positions"]),
            "var_1d": _round(var[0] * total) if var else None,
            "var_1d_pct": _round(var[0], 4) if var else None,
            "cvar_1d": _round(var[1] * total) if var else None,
            "var_horizon": _round(var[0] * math.sqrt(HORIZON_SESSIONS) * total) if var else None,
            "horizon_sessions": HORIZON_SESSIONS,
            "beta": _round(view["beta"]),
            "volatility": _round(view["volatility"], 4),
            "sessions": view["sessions"],
        },
        "exposure": {
            "largest": {"name": largest["name"], "weight": _round(largest["weight"], 4), "value": _round(largest["value"])},
            "sectors": [{"sector": s["sector"], "weight": _round(s["weight"], 4), "value": _round(s["value"]),
                         "holdings": s["holdings"]} for s in view["sectors"]],
            "effective_holdings": _round(view["effective_holdings"], 1),
        },
        "unpriced": view["unpriced"],
    }


# --------------------------------------------------------------------------- streams


def _age(stamp: str | None) -> float | None:
    if not stamp:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()


def _refresh_stale_feeds() -> None:
    """Ask the feeds that have not answered recently, all at once and with a
    deadline, so a slow source cannot hold up the report."""
    seen = health.snapshot()
    probes = {
        "indices": get_indices,
        "macro": get_macro,
        "news": lambda: get_news(f"{GENERAL_NEWS_QUERY} when:1d", 10),
        "weather": get_weather_outlook,
        "sentiment": sentiment.backend,  # loads the model on first use
    }
    due = []
    for name, probe in probes.items():
        age = _age(seen.get(name, {}).get("checked_at"))
        if name == "sentiment" or age is None or age > RECHECK_SECONDS[name]:
            due.append(probe)
    pool = ThreadPoolExecutor(max_workers=len(due) or 1)
    wait([pool.submit(probe) for probe in due], timeout=PROBE_SECONDS)
    pool.shutdown(wait=False)


def _feed(key: str, label: str, source: str, entry: dict | None, fallback: str | None = None) -> dict:
    """A row for a feed that app.tools.health keeps a record of."""
    if entry is None:
        return {"key": key, "group": "feed", "label": label, "source": source, "status": IDLE,
                "detail": "Not requested yet", "checked_at": None, "latency_ms": None}
    if entry["ok"]:
        status = LIVE
    else:
        status = DEGRADED if fallback and fallback in entry["detail"] else DOWN
    return {"key": key, "group": "feed", "label": label, "source": source, "status": status,
            "detail": entry["detail"] or ("answered" if entry["ok"] else "last request failed"),
            "checked_at": entry["checked_at"], "latency_ms": entry["latency_ms"]}


def _geopolitics() -> dict:
    scan = gdelt.signals()
    read, wanted = len(scan["themes"]), len(gdelt.THEMES)
    elevated = [t["label"] for t in scan["themes"].values() if t.get("elevated")]
    detail = f"{read} of {wanted} themes read"
    if elevated:
        detail += f"; elevated: {', '.join(elevated)}"
    if scan["error"]:
        detail += f"; {scan['error']}"
    if read == 0:
        status = DOWN if scan["error"] else IDLE
    else:
        status = LIVE if read == wanted and not scan["stale"] and not scan["error"] else DEGRADED
    return {"key": "geopolitics", "group": "feed", "label": "Geopolitical news scan", "source": "GDELT",
            "status": status, "detail": detail, "checked_at": scan["scanned_at"], "latency_ms": None}


def _vector() -> dict:
    state = vector_store.status()
    if state["backend"] == vector_store.PINECONE:
        detail = f'{state["events_indexed"]} past events and {state["news_indexed"]} headlines indexed'
        timings = [f"{label} {state[key]} ms" for label, key in
                   (("last index", "last_news_upsert_ms"), ("last search", "last_search_ms")) if state[key] is not None]
        if timings:
            detail += "; " + ", ".join(timings)
        status = LIVE
    else:
        detail = ("Pinecone unreachable, local text search in use" if state["configured"]
                  else "No Pinecone key set, local text search in use")
        status = DEGRADED
    return {"key": "vector", "group": "model", "label": "Vector database", "source": "Pinecone",
            "status": status, "detail": detail, "checked_at": None, "latency_ms": state["last_search_ms"]}


def _sentiment() -> dict:
    state = sentiment.status()
    finbert = state["backend"] == sentiment.FINBERT
    return {"key": "sentiment", "group": "model", "label": "Sentiment model",
            "source": "FinBERT" if finbert else "VADER lexicon",
            "status": LIVE if finbert else DEGRADED,
            "detail": "FinBERT scoring headlines" if finbert
            else "FinBERT is switched off on this host to stay inside its memory; lexicon scoring in use"
            if state["lexicon_only"]
            else f'FinBERT not loaded ({state["error"] or "not tried yet"}); lexicon scoring in use',
            "checked_at": None, "latency_ms": None}


def _llm() -> dict:
    state = llm.status()
    if not state["configured"]:
        status, detail = DEGRADED, "No API key set; planning and wording are rule-based"
    elif state["last_error"]:
        status, detail = DEGRADED, f'{state["model"]}: last call failed, rule-based wording used'
    else:
        status = LIVE if state["calls"] else IDLE
        detail = (f'{state["model"]}, {state["calls"]} call(s), {state["failures"]} failed' if state["calls"]
                  else f'{state["model"]} configured, not called yet')
    return {"key": "llm", "group": "model", "label": "Language model", "source": "Gemini",
            "status": status, "detail": detail, "checked_at": None, "latency_ms": None}


@router.get("/streams")
def streams() -> dict:
    """The state of each feed and model: live, degraded (a fallback is in use),
    down, or idle (not asked yet). Nothing here is assumed; every row reports the
    outcome of a real request."""
    return _streams()


@cached(20)
def _streams() -> dict:
    _refresh_stale_feeds()
    seen = health.snapshot()
    # Quotes and index levels come from the same source; report whichever answered last.
    quotes = max((seen[k] for k in ("quotes", "indices") if k in seen),
                 key=lambda e: e["checked_at"], default=None)
    rows = [
        _feed("quotes", "Live quotes", "Yahoo Finance", quotes),
        _feed("history", "Price history", "Yahoo Finance", seen.get("history"), fallback="saved copy"),
        _feed("macro", "Macro indicators", "Yahoo Finance", seen.get("macro")),
        _feed("news", "News headlines", "Google News", seen.get("news")),
        _feed("weather", "Weather forecast", "Open-Meteo", seen.get("weather")),
        _geopolitics(),
        _vector(),
        _sentiment(),
        _llm(),
    ]
    return {"streams": rows, "generated_at": datetime.now(timezone.utc).isoformat(),
            "counts": {status: sum(r["status"] == status for r in rows) for status in (LIVE, DEGRADED, DOWN, IDLE)}}
