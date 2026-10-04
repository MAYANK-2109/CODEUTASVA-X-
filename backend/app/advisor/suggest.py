"""Sector and stock suggestions for a portfolio.

Five steps, each built only from modules that already exist in this app and
from the arithmetic in app.risk.portfolio_metrics:

1. diagnose the portfolio's gaps;
2. rank eleven sector baskets on portfolio fit, evidence, macro fit and trend;
3. pick up to three stocks in each of the top three sectors;
4. size the additions with a capped optimizer and check them walk-forward;
5. write each suggestion out with its evidence, risks and triggers.

Every figure is computed here. Where an input is missing the output says
"insufficient data" and the figure is None. Statistics are measured on the
last three years of prices and are in-sample unless marked walk-forward.
"""

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.advisor import config
from app.advisor.data import fundamentals, source_note
from app.agents import llm
from app.agents.nodes import _macro_flags, _numbers
from app.broker.paper import UNKNOWN_LIQUIDITY_BPS, share_slippage_bps
from app.ingestion.news import SECTOR_TOPICS, get_news, names_holding
from app.ml import impact, radar
from app.ml.features import BRENT, HORIZON, RUPEE, VIX
from app.risk import metrics
from app.risk import portfolio_metrics as pm
from app.tools import sectors as sector_lookup
from app.tools import sentiment as sentiment_tool
from app.tools.events import load_events
from app.tools.market import NIFTY, SECTORS, get_cross_assets, get_history, get_macro, normalise_holdings

INSUFFICIENT = "insufficient data"
# Plain names for the impact model's inputs.
DRIVER_LABELS = {
    "vol_20": "the stock's 20-day volatility", "vol_60": "the stock's 60-day volatility",
    "vix_change_5": "the 5-day change in India VIX", "brent_change_21": "the 21-day change in Brent",
    "brent_change_5": "the 5-day change in Brent", "usual_downside": "the stock's usual weekly downside",
    "nifty_vol_60": "the Nifty's 60-day volatility", "nifty_return_21": "the Nifty's 21-day return",
    "rupee_change_21": "the 21-day change in USD/INR", "vol_regime": "recent against long-run volatility",
    "high_gap": "distance from the 52-week high", "return_21": "the stock's 21-day return",
}


# --------------------------------------------------------------------------- helpers


def pct(fraction: float | None, digits: int = 1, signed: bool = False) -> str:
    if fraction is None:
        return INSUFFICIENT
    value = fraction * 100
    return f"{value:+.{digits}f}%" if signed else f"{value:.{digits}f}%"


def rank_scores(values: dict[str, float | None], higher_is_better: bool = True) -> dict[str, float | None]:
    """Each value's percentile among the others, 0 to 100. A missing value stays missing."""
    known = {key: value for key, value in values.items() if value is not None and not math.isnan(value)}
    if not known:
        return {key: None for key in values}
    ordered = sorted(known, key=lambda key: known[key], reverse=not higher_is_better)
    place = {key: (index / (len(ordered) - 1) * 100 if len(ordered) > 1 else 50.0) for index, key in enumerate(ordered)}
    # Equal values share the average of their places.
    for value in set(known.values()):
        tied = [key for key in known if known[key] == value]
        if len(tied) > 1:
            shared = sum(place[key] for key in tied) / len(tied)
            place.update({key: shared for key in tied})
    return {key: place.get(key) for key in values}


def mean_score(parts: list[float | None]) -> float | None:
    """The average of the sub-scores that exist; None when none does."""
    known = [part for part in parts if part is not None]
    return sum(known) / len(known) if known else None


def composite(scores: dict[str, float | None], weights: dict[str, float]) -> float:
    """Weighted sum out of 100. A missing sub-score counts as the neutral 50."""
    return sum(weights[name] * (scores.get(name) if scores.get(name) is not None else 50.0) for name in weights) / 100


def sector_universe() -> dict[str, list[str]]:
    universe: dict[str, list[str]] = {sector: [] for sector in config.RANKED_SECTORS}
    for symbol, sector in SECTORS.items():
        if sector in universe:
            universe[sector].append(f"{symbol}.NS")
    return universe


def weekly_betas(series: pd.Series, factors: pd.DataFrame) -> dict | None:
    """Sensitivity of a return series to weekly moves in the Nifty, Brent, USD/INR
    and India VIX, estimated together by least squares."""
    weekly = (1 + series.dropna()).resample("W-FRI").prod() - 1
    frame = pd.concat([weekly.rename("y"), factors], axis=1, sort=True).dropna()
    if len(frame) < 52:
        return None
    x = np.column_stack([np.ones(len(frame)), frame[factors.columns].to_numpy()])
    solution = np.linalg.lstsq(x, frame["y"].to_numpy(), rcond=None)[0]
    return {**{name: float(value) for name, value in zip(factors.columns, solution[1:])}, "weeks": int(len(frame))}


def macro_factors(closes: pd.DataFrame) -> pd.DataFrame | None:
    """Weekly changes in the four macro drivers, or None when their history is unavailable."""
    cross = get_cross_assets()
    if cross is None or any(column not in cross.columns for column in (BRENT, RUPEE, VIX)):
        return None
    weekly = cross[[BRENT, RUPEE, VIX]].ffill().resample("W-FRI").last().pct_change(fill_method=None)
    nifty = closes[NIFTY].resample("W-FRI").last().pct_change(fill_method=None)
    return pd.concat([nifty.rename("nifty"), weekly.rename(columns={BRENT: "brent", RUPEE: "usdinr", VIX: "vix"})],
                     axis=1, sort=True)


# What each stress flag asks of a sector: the factor it reads, and whether a
# higher sensitivity to that factor is good (+1) or bad (-1) while the flag is up.
REGIME_RULES = {
    "crude rising fast": ("brent", 1), "rupee weakening": ("usdinr", 1),
    "volatility elevated": ("vix", 1), "market in drawdown": ("nifty", -1),
}


# --------------------------------------------------------------------------- inputs


def load(raw_holdings: list[dict] | None) -> dict:
    """Prices for the holdings and the sector universe, as daily returns over the lookback."""
    holdings, source = normalise_holdings(raw_holdings)
    looked_up = sector_lookup.resolve([h["ticker"] for h in holdings if h["sector"] == sector_lookup.UNKNOWN])
    holdings = [{**h, "sector": looked_up.get(h["ticker"], h["sector"])} for h in holdings]
    universe = sector_universe()
    universe_closes, _ = get_history(sorted({t for tickers in universe.values() for t in tickers}))
    holding_closes, _ = get_history([h["ticker"] for h in holdings])
    if universe_closes is None or holding_closes is None:
        return {"problem": "Price history is unavailable right now."}
    extra = [c for c in holding_closes.columns if c not in universe_closes.columns]
    closes = universe_closes.join(holding_closes[extra], how="outer").sort_index().ffill()

    age = (datetime.now(timezone.utc).date() - closes.index[-1].date()).days
    if age > config.STALE_AFTER_DAYS:
        return {"problem": f"The latest prices are {age} days old, so nothing is suggested from them."}

    latest = closes.iloc[-1]
    positions = []
    for h in holdings:
        price = latest.get(h["ticker"])
        if price is not None and not math.isnan(price):
            positions.append({**h, "price": float(price), "value": float(price) * h["units"]})
    total = sum(p["value"] for p in positions)
    if total <= 0:
        return {"problem": "None of the holdings could be priced."}
    returns = closes.pct_change(fill_method=None).iloc[-config.LOOKBACK_SESSIONS:]
    weights = {p["ticker"]: p["value"] / total for p in positions}
    usable = {t: w for t, w in weights.items() if returns[t].notna().sum() >= config.MIN_SESSIONS}
    if not usable:
        return {"problem": "None of the holdings has a year of price history."}
    scale = sum(usable.values())
    return {"source": source, "closes": closes, "returns": returns, "positions": positions, "total": total,
            "weights": {t: w / scale for t, w in usable.items()}, "universe": universe,
            "unpriced": [h["name"] for h in holdings if h["ticker"] not in weights],
            "short_history": [p["name"] for p in positions if p["ticker"] not in usable]}


def sector_baskets(returns: pd.DataFrame, universe: dict[str, list[str]]) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """An equal-weighted daily return for each sector, from its stocks that have a full enough history."""
    baskets, members = {}, {}
    for sector, tickers in universe.items():
        usable = [t for t in tickers if t in returns.columns and returns[t].notna().sum() >= config.MIN_SESSIONS]
        if len(usable) >= 2:
            baskets[f"sector:{sector}"] = returns[usable].mean(axis=1)
            members[sector] = usable
    return pd.DataFrame(baskets), members


# --------------------------------------------------------------------------- step 1: gaps


def diagnose(state: dict, factors: pd.DataFrame | None, flags: list[str]) -> dict:
    returns, weights, positions = state["returns"], state["weights"], state["positions"]
    names = {p["ticker"]: p["name"] for p in positions}
    sector_of = {p["ticker"]: p["sector"] for p in positions}
    tickers = list(weights)
    frame = returns[tickers].dropna(how="any")
    w = np.array([weights[t] for t in tickers])
    cov, shrinkage = pm.ledoit_wolf(frame.to_numpy())
    risk = dict(zip(tickers, pm.risk_contributions(w, cov)))

    by_sector: dict[str, dict] = {}
    for t in tickers:
        entry = by_sector.setdefault(sector_of[t], {"sector": sector_of[t], "weight": 0.0, "risk_share": 0.0, "holdings": []})
        entry["weight"] += weights[t]
        entry["risk_share"] += float(risk[t])
        entry["holdings"].append(names[t])
    reference = 1 / len(config.RANKED_SECTORS)
    sector_rows = sorted(by_sector.values(), key=lambda s: -s["weight"])
    for row in sector_rows:
        row["reference"] = reference if row["sector"] in config.RANKED_SECTORS else 0.0
    missing = [s for s in config.RANKED_SECTORS if s not in by_sector]

    clusters = [
        {"holdings": [names[t] for t in group], "risk_share": float(sum(risk[t] for t in group)),
         "weight": float(sum(weights[t] for t in group))}
        for group in pm.correlation_clusters(frame.corr(), config.CLUSTER_CORRELATION) if len(group) >= 2
    ]
    clusters.sort(key=lambda c: -c["risk_share"])

    portfolio = pm.portfolio_series(frame, weights)
    betas = weekly_betas(portfolio, factors) if factors is not None else None
    market_beta = metrics.beta(portfolio, returns[NIFTY].reindex(portfolio.index))
    effective = pm.effective_n(w)
    effective_risk = pm.effective_n(np.array(list(risk.values())))

    gaps = []
    defensive = sum(row["weight"] for row in sector_rows if row["sector"] in config.DEFENSIVE_SECTORS)
    if defensive < 0.05:
        gaps.append((0.9, "No defensive exposure",
                     f"{pct(defensive)} of the portfolio is in FMCG, Pharma or Utilities, the sectors that usually "
                     f"fall least in a sell-off."))
    top = max(sector_rows, key=lambda s: s["risk_share"])
    if top["risk_share"] >= 0.4:
        gaps.append((top["risk_share"], f"Risk is concentrated in {top['sector']}",
                     f"{pct(top['risk_share'], 0)} of portfolio risk comes from {top['sector']}, which is "
                     f"{pct(top['weight'], 0)} of its value."))
    if clusters and clusters[0]["risk_share"] >= 0.4:
        group = clusters[0]
        gaps.append((group["risk_share"] * 0.95, "One group of holdings moves together",
                     f"{pct(group['risk_share'], 0)} of risk sits in {len(group['holdings'])} holdings whose returns "
                     f"are correlated at {config.CLUSTER_CORRELATION} or more: {', '.join(group['holdings'][:4])}."))
    if effective_risk < 4:
        gaps.append(((4 - effective_risk) / 4, "Few independent sources of risk",
                     f"Risk is spread like {effective_risk:.1f} equal positions, although there are {len(tickers)} holdings."))
    if len(missing) >= 4:
        gaps.append((len(missing) / len(config.RANKED_SECTORS) * 0.6, "Several sectors are absent",
                     f"No exposure to {len(missing)} of {len(config.RANKED_SECTORS)} sectors: {', '.join(missing[:6])}."))
    for flag in flags:
        factor, direction = REGIME_RULES[flag]
        if betas and betas[factor] * direction < 0 and factor != "nifty":
            gaps.append((0.5 + min(abs(betas[factor]), 0.4), f"Exposed while {flag.replace(' fast', '')}",
                         f"The portfolio moves {betas[factor] * 100:+.2f}% for each 1% weekly move in "
                         f"{ {'brent': 'Brent', 'usdinr': 'USD/INR', 'vix': 'India VIX'}[factor] }, and the regime "
                         f"flag '{flag}' is raised."))
    if market_beta is not None and market_beta > 1.2:
        gaps.append((min(market_beta - 0.7, 0.9), "High market beta",
                     f"The portfolio's beta to the Nifty 50 is {market_beta:.2f}, so it falls more than the index in a sell-off."))
    over = max(sector_rows, key=lambda s: s["weight"] - s["reference"])
    gaps.append((min(over["weight"] - over["reference"], 0.45), f"{over['sector']} is overweight",
                 f"{over['sector']} is {pct(over['weight'], 0)} of the portfolio against a reference of "
                 f"{pct(over['reference'], 0)}."))
    gaps.append((0.2 if effective < 8 else 0.05, "Number of independent positions",
                 f"By value the portfolio is spread like {effective:.1f} equal positions; by risk, like "
                 f"{effective_risk:.1f}."))
    gaps.sort(key=lambda g: -g[0])

    return {
        "sectors": sector_rows, "missing_sectors": missing,
        "reference": f"equal weight across the {len(config.RANKED_SECTORS)} ranked sectors ({pct(reference)} each); "
                     f"index sector weights are not available in this app",
        "effective_n": effective, "effective_n_risk": effective_risk, "holdings": len(tickers),
        "clusters": clusters[:3], "macro_betas": betas, "market_beta": market_beta,
        "covariance_shrinkage": shrinkage, "sessions": int(len(frame)),
        "top_risk_share": float(max(risk.values())),
        "gaps": [{"title": title, "detail": detail, "severity": round(severity, 2)} for severity, title, detail in gaps[:3]],
    }


# --------------------------------------------------------------------------- step 2: sectors


def sector_sentiment(sector: str) -> dict:
    items = get_news(f"{SECTOR_TOPICS[sector][1]} India when:7d", config.HEADLINES_PER_SECTOR)
    scores = sentiment_tool.score_many([item["title"] for item in items])
    return {"mean": sum(scores) / len(scores) if scores else None, "headlines": len(scores)}


def analog_moves(baskets: pd.DataFrame, closes: pd.DataFrame) -> dict[str, dict]:
    """For every live crisis signal, how each sector basket moved beyond the Nifty
    over the sessions after past events of the same kind."""
    signals = [s for s in radar.current_signals() if not s["drill"] and s["event_type"]]
    if not signals:
        return {}
    levels = (1 + baskets.fillna(0)).cumprod()
    levels[NIFTY] = closes[NIFTY].reindex(levels.index)
    events = [e for e in load_events() if e["type"] in {s["event_type"] for s in signals}]
    out = {}
    for column in baskets.columns:
        moves = []
        for event in events:
            window = metrics.event_window_returns(levels[[column, NIFTY]], event["date"], HORIZON)
            if window is not None and not (math.isnan(window[column]) or math.isnan(window[NIFTY])):
                moves.append(float(window[column] - window[NIFTY]))
        if moves:
            out[column.split(":", 1)[1]] = {"mean": sum(moves) / len(moves), "min": min(moves), "max": max(moves),
                                            "n": len(moves), "signals": [s["title"] for s in signals]}
    return out


def rank_sectors(state: dict, baskets: pd.DataFrame, members: dict[str, list[str]],
                 factors: pd.DataFrame | None, flags: list[str]) -> list[dict]:
    closes, returns, weights = state["closes"], state["returns"], state["weights"]
    sectors = list(members)
    with ThreadPoolExecutor(max_workers=min(config.NEWS_WORKERS, len(sectors))) as pool:
        sentiment = dict(zip(sectors, pool.map(sector_sentiment, sectors)))
    analogs = analog_moves(baskets, closes)
    trained = impact.model()
    stocks = [{"ticker": t, "sector": sector} for sector, tickers in members.items() for t in tickers]
    try:
        scored = impact.score_positions(stocks, closes) or {}
    except Exception:
        scored = {}

    rows = {}
    combined = returns.join(baskets)
    nifty_level = closes[NIFTY]
    for sector in sectors:
        column = f"sector:{sector}"
        level = (1 + baskets[column].fillna(0)).cumprod()
        fit = pm.candidate_improvement(combined, weights, column, config.CANDIDATE_SHARE, config.RISK_FREE_RATE)
        strength = {}
        for label, sessions in (("3m", 63), ("6m", 126), ("12m", 252)):
            if len(level) > sessions:
                strength[label] = float(level.iloc[-1] / level.iloc[-sessions - 1] - 1
                                        - (nifty_level.iloc[-1] / nifty_level.iloc[-sessions - 1] - 1))
            else:
                strength[label] = None
        above = [closes[t].iloc[-1] > closes[t].iloc[-200:].mean() for t in members[sector] if closes[t].notna().sum() >= 200]
        extension = float(level.iloc[-1] / level.iloc[-200:].mean() - 1) if len(level) >= 200 else None
        falls = [scored[t]["fall"] for t in members[sector] if t in scored]
        rows[sector] = {
            "sector": sector, "constituents": len(members[sector]), "fit": fit,
            "trend": {"relative_strength": strength, "breadth": sum(above) / len(above) if above else None,
                      "extension": extension},
            "evidence": {"sentiment": sentiment[sector]["mean"], "headlines": sentiment[sector]["headlines"],
                         "fall_chance": sum(falls) / len(falls) if falls else None, "stocks_scored": len(falls),
                         "typical_fall_chance": trained["fall"]["base_rate"] if trained else None,
                         "analog": analogs.get(sector)},
            "macro": {"betas": weekly_betas(baskets[column], factors) if factors is not None else None},
        }

    def collect(read) -> dict[str, float | None]:
        return {sector: read(rows[sector]) for sector in sectors}

    fit_scores = [
        rank_scores(collect(lambda r: r["fit"] and r["fit"]["vol_change"]), higher_is_better=False),
        rank_scores(collect(lambda r: r["fit"] and r["fit"]["cvar_change"]), higher_is_better=False),
        rank_scores(collect(lambda r: r["fit"] and r["fit"]["sharpe_change"])),
        rank_scores(collect(lambda r: r["fit"] and r["fit"]["correlation"]), higher_is_better=False),
    ]
    evidence_scores = [
        rank_scores(collect(lambda r: r["evidence"]["sentiment"])),
        rank_scores(collect(lambda r: r["evidence"]["fall_chance"]), higher_is_better=False),
        rank_scores(collect(lambda r: r["evidence"]["analog"] and r["evidence"]["analog"]["mean"])),
    ]
    trend_scores = [rank_scores(collect(lambda r, k=key: r["trend"]["relative_strength"][k])) for key in ("3m", "6m", "12m")]
    trend_scores.append(rank_scores(collect(lambda r: r["trend"]["breadth"])))

    def favour(row: dict) -> float | None:
        betas = row["macro"]["betas"]
        if not flags or betas is None:
            return None
        return sum(betas[REGIME_RULES[flag][0]] * REGIME_RULES[flag][1] for flag in flags)

    macro_scores = rank_scores(collect(favour))

    ranked = []
    for sector in sectors:
        row = rows[sector]
        evidence = row["evidence"]
        thin = ((evidence["analog"] or {}).get("n", 0) < config.THIN_ANALOGS
                or evidence["headlines"] < config.THIN_HEADLINES)
        extended = row["trend"]["extension"] is not None and row["trend"]["extension"] > config.EXTENDED_ABOVE_200D
        scores = {
            "fit": mean_score([s[sector] for s in fit_scores]),
            "evidence": mean_score([s[sector] for s in evidence_scores]),
            "macro": macro_scores[sector],
            "trend": mean_score([s[sector] for s in trend_scores]),
        }
        flags_out = []
        if thin:
            scores["evidence"] = scores["evidence"] * config.THIN_PENALTY if scores["evidence"] is not None else None
            flags_out.append(
                "Thin evidence: " + ("no live crisis signal, so there are no comparable past events"
                                     if not evidence["analog"] else
                                     f'{evidence["analog"]["n"]} comparable past events') +
                f' and {evidence["headlines"]} headlines')
        if extended:
            scores["trend"] = scores["trend"] * config.EXTENDED_PENALTY if scores["trend"] is not None else None
            flags_out.append(f'Extended: {pct(row["trend"]["extension"])} above its 200-day average')
        if scores["macro"] is None:
            flags_out.append("Macro fit is neutral: " + ("no stress flag is raised" if not flags else "macro history unavailable"))
        row["macro"].update(regime=flags, favour=favour(row))
        ranked.append({**row, "scores": scores, "composite": round(composite(scores, config.SECTOR_WEIGHTS), 1),
                       "flags": flags_out, "thin_evidence": thin, "extended": extended})
    ranked.sort(key=lambda r: -r["composite"])
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    return ranked


# --------------------------------------------------------------------------- step 3: stocks


def risk_effective_n(returns: pd.DataFrame, weights: dict[str, float]) -> float | None:
    frame = returns[list(weights)].dropna(how="any")
    if len(frame) < config.MIN_SESSIONS:
        return None
    cov, _ = pm.ledoit_wolf(frame.to_numpy())
    return pm.effective_n(pm.risk_contributions(np.array(list(weights.values())), cov))


def pick_stocks(state: dict, sector: str, tickers: list[str]) -> dict:
    """Up to three stocks in the sector that the user does not hold, pass the size
    and liquidity filters and do not raise the portfolio's risk concentration."""
    closes, returns, weights = state["closes"], state["returns"], state["weights"]
    held = {p["ticker"] for p in state["positions"]}
    facts = fundamentals(tickers)
    try:
        scored = impact.score_positions([{"ticker": t, "sector": sector} for t in tickers], closes) or {}
    except Exception:
        scored = {}
    name_of = {t: t.split(".")[0] for t in tickers}
    headlines = get_news(" OR ".join(name_of[t] for t in tickers) + " when:7d", 30)
    before_n = risk_effective_n(returns, weights)

    excluded, candidates = [], {}
    for ticker in tickers:
        symbol, fact = name_of[ticker], facts.get(ticker, {})
        price = float(closes[ticker].iloc[-1])
        daily_value = fact["average_volume"] * price if fact.get("average_volume") else None
        if ticker in held:
            excluded.append({"symbol": symbol, "reason": "already held"})
            continue
        if fact.get("market_cap") is None or daily_value is None:
            excluded.append({"symbol": symbol, "reason": "size or liquidity unknown, so the filters cannot be checked"})
            continue
        if fact["market_cap"] < config.MIN_MARKET_CAP:
            excluded.append({"symbol": symbol, "reason": "market value below Rs 20,000 crore"})
            continue
        if daily_value < config.MIN_DAILY_VALUE:
            excluded.append({"symbol": symbol, "reason": "trades less than Rs 25 crore a day"})
            continue
        improvement = pm.candidate_improvement(returns, weights, ticker, config.CANDIDATE_SHARE, config.RISK_FREE_RATE)
        if improvement is None:
            excluded.append({"symbol": symbol, "reason": "less than a year of shared price history"})
            continue
        after_n = risk_effective_n(returns, pm.blend(weights, ticker, config.CANDIDATE_SHARE))
        spreads_less = before_n is not None and after_n is not None and after_n < before_n
        moves_alike = improvement["correlation"] is not None and improvement["correlation"] >= config.MAX_CORRELATION
        if spreads_less or moves_alike:
            excluded.append({"symbol": symbol, "reason": "adding it would concentrate portfolio risk further"
                             + (f' (its correlation with the portfolio is {improvement["correlation"]:.2f})'
                                if moves_alike else "")})
            continue
        own = returns[ticker].dropna()
        level = closes[ticker].iloc[-config.LOOKBACK_SESSIONS:]
        named = [h["title"] for h in headlines if names_holding(h["title"], symbol, ticker)]
        tones = sentiment_tool.score_many(named)
        candidates[ticker] = {
            "ticker": ticker, "symbol": symbol, "price": round(price, 2), "improvement": improvement,
            "fundamentals": {k: fact.get(k) for k in ("market_cap", "roe", "margin", "debt_to_equity", "pe")},
            "daily_value": daily_value,
            "momentum_6m": float(closes[ticker].iloc[-1] / closes[ticker].iloc[-127] - 1) if len(closes[ticker].dropna()) > 127 else None,
            "volatility": float(own.std()) * math.sqrt(pm.TRADING_DAYS),
            "max_drawdown": float((level / level.cummax() - 1).min()),
            "above_200d": float(closes[ticker].iloc[-1] / closes[ticker].iloc[-200:].mean() - 1),
            "sentiment": sum(tones) / len(tones) if tones else None, "headlines": len(tones),
            "fall_chance": scored.get(ticker, {}).get("fall"), "downside": scored.get(ticker, {}).get("downside"),
        }

    def collect(read) -> dict[str, float | None]:
        return {t: read(c) for t, c in candidates.items()}

    pes = [c["fundamentals"]["pe"] for c in candidates.values() if c["fundamentals"]["pe"] and c["fundamentals"]["pe"] > 0]
    median_pe = float(np.median(pes)) if pes else None
    parts = {
        "improvement": [rank_scores(collect(lambda c: c["improvement"]["sharpe_change"])),
                        rank_scores(collect(lambda c: c["improvement"]["vol_change"]), higher_is_better=False)],
        "quality": [rank_scores(collect(lambda c: c["fundamentals"]["roe"])),
                    rank_scores(collect(lambda c: c["fundamentals"]["margin"])),
                    rank_scores(collect(lambda c: c["fundamentals"]["debt_to_equity"]), higher_is_better=False)],
        "valuation": [rank_scores(collect(lambda c: c["fundamentals"]["pe"] / median_pe
                                          if median_pe and c["fundamentals"]["pe"] and c["fundamentals"]["pe"] > 0 else None),
                                  higher_is_better=False)],
        "momentum": [rank_scores(collect(lambda c: c["momentum_6m"]))],
        "sentiment": [rank_scores(collect(lambda c: c["sentiment"]))],
        "risk": [rank_scores(collect(lambda c: c["volatility"]), higher_is_better=False),
                 rank_scores(collect(lambda c: c["max_drawdown"]))],
    }
    for ticker, candidate in candidates.items():
        candidate["scores"] = {name: mean_score([s[ticker] for s in group]) for name, group in parts.items()}
        candidate["composite"] = round(composite(candidate["scores"], config.STOCK_WEIGHTS), 1)
        candidate["pe_vs_sector"] = (candidate["fundamentals"]["pe"] / median_pe
                                     if median_pe and candidate["fundamentals"]["pe"] else None)
    ranked = sorted(candidates.values(), key=lambda c: -c["composite"])
    return {"sector": sector, "considered": len(tickers), "sector_median_pe": median_pe,
            "excluded": excluded, "stocks": ranked[:config.PICKS_PER_SECTOR],
            "fundamentals_note": source_note(facts)}


# --------------------------------------------------------------------------- step 4: sizing and validation


def size_and_validate(state: dict, picks: list[dict]) -> dict:
    """Weights for the additions from the capped optimizer, what is trimmed to pay
    for them, the cost of the trades, and a walk-forward comparison with the
    portfolio as it is and with equal weights."""
    returns, weights, total = state["returns"], state["weights"], state["total"]
    names = {p["ticker"]: p["name"] for p in state["positions"]} | {c["ticker"]: c["symbol"] for c in picks}
    new = [c["ticker"] for c in picks]
    assets = [*weights, *new]
    frame = returns[assets].dropna(how="any")
    if not new or len(frame) < config.MIN_SESSIONS:
        return {"status": INSUFFICIENT, "reason": "no stock passed the filters" if not new else
                "the holdings and picks share less than a year of price history"}

    start = np.array([weights[t] for t in weights] + [0.0] * len(new))
    # A holding can only be trimmed, by at most half; a new stock is capped, and so are all of them together.
    caps = np.array([weights[t] for t in weights] + [config.MAX_NEW_WEIGHT] * len(new))
    floors = np.array([weights[t] * (1 - config.MAX_TRIM_OF_HOLDING) for t in weights] + [0.0] * len(new))
    additions = np.array([False] * len(weights) + [True] * len(new))

    def proposed_weights(history: pd.DataFrame) -> np.ndarray:
        data = history.to_numpy()
        return pm.optimise(pm.shrunk_means(data), pm.ledoit_wolf(data)[0], caps, start, floors,
                           additions, config.MAX_TOTAL_NEW)

    proposed = proposed_weights(frame)
    cov, shrinkage = pm.ledoit_wolf(frame.to_numpy())
    turnover = radar.daily_turnover(assets)
    rows, cost = [], 0.0
    for ticker, now, target in zip(assets, start, proposed):
        change = float(target - now)
        value = abs(change) * total
        bps = share_slippage_bps(value, turnover.get(ticker)) if value > 0 else 0.0
        cost += value * bps / 10_000
        rows.append({"ticker": ticker, "name": names[ticker], "current": float(now), "proposed": float(target),
                     "change": change, "trade_value": round(value), "new": ticker in new})

    lookback = min(config.WALK_FORWARD_LOOKBACK, len(frame) - config.WALK_FORWARD_HOLD * 3)
    periods = min(config.WALK_FORWARD_PERIODS, (len(frame) - lookback) // config.WALK_FORWARD_HOLD)
    tests = {
        "proposed": pm.walk_forward(frame, proposed_weights, lookback, config.WALK_FORWARD_HOLD, periods),
        "current": pm.walk_forward(frame, lambda history: start, lookback, config.WALK_FORWARD_HOLD, periods),
        "equal_weight": pm.walk_forward(frame, lambda history: np.full(len(assets), 1 / len(assets)), lookback,
                                        config.WALK_FORWARD_HOLD, periods),
    }
    measured = {name: pm.series_stats(series, config.RISK_FREE_RATE) for name, series in tests.items()}
    for name, series in tests.items():
        measured[name]["sharpe_interval"] = pm.sharpe_interval(series, config.RISK_FREE_RATE)

    def beats(other: str) -> dict | None:
        mine, theirs = measured["proposed"], measured[other]
        if mine["sharpe"] is None or theirs["sharpe"] is None:
            return None
        return {"sharpe": mine["sharpe"] > theirs["sharpe"], "vol": mine["vol"] <= theirs["vol"],
                "drawdown": mine["max_drawdown"] >= theirs["max_drawdown"]}

    versus_current, versus_equal = beats("current"), beats("equal_weight")
    labels = (("sharpe", "Sharpe ratio"), ("vol", "volatility"), ("drawdown", "drawdown"))

    def shortfall(result: dict, against: str) -> str | None:
        failed = [label for key, label in labels if not result[key]]
        return f"{' and '.join(failed)} against {against}" if failed else None

    if versus_current is None or versus_equal is None:
        change = False
        verdict = "The walk-forward window is too short to judge, so no change is recommended."
    else:
        misses = [m for m in (shortfall(versus_current, "the current portfolio"),
                              shortfall(versus_equal, "an equal-weighted mix of the same stocks")) if m]
        change = not misses
        verdict = ("Out of sample the proposed portfolio beat both the current portfolio and an equal-weighted mix "
                   "on Sharpe ratio, volatility and drawdown, so the additions are recommended." if change else
                   f"Out of sample the proposed portfolio did not do better on {'; nor on '.join(misses)}. No change "
                   f"is recommended; the suggestions below are shown for information only.")
    return {
        "status": "ok", "weights": rows, "covariance_shrinkage": shrinkage, "sessions": int(len(frame)),
        "trade_cost": round(cost), "turnover_share": float(sum(abs(r["change"]) for r in rows) / 2),
        "liquidity_unknown": [names[t] for t in assets if t not in turnover],
        "method": f"Mean-variance weights with Ledoit-Wolf covariance and returns shrunk halfway to their average. "
                  f"A holding can only be trimmed, by at most {pct(config.MAX_TRIM_OF_HOLDING, 0)}; a new stock is "
                  f"capped at {pct(config.MAX_NEW_WEIGHT, 0)} and all additions together at "
                  f"{pct(config.MAX_TOTAL_NEW, 0)}.",
        "walk_forward": {
            "rebalances": int(periods), "hold_sessions": config.WALK_FORWARD_HOLD, "lookback_sessions": int(lookback),
            "results": measured, "beats_current": versus_current, "beats_equal_weight": versus_equal,
            "note": "The weights at each rebalance used only earlier prices. The choice of stocks used today's "
                    "data, so the test checks the sizing, not the stock selection.",
        },
        "recommend_change": change, "verdict": verdict,
    }


# --------------------------------------------------------------------------- step 5: suggestions


def build_suggestion(state: dict, sector: dict, stock: dict, sizing: dict, diagnosis: dict) -> dict:
    returns, weights = state["returns"], state["weights"]
    row = next((r for r in sizing.get("weights", []) if r["ticker"] == stock["ticker"]), None)
    weight = row["proposed"] if row and row["proposed"] >= 0.005 else None
    tested_at = weight or config.CANDIDATE_SHARE
    effect = pm.candidate_improvement(returns, weights, stock["ticker"], tested_at, config.RISK_FREE_RATE)
    frame = returns[[*weights, stock["ticker"]]].dropna(how="any")
    mixed = pm.blend(weights, stock["ticker"], tested_at)
    before_series, after_series = pm.portfolio_series(frame, weights), pm.portfolio_series(frame, mixed)
    trims = [(r["name"], -r["change"]) for r in sizing.get("weights", []) if not r["new"] and r["change"] < -0.0005]
    trimmed = sum(amount for _, amount in trims)
    # Every addition is paid for by the same trims, each in proportion to its size.
    funding = [{"name": name, "weight": (weight or 0) * amount / trimmed}
               for name, amount in sorted(trims, key=lambda t: -t[1])] if trimmed and row else []
    evidence, trend = sector["evidence"], sector["trend"]
    trained = impact.model()
    analog = evidence["analog"]

    thesis = [
        f'{sector["sector"]} ranks {sector["rank"]} of {len(config.RANKED_SECTORS)} for this portfolio (score '
        f'{sector["composite"]:.1f} of 100). At {pct(tested_at)} of the portfolio, {stock["symbol"]} changes its '
        f'volatility by {pct(effect["vol_change"], 2, signed=True)} and its Sharpe ratio by {effect["sharpe_change"]:+.2f} '
        f'over the last {effect["n"]} sessions.',
        "Evidence: sector news sentiment "
        + (f'{evidence["sentiment"]:+.2f} across {evidence["headlines"]} headlines (Google News, last 7 days, scored by '
           f"{sentiment_tool.backend()})" if evidence["sentiment"] is not None else INSUFFICIENT)
        + "; impact model chance of a sharp fall "
        + (f'{pct(evidence["fall_chance"], 0)} against a typical {pct(evidence["typical_fall_chance"], 0)}'
           if evidence["fall_chance"] is not None else INSUFFICIENT)
        + "; "
        + (f'{analog["n"]} comparable past events moved the sector {pct(analog["mean"], signed=True)} beyond the Nifty '
           f'over {HORIZON} sessions (range {pct(analog["min"], signed=True)} to {pct(analog["max"], signed=True)})'
           if analog else "no live crisis signal, so no comparable past events")
        + ".",
    ]
    risks = [f'Its own volatility is {pct(stock["volatility"], 0)} a year and its worst fall in the last three years '
             f'was {pct(stock["max_drawdown"], 0)}.']
    if stock["above_200d"] > config.EXTENDED_ABOVE_200D:
        risks.append(f'It is {pct(stock["above_200d"], 0)} above its 200-day average, so it may be extended.')
    if sector["thin_evidence"]:
        risks.append("The sector's evidence is thin, so its rank leans on portfolio fit and trend.")
    missing = [label for label, key in (("return on equity", "roe"), ("profit margin", "margin"),
                                        ("debt to equity", "debt_to_equity"), ("P/E", "pe"))
               if stock["fundamentals"][key] is None]
    if missing:
        risks.append(f'{", ".join(missing).capitalize()}: {INSUFFICIENT}, so those did not count in its score.')

    cutoff = trained["fall"]["elevated_cutoff"] if trained else None
    triggers = [
        f'{sector["sector"]} 3-month return versus the Nifty turns negative (now '
        f'{pct(trend["relative_strength"]["3m"], signed=True)}).',
        f'{stock["symbol"]} closes below its 200-day average (now {pct(stock["above_200d"], signed=True)} from it).',
    ]
    if cutoff is not None and stock["fall_chance"] is not None:
        triggers.append(f'The impact model\'s chance of a sharp fall for {stock["symbol"]} rises to {pct(cutoff, 0)} or '
                        f'more (now {pct(stock["fall_chance"], 0)}).')
    if evidence["sentiment"] is not None:
        triggers.append(f'Sector news sentiment falls to {sentiment_tool.cutoffs()[1]:+.2f} or lower (now '
                        f'{evidence["sentiment"]:+.2f}).')

    cov_before = pm.ledoit_wolf(frame[list(weights)].to_numpy())[0]
    cov_after = pm.ledoit_wolf(frame[list(mixed)].to_numpy())[0]
    return {
        "sector": sector["sector"], "sector_rank": sector["rank"], "symbol": stock["symbol"], "ticker": stock["ticker"],
        "stock_score": stock["composite"], "stock_scores": stock["scores"],
        "thesis": thesis,
        "model_drivers": {
            "note": "SHAP values are not computed in this app. These are the impact model's overall feature "
                    "importances, the same for every stock.",
            "top": [(DRIVER_LABELS.get(name, name.replace("_", " ")), share)
                    for name, share in list(trained["fall"]["what_drives_it"].items())[:3]] if trained else [],
        },
        "weight": weight, "tested_at": tested_at,
        "sizing_note": None if weight else "The optimizer gave this stock no weight, so its effect is shown at "
                                           f"{pct(config.CANDIDATE_SHARE, 0)} for comparison only.",
        "funding": funding if weight else [],
        "metrics": {
            "sessions": effect["n"], "basis": "historical, in-sample",
            "vol": {"before": effect["before"]["vol"], "after": effect["after"]["vol"]},
            "cvar": {"before": effect["before"]["cvar"], "after": effect["after"]["cvar"]},
            "sharpe": {"before": effect["before"]["sharpe"], "after": effect["after"]["sharpe"],
                       "before_interval": pm.sharpe_interval(before_series, config.RISK_FREE_RATE),
                       "after_interval": pm.sharpe_interval(after_series, config.RISK_FREE_RATE)},
            "effective_n": {"before": pm.effective_n(np.array(list(weights.values()))),
                            "after": pm.effective_n(np.array(list(mixed.values())))},
            "top_risk_share": {
                "before": float(pm.risk_contributions(np.array(list(weights.values())), cov_before).max()),
                "after": float(pm.risk_contributions(np.array(list(mixed.values())), cov_after).max())},
            "correlation": effect["correlation"],
        },
        "fundamentals": {**stock["fundamentals"], "pe_vs_sector": stock["pe_vs_sector"]},
        "momentum_6m": stock["momentum_6m"], "sentiment": stock["sentiment"], "headlines": stock["headlines"],
        "entry_risks": risks,
        "bear_case": (f'The impact model puts a 1-in-20 week for {stock["symbol"]} at a fall of '
                      f'{pct(abs(stock["downside"]))} or more over {HORIZON} sessions. ' if stock["downside"] is not None
                      else f"The impact model could not score {stock['symbol']}. ")
                     + f'Its worst peak-to-trough fall in the last three years was {pct(abs(stock["max_drawdown"]), 0)}.',
        "triggers": triggers,
    }


# --------------------------------------------------------------------------- narration


def narrate(statements: list[str]) -> tuple[str | None, str]:
    """A short summary by the language model, kept only if every figure in it
    appears in the statements it was given. Returns the text and who wrote it."""
    if not llm.available():
        return None, "none: no language model key is configured"
    text = llm.complete(
        "You summarise a portfolio analysis for an investor in under 120 words. Use only the statements given. "
        "Copy every figure exactly as written; do not round, convert or calculate. No advice beyond the statements.",
        "\n".join(statements))
    if not isinstance(text, str) or not text.strip():
        return None, "none: the language model did not reply"
    allowed = set().union(*(_numbers(s) for s in statements)) if statements else set()
    invented = _numbers(text) - allowed
    if invented:
        return None, f"none: the model's summary was rejected for figures not in the analysis ({', '.join(sorted(invented)[:5])})"
    return text.strip(), "language model, every figure checked against the analysis"


# --------------------------------------------------------------------------- entry point


def suggest(raw_holdings: list[dict] | None) -> dict:
    state = load(raw_holdings)
    base = {"generated_at": datetime.now(timezone.utc).isoformat(), "footer": config.FOOTER,
            "config": {"sector_weights": config.SECTOR_WEIGHTS, "stock_weights": config.STOCK_WEIGHTS,
                       "candidate_share": config.CANDIDATE_SHARE, "max_new_weight": config.MAX_NEW_WEIGHT,
                       "risk_free_rate": config.RISK_FREE_RATE, "lookback_sessions": config.LOOKBACK_SESSIONS}}
    if "problem" in state:
        return {**base, "status": INSUFFICIENT, "reason": state["problem"]}

    try:
        flags = _macro_flags(get_macro()[0])
    except Exception:
        flags = []
    factors = macro_factors(state["closes"])
    baskets, members = sector_baskets(state["returns"], state["universe"])
    diagnosis = diagnose(state, factors, flags)
    ranking = rank_sectors(state, baskets, members, factors, flags)

    top = ranking[:config.MAX_SECTORS]
    picks = [pick_stocks(state, sector["sector"], members[sector["sector"]]) for sector in top]
    chosen = [stock for group in picks for stock in group["stocks"]]
    sizing = size_and_validate(state, chosen)
    suggestions = [build_suggestion(state, sector, stock, sizing, diagnosis)
                   for sector, group in zip(top, picks) for stock in group["stocks"]]

    statements = [gap["detail"] for gap in diagnosis["gaps"]]
    statements += [f'{s["sector"]} scores {s["composite"]:.1f} of 100 and ranks {s["rank"]}.' for s in top]
    statements += [line for s in suggestions for line in s["thesis"]]
    if sizing.get("verdict"):
        statements.append(sizing["verdict"])
    narrative, narrator = narrate(statements)

    return {
        **base, "status": "ok", "portfolio_source": state["source"],
        "as_of": state["closes"].index[-1].date().isoformat(),
        "data_notes": [
            f'All statistics use the last {len(state["returns"])} sessions of closing prices and are historical and '
            f"in-sample, except the walk-forward results.",
            "Sectors are equal-weighted baskets of the stocks in this app's sector map, measured against the Nifty 50.",
            *([f'Left out for lack of a price: {", ".join(state["unpriced"])}.'] if state["unpriced"] else []),
            *([f'Left out for less than a year of prices: {", ".join(state["short_history"])}.'] if state["short_history"] else []),
            *(["Macro history was unavailable, so macro sensitivities and macro fit are missing."] if factors is None else []),
            *dict.fromkeys(group["fundamentals_note"] for group in picks if group.get("fundamentals_note")),
        ],
        "regime": flags,
        "diagnosis": diagnosis, "sectors": ranking, "picks": picks, "sizing": sizing,
        "suggestions": suggestions, "narrative": narrative, "narrator": narrator,
    }
