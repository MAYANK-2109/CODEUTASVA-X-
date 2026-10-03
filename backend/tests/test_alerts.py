import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml import alerts, assets, features, gbm, impact, radar, solutions
from app.tools import gdelt
from app.tools import resolver
from app.tools.market import NIFTY

HOLDINGS = [
    {"name": "Alpha Refining", "symbol": "AAA", "units": 100, "type": "STOCK"},
    {"name": "Beta Bank", "symbol": "BBB", "units": 10, "type": "STOCK"},
]


def closes(last_day_move: float = 0.0, market_shock: bool = False) -> pd.DataFrame:
    """Two stocks and the index as quiet random walks; AAA's last day is set explicitly."""
    index = pd.bdate_range("2023-01-02", "2026-09-30")
    rng = np.random.default_rng(11)
    aaa = rng.normal(0, 0.01, len(index))
    bbb = rng.normal(0, 0.01, len(index))
    market = rng.normal(0, 0.006, len(index))
    aaa[-1] = last_day_move
    if market_shock:  # AAA jumped two days ago and the whole market lurched today
        aaa[-3:] = [0.05, 0.003, 0.001]
        market[-1] = 0.025
    return pd.DataFrame(
        {
            "AAA.NS": 100 * np.cumprod(1 + aaa),
            "BBB.NS": 100 * np.cumprod(1 + bbb),
            NIFTY: 20000 * np.cumprod(1 + market),
        },
        index=index,
    )


@pytest.fixture
def quiet(monkeypatch):
    """No news, no weather alerts, calm markets; price history supplied by the test."""
    monkeypatch.setenv("SENTIMENT_MODEL", "vader")
    monkeypatch.setattr(resolver, "search", lambda query: None)
    monkeypatch.setattr(alerts, "get_news", lambda query, limit=8: [])
    monkeypatch.setattr(alerts, "get_macro", lambda: ([], None))
    monkeypatch.setattr(radar, "get_weather_outlook", lambda: [])
    monkeypatch.setattr(radar, "get_macro", lambda: ([], None))
    monkeypatch.setattr(radar, "daily_turnover", lambda tickers: {})
    monkeypatch.setattr(radar.gdelt, "signals", lambda: {"themes": {}, "scanned_at": None, "stale": True, "error": None})
    # Without macro history the impact model stands down and the price-only model is used.
    monkeypatch.setattr(impact, "get_cross_assets", lambda: None)
    monkeypatch.setattr(impact, "get_weather_history", lambda: None)

    def use(frame):
        monkeypatch.setattr(alerts, "get_history", lambda tickers: (frame, "live" if frame is not None else "unavailable"))

    return use


def by_category(result: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for alert in result["alerts"]:
        grouped.setdefault(alert["category"], []).append(alert)
    return grouped


def test_a_sharp_drop_raises_a_critical_price_alert_with_the_base_rate(quiet):
    quiet(closes(last_day_move=-0.08))
    result = alerts.build_alerts(HOLDINGS)
    (price,) = by_category(result)["price"]
    assert price["severity"] == "critical" and price["holding"] == "Alpha Refining"
    assert "fell 8.0% on 2026-09-30" in price["title"]
    assert "coin flip" in price["basis"]
    assert "do not sell on the move alone" in price["recommendation"]
    assert result["alerts"][0]["severity"] == "critical"  # most severe first
    assert result["prices_as_of"] == "2026-09-30"


def test_a_sharp_rise_is_informational(quiet):
    quiet(closes(last_day_move=0.08))
    (price,) = by_category(alerts.build_alerts(HOLDINGS))["price"]
    assert price["severity"] == "info" and "rose 8.0%" in price["title"]


def test_a_quiet_day_raises_no_price_alert(quiet):
    quiet(closes(last_day_move=0.001))
    assert "price" not in by_category(alerts.build_alerts(HOLDINGS))


def test_a_market_lurch_after_a_recent_jump_raises_an_elevated_risk_alert(quiet):
    quiet(closes(market_shock=True))
    risk = by_category(alerts.build_alerts(HOLDINGS))["risk"]
    assert "Alpha Refining" in [a["holding"] for a in risk]
    assert all("Model AUC" in a["basis"] for a in risk)


def test_shock_probability_matches_the_trained_weights():
    model = alerts._trained("shock_model")
    at_the_mean = alerts.shock_probability(dict(model["mean"]), model)
    assert at_the_mean == pytest.approx(1 / (1 + np.exp(-model["intercept"])))
    stressed = {**model["mean"], "market_today": model["mean"]["market_today"] + 3}
    assert alerts.shock_probability(stressed, model) > at_the_mean


def test_a_dominant_holding_raises_a_concentration_alert(quiet):
    quiet(closes())
    (alert,) = by_category(alerts.build_alerts(HOLDINGS))["concentration"]
    assert "Alpha Refining is" in alert["title"] and "institutional" in alert["basis"]

    # Ten equal positions: the largest is 10%, below the institutional 90th percentile.
    spread = [{"name": f"Co {i}", "ticker": f"C{i}.NS", "value": 100.0} for i in range(10)]
    assert alerts._concentration_alert(spread, 1000.0) == []


def test_negative_headline_about_a_holding_becomes_a_news_alert(quiet, monkeypatch):
    quiet(closes())
    monkeypatch.setattr(alerts, "get_news", lambda query, limit=8: [
        {"title": "Alpha Refining plunges after fraud probe and plant shutdown", "source": "Wire", "url": "u1"},
        {"title": "Markets crash as unrelated company collapses in scandal", "source": "Wire", "url": "u2"},
        {"title": "Beta Bank posts record profit and surges", "source": "Wire", "url": "u3"},
    ])
    news = by_category(alerts.build_alerts(HOLDINGS)).get("news", [])
    assert [a["holding"] for a in news] == ["Alpha Refining"]
    assert "fraud probe" in news[0]["detail"]


RELIANCE = [{"name": "Reliance Industries", "symbol": "RELIANCE", "units": 100, "type": "STOCK"}]
JAMNAGAR_GALE = {"name": "Jamnagar", "flags": ["gale-force gusts", "heavy rain"], "max_rain_mm": 180.0,
                 "max_gust_kmh": 140.0, "max_temp_c": 29.0}


def reliance_closes(recent_abnormal_drop: float = 0.0) -> pd.DataFrame:
    frame = closes().rename(columns={"AAA.NS": "RELIANCE.NS"})
    if recent_abnormal_drop:
        frame.iloc[-1, frame.columns.get_loc("RELIANCE.NS")] *= 1 + recent_abnormal_drop
    return frame


def alt_data(result: dict) -> list[dict]:
    return [a for a in result["alerts"] if a["category"] == "alt-data"]


def test_a_storm_forecast_reaches_only_holdings_with_assets_nearby(quiet, monkeypatch):
    quiet(reliance_closes())
    monkeypatch.setattr(radar, "get_weather_outlook", lambda: [JAMNAGAR_GALE])
    (alert,) = alt_data(alerts.build_alerts(RELIANCE))
    assert "forecast at Jamnagar" in alert["title"]
    assert "Jamnagar refinery complex" in alert["detail"]
    assert "NOAA IBTrACS" in alert["basis"] and "past storms within" in alert["basis"]
    assert alert["hedge"]["drill"] is False and alert["hedge"]["confidence"] == "Low"

    # The same forecast is not an alert for a portfolio with nothing there.
    quiet(closes())
    monkeypatch.setattr(radar, "get_weather_outlook", lambda: [JAMNAGAR_GALE])
    assert alt_data(alerts.build_alerts(HOLDINGS)) == []


def test_a_drill_is_labelled_and_never_counts_as_priced_in(quiet):
    quiet(reliance_closes(recent_abnormal_drop=-0.10))
    (alert,) = alt_data(alerts.build_alerts(RELIANCE, scenario="cyclone_gujarat"))
    assert alert["title"].startswith("Drill: ") and alert["severity"] == "info"
    assert "This is a drill" in alert["detail"]
    assert alert["hedge"]["drill"] is True and alert["hedge"]["priced_in"] is None
    # The drill leads the list even though other alerts are more severe.
    listed = alerts.build_alerts(RELIANCE, scenario="cyclone_gujarat")["alerts"]
    assert listed[0]["title"].startswith("Drill: ") and len(listed) > 1
    assert alt_data(alerts.build_alerts(RELIANCE, scenario="not-a-drill")) == []


SIGNAL = {"kind": "geopolitical", "event_type": "geopolitical", "drill": False, "title": "t", "detail": "d",
          "key": "k", "sources": ["GDELT"]}


def hedge_for(move: float, frame: pd.DataFrame, drill: bool = False) -> dict:
    positions = [{"ticker": "AAA.NS", "name": "Alpha Refining", "value": 100_000.0},
                 {"ticker": "BBB.NS", "name": "Beta Bank", "value": 100_000.0}]
    moves = {"AAA.NS": {"move": move, "cases": 5, "where": "", "basis": "own"},
             "BBB.NS": {"move": 0.01, "cases": 5, "where": "", "basis": "own"}}
    return radar.build_hedge({**SIGNAL, "drill": drill}, positions, moves, frame)


def test_hedge_is_recommended_only_when_the_expected_loss_exceeds_its_cost(quiet):
    quiet(closes())
    # AAA has about 16% annual volatility, so five sessions of protection cost under 1.5% of its value.
    large = hedge_for(-0.08, closes())
    assert large["action"] == "hedge" and large["expected_loss"] == 8000 and large["offsets"] == 1000
    assert 0 < large["cost"] < 8000 and large["notional"] == 100_000
    assert large["instrument"].startswith("Protective puts on Alpha Refining")
    assert "Protect Alpha Refining" in large["summary"] and "shorting the index would not offset it" in large["summary"]

    small = hedge_for(-0.002, closes())
    assert small["action"] in ("monitor", "none") and small["instrument"] is None
    assert "Do not hedge" in small["summary"]

    gains_only = radar.build_hedge(SIGNAL, [{"ticker": "BBB.NS", "name": "Beta Bank", "value": 1000.0}],
                                   {"BBB.NS": {"move": 0.02, "cases": 3, "where": "", "basis": "own"}}, closes())
    assert gains_only["action"] == "none" and "no hedge is recommended" in gains_only["summary"]


def test_no_new_hedge_once_the_market_has_priced_the_move(quiet):
    frame = closes(last_day_move=-0.09)  # AAA already fell about 9% beyond the market
    quiet(frame)
    priced = hedge_for(-0.08, frame)
    assert priced["action"] == "monitor" and priced["priced_in"] >= 0.8
    assert "largely priced this in" in priced["summary"]
    # In a drill the same recent fall is ignored.
    assert hedge_for(-0.08, frame, drill=True)["action"] == "hedge"


def test_put_cost_matches_black_scholes_at_the_money():
    # 20% volatility over 5 of 252 sessions: 0.3989 x 0.20 x sqrt(5/252) = 1.124% of the amount protected.
    assert radar.put_cost_fraction(0.20) == pytest.approx(0.01124, abs=1e-4)
    assert radar.put_cost_fraction(0.40) > radar.put_cost_fraction(0.20)


def test_asset_exposure_by_distance_and_capacity_share():
    assert assets.owner_of("VINDH_CHAL STPS") == "NTPC" and assets.owner_of("MUNDRA UMPP") == "TATAPOWER"
    assert assets.owner_of("KOYNA COMPLEX") is None
    assert assets.distance_km(22.35, 69.85, 22.74, 69.70) == pytest.approx(46, abs=3)
    near = assets.exposure("RELIANCE.NS", 22.47, 70.06)
    assert near["assets"][0]["name"] == "Jamnagar refinery complex" and near["share"] is None
    assert assets.exposure("RELIANCE.NS", 13.08, 80.27) is None  # nothing mapped near Chennai
    simhadri = assets.exposure("NTPC.NS", 17.69, 83.22)
    assert simhadri["mw"] == 2000 and 0 < simhadri["share"] < 0.1


def test_news_volume_spike_detection():
    quiet_month = [{"date": f"202609{d:02d}T000000Z", "value": 50 + d % 3, "norm": 100_000} for d in range(1, 29)]
    calm = gdelt.spike(gdelt.daily_share(quiet_month + [{"date": "20260929T000000Z", "value": 51, "norm": 100_000}]))
    surge = gdelt.spike(gdelt.daily_share(quiet_month + [{"date": "20260929T000000Z", "value": 400, "norm": 100_000}]))
    assert calm["elevated"] is False and surge["elevated"] is True and surge["z"] > 10
    assert gdelt.spike(gdelt.daily_share(quiet_month[:5])) is None  # too little history to judge


def test_a_news_surge_becomes_a_geopolitical_alert_with_analogues(quiet, monkeypatch):
    frame = pd.DataFrame(
        {c: 100.0 * np.cumprod(1 + np.random.default_rng(i).normal(0, 0.01, 3600)) for i, c in enumerate(["AAA.NS", "BBB.NS", NIFTY])},
        index=pd.bdate_range("2013-01-01", periods=3600),
    )
    quiet(frame)
    monkeypatch.setattr(radar.gdelt, "signals", lambda: {"stale": False, "scanned_at": "now", "error": None, "themes": {
        "india_pakistan": {"label": "India-Pakistan military tension", "event_type": "geopolitical", "elevated": True,
                           "z": 4.2, "articles": 900, "day": "20260930"},
        "trade_tariffs": {"label": "Tariffs on Indian exports", "event_type": "geopolitical", "elevated": False,
                          "z": 0.3, "articles": 40, "day": "20260930"}}})
    (alert,) = alt_data(alerts.build_alerts(HOLDINGS))
    assert alert["title"] == "India-Pakistan military tension"
    assert "900 articles on 2026-09-30" in alert["detail"] and "GDELT news volume (+4.2σ)" in alert["basis"]
    assert "past events of this kind" in alert["basis"]


def test_alerts_survive_missing_prices(quiet):
    quiet(None)
    result = alerts.build_alerts(HOLDINGS)
    assert result["alerts"] == [] and "prices" in result["unavailable"]


def test_alerts_endpoint_and_stable_ids(quiet):
    quiet(closes(last_day_move=-0.08))
    first = TestClient(app).post("/api/alerts", json={"holdings": HOLDINGS}).json()
    second = TestClient(app).post("/api/alerts", json={"holdings": HOLDINGS}).json()
    assert [a["id"] for a in first["alerts"]] == [a["id"] for a in second["alerts"]]
    assert first["risk_model"]["auc"] > 0.5


def test_risk_features_use_only_information_available_that_day():
    frame = features.risk_features(features.abnormal_returns(closes()["AAA.NS"], closes()[NIFTY]))
    shortened = closes().iloc[:-30]
    earlier = features.risk_features(features.abnormal_returns(shortened["AAA.NS"], shortened[NIFTY]))
    day = shortened.index[-1]
    for name in features.FEATURES:
        assert frame.loc[day, name] == pytest.approx(earlier.loc[day, name])
    # The label looks ahead, so the last days of any series cannot have one.
    assert earlier["shock_ahead"].iloc[-features.HORIZON:].isna().all()


def test_news_scan_stops_on_a_rate_limit_and_keeps_earlier_readings(monkeypatch, tmp_path):
    import httpx

    month = [{"date": f"202609{d:02d}T000000Z", "value": 50, "norm": 100_000} for d in range(1, 30)]
    calls = []

    def fetch(query):
        calls.append(query)
        if len(calls) == 2:
            request = httpx.Request("GET", "https://example.test")
            raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
        return month

    monkeypatch.setattr(gdelt, "_fetch", fetch)
    monkeypatch.setattr(gdelt, "REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(gdelt, "CACHE_FILE", tmp_path / "gdelt.json")
    monkeypatch.setattr(gdelt, "_state", {"themes": {}, "scanned_at": None, "error": None})
    with pytest.raises(gdelt.RateLimited):
        gdelt.scan()
    result = gdelt.signals()
    assert len(calls) == 2 and list(result["themes"]) == ["india_pakistan"]
    assert result["error"] == "rate limited by GDELT" and result["stale"] is False


# --------------------------------------------------------------------------- impact model and solutions


def test_exported_trees_are_walked_to_the_right_leaf():
    tree = {"feature": [0, -1, 1, -1, -1], "threshold": [0.5, 0, 10.0, 0, 0],
            "left": [1, 0, 3, 0, 0], "right": [2, 0, 4, 0, 0], "value": [0, -1.0, 0, 2.0, 3.0]}
    model = {"features": ["a", "b"], "objective": "regression", "trees": [tree, tree]}
    assert gbm.predict(model, {"a": 0.5, "b": 99}) == -2.0      # a <= 0.5 goes left
    assert gbm.predict(model, {"a": 0.6, "b": 10}) == 4.0
    assert gbm.predict(model, {"a": 0.6, "b": 11}) == 6.0
    binary = {**model, "objective": "binary", "trees": [tree]}
    assert gbm.predict(binary, {"a": 0.0, "b": 0}) == pytest.approx(1 / (1 + np.e))


def test_the_saved_impact_model_matches_the_feature_code():
    model = impact.model()
    assert model["features"] == features.IMPACT_FEATURES
    assert {"fall", "downside"} == set(model["models"])
    assert all(m["features"] == features.IMPACT_FEATURES for m in model["models"].values())
    assert model["fall"]["rate_when_elevated"] > model["fall"]["rate_otherwise"]
    assert model["sectors"] == impact.sector_codes()


def macro(index: pd.DatetimeIndex) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    walk = lambda start, spread: start * np.cumprod(1 + rng.normal(0, spread, len(index)))  # noqa: E731
    return pd.DataFrame({features.VIX: walk(15, 0.03), features.BRENT: walk(80, 0.015),
                         features.RUPEE: walk(83, 0.002)}, index=index)


def test_impact_features_use_only_information_available_that_day():
    frame = closes()
    days = features.macro_features(frame[NIFTY], macro(frame.index))
    before = features.impact_features(frame["AAA.NS"], frame[NIFTY], days, 3, ["2026-09-20"])
    changed = frame.copy()
    changed.iloc[-1] *= 1.2      # tomorrow's prices must not change what was known a week earlier
    after = features.impact_features(changed["AAA.NS"], changed[NIFTY],
                                     features.macro_features(changed[NIFTY], macro(frame.index)), 3, ["2026-09-20"])
    row = frame.index[-6]
    pd.testing.assert_series_equal(before.loc[row, features.IMPACT_FEATURES], after.loc[row, features.IMPACT_FEATURES])
    assert before["site_alert_days"].loc["2026-09-21":"2026-09-25"].eq(1).all()
    assert before["site_alert_days"].loc["2026-09-29"] == 0          # more than a week later
    assert before["forward_return"].iloc[-1] != before["forward_return"].iloc[-1]   # the future is blank


def priced(frame: pd.DataFrame) -> list[dict]:
    return [{"ticker": "AAA.NS", "name": "Alpha Refining", "units": 100.0, "sector": "Energy",
             "price": float(frame["AAA.NS"].iloc[-1]), "value": float(frame["AAA.NS"].iloc[-1]) * 100}]


def test_holdings_are_scored_when_macro_history_is_available(monkeypatch):
    frame = closes()
    monkeypatch.setattr(impact, "get_weather_history", lambda: None)
    monkeypatch.setattr(impact, "get_cross_assets", lambda: macro(frame.index))
    score = impact.score_positions(priced(frame), frame)["AAA.NS"]
    assert 0 < score["fall"] < 1 and score["downside"] < 0 and score["usual_downside"] < 0
    assert score["elevated"] == (score["fall"] >= impact.model()["fall"]["elevated_cutoff"])

    monkeypatch.setattr(impact, "get_cross_assets", lambda: macro(frame.index).drop(columns=[features.VIX]))
    assert impact.score_positions(priced(frame), frame) is None       # an input is missing: no score, no guess


POSITION = {"name": "Alpha Refining", "ticker": "AAA.NS", "units": 100.0, "price": 200.0, "value": 20000.0}


def test_protection_is_bought_only_when_it_costs_less_than_the_extra_downside():
    score = {"fall": 0.2, "downside": -0.10, "usual_downside": -0.04, "elevated": True}
    cheap = solutions.protect_or_trim(POSITION, score, 0.20, 100000.0, 1.0, 0.057)
    cost = 20000 * radar.put_cost_fraction(0.20)
    assert cost < 1200 and cheap["action"] == "hedge"                # extra downside is 2,000 - 800 = 1,200
    assert f"₹{cost:,.0f}" in cheap["headline"] and "sell 60 of your 100 shares" in cheap["alternative"]

    dear = solutions.protect_or_trim(POSITION, score, 1.50, 100000.0, 1.0, 0.057)
    assert dear["action"] == "trim" and dear["headline"] == "Trim Alpha Refining by 60 shares, about ₹12,000"
    assert any("not worth buying" in step for step in dear["steps"])
    assert {f["label"] for f in dear["figures"]} >= {"Chance of a sharp fall", "Its usual downside"}


def test_no_trade_when_the_downside_is_usual_or_the_amount_is_trivial():
    usual = solutions.protect_or_trim(POSITION, {"fall": 0.2, "downside": -0.04, "usual_downside": -0.05,
                                                 "elevated": True}, 0.2, 100000.0, 1.0, 0.057)
    assert usual["action"] == "watch" and "₹192.00" in usual["headline"]        # 200 x (1 - 0.04)
    small = solutions.protect_or_trim(POSITION, {"fall": 0.2, "downside": -0.051, "usual_downside": -0.05,
                                                 "elevated": True}, 0.2, 10_000_000.0, 1.0, 0.057)
    assert small["action"] == "watch" and "no trade is needed" in small["steps"][0]


def test_a_flagged_holding_gets_a_risk_alert_with_a_sized_solution(quiet, monkeypatch):
    frame = closes()
    quiet(frame)
    flagged = {"fall": 0.21, "downside": -0.12, "usual_downside": -0.04, "elevated": True}
    calm = {"fall": 0.03, "downside": -0.03, "usual_downside": -0.04, "elevated": False}
    monkeypatch.setattr(impact, "score_positions", lambda positions, closes: {"AAA.NS": flagged, "BBB.NS": calm})
    result = alerts.build_alerts(HOLDINGS)

    (risk,) = by_category(result)["risk"]
    assert risk["holding"] == "Alpha Refining" and "21% chance of a 5%+ fall this week" in risk["title"]
    assert risk["solution"]["action"] in ("hedge", "trim") and risk["severity"] == "warning"
    assert risk["recommendation"].startswith(risk["solution"]["headline"])
    assert "LightGBM" in risk["basis"] and "price history alone" in risk["basis"]

    ranking = result["risk_ranking"]
    assert [r["name"] for r in ranking] == ["Alpha Refining", "Beta Bank"]       # largest rupee downside first
    assert ranking[0]["elevated"] and ranking[0]["downside_amount"] == round(0.12 * frame["AAA.NS"].iloc[-1] * 100)
    assert result["risk_model"]["name"] == "LightGBM impact model" and result["unavailable"] == []


def test_without_macro_data_the_price_only_model_is_used_and_said_so(quiet):
    quiet(closes(market_shock=True))
    result = alerts.build_alerts(HOLDINGS)
    assert result["risk_ranking"] == [] and result["risk_model"]["name"] == "Price-only logistic model"
    assert any("impact model" in item for item in result["unavailable"])
    assert all("price-only" in a["basis"] for a in by_category(result)["risk"])


def test_concentration_solution_sizes_the_sale_and_its_effect(quiet):
    frame = closes()
    quiet(frame)
    (alert,) = by_category(alerts.build_alerts(HOLDINGS))["concentration"]
    solution = alert["solution"]
    assert solution["action"] == "rebalance" and solution["headline"].startswith("Sell ")
    assert "Alpha Refining" in solution["headline"] and "to bring it to 24%" in solution["headline"]
    labels = {f["label"]: f["value"] for f in solution["figures"]}
    before, after = (float(v.replace("₹", "").replace(",", "")) for v in labels["1-day 95% VaR"].split(" → "))
    assert after < before                                           # the sale lowers risk
    assert any("Beta Bank (correlation" in step for step in solution["steps"])


def test_every_alert_carries_a_solution_and_rupees_use_indian_grouping(quiet):
    quiet(closes(last_day_move=-0.08))
    result = alerts.build_alerts(HOLDINGS, scenario="cyclone_gujarat")
    assert result["alerts"]
    for alert in result["alerts"]:
        assert alert["solution"]["action"] in ("hedge", "trim", "rebalance", "watch", "hold", "review")
        assert alert["solution"]["headline"] and alert["solution"]["steps"]
    assert solutions.inr(-351300) == "₹3,51,300"
