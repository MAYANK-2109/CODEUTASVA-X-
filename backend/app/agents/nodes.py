"""The seven agents. Each reads shared state and returns findings, evidence and a
one-line summary. Numbers come from tools and app.risk.metrics, never the LLM."""

import math
import re
import time
from functools import wraps

from app.agents import llm
from app.agents.state import State, evidence, inr, pct
from app.ingestion.news import get_news
from app.risk import metrics
from app.tools import sentiment as sentiment_tool
from app.tools import vector_store
from app.tools.events import tokenize
from app.tools.market import NIFTY, get_history, get_macro
from app.tools.weather import (
    GALE_GUST_KMH, HEAT_C, HEAVY_RAIN_MM, get_weather_outlook, holdings_at, sites_named_in,
)

HORIZON_SESSIONS = 5
MAX_EVENTS = 6

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
        "event_type": {"type": "string", "enum": [*EVENT_TYPES, "none"]},
        "news_query": {"type": "string"},
    },
    "required": ["event_type", "news_query"],
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


def classify_event(query: str) -> str | None:
    text = query.lower()
    for event_type, (_, pattern) in EVENT_TYPES.items():
        if re.search(pattern, text):
            return event_type
    return None


def _llm_plan(query: str) -> dict | None:
    result = llm.complete(
        "You route questions for a portfolio risk assistant covering Indian equities. "
        "Pick the single event type the question is about, or 'none' if it is a general "
        "question about the portfolio. Write news_query as 3-6 search keywords for the event.",
        query,
        PLAN_SCHEMA,
    )
    if not isinstance(result, dict) or result.get("event_type") not in (*EVENT_TYPES, "none"):
        return None
    return result


@agent("supervisor")
def supervisor(state: State) -> dict:
    query = state["query"]
    planned = _llm_plan(query)
    if planned:
        event_type = None if planned["event_type"] == "none" else planned["event_type"]
        news_query, planner = planned["news_query"].strip(), "llm"
    else:
        event_type = classify_event(query)
        news_query = " ".join(tokenize(query)[:6]) if event_type else GENERAL_NEWS_QUERY
        planner = "rules"
    news_query = news_query or GENERAL_NEWS_QUERY

    label = EVENT_TYPES[event_type][0] if event_type else None
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
            "event_label": label,
            "news_query": news_query,
            "subtasks": subtasks,
            "planner": planner,
        },
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

    primary = topic or held
    return {
        "findings": {"sentiment": {"topic": topic, "holdings": held}},
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

    matches, backend = vector_store.search_events(state["query"], state["plan"]["event_type"], MAX_EVENTS)
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
    if source == "cache":
        gaps.append("Live prices unavailable; used the last saved price history")
    if not events:
        gaps.append("No comparable past events in the corpus for this question")
    return {
        "findings": {"historical": {"events": events, "per_holding": per_holding, "nifty_mean": nifty_mean}},
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
    var_5d = None
    if var:
        var_5d = var[0] * math.sqrt(HORIZON_SESSIONS) * total
        rows.append(
            evidence("R", len(rows) + 1,
                     f"1-day 95% VaR {pct(var[0], signed=False)} ({inr(var[0] * total)}), expected shortfall "
                     f"{pct(var[1], signed=False)} ({inr(var[1] * total)}); {HORIZON_SESSIONS}-day 95% VaR "
                     f"{inr(var_5d)} by square-root-of-time scaling",
                     f"Historical simulation, last {len(returns)} sessions"))

    betas = {t: metrics.beta(returns[t], returns[NIFTY]) for t in weights}
    betas = {t: b for t, b in betas.items() if b is not None}
    portfolio_beta = sum(weights[t] * b for t, b in betas.items()) if betas else None
    if portfolio_beta is not None:
        rows.append(evidence("R", len(rows) + 1, f"Portfolio beta to Nifty 50: {portfolio_beta:.2f}",
                             f"Daily returns, last {len(returns)} sessions"))

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
        drags = [c for c in contributions if c["pnl"] < 0][:3]
        lifts = [c for c in reversed(contributions) if c["pnl"] > 0][:3]
        if drags:
            rows.append(evidence("R", len(rows) + 1, "Largest expected drags: " + "; ".join(
                f'{c["name"]} {inr(c["pnl"])} ({pct(c["mean"])})' for c in drags), "Event study"))
        if lifts:
            rows.append(evidence("R", len(rows) + 1, "Largest expected offsets: " + "; ".join(
                f'{c["name"]} {inr(c["pnl"])} ({pct(c["mean"])})' for c in lifts), "Event study"))

    return {
        "findings": {
            "risk": {
                "total": total, "weights": weights, "sectors": sector_rank, "var": var, "var_5d": var_5d,
                "beta": portfolio_beta, "scenario": scenario,
            }
        },
        "evidence": rows,
        "gaps": [f'Could not price: {", ".join(unpriced)}'] if unpriced else [],
        "summary": f"Value {inr(total)}; "
        + (f"5-day VaR {inr(var_5d)}; " if var_5d else "")
        + (f'event scenario {pct(scenario["mean"])}' if scenario else "no event scenario"),
    }


# --------------------------------------------------------------------------- hedging


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

    if scenario is None:
        action = "monitor"
        claim = "No event scenario could be built, so no event-specific hedge is sized."
        if full_notional:
            claim += (f" For reference, shorting {inr(full_notional)} of Nifty 50 futures "
                      f"(beta {beta_value:.2f} x portfolio value) would neutralise market exposure")
        add(claim)
    elif scenario["pnl"] >= 0:
        action = "no_hedge"
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
            add(f"Expected event loss {inr(loss)} is within the {HORIZON_SESSIONS}-day 95% VaR of {inr(budget)}, "
                "which is normal risk for this portfolio. Monitor rather than hedge.")
        else:
            action = "hedge"
            add(f"Expected event loss {inr(loss)} exceeds the {HORIZON_SESSIONS}-day 95% VaR"
                + (f" of {inr(budget)}" if budget else "") + ", so a hedge is warranted.")

        if index_helps:
            hedge_id = add(
                ("Optional: short " if action == "monitor" else "Short ")
                + f"{inr(full_notional)} of Nifty 50 futures (beta {beta_value:.2f} x portfolio value). "
                f"With the index averaging {pct(nifty_mean)} in comparable events this offsets about "
                f"{inr(offset)}, leaving roughly {inr(max(loss - offset, 0))} of stock-specific loss")
            hedges.append({"instrument": "Nifty 50 futures", "side": "short",
                           "notional": round(full_notional), "expected_offset": round(offset),
                           "optional": action != "hedge", "evidence": hedge_id})
        elif full_notional is not None:
            add(f"An index hedge would not have helped: the Nifty 50 averaged {pct(nifty_mean or 0)} in "
                "comparable events, so the expected loss is stock-specific")

        residual = loss - offset
        vulnerable = [c for c in scenario["contributions"] if c["pnl"] < 0]
        if action == "hedge" and budget and residual > budget and vulnerable:
            vulnerable_loss = -sum(c["pnl"] for c in vulnerable)
            trim = min(1.0, (residual - budget) / vulnerable_loss)
            names = "; ".join(f'{c["name"]} sell {inr(c["value"] * trim)}' for c in vulnerable[:3])
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
    return {
        "findings": {"hedging": {"action": action, "hedges": hedges}},
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


def _template(state: State, rows: list[dict]) -> str:
    findings = state["findings"]
    risk_view, hedge = findings.get("risk"), findings.get("hedging", {})
    by_id = {r["id"]: r["claim"] for r in rows}
    of = lambda prefix: [r for r in rows if r["id"].startswith(prefix)]  # noqa: E731
    lines = []

    scenario = risk_view and risk_view["scenario"]
    verdict = {"hedge": "A hedge is recommended.", "monitor": "No hedge is needed for now; monitor.",
               "no_hedge": "No hedge is recommended.", "none": "A hedge could not be sized."}[hedge.get("action", "none")]
    if scenario:
        scenario_id = next(r["id"] for r in rows if "comparable events imply" in r["claim"])
        lines.append(
            f'**Bottom line:** in {scenario["n"]} comparable past events, a portfolio weighted like yours moved '
            f'{pct(scenario["mean"])} ({inr(scenario["pnl"])}) on average over {HORIZON_SESSIONS} sessions '
            f"[{scenario_id}]. {verdict}")
    elif risk_view:
        lines.append(f"**Bottom line:** no comparable past events were found, so there is no event estimate. "
                     f'Your portfolio is worth {inr(risk_view["total"])} [R1]. {verdict}')
    else:
        lines.append("**Bottom line:** prices could not be fetched, so risk could not be measured.")

    def section(title: str, prefix: str, limit: int = 4) -> None:
        picked = of(prefix)[:limit]
        if picked:
            lines.append(f"## {title}")
            lines.extend(f'- {r["claim"]} [{r["id"]}]' for r in picked)

    section("Risk", "R", 8)
    history = of("H")
    if history:
        lines.append("## Past events")
        lines.append(f"- {history[-1]['claim']} [{history[-1]['id']}]")
        lines.append(f"- Events used:{_ids(history[:-1], 'H')}")
    section("News sentiment", "S", 3)
    weather_macro_rows = of("W")[:3] + of("M")
    if weather_macro_rows:
        lines.append("## Weather and macro")
        lines.extend(f'- {r["claim"]} [{r["id"]}]' for r in weather_macro_rows)
    section("Hedge", "G", 5)
    return "\n".join(lines) if by_id else lines[0]


def _llm_answer(state: State, rows: list[dict], draft: str) -> str | None:
    listing = "\n".join(f'[{r["id"]}] {r["claim"]}' for r in rows)
    text = llm.complete(
        "You are the synthesiser of a portfolio risk assistant. Answer the user's question directly in "
        "under 170 words, in plain language, for an investor.\n"
        "Rules: use only facts from the evidence list. Copy every figure exactly as written there; do not "
        "round, convert or calculate new numbers. Put the supporting evidence ID in square brackets after "
        "each claim, like [R3]. Start with a line beginning '**Bottom line:**'. Then short '- ' bullets. "
        "Say plainly if evidence is missing. No headings, no disclaimers.",
        f'Question: {state["query"]}\n\nEvidence:\n{listing}\n\nDraft answer for reference:\n{draft}',
    )
    if not isinstance(text, str) or not text.strip():
        return None
    known = {r["id"] for r in rows}
    cited = set(re.findall(r"[A-Z]\d+", " ".join(CITATION.findall(text))))
    if ungrounded_numbers(text, rows) or not cited or not cited <= known:
        return None
    return text.strip()


@agent("synthesiser")
def synthesiser(state: State) -> dict:
    rows = state.get("evidence", [])
    order = {"R": 0, "H": 1, "S": 2, "W": 3, "M": 4, "G": 5}
    rows = sorted(rows, key=lambda r: (order.get(r["id"][0], 9), int(r["id"][1:])))

    draft = _template(state, rows)
    written = _llm_answer(state, rows, draft)
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
            "gaps": gaps,
            "writer": "llm" if written else "template",
            "planner": state["plan"]["planner"],
        },
        "summary": f"Answer written with {len(rows)} evidence items"
        + ("" if written else " (template wording)"),
    }
