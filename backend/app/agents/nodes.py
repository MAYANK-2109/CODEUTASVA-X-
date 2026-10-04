"""The seven agents. Each reads shared state and returns findings, evidence and a
one-line summary. Numbers come from tools and app.risk.metrics, never the LLM."""

import json
import math
import re
import time
from functools import wraps
from pathlib import Path

from app.agents import llm
from app.agents.state import State, evidence, inr, pct
from app.ingestion.news import get_news
from app.risk import metrics
from app.tools import sentiment as sentiment_tool
from app.tools import vector_store
from app.tools.events import tokenize
from app.tools.market import CROSS_ASSETS, NIFTY, get_cross_assets, get_history, get_macro
from app.tools.weather import (
    GALE_GUST_KMH, HEAT_C, HEAVY_RAIN_MM, LOCATIONS, get_weather_outlook, holdings_at, sites_named_in,
)

HORIZON_SESSIONS = 5
MAX_EVENTS = 6
MAX_SCENARIOS = 2   # a question may name more than one event; this many are analysed
# A size stated in the question ("15%", "$110", "50 bps") that the event study does not scale to.
STATED_SIZE = re.compile(r"(?:[$₹]\s?\d[\d,.]*|\d[\d,.]*\s?(?:%|percent|per cent|bps|basis points))", re.I)
MAX_FORECAST_HOLDINGS = 8
MAX_INDEXED_HEADLINES = 5
ROUTING_EVAL_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "trained" / "routing_eval.json"

UP = r"(?:up|spik\w*|surg\w*|ris\w*|rose|jump\w*|soar\w*|higher|shock|expensive|hik\w*|rais\w*|increas\w*|tighten\w*)"
DOWN = r"(?:down|crash\w*|fall\w*|fell|drop\w*|plung\w*|slump\w*|lower|cheap\w*|cut\w*|reduc\w*|eas\w*)"
OIL = r"(?:crude|oil|brent|opec)"
RATES = r"(?:rates?|repo|rbi)"
RUPEE = r"(?:rupee|inr)"


def _either_order(a: str, b: str) -> str:
    return rf"\b{a}\b.*\b{b}\b|\b{b}\b.*\b{a}\b"


# Checked in order, so the specific market drivers come before the broad shocks.
EVENT_TYPES = {
    "cyclone": ("cyclone or severe storm", r"\b(?:cyclon\w*|hurricane|typhoon|storm|landfall)\b"),
    "flood": ("flooding", r"\b(?:flood\w*|cloudburst|heavy rain\w*)\b"),
    "monsoon_deficit": ("weak monsoon or drought", r"\b(?:drought|monsoon|el nino|dry spell|rainfall deficit)\b"),
    "oil_up": ("crude oil price spike", _either_order(OIL, UP)),
    "oil_down": ("crude oil price fall", _either_order(OIL, DOWN)),
    "rate_hike": ("interest rate hike", _either_order(RATES, UP)),
    "rate_cut": ("interest rate cut", _either_order(RATES, DOWN)),
    "currency_weak": ("rupee depreciation", _either_order(RUPEE, DOWN) + r"|\b(?:depreciat\w*|weak\w* rupee)\b"),
    "geopolitical": ("geopolitical shock", r"\b(?:war|attack\w*|airstrike\w*|conflict|border|tariffs?|sanctions?|terror\w*|invasion)\b"),
    "market_shock": ("broad market shock", r"\b(?:crash\w*|sell-?off|pandemic|lockdown|recession|meltdown)\b"),
}
GENERAL_NEWS_QUERY = "Nifty Sensex stock market"

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "event_types": {"type": "array", "items": {"type": "string", "enum": list(EVENT_TYPES)}},
        "news_query": {"type": "string"},
    },
    "required": ["event_types", "news_query"],
    "additionalProperties": False,
}


def agent(name: str):
    """Time a node and turn its `summary`/`status` into a trace entry."""

    def decorate(fn):
        @wraps(fn)
        def node(state: State) -> dict:
            started = time.perf_counter()
            out = fn(state)
            out["trace"] = [
                {
                    "agent": name,
                    "summary": out.pop("summary"),
                    "status": out.pop("status", "done"),
                    "ms": round((time.perf_counter() - started) * 1000),
                }
            ]
            return out

        return node

    return decorate


# --------------------------------------------------------------------------- supervisor


def _routing_record(planner: str) -> str:
    """How often this router named exactly the right events on the labelled question set."""
    try:
        measured = json.loads(ROUTING_EVAL_FILE.read_text())["llm" if planner == "llm" else "rules"]
        return (f' On a labelled set of {measured["questions"]} questions it named exactly the right events '
                f'for {measured["correct"]} ({measured["accuracy"]:.0%}).')
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def classify_events(query: str) -> list[str]:
    """Every event type the question names, in the order the types are listed."""
    text = query.lower()
    return [event_type for event_type, (_, pattern) in EVENT_TYPES.items() if re.search(pattern, text)]


def classify_event(query: str) -> str | None:
    found = classify_events(query)
    return found[0] if found else None


def _llm_plan(query: str) -> dict | None:
    result = llm.complete(
        "You route questions for a portfolio risk assistant covering Indian equities. "
        "List in event_types every event the question asks about, most important first. A question "
        "that names two events gets both. Leave the list empty for a general question about the "
        "portfolio, or about a company, that names no such event. Write news_query as 3-6 search "
        "keywords for the question.",
        query,
        PLAN_SCHEMA,
    )
    if not isinstance(result, dict) or not isinstance(result.get("event_types"), list) \
            or not isinstance(result.get("news_query"), str):
        return None
    result["event_types"] = [t for t in dict.fromkeys(result["event_types"]) if t in EVENT_TYPES]
    return result


@agent("supervisor")
def supervisor(state: State) -> dict:
    query = state["query"]
    planned = _llm_plan(query)
    if planned:
        found, news_query, planner = planned["event_types"], planned["news_query"].strip(), "llm"
    else:
        found = classify_events(query)
        news_query = " ".join(tokenize(query)[:6]) if found else GENERAL_NEWS_QUERY
        planner = "rules"
    news_query = news_query or GENERAL_NEWS_QUERY
    event_types, left_out = found[:MAX_SCENARIOS], found[MAX_SCENARIOS:]
    event_type = event_types[0] if event_types else None

    # Whatever part of the question is not analysed is said, not dropped silently.
    gaps = []
    if left_out:
        gaps.append("The question also names " + ", ".join(EVENT_TYPES[t][0] for t in left_out)
                    + f", which is not analysed: at most {MAX_SCENARIOS} scenarios are covered per question")
    if len(event_types) > 1:
        gaps.append("The question names " + " and ".join(EVENT_TYPES[t][0] for t in event_types)
                    + ". The estimate averages past events of either kind; no past event in the library "
                      "combined them, so their effects are not added together")
    sizes = STATED_SIZE.findall(query) if event_types else []
    if sizes:
        gaps.append(f"The question states a size ({', '.join(dict.fromkeys(s.strip() for s in sizes))}). The "
                    "estimate is the average move in past events of this kind and is not scaled to that size")

    label = " and ".join(EVENT_TYPES[t][0] for t in event_types) if event_types else None
    subtasks = {
        "sentiment": f'Score news sentiment for "{news_query}" and for the holdings',
        "weather_macro": "Check the 7-day weather outlook at key economic sites and the macro backdrop",
        "historical": f"Find past {label} events and measure how each holding moved" if label
        else "Search past events similar to the question and measure how each holding moved",
        "risk": "Compute exposure, VaR, beta and the event scenario from the findings",
        "hedging": "Size hedges from the risk numbers",
    }
    return {
        "plan": {
            "event_type": event_type,
            "event_types": event_types,
            "event_label": label,
            "news_query": news_query,
            "subtasks": subtasks,
            "planner": planner,
        },
        "gaps": gaps,
        "summary": f"Identified scenario: {label}" if label else "General portfolio question, no specific event",
    }


# --------------------------------------------------------------------------- sentiment

@agent("sentiment")
def sentiment(state: State) -> dict:
    keywords = state["plan"]["news_query"]
    names = [h["name"] for h in state["holdings"][:8]]
    holdings_query = " OR ".join(f'"{n}"' for n in names) + " when:3d"

    # A long keyword list often matches nothing recent, so widen once.
    topic_items, topic_window = get_news(f"{keywords} when:7d", 10), "last 7 days"
    if not topic_items:
        short = " ".join(keywords.split()[:2])
        topic_items, topic_window = get_news(f"{short} India when:30d", 10), "last 30 days"
    held_items = get_news(holdings_query, 10)
    vector_store.index_news(topic_items + held_items)
    topic = sentiment_tool.score_headlines(topic_items)
    held = sentiment_tool.score_headlines(held_items)

    rows, gaps = [], []
    scopes = ((f"Scenario news ({topic_window})", topic), ("News on your holdings (last 3 days)", held))
    for scope, result in scopes:
        if result is None:
            gaps.append(f"{scope}: no headlines returned")
            continue
        rows.append(
            evidence(
                "S", len(rows) + 1,
                f'{scope}: mean sentiment {result["mean"]:+.2f} ({sentiment_tool.label(result["mean"])}) '
                f'across {result["count"]} headlines, {result["negative"]} negative and {result["positive"]} positive',
                sentiment_tool.source_label(),
            )
        )
    worst = topic["items"][0] if topic else None
    if worst and worst["score"] <= sentiment_tool.cutoffs()[1]:
        rows.append(
            evidence(
                "S", len(rows) + 1,
                f'Most negative scenario headline ({worst["score"]:+.2f}): "{worst["title"]}"',
                worst["source"] or "Google News",
            )
        )

    # Headlines the ingestion stream indexed earlier, found by meaning rather than by keyword.
    seen = {item["title"] for item in topic_items + held_items}
    indexed, backend = vector_store.search_news(state["query"], MAX_INDEXED_HEADLINES + len(seen))
    indexed = [item for item in indexed if item["title"] not in seen][:MAX_INDEXED_HEADLINES]
    earlier = sentiment_tool.score_headlines(indexed)
    if earlier:
        closest = max(indexed, key=lambda item: item["similarity"])
        rows.append(
            evidence(
                "S", len(rows) + 1,
                f'Earlier headlines closest to the question: mean sentiment {earlier["mean"]:+.2f} '
                f'({sentiment_tool.label(earlier["mean"])}) across {earlier["count"]} headlines. Closest '
                f'(similarity {closest["similarity"]:.2f}): "{closest["title"]}"',
                ("Pinecone vector search" if backend == vector_store.PINECONE else "local text search")
                + " over indexed headlines; " + sentiment_tool.source_label(),
            )
        )

    primary = topic or held
    return {
        "findings": {"sentiment": {"topic": topic, "holdings": held, "indexed": earlier}},
        "evidence": rows,
        "gaps": gaps,
        "status": "done" if primary else "degraded",
        "summary": f'{sentiment_tool.backend()} sentiment {sentiment_tool.label(primary["mean"])} '
        f'({primary["mean"]:+.2f}, {primary["count"]} headlines)'
        if primary else "News feed unavailable",
    }


# --------------------------------------------------------------------------- weather and macro


def _macro_flags(indicators: list[dict]) -> list[str]:
    by_ticker = {i["ticker"]: i for i in indicators}
    flags = []
    vix = by_ticker.get("^INDIAVIX")
    if vix and (vix["value"] >= 20 or vix["change_1m_pct"] >= 20):
        flags.append("volatility elevated")
    nifty = by_ticker.get(NIFTY)
    if nifty and nifty["change_1m_pct"] <= -5:
        flags.append("market in drawdown")
    brent = by_ticker.get("BZ=F")
    if brent and brent["change_1m_pct"] >= 10:
        flags.append("crude rising fast")
    rupee = by_ticker.get("INR=X")
    if rupee and rupee["change_1m_pct"] >= 2:
        flags.append("rupee weakening")
    return flags


@agent("weather_macro")
def weather_macro(state: State) -> dict:
    rows, gaps = [], []

    outlook = get_weather_outlook()
    flagged = [site for site in outlook or [] if site["flags"]]
    if outlook is None:
        gaps.append("Weather forecast service unreachable")
    elif not flagged:
        rows.append(
            evidence(
                "W", 1,
                f"7-day forecast for {len(outlook)} economic sites ({', '.join(s['name'] for s in outlook)}): "
                f"none reaches the heavy-rain ({HEAVY_RAIN_MM} mm/day), gale ({GALE_GUST_KMH} km/h gusts) "
                f"or extreme-heat ({HEAT_C} C) thresholds",
                "Open-Meteo forecast API, IMD thresholds",
            )
        )
    def named(site: dict) -> str:
        present = holdings_at(site, state["holdings"])
        if not present:
            return "None of your holdings has a mapped operation there"
        return "Your holdings with operations there: " + "; ".join(
            f'{h["name"]} ({h["operation"]})' for h in present)

    for site in flagged:
        rows.append(
            evidence(
                "W", len(rows) + 1,
                f'{site["name"]} ({site["relevance"]}): {", ".join(site["flags"])} in the next 7 days, '
                f'peak rain {site["max_rain_mm"]} mm/day, gusts {site["max_gust_kmh"]} km/h, '
                f'max temperature {site["max_temp_c"]} C. {named(site)}',
                "Open-Meteo forecast API, IMD thresholds; site-to-company map",
            )
        )
    # A question about a region is about the companies there, alert or not.
    mentioned = [s for s in sites_named_in(state["query"], outlook or []) if s not in flagged]
    for site in mentioned:
        rows.append(
            evidence(
                "W", len(rows) + 1,
                f'The question names the {site["name"]} area ({site["relevance"]}). {named(site)}',
                "Site-to-company map",
            )
        )
    exposed = [
        {"site": site["name"], "holdings": holdings_at(site, state["holdings"])}
        for site in [*flagged, *mentioned]
    ]

    indicators, as_of = get_macro()
    if not indicators:
        gaps.append("Macro indicators unavailable")
    macro_rows = [
        evidence(
            "M", n,
            f'{i["label"]}: {i["value"]:,.2f}, {i["change_1m_pct"]:+.1f}% over one month',
            "Yahoo Finance",
        )
        for n, i in enumerate(indicators, 1)
    ]
    regime = _macro_flags(indicators)

    parts = []
    if outlook is not None:
        parts.append(f"{len(flagged)} weather alert(s)" if flagged else "no weather alerts")
    if indicators:
        parts.append("macro: " + (", ".join(regime) if regime else "no stress flags"))
    return {
        "findings": {
            "weather_macro": {"flagged_sites": flagged, "exposed": exposed, "weather_ok": outlook is not None,
                              "indicators": indicators, "regime": regime}
        },
        "evidence": rows + macro_rows,
        "gaps": gaps,
        "status": "degraded" if gaps else "done",
        "summary": "; ".join(parts).capitalize() if parts else "Weather and macro data unavailable",
    }


# --------------------------------------------------------------------------- historical


def _clean(value) -> float | None:
    return None if value is None or math.isnan(value) else float(value)


def _cross_asset_moves(events: list[dict]) -> list[dict]:
    """How crude, gas, gold and the rupee moved over each event window, each
    measured on its own trading calendar. Empty without events or prices."""
    closes = get_cross_assets() if events else None
    if closes is None:
        return []
    moves = []
    for ticker, label in CROSS_ASSETS.items():
        if ticker not in closes.columns:
            continue
        series = closes[[ticker]].dropna()
        seen = []
        for event in events:
            window = metrics.event_window_returns(series, event["date"], HORIZON_SESSIONS)
            value = None if window is None else _clean(window[ticker])
            if value is not None:
                seen.append(value)
        if seen:
            moves.append({"ticker": ticker, "label": label, "mean": sum(seen) / len(seen),
                          "min": min(seen), "max": max(seen), "n": len(seen)})
    return moves


@agent("historical")
def historical(state: State) -> dict:
    tickers = [h["ticker"] for h in state["holdings"]]
    names = {h["ticker"]: h["name"] for h in state["holdings"]}
    closes, source = get_history(tickers)
    if closes is None:
        return {
            "findings": {"historical": {"events": [], "per_holding": {}, "nifty_mean": None}},
            "gaps": ["Price history unavailable, so past events could not be measured"],
            "status": "degraded",
            "summary": "Price history unavailable",
        }

    # A question that names no event has nothing to compare with. A similarity
    # search would still return its nearest events, however unrelated, and a
    # forecast built on them would be invented.
    event_types = state["plan"]["event_types"]
    if not event_types:
        return {
            "findings": {"historical": {"events": [], "per_holding": {}, "nifty_mean": None}},
            "summary": "General question: no past event to compare with",
        }
    per_type = MAX_EVENTS if len(event_types) == 1 else max(2, MAX_EVENTS // len(event_types))
    matches, backends = [], set()
    for event_type in event_types:
        found, used = vector_store.search_events(state["query"], event_type, per_type)
        matches += found
        backends.add(used)
    backend = vector_store.PINECONE if backends == {vector_store.PINECONE} else vector_store.LOCAL
    search = "Pinecone vector search" if backend == vector_store.PINECONE else "local text search"
    events, rows = [], []
    for event in matches:
        window = metrics.event_window_returns(closes, event["date"], HORIZON_SESSIONS)
        first_day = metrics.event_window_returns(closes, event["date"], 1)
        if window is None:
            continue
        returns = {t: _clean(window.get(t)) for t in tickers}
        returns = {t: r for t, r in returns.items() if r is not None}
        nifty = _clean(window.get(NIFTY))
        if nifty is None or not returns:
            continue
        events.append({**event, "nifty": nifty, "returns": returns})
        note = f' Note: {event["confound"]}.' if event.get("confound") else ""
        rows.append(
            evidence(
                "H", len(rows) + 1,
                f'{event["title"]} ({event["date"]}): Nifty 50 moved {pct(_clean(first_day[NIFTY]) or 0)} '
                f"on the day and {pct(nifty)} over {HORIZON_SESSIONS} sessions.{note}",
                f'{search} (similarity {event["similarity"]:.2f}), event {event["id"]}; Yahoo Finance prices',
            )
        )

    per_holding = {}
    for ticker in tickers:
        moves = [e["returns"][ticker] for e in events if ticker in e["returns"]]
        if moves:
            per_holding[ticker] = {
                "mean": sum(moves) / len(moves), "min": min(moves), "max": max(moves), "n": len(moves),
            }
    nifty_mean = sum(e["nifty"] for e in events) / len(events) if events else None

    event_rows = [r["id"] for r in rows]
    if per_holding:
        ranked = sorted(per_holding.items(), key=lambda kv: kv[1]["mean"])
        shown = ranked if len(ranked) <= 8 else ranked[:5] + ranked[-3:]
        rows.append(
            evidence(
                "H", len(rows) + 1,
                f"Average {HORIZON_SESSIONS}-session move per holding across these events: "
                + "; ".join(f'{names[t]} {pct(s["mean"])} (n={s["n"]})' for t, s in shown)
                + f". Nifty 50 average {pct(nifty_mean)}",
                "Event study on Yahoo Finance prices",
            )
        )

    gaps = []
    cross_assets, cross_row = _cross_asset_moves(events), None
    if cross_assets:
        rows.append(
            evidence(
                "H", len(rows) + 1,
                f"Commodities and the rupee over the same {HORIZON_SESSIONS} sessions, averaged across these events: "
                + "; ".join(f'{c["label"]} {pct(c["mean"])} (range {pct(c["min"])} to {pct(c["max"])}, n={c["n"]})'
                            for c in cross_assets)
                + ". A rise in USD/INR is a weaker rupee",
                "Event study on Yahoo Finance futures and currency prices",
            )
        )
        cross_row = rows[-1]["id"]
    elif events:
        gaps.append("Commodity and currency prices unavailable, so there is no commodity forecast")
    if source == "cache":
        gaps.append("Live prices unavailable; used the last saved price history")
    if not events:
        gaps.append("No comparable past events in the corpus for this question")
    return {
        "findings": {"historical": {"events": events, "per_holding": per_holding, "nifty_mean": nifty_mean,
                                    "cross_assets": cross_assets, "cross_row": cross_row,
                                    "event_rows": event_rows, "search": search}},
        "evidence": rows,
        "gaps": gaps,
        "status": "done" if events else "degraded",
        "summary": f"{len(events)} comparable events via {search}; Nifty averaged {pct(nifty_mean)} "
        f"over {HORIZON_SESSIONS} sessions"
        if events else "No comparable past events found",
    }


# --------------------------------------------------------------------------- risk


@agent("risk")
def risk(state: State) -> dict:
    holdings = state["holdings"]
    closes, _ = get_history([h["ticker"] for h in holdings])
    if closes is None:
        return {
            "findings": {"risk": None},
            "gaps": ["Risk metrics unavailable without prices"],
            "status": "degraded",
            "summary": "Prices unavailable",
        }

    latest = closes.iloc[-1]
    positions = []
    for h in holdings:
        price = _clean(latest.get(h["ticker"]))
        if price is not None:
            positions.append({**h, "price": price, "value": price * h["units"]})
    unpriced = [h["name"] for h in holdings if h["ticker"] not in {p["ticker"] for p in positions}]
    if not positions:
        return {
            "findings": {"risk": None},
            "gaps": ["None of the holdings could be priced"],
            "status": "degraded",
            "summary": "No holdings could be priced",
        }

    total = sum(p["value"] for p in positions)
    weights = {p["ticker"]: p["value"] / total for p in positions}
    sectors: dict[str, float] = {}
    for p in positions:
        sectors[p["sector"]] = sectors.get(p["sector"], 0.0) + p["value"] / total
    sector_rank = sorted(sectors.items(), key=lambda kv: -kv[1])
    top = max(positions, key=lambda p: p["value"])

    rows = [
        evidence("R", 1, f"Portfolio value {inr(total)} across {len(positions)} priced holdings "
                         f"(closing prices of {closes.index[-1].date()})", "Yahoo Finance"),
        evidence("R", 2, "Sector exposure: " + ", ".join(f"{s} {pct(w, signed=False)}" for s, w in sector_rank)
                 + f'. Largest position: {top["name"]} at {pct(weights[top["ticker"]], signed=False)}',
                 "Computed from holdings and prices"),
    ]

    returns = metrics.daily_returns(closes)
    var = metrics.historical_var(metrics.portfolio_returns(returns, weights))
    var_5d = var_row = beta_row = None
    if var:
        var_5d = var[0] * math.sqrt(HORIZON_SESSIONS) * total
        rows.append(
            evidence("R", len(rows) + 1,
                     f"1-day 95% VaR {pct(var[0], signed=False)} ({inr(var[0] * total)}), expected shortfall "
                     f"{pct(var[1], signed=False)} ({inr(var[1] * total)}); {HORIZON_SESSIONS}-day 95% VaR "
                     f"{inr(var_5d)} by square-root-of-time scaling",
                     f"Historical simulation, last {len(returns)} sessions"))
        var_row = rows[-1]["id"]

    betas = {t: metrics.beta(returns[t], returns[NIFTY]) for t in weights}
    betas = {t: b for t, b in betas.items() if b is not None}
    portfolio_beta = sum(weights[t] * b for t, b in betas.items()) if betas else None
    if portfolio_beta is not None:
        rows.append(evidence("R", len(rows) + 1, f"Portfolio beta to Nifty 50: {portfolio_beta:.2f}",
                             f"Daily returns, last {len(returns)} sessions"))
        beta_row = rows[-1]["id"]

    # Per-holding risk, so "which holding is riskiest" has evidence behind it.
    volatility = {t: float(returns[t].std()) * math.sqrt(metrics.TRADING_DAYS_1Y)
                  for t in weights if returns[t].notna().sum() >= 60}
    if volatility:
        riskiest = sorted(positions, key=lambda p: -volatility.get(p["ticker"], 0))[:5]
        rows.append(evidence(
            "R", len(rows) + 1,
            "Riskiest holdings by 1-year annualised volatility: " + "; ".join(
                f'{p["name"]} {pct(volatility[p["ticker"]], signed=False)}'
                + (f' (beta {betas[p["ticker"]]:.2f})' if p["ticker"] in betas else "")
                for p in riskiest if p["ticker"] in volatility),
            f"Daily returns, last {len(returns)} sessions"))

    history = state["findings"].get("historical", {})
    scenario = None
    event_moves = []
    for event in history.get("events", []):
        covered = {t: r for t, r in event["returns"].items() if t in weights}
        cover_weight = sum(weights[t] for t in covered)
        if cover_weight > 0:
            move = sum(weights[t] * r for t, r in covered.items()) / cover_weight
            event_moves.append((move, event["title"]))
    if event_moves:
        mean_move = sum(m for m, _ in event_moves) / len(event_moves)
        worst, best = min(event_moves), max(event_moves)
        contributions = sorted(
            (
                {"ticker": p["ticker"], "name": p["name"], "value": p["value"],
                 "mean": history["per_holding"][p["ticker"]]["mean"],
                 "pnl": p["value"] * history["per_holding"][p["ticker"]]["mean"],
                 "beta": betas.get(p["ticker"])}
                for p in positions if p["ticker"] in history["per_holding"]
            ),
            key=lambda c: c["pnl"],
        )
        scenario = {
            "mean": mean_move, "pnl": mean_move * total, "worst": worst, "best": best,
            "n": len(event_moves), "contributions": contributions,
        }
        rows.append(
            evidence("R", len(rows) + 1,
                     f"Applied to today's weights, the {len(event_moves)} comparable events imply an average "
                     f"{HORIZON_SESSIONS}-session move of {pct(mean_move)} ({inr(mean_move * total)}); worst "
                     f"{pct(worst[0])} ({worst[1]}), best {pct(best[0])} ({best[1]})",
                     "Event study on current portfolio weights"))
        scenario["row"] = rows[-1]["id"]
        if contributions:
            # The biggest rupee effects in either direction; the rest are named as a count.
            shown = sorted(contributions, key=lambda c: -abs(c["pnl"]))[:MAX_FORECAST_HOLDINGS]
            shown.sort(key=lambda c: c["pnl"])
            rest = len(contributions) - len(shown)
            rows.append(evidence(
                "R", len(rows) + 1,
                f"Forecast per holding over {HORIZON_SESSIONS} sessions, from the average of these events: "
                + "; ".join(f'{c["name"]} {pct(c["mean"])} ({inr(c["pnl"])})' for c in shown)
                + (f". {rest} smaller holding(s) not listed" if rest else ""),
                "Event study on current position values"))
            scenario["forecast_row"] = rows[-1]["id"]

    return {
        "findings": {
            "risk": {
                "total": total, "weights": weights, "sectors": sector_rank, "var": var, "var_5d": var_5d,
                "beta": portfolio_beta, "scenario": scenario, "var_row": var_row, "beta_row": beta_row,
                "sessions": len(returns),
            }
        },
        "evidence": rows,
        "gaps": [f'Could not price: {", ".join(unpriced)}'] if unpriced else [],
        "summary": f"Value {inr(total)}; "
        + (f"5-day VaR {inr(var_5d)}; " if var_5d else "")
        + (f'event scenario {pct(scenario["mean"])}' if scenario else "no event scenario"),
    }


# --------------------------------------------------------------------------- hedging


HEDGE_RULE = (f"A hedge is recommended only when the loss expected from comparable past events is larger than "
              f"the {HORIZON_SESSIONS}-day 95% VaR, which is the loss this portfolio already risks in an ordinary week.")


@agent("hedging")
def hedging(state: State) -> dict:
    findings = state["findings"]
    risk_view = findings.get("risk")
    if not risk_view:
        return {"findings": {"hedging": {"action": "none", "hedges": []}},
                "summary": "No hedge sized without risk numbers", "status": "degraded"}

    total, beta_value = risk_view["total"], risk_view["beta"]
    scenario, budget = risk_view["scenario"], risk_view["var_5d"]
    nifty_mean = findings.get("historical", {}).get("nifty_mean")
    full_notional = beta_value * total if beta_value is not None else None
    rows, hedges = [], []

    def add(claim: str) -> str:
        rows.append(evidence("G", len(rows) + 1, claim, "Hedging rules on risk agent output"))
        return rows[-1]["id"]

    sizing: list[str] = []
    uses = [i for i in ((scenario or {}).get("row"), risk_view.get("var_row")) if i]
    if scenario is None:
        action = "monitor"
        comparison = (("No comparable past events were found" if state["plan"]["event_types"]
                       else "The question names no market event") + ", so there is no expected loss to compare "
                      "with the VaR. The portfolio is monitored, not hedged.")
        claim = "No event scenario could be built, so no event-specific hedge is sized."
        if full_notional:
            claim += (f" For reference, shorting {inr(full_notional)} of Nifty 50 futures "
                      f"(beta {beta_value:.2f} x portfolio value) would neutralise market exposure")
        add(claim)
    elif scenario["pnl"] >= 0:
        action = "no_hedge"
        comparison = (f'Comparable events moved a portfolio weighted like this one {pct(scenario["mean"])} on '
                      f"average, a gain, so there is no expected loss to hedge.")
        claim = (f'Comparable events were on average favourable for this portfolio ({pct(scenario["mean"])}), '
                 "so no hedge is recommended on expected value.")
        worst_loss = -scenario["worst"][0] * total
        if budget and worst_loss > budget:
            claim += (f" The worst past case would lose {inr(worst_loss)}, above the {HORIZON_SESSIONS}-day "
                      f"VaR of {inr(budget)}, so a partial index hedge is reasonable if you want tail protection")
        add(claim)
    else:
        loss = -scenario["pnl"]
        index_helps = full_notional is not None and nifty_mean is not None and nifty_mean < 0
        offset = -full_notional * nifty_mean if index_helps else 0.0
        if budget and loss <= budget:
            action = "monitor"
            comparison = (f"The expected event loss of {inr(loss)} is smaller than the {HORIZON_SESSIONS}-day 95% "
                          f"VaR of {inr(budget)}, so no hedge is needed.")
            add(f"Expected event loss {inr(loss)} is within the {HORIZON_SESSIONS}-day 95% VaR of {inr(budget)}, "
                "which is normal risk for this portfolio. Monitor rather than hedge.")
        else:
            action = "hedge"
            comparison = (f"The expected event loss of {inr(loss)} is larger than the {HORIZON_SESSIONS}-day 95% VaR"
                          + (f" of {inr(budget)}" if budget else "") + ", so a hedge is sized.")
            add(f"Expected event loss {inr(loss)} exceeds the {HORIZON_SESSIONS}-day 95% VaR"
                + (f" of {inr(budget)}" if budget else "") + ", so a hedge is warranted.")

        if index_helps:
            hedge_id = add(
                ("Optional: short " if action == "monitor" else "Short ")
                + f"{inr(full_notional)} of Nifty 50 futures (beta {beta_value:.2f} x portfolio value). "
                f"With the index averaging {pct(nifty_mean)} in comparable events this offsets about "
                f"{inr(offset)}, leaving roughly {inr(max(loss - offset, 0))} of stock-specific loss")
            sizing.append(f"Index hedge = beta {beta_value:.2f} x portfolio value {inr(total)} = "
                          f"{inr(full_notional)}. It is offered because the Nifty fell {pct(-nifty_mean, signed=False)} on "
                          f"average in the comparable events, which would offset about {inr(offset)}.")
            hedges.append({"instrument": "Nifty 50 futures", "side": "short",
                           "notional": round(full_notional), "expected_offset": round(offset),
                           "optional": action != "hedge", "evidence": hedge_id})
        elif full_notional is not None:
            sizing.append("No index hedge: the Nifty did not fall on average in the comparable events, so "
                          "shorting it would not offset a loss that is specific to these stocks.")
            add(f"An index hedge would not have helped: the Nifty 50 averaged {pct(nifty_mean or 0)} in "
                "comparable events, so the expected loss is stock-specific")

        residual = loss - offset
        vulnerable = [c for c in scenario["contributions"] if c["pnl"] < 0]
        if action == "hedge" and budget and residual > budget and vulnerable:
            vulnerable_loss = -sum(c["pnl"] for c in vulnerable)
            trim = min(1.0, (residual - budget) / vulnerable_loss)
            names = "; ".join(f'{c["name"]} sell {inr(c["value"] * trim)}' for c in vulnerable[:3])
            sizing.append(f"Trim = (loss left after the index hedge {inr(residual)} - VaR {inr(budget)}) / loss "
                          f"expected on the holdings with negative event history {inr(vulnerable_loss)} = "
                          f"{pct(trim, signed=False)} of each of those holdings.")
            hedge_id = add(
                f"To bring the remaining loss inside the VaR budget, trim the holdings with negative "
                f"event history by {pct(trim, signed=False)}: {names}")
            hedges.extend(
                {"instrument": c["name"], "side": "reduce", "notional": round(c["value"] * trim),
                 "expected_offset": round(-c["pnl"] * trim), "optional": False, "evidence": hedge_id}
                for c in vulnerable[:3]
            )

    topic = (findings.get("sentiment") or {}).get("topic")
    regime = (findings.get("weather_macro") or {}).get("regime") or []
    stress = [*regime]
    if topic and topic["mean"] <= -0.15:
        stress.append("scenario news is negative")
    if stress and action != "no_hedge":
        add("Current conditions add to the case for caution: " + ", ".join(stress))

    labels = {"hedge": "Hedge recommended", "monitor": "Monitor, no hedge needed",
              "no_hedge": "No hedge recommended", "none": "No hedge sized"}
    decision = {"action": action, "rule": HEDGE_RULE, "comparison": comparison, "sizing": sizing,
                "evidence": [*uses, rows[0]["id"]]}
    return {
        "findings": {"hedging": {"action": action, "hedges": hedges, "decision": decision}},
        "evidence": rows,
        "summary": labels[action] + (f" ({len(hedges)} action(s) sized)" if hedges else ""),
    }


# --------------------------------------------------------------------------- synthesiser

CITATION = re.compile(r"\[[A-Z]\d+(?:\s*,\s*[A-Z]\d+)*\]")
NUMBER = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {f"{float(n.replace(',', '')):g}" for n in NUMBER.findall(CITATION.sub(" ", text))}


def ungrounded_numbers(text: str, rows: list[dict]) -> set[str]:
    """Numbers in `text` that appear in no evidence claim."""
    allowed = set().union(*(_numbers(r["claim"]) for r in rows)) if rows else set()
    return _numbers(text) - allowed


def _ids(rows: list[dict], prefix: str) -> str:
    ids = [r["id"] for r in rows if r["id"].startswith(prefix)]
    return f' [{", ".join(ids)}]' if ids else ""


def _forecast_rows(state: State) -> list[str]:
    """Evidence IDs of the per-holding and the commodity forecast, when they exist."""
    scenario = (state["findings"].get("risk") or {}).get("scenario") or {}
    history = state["findings"].get("historical") or {}
    return [i for i in (scenario.get("forecast_row"), history.get("cross_row")) if i]


def _forecast(state: State) -> dict | None:
    """The forecast as data, for the chart beside the answer. Same figures as the evidence rows."""
    scenario = (state["findings"].get("risk") or {}).get("scenario")
    if not scenario:
        return None
    history = state["findings"].get("historical") or {}
    per_holding = history.get("per_holding", {})
    return {
        "horizon_sessions": HORIZON_SESSIONS,
        "events": scenario["n"],
        "portfolio": {"mean": scenario["mean"], "pnl": round(scenario["pnl"]),
                      "worst": scenario["worst"][0], "best": scenario["best"][0], "evidence": scenario["row"]},
        "holdings": [
            {"name": c["name"], "mean": c["mean"], "pnl": round(c["pnl"]), "n": per_holding[c["ticker"]]["n"],
             "min": per_holding[c["ticker"]]["min"], "max": per_holding[c["ticker"]]["max"]}
            for c in scenario["contributions"]
        ],
        "holdings_evidence": scenario.get("forecast_row"),
        "cross_assets": [{k: c[k] for k in ("label", "mean", "min", "max", "n")}
                         for c in history.get("cross_assets") or []],
        "cross_assets_evidence": history.get("cross_row"),
    }


def _template(state: State, rows: list[dict]) -> str:
    findings = state["findings"]
    risk_view, hedge = findings.get("risk"), findings.get("hedging", {})
    by_id = {r["id"]: r["claim"] for r in rows}
    of = lambda prefix: [r for r in rows if r["id"].startswith(prefix)]  # noqa: E731
    lines = []

    scenario = risk_view and risk_view["scenario"]
    verdict = {"hedge": "A hedge is recommended.", "monitor": "No hedge is needed for now; monitor.",
               "no_hedge": "No hedge is recommended.", "none": "A hedge could not be sized."}[hedge.get("action", "none")]
    forecast_ids = _forecast_rows(state)
    if scenario:
        scenario_id = scenario["row"]
        lines.append(
            f'**Bottom line:** in {scenario["n"]} comparable past events, a portfolio weighted like yours moved '
            f'{pct(scenario["mean"])} ({inr(scenario["pnl"])}) on average over {HORIZON_SESSIONS} sessions '
            f"[{scenario_id}]. {verdict}")
    elif risk_view:
        reason = ("no comparable past events were found" if state["plan"]["event_types"]
                  else "the question names no market event, and prices are not predicted")
        lines.append(f"**Bottom line:** {reason}, so there is no event forecast. "
                     f'Your portfolio is worth {inr(risk_view["total"])} [R1]. {verdict}')
    else:
        lines.append("**Bottom line:** prices could not be fetched, so risk could not be measured.")

    def section(title: str, prefix: str, limit: int = 4) -> None:
        picked = of(prefix)[:limit]
        if picked:
            lines.append(f"## {title}")
            lines.extend(f'- {r["claim"]} [{r["id"]}]' for r in picked)

    if forecast_ids:
        lines.append(f"## Forecast for the next {HORIZON_SESSIONS} sessions")
        lines.extend(f"- {by_id[i]} [{i}]" for i in forecast_ids)

    risk_rows = [r for r in of("R") if r["id"] not in forecast_ids][:8]
    if risk_rows:
        lines.append("## Risk")
        lines.extend(f'- {r["claim"]} [{r["id"]}]' for r in risk_rows)
    history = [r for r in of("H") if r["id"] not in forecast_ids]
    event_ids = (findings.get("historical") or {}).get("event_rows", [])
    events_used = [r for r in history if r["id"] in event_ids]
    summary = [r for r in history if r["id"] not in event_ids]
    if history:
        lines.append("## Past events")
        lines.extend(f"- {r['claim']} [{r['id']}]" for r in summary)
        lines.append(f"- Events used:{_ids(events_used, 'H')}")
    section("News sentiment", "S", 3)
    weather_macro_rows = of("W")[:3] + of("M")
    if weather_macro_rows:
        lines.append("## Weather and macro")
        lines.extend(f'- {r["claim"]} [{r["id"]}]' for r in weather_macro_rows)
    section("Hedge", "G", 5)
    return "\n".join(lines) if by_id else lines[0]


def _llm_answer(state: State, rows: list[dict], draft: str) -> tuple[str | None, str | None]:
    """The model's wording if it passes every check, else None and the reason it was not used."""
    if not llm.available():
        return None, "No language model key is configured"
    listing = "\n".join(f'[{r["id"]}] {r["claim"]}' for r in rows)
    forecast_ids = _forecast_rows(state)
    text = llm.complete(
        "You are the synthesiser of a portfolio risk assistant. Answer the user's question directly in "
        "under 110 words, in plain language, for an investor. At most four short bullets.\n"
        "Rules: use only facts from the evidence list. Copy every figure exactly as written there; do not "
        "round, convert or calculate new numbers. Put the supporting evidence ID in square brackets after "
        "each claim, like [R3]. Start with a line beginning '**Bottom line:**'. Then short '- ' bullets. "
        "Say plainly if evidence is missing. No headings, no disclaimers.\n"
        + (f"The evidence holds a forecast in {' and '.join(forecast_ids)}. Whatever the hedge decision, "
           "even when no hedge is needed, give one bullet starting 'Forecast for your holdings:' naming the "
           "holding with the lowest forecast and the one with the highest, each with its figure. Call a move a "
           "fall only if its figure is negative and a rise only if it is positive. If there is a commodity "
           "row, add one bullet starting 'Forecast for commodities:' with each commodity and the rupee. Then the "
           "recommendation." if forecast_ids else ""),
        f'Question: {state["query"]}\n\nEvidence:\n{listing}\n\nDraft answer for reference:\n{draft}',
    )
    if not isinstance(text, str) or not text.strip():
        return None, "The language model did not reply"
    known = {r["id"] for r in rows}
    cited = set(re.findall(r"[A-Z]\d+", " ".join(CITATION.findall(text))))
    invented = ungrounded_numbers(text, rows)
    if invented:
        return None, ("The language model's wording was rejected because it contained figures that are not in "
                      f"the evidence ({', '.join(sorted(invented)[:5])})")
    if not cited or not cited <= known:
        return None, "The language model's wording was rejected because its citations did not match the evidence"
    if not set(forecast_ids) <= cited:
        return None, "The language model's wording was rejected because it left out the forecast"
    return text.strip(), None


TRAIL_TITLES = {
    "supervisor": "Understood the question", "sentiment": "Read the news mood",
    "weather_macro": "Checked weather and market conditions", "historical": "Found comparable past events",
    "risk": "Measured the risk", "hedging": "Decided on a hedge", "synthesiser": "Wrote and checked the answer",
}
EVIDENCE_OWNER = {"S": "sentiment", "W": "weather_macro", "M": "weather_macro", "H": "historical",
                  "R": "risk", "G": "hedging"}


def _trail(state: State, rows: list[dict], text: str, problem: str | None) -> list[dict]:
    """The analysis as ordered steps: what each agent was asked, how it worked it
    out, which evidence it produced and which earlier evidence it relied on."""
    plan, findings = state["plan"], state["findings"]
    said = {entry["agent"]: entry["summary"] for entry in state.get("trace", [])}
    found = {agent: [r["id"] for r in rows if EVIDENCE_OWNER.get(r["id"][0]) == agent]
             for agent in TRAIL_TITLES}
    history, risk_view = findings.get("historical") or {}, findings.get("risk") or {}
    decision = (findings.get("hedging") or {}).get("decision")
    scenario = risk_view.get("scenario") or {}
    negative = sentiment_tool.cutoffs()[1]

    how = {
        "supervisor": (
            f"Gemini read the question and picked the scenario from a fixed list of {len(EVENT_TYPES)} event types."
            if plan["planner"] == "llm" else
            f"Keyword rules matched the question against {len(EVENT_TYPES)} event types; no language model was used."
        ) + _routing_record(plan["planner"]),
        "sentiment": (
            f"Fetched recent headlines from Google News for the scenario and for your holdings, then scored each "
            f"with {sentiment_tool.backend()}. A score runs from -1 (negative) to +1 (positive); {negative:+.2f} "
            f"or lower counts as negative."),
        "weather_macro": (
            f"Read the 7-day forecast at {len(LOCATIONS)} economic sites from Open-Meteo and compared each day with "
            f"India Meteorological Department alert levels ({HEAVY_RAIN_MM} mm of rain, {GALE_GUST_KMH} km/h gusts, "
            f"{HEAT_C} C). Read Brent, USD/INR, India VIX and the Nifty 50 from Yahoo Finance and checked their "
            f"one-month change against fixed stress levels."),
        "historical": (
            f"Searched the library of past events with {history.get('search', 'text search')} for events like this "
            f"one. For each match, measured the move from the close before the event to {HORIZON_SESSIONS} sessions "
            f"later, for the Nifty 50, each holding, and crude, gas, gold and the rupee."),
        "risk": (
            f"Valued each holding at its latest close. VaR is the loss exceeded on only 5% of the last "
            f"{risk_view.get('sessions', metrics.TRADING_DAYS_1Y)} sessions at today's weights (historical "
            f"simulation); beta is measured against the Nifty 50. Each comparable event's moves were then applied "
            f"to today's weights to get the expected move."),
        "hedging": HEDGE_RULE,
        "synthesiser": (
            "Gemini wrote the wording from the evidence list only. It was accepted after two checks: every figure "
            "in it appears in the evidence, and every citation points to a real evidence row."
            if problem is None else
            f"{problem}, so the answer uses fixed rule-based wording built directly from the evidence rows."),
    }
    result = {
        **said,
        "supervisor": (f'Scenario: {plan["event_label"]}. News search: "{plan["news_query"]}".' if plan["event_label"]
                       else "No specific event named, so it is treated as a general question about the portfolio."),
        "hedging": decision["comparison"] if decision else said.get("hedging", "No hedge could be sized."),
        "synthesiser": (f"{len(_numbers(text))} figures and {len(set(CITATION.findall(text)))} citations in the "
                        f"answer, all traced to the {len(rows)} evidence rows."),
    }
    uses = {
        "risk": history.get("event_rows", []),
        "hedging": [i for i in (scenario.get("row"), risk_view.get("var_row"), risk_view.get("beta_row")) if i],
        # The rows the answer actually cites, in evidence order.
        "synthesiser": [r["id"] for r in rows
                        if r["id"] in set(re.findall(r"[A-Z]\d+", " ".join(CITATION.findall(text))))],
    }
    notes = {
        "supervisor": [f"{TRAIL_TITLES[agent_name]}: {task}" for agent_name, task in plan["subtasks"].items()],
        "hedging": (decision or {}).get("sizing", []),
    }
    return [
        {"agent": name, "title": title, "how": how[name], "result": result.get(name, "No result."),
         "evidence": found[name], "uses": uses.get(name, []), "notes": notes.get(name, [])}
        for name, title in TRAIL_TITLES.items()
    ]


@agent("synthesiser")
def synthesiser(state: State) -> dict:
    rows = state.get("evidence", [])
    order = {"R": 0, "H": 1, "S": 2, "W": 3, "M": 4, "G": 5}
    rows = sorted(rows, key=lambda r: (order.get(r["id"][0], 9), int(r["id"][1:])))

    draft = _template(state, rows)
    written, problem = _llm_answer(state, rows, draft)
    gaps = list(state.get("gaps", []))
    scenario = (state["findings"].get("risk") or {}).get("scenario")
    if scenario:
        gaps.append(
            f'The event estimate rests on {scenario["n"]} past events. That is a small sample, and each '
            "window also contains market moves unrelated to the event"
        )
    if state.get("portfolio_source") == "sample":
        gaps.insert(0, "No holdings with a symbol and quantity were found, so a sample portfolio was analysed")

    return {
        "answer": {
            "text": written or draft,
            "evidence": rows,
            "hedges": state["findings"].get("hedging", {}).get("hedges", []),
            "action": state["findings"].get("hedging", {}).get("action", "none"),
            "forecast": _forecast(state),
            "decision": state["findings"].get("hedging", {}).get("decision"),
            "trail": _trail(state, rows, written or draft, problem),
            "gaps": gaps,
            "writer": "llm" if written else "template",
            "planner": state["plan"]["planner"],
        },
        "summary": f"Answer written with {len(rows)} evidence items"
        + ("" if written else " (template wording)"),
    }
