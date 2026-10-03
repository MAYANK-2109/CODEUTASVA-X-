import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import terminal
from app.agents import llm
from app.main import app
from app.risk import exposure
from app.tools import gdelt, health, sentiment, vector_store, weather
from app.tools.market import NIFTY

client = TestClient(app)


def closes() -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", "2026-09-30")
    rng = np.random.default_rng(3)
    market = rng.normal(0, 0.01, len(index))
    return pd.DataFrame({
        "AAA.NS": 100 * np.cumprod(1 + 2 * market),           # beta 2 by construction
        "BBB.NS": 50 * np.cumprod(1 + rng.normal(0, 0.01, len(index))),
        "CCC.NS": np.nan,                                       # never priced
        NIFTY: 20000 * np.cumprod(1 + market),
    }, index=index)


HOLDINGS = [
    {"ticker": "AAA.NS", "name": "Alpha", "units": 30, "buy_price": 90.0, "sector": "Energy"},
    {"ticker": "BBB.NS", "name": "Beta", "units": 20, "buy_price": 40.0, "sector": "Banking"},
    {"ticker": "CCC.NS", "name": "Gamma", "units": 5, "buy_price": 10.0, "sector": "IT"},
]


def test_snapshot_weights_sectors_and_beta():
    frame = closes()
    view = exposure.snapshot(HOLDINGS, frame)
    alpha, beta = (frame[t].iloc[-1] * units for t, units in (("AAA.NS", 30), ("BBB.NS", 20)))
    assert view["unpriced"] == ["Gamma"] and view["total"] == pytest.approx(alpha + beta)
    assert sum(p["weight"] for p in view["positions"]) == pytest.approx(1)
    assert view["positions"][0]["value"] >= view["positions"][1]["value"]
    by_name = {p["name"]: p for p in view["positions"]}
    assert by_name["Alpha"]["beta"] == pytest.approx(2, abs=0.01)
    assert {s["sector"] for s in view["sectors"]} == {"Energy", "Banking"}
    weights = [p["weight"] for p in view["positions"]]
    assert view["effective_holdings"] == pytest.approx(1 / sum(w * w for w in weights))
    assert view["var"][1] >= view["var"][0] > 0
    assert exposure.snapshot([HOLDINGS[2]], frame) is None


def test_overview_reports_risk_and_exposure(monkeypatch):
    frame = closes()
    monkeypatch.setattr(terminal, "normalise_holdings", lambda raw: (HOLDINGS, "user"))
    monkeypatch.setattr(terminal, "get_history", lambda tickers: (frame, "live"))
    body = client.post("/api/terminal/overview", json={"holdings": [{"symbol": "AAA"}]}).json()

    risk, exposed = body["risk"], body["exposure"]
    assert body["as_of"] == "2026-09-30" and body["unpriced"] == ["Gamma"] and risk["holdings"] == 2
    assert risk["var_1d"] == pytest.approx(risk["var_1d_pct"] * risk["total"], rel=0.01)
    assert risk["cvar_1d"] >= risk["var_1d"]
    assert risk["var_horizon"] == pytest.approx(risk["var_1d"] * 5 ** 0.5, rel=0.001)
    assert exposed["largest"]["weight"] == exposed["sectors"][0]["weight"]
    assert sum(s["weight"] for s in exposed["sectors"]) == pytest.approx(1, abs=0.001)


def test_overview_is_unavailable_without_prices(monkeypatch):
    monkeypatch.setattr(terminal, "normalise_holdings", lambda raw: (HOLDINGS, "user"))
    monkeypatch.setattr(terminal, "get_history", lambda tickers: (None, "unavailable"))
    assert client.post("/api/terminal/overview", json={}).status_code == 503
    monkeypatch.setattr(terminal, "get_history", lambda tickers: (closes(), "live"))
    monkeypatch.setattr(terminal, "normalise_holdings", lambda raw: ([HOLDINGS[2]], "user"))
    assert client.post("/api/terminal/overview", json={}).status_code == 422


@pytest.fixture
def quiet(monkeypatch):
    """No network: probes do nothing, and every status source starts empty."""
    monkeypatch.setattr(terminal, "_refresh_stale_feeds", lambda: None)
    monkeypatch.setattr(health, "_streams", {})
    monkeypatch.setattr(gdelt, "_state", {"themes": {}, "scanned_at": None, "error": None})
    monkeypatch.setattr(vector_store, "_state", {**vector_store._state, "ready": False, "error": None})
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("SENTIMENT_MODEL", "vader")


def rows() -> dict:
    return {row["key"]: row for row in client.get("/api/terminal/streams").json()["streams"]}


def test_streams_are_idle_until_a_source_has_answered(quiet):
    report = rows()
    assert [report[k]["status"] for k in ("quotes", "history", "macro", "news", "weather", "geopolitics")] == ["idle"] * 6
    # Fallbacks are reported as fallbacks, not as live.
    assert report["vector"]["status"] == "degraded" and "local text search" in report["vector"]["detail"]
    assert report["sentiment"]["status"] == "degraded" and report["sentiment"]["source"] == "VADER lexicon"
    assert report["llm"]["status"] == "degraded" and "rule-based" in report["llm"]["detail"]


def test_streams_report_what_each_source_last_did(quiet, monkeypatch):
    health.record("quotes", True, ms=412.4, items=8, detail="8 of 8 quotes")
    health.record("history", False, detail="live fetch failed, saved copy to 2026-09-30 in use")
    health.record("news", False, detail="ConnectTimeout")
    health.record("weather", True, ms=230, items=6, detail="7-day forecast for 6 sites")
    monkeypatch.setattr(gdelt, "_state", {
        "themes": {"india_pakistan": {"label": "India-Pakistan tension", "elevated": True}},
        "scanned_at": "2026-10-03T10:00:00+00:00", "error": "rate limited by GDELT"})
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(llm, "_state", {"model": "gemini-x", "calls": 3, "failures": 0, "last_error": None})

    report = rows()
    assert (report["quotes"]["status"], report["quotes"]["latency_ms"]) == ("live", 412)
    assert report["history"]["status"] == "degraded"       # a saved copy is standing in
    assert report["news"]["status"] == "down" and report["news"]["detail"] == "ConnectTimeout"
    assert report["weather"]["status"] == "live"
    assert report["geopolitics"]["status"] == "degraded"
    assert "1 of 5 themes read" in report["geopolitics"]["detail"] and "India-Pakistan" in report["geopolitics"]["detail"]
    assert report["llm"]["status"] == "live" and "3 call(s)" in report["llm"]["detail"]
    counts = client.get("/api/terminal/streams").json()["counts"]
    assert sum(counts.values()) == 9 and counts["down"] == 1


def test_sources_record_their_own_outcome(monkeypatch):
    monkeypatch.setattr(health, "_streams", {})
    monkeypatch.setattr(weather, "_cache", {"at": 0.0, "value": None})

    def refuse(*args, **kwargs):
        raise weather.httpx.ConnectError("no route")

    monkeypatch.setattr(weather.httpx, "get", refuse)
    assert weather.get_weather_outlook() is None
    entry = health.snapshot()["weather"]
    assert entry["ok"] is False and entry["last_ok_at"] is None and entry["detail"] == "ConnectError"
