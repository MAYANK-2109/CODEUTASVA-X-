"""Data for the Insights page: price history with event markers, exposure and weather."""

import math
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.nodes import EVENT_TYPES, HORIZON_SESSIONS
from app.risk import metrics
from app.tools.events import load_events
from app.tools.market import NIFTY, get_history, normalise_holdings
from app.tools.weather import GALE_GUST_KMH, HEAT_C, HEAVY_RAIN_MM, get_weather_outlook

router = APIRouter(prefix="/api/insights", tags=["insights"])

RANGE_YEARS = {"1y": 1, "3y": 3, "5y": 5, "max": None}
MAX_SERIES = 8  # one fixed colour per holding; the chart palette has eight


class PortfolioRequest(BaseModel):
    holdings: list[dict] | None = None
    range: Literal["1y", "3y", "5y", "max"] = "3y"


def _number(value, digits: int = 2) -> float | None:
    return None if value is None or math.isnan(value) else round(float(value), digits)


@router.post("/portfolio")
def portfolio(request: PortfolioRequest) -> dict:
    holdings, source = normalise_holdings(request.holdings)
    closes, price_source = get_history([h["ticker"] for h in holdings])
    if closes is None:
        raise HTTPException(status_code=503, detail="Price history is unavailable right now.")

    latest = closes.iloc[-1]
    positions = []
    for h in holdings:
        price = _number(latest.get(h["ticker"]))
        if price is not None:
            positions.append({**h, "price": price, "value": round(price * h["units"], 2)})
    if not positions:
        raise HTTPException(status_code=422, detail="None of the holdings could be priced.")
    positions.sort(key=lambda p: -p["value"])
    total = sum(p["value"] for p in positions)
    for p in positions:
        p["weight"] = round(p["value"] / total, 4)
    weights = {p["ticker"]: p["value"] / total for p in positions}

    sectors: dict[str, dict] = {}
    for p in positions:
        entry = sectors.setdefault(p["sector"], {"sector": p["sector"], "value": 0.0, "holdings": []})
        entry["value"] += p["value"]
        entry["holdings"].append(p["name"])
    sector_rows = sorted(
        ({**s, "value": round(s["value"], 2), "weight": round(s["value"] / total, 4)} for s in sectors.values()),
        key=lambda s: -s["value"],
    )

    returns = metrics.daily_returns(closes)
    var = metrics.historical_var(metrics.portfolio_returns(returns, weights))
    betas = {t: metrics.beta(returns[t], returns[NIFTY]) for t in weights}
    known = {t: b for t, b in betas.items() if b is not None}
    beta_value = sum(weights[t] * b for t, b in known.items()) if known else None

    # Price series, indexed to 100 at the start of the window so every holding
    # and the index share one axis.
    years = RANGE_YEARS[request.range]
    window = closes if years is None else closes[closes.index >= closes.index[-1] - pd.DateOffset(years=years)]
    if years is None or years > 1:
        window = window.resample("W-FRI").last().dropna(how="all")
    plotted = [p["ticker"] for p in positions[:MAX_SERIES]]

    def indexed(ticker: str) -> list[float | None]:
        series = window[ticker]
        first = series.first_valid_index()
        if first is None:
            return [None] * len(series)
        return [_number(v) for v in series / series[first] * 100]

    names = {p["ticker"]: p["name"] for p in positions}
    events = []
    for event in load_events():
        date = pd.Timestamp(event["date"])
        if date < window.index[0] or date > closes.index[-1]:
            continue
        moves = metrics.event_window_returns(closes, event["date"], HORIZON_SESSIONS)
        if moves is None:
            continue
        slot = min(int(window.index.searchsorted(date)), len(window.index) - 1)
        events.append(
            {
                "id": event["id"], "date": event["date"], "type": event["type"],
                "type_label": EVENT_TYPES[event["type"]][0], "title": event["title"],
                "description": event["description"], "confound": event.get("confound"),
                "index": slot, "nifty": _number(moves.get(NIFTY), 4),
                "moves": {t: _number(moves.get(t), 4) for t in plotted},
            }
        )

    events.sort(key=lambda e: e["date"])

    return {
        "portfolio_source": source,
        "price_source": price_source,
        "as_of": closes.index[-1].date().isoformat(),
        "horizon_sessions": HORIZON_SESSIONS,
        "stats": {
            "total": round(total, 2),
            "holdings": len(positions),
            "var_1d": _number(var[0] * total) if var else None,
            "var_1d_pct": _number(var[0], 4) if var else None,
            "beta": _number(beta_value),
            "largest": {"name": positions[0]["name"], "weight": positions[0]["weight"]},
        },
        "sectors": sector_rows,
        "positions": [
            {"ticker": p["ticker"], "name": p["name"], "sector": p["sector"],
             "value": p["value"], "weight": p["weight"], "beta": _number(betas.get(p["ticker"]))}
            for p in positions
        ],
        "prices": {
            "dates": [d.date().isoformat() for d in window.index],
            "series": [{"ticker": t, "name": names[t], "values": indexed(t)} for t in plotted],
            "benchmark": {"name": "Nifty 50", "values": indexed(NIFTY)},
            "truncated": len(positions) > MAX_SERIES,
        },
        "events": events,
    }


@router.get("/weather")
def weather() -> dict:
    sites = get_weather_outlook()
    if sites is None:
        raise HTTPException(status_code=503, detail="The weather forecast service is unreachable.")
    return {
        "sites": sites,
        "thresholds": {"rain_mm": HEAVY_RAIN_MM, "gust_kmh": GALE_GUST_KMH, "temp_c": HEAT_C},
        "source": "Open-Meteo 7-day forecast; India Meteorological Department thresholds",
    }
