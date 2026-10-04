"""Data for the Insights page: price history with event markers, exposure and weather."""

import math
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.nodes import EVENT_TYPES, HORIZON_SESSIONS
from app.risk import exposure, metrics
from app.tools.cache import cached
from app.tools.events import load_events
from app.tools.market import NIFTY, get_history, normalise_holdings
from app.tools.weather import (
    GALE_GUST_KMH, HEAT_C, HEAVY_RAIN_MM, KINDS, LOCATIONS, get_weather_history, get_weather_outlook, holdings_at,
)

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
    return _portfolio(request.holdings, request.range)


@cached(300)
def _portfolio(raw_holdings: list[dict] | None, span: str) -> dict:
    holdings, source = normalise_holdings(raw_holdings)
    closes, price_source = get_history([h["ticker"] for h in holdings])
    if closes is None:
        raise HTTPException(status_code=503, detail="Price history is unavailable right now.")

    view = exposure.snapshot(holdings, closes)
    if view is None:
        raise HTTPException(status_code=422, detail="None of the holdings could be priced.")
    positions, total, var, beta_value = view["positions"], view["total"], view["var"], view["beta"]
    sector_rows = [{**s, "value": round(s["value"], 2), "weight": round(s["weight"], 4),
                    "risk_share": _number(s["risk_share"], 4)} for s in view["sectors"]]

    # Price series, indexed to 100 at the start of the window so every holding
    # and the index share one axis.
    years = RANGE_YEARS[span]
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
            "largest": {"name": positions[0]["name"], "weight": round(positions[0]["weight"], 4)},
        },
        "sectors": sector_rows,
        "positions": [
            {"ticker": p["ticker"], "name": p["name"], "sector": p["sector"],
             "value": round(p["value"], 2), "weight": round(p["weight"], 4), "beta": _number(p["beta"]),
             "risk_share": _number(p["risk_share"], 4)}
            for p in positions
        ],
        "prices": {
            "dates": [d.date().isoformat() for d in window.index],
            "series": [{"ticker": t, "name": names[t], "values": indexed(t)} for t in plotted],
            "benchmark": {"name": "Nifty 50", "values": indexed(NIFTY)},
            "truncated": len(positions) > MAX_SERIES,
        },
        "events": events,
        "weather": _weather_on(window.index, positions),
    }


def _weather_on(dates: pd.DatetimeIndex, positions: list[dict]) -> dict | None:
    """Alert days at each site, placed on the chart's dates, plus the forecast
    days still ahead of the last price. None when the weather history is missing."""
    history = get_weather_history()
    if history is None:
        return None
    first, last = dates[0], len(dates) - 1
    marks: dict[tuple[int, int, str], dict] = {}
    for site_index, place in enumerate(LOCATIONS):
        for day, kind, value in history["sites"].get(place["name"], []):
            stamp = pd.Timestamp(day)
            if stamp < first:
                continue
            # A weekly point stands for the days up to it; a weekend falls to the next session.
            slot = min(int(dates.searchsorted(stamp)), last)
            mark = marks.setdefault((slot, site_index, kind),
                                    {"index": slot, "site": site_index, "kind": kind, "value": value, "date": day, "days": 0})
            mark["days"] += 1
            if value > mark["value"]:
                mark.update(value=value, date=day)

    outlook = get_weather_outlook()
    ahead = None
    if outlook is not None:
        by_name = {site["name"]: site for site in outlook}
        today = pd.Timestamp.now(tz="Asia/Kolkata").date().isoformat()
        days = sorted({d["date"] for site in outlook for d in site["days"] if d["date"] >= today})
        fields = {"rain": "rain_mm", "wind": "gust_kmh", "heat": "temp_c"}
        ahead = {
            "dates": days,
            "flags": [
                {"site": site_index, "date": d["date"], "kind": kind, "value": d[fields[kind]]}
                for site_index, place in enumerate(LOCATIONS)
                for d in by_name.get(place["name"], {}).get("days", [])
                for kind, (_, threshold) in KINDS.items()
                if d["date"] >= today and d[fields[kind]] is not None and d[fields[kind]] >= threshold
            ],
        }
    return {
        "sites": [{"name": place["name"], "relevance": place["relevance"],
                   "holdings": [{"name": h["name"], "operation": h["operation"]} for h in holdings_at(place, positions)]}
                  for place in LOCATIONS],
        "marks": sorted(marks.values(), key=lambda m: (m["index"], m["site"], m["kind"])),
        "forecast": ahead,
        "thresholds": {"rain_mm": HEAVY_RAIN_MM, "gust_kmh": GALE_GUST_KMH, "temp_c": HEAT_C},
        "through": history["through"],
        "source": history["source"],
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
