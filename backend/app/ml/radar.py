"""Alternative-data risk radar: localised weather and geopolitical signals,
linked to the holdings they reach, with a sized hedge and a check on how much
of the move the market has already made.

Pipeline for each signal:
  signal -> exposed holdings -> expected move from past analogues
         -> how much is already priced -> hedge versus its cost -> confidence
Every figure is computed here from live data and from data/trained/*.json.
"""

import json
import math
import time
from pathlib import Path

import yfinance as yf

from app.ml import assets
from app.ml.features import HORIZON, abnormal_returns
from app.risk import metrics
from app.tools import gdelt
from app.tools.events import load_events
from app.tools.market import NIFTY, get_macro
from app.tools.weather import LOCATIONS, get_weather_outlook
from app.agents.state import inr as indian_rupees

TRAINED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "trained"
ANALOGUE_RADIUS_KM = 400
PRICED_SESSIONS = 2
LIQUIDITY_TTL_SECONDS = 1800

# Hypothetical signals for rehearsing the response. They are always labelled as drills.
DRILLS = {
    "cyclone_gujarat": {"kind": "weather", "event_type": "cyclone", "site": "Jamnagar",
                        "title": "Severe cyclone forecast to make landfall near Jamnagar"},
    "cyclone_andhra": {"kind": "weather", "event_type": "cyclone", "site": "Visakhapatnam",
                       "title": "Severe cyclone forecast to make landfall near Visakhapatnam"},
    "cyclone_mumbai": {"kind": "weather", "event_type": "cyclone", "site": "Mumbai",
                       "title": "Severe cyclone forecast to pass close to Mumbai"},
    "middle_east_oil": {"kind": "geopolitical", "event_type": "oil_up",
                        "title": "Middle East conflict threatening oil supply"},
    "india_pakistan": {"kind": "geopolitical", "event_type": "geopolitical",
                       "title": "India-Pakistan military tension"},
}

_liquidity: dict[str, tuple[float, float]] = {}


def _trained(name: str) -> dict | None:
    try:
        return json.loads((TRAINED_DIR / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None


def _inr(amount: float) -> str:
    """Rupees without a sign, in Indian digit grouping."""
    return indian_rupees(abs(amount))


def _pct(fraction: float, digits: int = 1) -> str:
    return f"{fraction * 100:+.{digits}f}%"


# --------------------------------------------------------------------------- signals


def current_signals(scenario: str | None = None) -> list[dict]:
    """Live weather and geopolitical signals, plus a drill if one was asked for."""
    signals = []
    coordinates = {site["name"]: (site["lat"], site["lon"]) for site in LOCATIONS}

    for site in get_weather_outlook() or []:
        if not site.get("flags"):
            continue
        storm = "gale-force gusts" in site["flags"]
        signals.append({
            "key": f'weather|{site["name"]}|{",".join(site["flags"])}', "kind": "weather",
            "event_type": "cyclone" if storm else "flood" if "heavy rain" in site["flags"] else None,
            "title": f'{", ".join(site["flags"]).capitalize()} forecast at {site["name"]}',
            "lat": coordinates[site["name"]][0], "lon": coordinates[site["name"]][1],
            "detail": f'7-day forecast: peak rain {site["max_rain_mm"]} mm/day, gusts '
                      f'{site["max_gust_kmh"]} km/h, max temperature {site["max_temp_c"]} C.',
            "sources": ["Open-Meteo forecast against IMD thresholds"], "drill": False,
        })

    news = gdelt.signals()
    for key, theme in news["themes"].items():
        if not theme["elevated"] or theme["event_type"] == "cyclone" or news["stale"]:
            continue
        signals.append({
            "key": f"news|{key}|{theme['day']}", "kind": "geopolitical", "event_type": theme["event_type"],
            "title": theme["label"],
            "detail": f'{theme["articles"]:,} articles on {theme["day"][:4]}-{theme["day"][4:6]}-{theme["day"][6:]}, '
                      f'{theme["z"]:+.1f} standard deviations above the month\'s usual share of coverage.',
            "sources": [f'GDELT news volume ({theme["z"]:+.1f}σ)'], "drill": False,
        })

    drill = DRILLS.get(scenario or "")
    if drill:
        signal = {"key": f"drill|{scenario}", "kind": drill["kind"], "event_type": drill["event_type"],
                  "title": drill["title"], "sources": [], "drill": True,
                  "detail": "This is a drill: a hypothetical signal to rehearse the response. No such event "
                            "is in today's data."}
        if drill["kind"] == "weather":
            signal["lat"], signal["lon"] = coordinates[drill["site"]]
        signals.insert(0, signal)
    return signals


def corroboration(signal: dict) -> tuple[str, list[str]]:
    """How many independent sources back the signal."""
    if signal["drill"]:
        return "Drill", ["Hypothetical signal"]
    sources = list(signal["sources"])
    news = gdelt.signals()
    if signal["kind"] == "weather" and not news["stale"]:
        cyclone = news["themes"].get("cyclone_india")
        if cyclone and cyclone["elevated"]:
            sources.append(f'GDELT cyclone coverage ({cyclone["z"]:+.1f}σ, {cyclone["articles"]:,} articles)')
    indicators, _ = get_macro()
    vix = next((i for i in indicators if i["ticker"] == "^INDIAVIX"), None)
    if vix and vix["change_1m_pct"] >= 20:
        sources.append(f'India VIX up {vix["change_1m_pct"]:.0f}% in a month')
    return ("High" if len(sources) >= 3 else "Medium" if len(sources) == 2 else "Low"), sources


# --------------------------------------------------------------------------- expected move


def _storm_analogues(signal: dict) -> list[dict]:
    library = (_trained("storm_impact") or {}).get("library", [])
    return [s for s in library
            if assets.distance_km(signal["lat"], signal["lon"], s["lat"], s["lon"]) <= ANALOGUE_RADIUS_KM]


def expected_moves(signal: dict, positions: list[dict], closes) -> tuple[dict[str, dict], str]:
    """Per exposed ticker: {move, cases, where}. Also a sentence on the evidence base."""
    moves: dict[str, dict] = {}
    if signal["kind"] == "weather":
        storm_stats = _trained("storm_impact") or {}
        analogues = _storm_analogues(signal)
        group = storm_stats.get("exposed", {})
        for p in positions:
            found = assets.exposure(p["ticker"], signal["lat"], signal["lon"])
            if not found:
                continue
            symbol = p["ticker"].split(".")[0]
            own = [s["exposed_moves"][symbol] for s in analogues if symbol in s["exposed_moves"]]
            if own:
                moves[p["ticker"]] = {"move": sum(own) / len(own), "cases": len(own), "where": assets.describe(found),
                                      "basis": "own"}
            elif group.get("cases"):
                moves[p["ticker"]] = {"move": group["mean"], "cases": group["cases"],
                                      "where": assets.describe(found), "basis": "group"}
        names = ", ".join(f'{s["name"]} ({s["date"][:4]})' for s in analogues[-4:])
        basis = (f"{len(analogues)} past storms within {ANALOGUE_RADIUS_KM} km"
                 + (f": {names}" if names else "")
                 + f". Across all {storm_stats.get('storms', 0)} storms since 2014, companies with assets nearby "
                   f"moved {_pct(group.get('mean', 0))} on average beyond the market over {HORIZON} sessions "
                   f"({group.get('cases', 0)} cases), against {_pct(storm_stats.get('not_exposed', {}).get('mean', 0))} "
                   f"for companies with none. NOAA IBTrACS tracks, WRI plant locations.")
        return moves, basis

    events = [e for e in load_events() if e["type"] == signal["event_type"]]
    for p in positions:
        found = []
        for event in events:
            window = metrics.event_window_returns(closes[[p["ticker"], NIFTY]], event["date"], HORIZON)
            if window is not None and not (math.isnan(window[p["ticker"]]) or math.isnan(window[NIFTY])):
                found.append(float(window[p["ticker"]] - window[NIFTY]))
        if len(found) >= 2:
            moves[p["ticker"]] = {"move": sum(found) / len(found), "cases": len(found),
                                  "where": "market-wide event", "basis": "own"}
    titles = ", ".join(e["title"] for e in events[-3:])
    return moves, (f"{len(events)} past events of this kind in the event corpus, most recently: {titles}. "
                   f"Moves are returns beyond the Nifty over {HORIZON} sessions.")


# --------------------------------------------------------------------------- pricing, cost, liquidity


def already_priced(losers: list[dict], moves: dict[str, dict], closes) -> float:
    """Share of the expected loss that the exposed holdings have already made
    over the last sessions, beyond the market (0 to 1)."""
    expected = sum(p["value"] * moves[p["ticker"]]["move"] for p in losers)
    if expected >= 0:
        return 0.0
    recent = 0.0
    for p in losers:
        abnormal = abnormal_returns(closes[p["ticker"]], closes[NIFTY])["abnormal"].iloc[-PRICED_SESSIONS:]
        recent += p["value"] * float(abnormal.sum())
    return max(0.0, min(1.0, recent / expected))


def _turnover_text(amount: float) -> str:
    return f"₹{amount / 1e7:,.0f} crore" if amount >= 1e7 else f"₹{amount / 1e5:,.1f} lakh"


def put_cost_fraction(annual_volatility: float, sessions: int = HORIZON) -> float:
    """At-the-money put premium as a fraction of the amount protected
    (Black-Scholes with a zero interest rate)."""
    spread = annual_volatility * math.sqrt(sessions / 252)
    return 2 * (0.5 * (1 + math.erf(spread / 2 / math.sqrt(2)))) - 1


def daily_turnover(tickers: list[str]) -> dict[str, float]:
    """Average rupee value traded per day over the last 20 sessions."""
    now = time.time()
    missing = [t for t in tickers if t not in _liquidity or now - _liquidity[t][0] > LIQUIDITY_TTL_SECONDS]
    if missing:
        try:
            data = yf.download(missing, period="2mo", progress=False, auto_adjust=True, timeout=10)
            traded = (data["Close"] * data["Volume"]).tail(20).mean()
            for ticker in missing:
                value = float(traded[ticker]) if hasattr(traded, "__getitem__") and ticker in traded else float(traded)
                if not math.isnan(value) and value > 0:
                    _liquidity[ticker] = (now, value)
        except Exception:
            pass  # liquidity is reported as unknown
    return {t: _liquidity[t][1] for t in tickers if t in _liquidity}


# --------------------------------------------------------------------------- hedge


def build_hedge(signal: dict, positions: list[dict], moves: dict[str, dict], closes) -> dict:
    """Decide, holding by holding, whether protection is worth its cost.

    A holding is worth protecting when the loss still expected on it is larger
    than the estimated price of a put on it for the same few sessions.
    """
    losers = [p for p in positions if p["ticker"] in moves and moves[p["ticker"]]["move"] < 0]
    gainers = [p for p in positions if p["ticker"] in moves and moves[p["ticker"]]["move"] > 0]
    loss = -sum(p["value"] * moves[p["ticker"]]["move"] for p in losers)
    offsets = sum(p["value"] * moves[p["ticker"]]["move"] for p in gainers)
    hedge = {"action": "none", "expected_loss": round(loss), "offsets": round(offsets), "priced_in": None,
             "instrument": None, "notional": None, "cost": None, "liquidity": None, "summary": ""}
    if not moves:
        hedge["summary"] = "None of your holdings is linked to this signal, so no hedge is needed."
        return hedge
    if not losers:
        hedge["summary"] = (f"Similar past events were on average positive for every holding they reached "
                            f"(about {_inr(offsets)} gain expected), so no hedge is recommended.")
        return hedge

    # A drill has no real market reaction to measure, so nothing counts as priced in.
    priced = 0.0 if signal["drill"] else already_priced(losers, moves, closes)
    returns = metrics.daily_returns(closes)
    implied_ratio = (_trained("hedge_cost") or {}).get("implied_over_realised", {}).get("median", 1.0)

    worth, total_cost = [], 0.0
    for p in losers:
        volatility = float(returns[p["ticker"]].std()) * math.sqrt(252) * implied_ratio
        cost = p["value"] * put_cost_fraction(volatility)
        remaining = -p["value"] * moves[p["ticker"]]["move"] * (1 - priced)
        total_cost += cost
        if remaining > cost:
            worth.append({**p, "cost": cost, "remaining": remaining})

    turnover = daily_turnover([p["ticker"] for p in losers])
    priced_by_turnover = [p for p in losers if p["ticker"] in turnover]
    if priced_by_turnover:
        tightest = max(priced_by_turnover, key=lambda p: p["value"] / turnover[p["ticker"]])
        share = tightest["value"] / turnover[tightest["ticker"]]
        hedge["liquidity"] = (
            f'{tightest["name"]} trades about {_turnover_text(turnover[tightest["ticker"]])} a day and your position '
            f'is under {max(share * 100, 0.01):.2f}% of that, so it can be reduced within a session.'
            if share < 0.05 else
            f'Your {tightest["name"]} position is {share * 100:.1f}% of a day\'s trading; reducing it would '
            f'take more than one session.')

    remaining_loss = loss * (1 - priced)
    offset_note = f" Gains expected elsewhere in the portfolio offset about {_inr(offsets)} of that." if offsets else ""
    hedge.update(priced_in=None if signal["drill"] else round(priced, 2), cost=round(total_cost),
                 notional=round(sum(p["value"] for p in losers)))

    if priced >= 0.8:
        hedge["action"] = "monitor"
        hedge["summary"] = (f"Your exposed holdings have already fallen by about {priced * 100:.0f}% of the "
                            f"{_inr(loss)} expected. The market has largely priced this in, so a new hedge "
                            f"would mostly lock in the loss.")
    elif not worth:
        hedge["action"] = "monitor" if remaining_loss > offsets else "none"
        hedge["summary"] = (f"Expected further loss on exposed holdings is {_inr(remaining_loss)}, less than the "
                            f"roughly {_inr(total_cost)} it would cost to protect them for {HORIZON} sessions."
                            f"{offset_note} Do not hedge; keep watching.")
    else:
        value = sum(p["value"] for p in worth)
        names = ", ".join(p["name"] for p in worth[:3])
        cost = sum(p["cost"] for p in worth)
        remaining = sum(p["remaining"] for p in worth)
        hedge.update(action="hedge", cost=round(cost), notional=round(value),
                     instrument=f"Protective puts on {names}, or sell part of the position")
        hedge["summary"] = (f"Protect {names}: the further loss expected there is {_inr(remaining)}, more than the "
                            f"roughly {_inr(cost)} that {HORIZON}-session put protection on {_inr(value)} would cost, "
                            f"so hedging pays if you act before the move is priced. This loss is measured beyond "
                            f"the market, so shorting the index would not offset it.{offset_note}")
    return hedge


# --------------------------------------------------------------------------- alerts


def radar_alerts(positions: list[dict], closes, scenario: str | None = None) -> list[dict]:
    """One alert per signal that reaches the portfolio."""
    alerts = []
    for signal in current_signals(scenario):
        if signal["event_type"] is None:
            continue
        moves, basis = expected_moves(signal, positions, closes)
        if not moves and not signal["drill"]:
            continue  # a real signal that touches none of the holdings is not this user's alert
        hedge = build_hedge(signal, positions, moves, closes)
        confidence, sources = corroboration(signal)

        names = {p["ticker"]: p for p in positions}
        exposed = "; ".join(
            f'{names[t]["name"]} {_pct(m["move"])} expected'
            + (f' ({m["where"]})' if signal["kind"] == "weather" else "")
            + ("" if m["basis"] == "own" else " [group average]")
            for t, m in sorted(moves.items(), key=lambda kv: kv[1]["move"])[:4]
        )
        priced = hedge["priced_in"]
        priced_note = ("" if priced is None else
                       f" Already priced in: about {priced * 100:.0f}% of the expected loss, judging by the last "
                       f"{PRICED_SESSIONS} sessions.")
        severity = ("info" if signal["drill"] else
                    "critical" if hedge["action"] == "hedge" and confidence == "High" else
                    "warning" if hedge["action"] in ("hedge", "monitor") else "info")
        alerts.append({
            "severity": severity, "category": "alt-data", "key": signal["key"], "holding": None,
            "title": ("Drill: " if signal["drill"] else "") + signal["title"],
            "detail": f'{signal["detail"]} '
                      + (f"Holdings it reaches: {exposed}." if exposed else "It reaches none of your holdings.")
                      + priced_note,
            "recommendation": hedge["summary"] + (f' {hedge["liquidity"]}' if hedge["liquidity"] else ""),
            "basis": f"Confidence {confidence}: {'; '.join(sources)}. {basis}",
            "hedge": {**hedge, "confidence": confidence, "drill": signal["drill"]},
        })
    return alerts
