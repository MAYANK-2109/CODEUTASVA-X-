import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.advisor import config, suggest as advisor
from app.advisor import router as advisor_router
from app.main import app
from app.risk import portfolio_metrics as pm
from app.tools import resolver
from app.tools.market import NIFTY, SECTORS

client = TestClient(app)
UNIVERSE = {f"{symbol}.NS": sector for symbol, sector in SECTORS.items() if sector in config.RANKED_SECTORS}
HOLDINGS = [
    {"name": "Reliance Industries", "symbol": "RELIANCE", "units": 100, "type": "STOCK"},
    {"name": "Oil & Natural Gas Corp", "symbol": "ONGC", "units": 400, "type": "STOCK"},
    {"name": "HDFC Bank", "symbol": "HDFCBANK", "units": 20, "type": "STOCK"},
]


def market(days: int = 900, end=None, pharma_boost: float = 0.0) -> pd.DataFrame:
    """Every stock follows the index and its sector; pharma can be given a late run-up."""
    index = pd.bdate_range(end=end or pd.Timestamp.now().normalize(), periods=days)
    rng = np.random.default_rng(21)
    nifty = rng.normal(0.0004, 0.008, days)
    factors = {sector: rng.normal(0, 0.006, days) for sector in config.RANKED_SECTORS}
    factors["Pharma"][-150:] += pharma_boost
    frame = {NIFTY: 20000 * np.cumprod(1 + nifty)}
    for ticker, sector in UNIVERSE.items():
        beta = 1.2 if sector in ("Energy", "Banking", "Metals") else 0.6
        frame[ticker] = 100 * np.cumprod(1 + beta * nifty + factors[sector] + rng.normal(0, 0.006, days))
    return pd.DataFrame(frame, index=index)


def macro(index: pd.DatetimeIndex) -> pd.DataFrame:
    rng = np.random.default_rng(4)
    walk = lambda start, spread: start * np.cumprod(1 + rng.normal(0, spread, len(index)))  # noqa: E731
    return pd.DataFrame({"^INDIAVIX": walk(15, 0.03), "BZ=F": walk(80, 0.015), "INR=X": walk(83, 0.002)}, index=index)


FACTS = {ticker: {"market_cap": 5e11, "average_volume": 5_000_000, "roe": 0.15, "margin": 0.12,
                  "debt_to_equity": 40.0, "pe": 25.0} for ticker in UNIVERSE}


@pytest.fixture
def offline(monkeypatch):
    """Fixed prices, no network, no language model."""
    monkeypatch.setenv("SENTIMENT_MODEL", "vader")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(resolver, "search", lambda query: None)
    monkeypatch.setattr(advisor_router, "_cache", {})
    state = {"closes": market(), "facts": dict(FACTS), "signals": []}
    monkeypatch.setattr(advisor, "get_history", lambda tickers: (state["closes"][[*dict.fromkeys(tickers), NIFTY]].copy(), "live"))
    monkeypatch.setattr(advisor, "get_cross_assets", lambda: macro(state["closes"].index))
    monkeypatch.setattr(advisor, "get_macro", lambda: ([], None))
    monkeypatch.setattr(advisor, "get_news", lambda query, limit=8: [
        {"title": "Sector output steady this quarter", "source": "Wire", "url": query, "published_at": None}] * 6)
    monkeypatch.setattr(advisor, "fundamentals", lambda tickers: {t: state["facts"][t] for t in tickers if t in state["facts"]})
    monkeypatch.setattr(advisor.radar, "current_signals", lambda: state["signals"])
    monkeypatch.setattr(advisor.radar, "daily_turnover", lambda tickers: {})
    monkeypatch.setattr(advisor.impact, "score_positions", lambda positions, closes: {})
    monkeypatch.setattr(advisor.sector_lookup, "resolve", lambda tickers: {t: "Other" for t in tickers})
    return state


# --------------------------------------------------------------------------- scoring


def test_rank_scores_are_percentiles_and_missing_stays_missing():
    scores = advisor.rank_scores({"a": 1.0, "b": 3.0, "c": 2.0, "d": None})
    assert scores == {"a": 0.0, "b": 100.0, "c": 50.0, "d": None}
    assert advisor.rank_scores({"a": 1.0, "b": 3.0, "c": 2.0}, higher_is_better=False) == {"a": 100.0, "b": 0.0, "c": 50.0}
    assert advisor.rank_scores({"a": 2.0, "b": 2.0, "c": 5.0}) == {"a": 25.0, "b": 25.0, "c": 100.0}    # ties share a place
    assert advisor.rank_scores({"a": None}) == {"a": None} and advisor.rank_scores({"a": 4.0}) == {"a": 50.0}


def test_composite_uses_the_configured_weights_and_is_neutral_on_missing():
    assert sum(config.SECTOR_WEIGHTS.values()) == 100 and sum(config.STOCK_WEIGHTS.values()) == 100
    assert config.SECTOR_WEIGHTS["fit"] == max(config.SECTOR_WEIGHTS.values())            # fit carries the most weight
    full = advisor.composite({"fit": 100, "evidence": 0, "macro": 0, "trend": 0}, config.SECTOR_WEIGHTS)
    assert full == config.SECTOR_WEIGHTS["fit"]
    assert advisor.composite({"fit": 80, "evidence": 60, "macro": None, "trend": 40}, config.SECTOR_WEIGHTS) == pytest.approx(
        (40 * 80 + 25 * 60 + 20 * 50 + 15 * 40) / 100)
    assert advisor.mean_score([None, None]) is None and advisor.mean_score([40.0, None, 60.0]) == 50.0


def test_sector_ranking_shows_every_sub_score_and_penalises_thin_and_extended(offline):
    offline["closes"] = market(pharma_boost=0.004)                 # pharma has run far above its 200-day average
    body = advisor.suggest(HOLDINGS)
    sectors = {s["sector"]: s for s in body["sectors"]}
    assert set(sectors) == set(config.RANKED_SECTORS) and [s["rank"] for s in body["sectors"]] == list(range(1, 12))
    assert body["sectors"] == sorted(body["sectors"], key=lambda s: -s["composite"])
    for row in body["sectors"]:
        assert set(row["scores"]) == {"fit", "evidence", "macro", "trend"}
        assert row["composite"] == round(advisor.composite(row["scores"], config.SECTOR_WEIGHTS), 1)
        # No live crisis signal: no comparable events, so evidence is thin, flagged and capped.
        assert row["thin_evidence"] and row["evidence"]["analog"] is None
        assert row["scores"]["evidence"] <= 100 * config.THIN_PENALTY
        assert row["scores"]["macro"] is None            # no stress flag raised, so macro fit is neutral
    pharma = sectors["Pharma"]
    assert pharma["extended"] and pharma["trend"]["extension"] > config.EXTENDED_ABOVE_200D
    assert pharma["scores"]["trend"] <= 100 * config.EXTENDED_PENALTY
    assert any(flag.startswith("Extended") for flag in pharma["flags"])
    assert sectors["Energy"]["fit"]["correlation"] > sectors["FMCG"]["fit"]["correlation"]      # energy is already held


# --------------------------------------------------------------------------- exclusions


def test_stock_exclusion_rules(offline):
    offline["facts"]["SUNPHARMA.NS"] = {**FACTS["SUNPHARMA.NS"], "market_cap": 1e10}             # too small
    offline["facts"]["CIPLA.NS"] = {**FACTS["CIPLA.NS"], "average_volume": 10}                    # too illiquid
    del offline["facts"]["DRREDDY.NS"]                                                            # nothing known
    state = advisor.load(HOLDINGS)
    pharma = advisor.pick_stocks(state, "Pharma", [t for t, s in UNIVERSE.items() if s == "Pharma"])
    reasons = {e["symbol"]: e["reason"] for e in pharma["excluded"]}
    assert "below Rs 20,000 crore" in reasons["SUNPHARMA"] and "less than Rs 25 crore" in reasons["CIPLA"]
    assert "cannot be checked" in reasons["DRREDDY"]
    assert [s["symbol"] for s in pharma["stocks"]] == ["DIVISLAB"]

    energy = advisor.pick_stocks(state, "Energy", [t for t, s in UNIVERSE.items() if s == "Energy"])
    held = {e["symbol"] for e in energy["excluded"] if e["reason"] == "already held"}
    assert held == {"RELIANCE", "ONGC"}
    picked = {s["symbol"] for s in energy["stocks"]}
    assert not picked & {"RELIANCE", "ONGC"} and len(energy["stocks"]) <= config.PICKS_PER_SECTOR


def test_a_stock_that_concentrates_risk_is_excluded(offline):
    twin = offline["closes"]["RELIANCE.NS"] * 1.0        # moves exactly like the largest holding
    offline["closes"] = offline["closes"].assign(**{"BPCL.NS": twin})
    state = advisor.load([HOLDINGS[0]])                  # a one-stock portfolio: any twin adds no diversification
    energy = advisor.pick_stocks(state, "Energy", ["BPCL.NS", "IOC.NS"])
    reasons = {e["symbol"]: e["reason"] for e in energy["excluded"]}
    assert "concentrate portfolio risk" in reasons["BPCL"]
    assert [s["symbol"] for s in energy["stocks"]] == ["IOC"]


# --------------------------------------------------------------------------- the whole run


def test_suggestions_respect_every_limit_and_never_suggest_a_holding(offline):
    body = client.post("/api/advisor/suggestions", json={"holdings": HOLDINGS}).json()
    assert body["status"] == "ok" and body["footer"] == "Educational analysis, not financial advice."
    assert len(body["diagnosis"]["gaps"]) == 3 and len(body["picks"]) == config.MAX_SECTORS
    assert [p["sector"] for p in body["picks"]] == [s["sector"] for s in body["sectors"][:3]]
    assert 0 < len(body["suggestions"]) <= config.MAX_SECTORS * config.PICKS_PER_SECTOR
    held = {"RELIANCE", "ONGC", "HDFCBANK"}
    assert not {s["symbol"] for s in body["suggestions"]} & held

    rows = body["sizing"]["weights"]
    assert sum(r["proposed"] for r in rows) == pytest.approx(1, abs=1e-6)
    for row in rows:
        if row["new"]:
            assert row["current"] == 0 and row["proposed"] <= config.MAX_NEW_WEIGHT + 1e-9
        else:   # a holding is only ever trimmed, and by no more than half
            assert row["current"] * (1 - config.MAX_TRIM_OF_HOLDING) - 1e-9 <= row["proposed"] <= row["current"] + 1e-9
    assert sum(r["proposed"] for r in rows if r["new"]) <= config.MAX_TOTAL_NEW + 1e-6

    walk = body["sizing"]["walk_forward"]
    assert set(walk["results"]) == {"proposed", "current", "equal_weight"} and walk["rebalances"] >= 3
    assert body["sizing"]["recommend_change"] == (all(walk["beats_current"].values())
                                                  and all(walk["beats_equal_weight"].values()))
    assert ("no change is recommended" in body["sizing"]["verdict"].lower()) != body["sizing"]["recommend_change"]

    for suggestion in body["suggestions"]:
        assert len(suggestion["thesis"]) == 2 and suggestion["triggers"] and suggestion["bear_case"]
        assert suggestion["metrics"]["basis"] == "historical, in-sample" and suggestion["metrics"]["sessions"] >= 250
        low, high = suggestion["metrics"]["sharpe"]["after_interval"]
        assert low < suggestion["metrics"]["sharpe"]["after"] < high
        assert "SHAP values are not computed" in suggestion["model_drivers"]["note"]
        if suggestion["weight"]:
            assert sum(f["weight"] for f in suggestion["funding"]) == pytest.approx(suggestion["weight"], rel=1e-6)
    assert body["narrative"] is None and "no language model" in body["narrator"]


def test_stale_or_missing_prices_give_insufficient_data(offline, monkeypatch):
    offline["closes"] = market(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=30))
    stale = client.post("/api/advisor/suggestions", json={"holdings": HOLDINGS}).json()
    assert stale["status"] == "insufficient data" and "days old" in stale["reason"] and "suggestions" not in stale
    monkeypatch.setattr(advisor, "get_history", lambda tickers: (None, "unavailable"))
    assert advisor.suggest(HOLDINGS)["status"] == "insufficient data"


def test_live_signal_brings_analog_evidence_with_its_sample_size(offline):
    offline["signals"] = [{"drill": False, "event_type": "cyclone", "title": "Gale forecast at Jamnagar"}]
    row = advisor.suggest(HOLDINGS)["sectors"][0]
    analog = row["evidence"]["analog"]
    assert analog["n"] >= config.THIN_ANALOGS and analog["min"] <= analog["mean"] <= analog["max"]
    assert not any("no live crisis signal" in flag for flag in row["flags"])
    offline["signals"] = [{"drill": True, "event_type": "cyclone", "title": "Drill"}]       # a drill is not evidence
    assert advisor.suggest(HOLDINGS)["sectors"][0]["evidence"]["analog"] is None


def test_narration_is_dropped_when_it_contains_a_figure_not_in_the_analysis(monkeypatch):
    statements = ["Energy is 40% of the portfolio.", "Pharma scores 77.9 of 100 and ranks 1."]
    monkeypatch.setattr(advisor.llm, "available", lambda: True)
    monkeypatch.setattr(advisor.llm, "complete", lambda system, user: "Energy is 40% of the portfolio; expect a 12% gain.")
    text, narrator = advisor.narrate(statements)
    assert text is None and "rejected" in narrator and "12" in narrator
    monkeypatch.setattr(advisor.llm, "complete", lambda system, user: "Energy is 40% of the portfolio; Pharma ranks 1.")
    assert advisor.narrate(statements) == ("Energy is 40% of the portfolio; Pharma ranks 1.",
                                           "language model, every figure checked against the analysis")


def test_group_limit_and_floors_in_the_optimiser():
    rng = np.random.default_rng(2)
    data = rng.normal(0.0005, 0.01, (500, 5))
    data[:, 2:] += 0.002                                    # the three new assets look far better
    caps, floors = np.array([0.6, 0.4, 0.05, 0.05, 0.05]), np.array([0.3, 0.2, 0, 0, 0])
    group = np.array([False, False, True, True, True])
    weights = pm.optimise(pm.shrunk_means(data), pm.ledoit_wolf(data)[0], caps, np.array([0.6, 0.4, 0, 0, 0]),
                          floors, group, 0.10)
    assert weights.sum() == pytest.approx(1, abs=1e-6) and weights[group].sum() <= 0.10 + 1e-6
    assert (weights >= floors - 1e-9).all() and (weights <= caps + 1e-9).all()
