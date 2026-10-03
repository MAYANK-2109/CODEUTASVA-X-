import json
import re

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.agents import graph, llm, nodes
from app.agents.state import inr
from app.main import app
from app.risk import metrics
from app.tools.events import find_similar_events, load_events
from app.tools import sentiment as sentiment_tool
from app.tools import vector_store
from app.tools.market import NIFTY, normalise_holdings

CYCLONE_DATES = [e["date"] for e in load_events() if e["type"] == "cyclone"]


def synthetic_closes(event_drop: float = 0.0, market_drop: float = 0.0) -> pd.DataFrame:
    """Flat-ish random walks, with a one-day shock on every cyclone date."""
    index = pd.bdate_range("2013-01-01", "2026-09-30")
    rng = np.random.default_rng(7)
    stock = rng.normal(0, 0.01, len(index))
    market = rng.normal(0, 0.008, len(index))
    for date in CYCLONE_DATES:
        pos = index.searchsorted(pd.Timestamp(date))
        stock[pos] = event_drop
        market[pos] = market_drop
    return pd.DataFrame(
        {"AAA.NS": 100 * np.cumprod(1 + stock), NIFTY: 20000 * np.cumprod(1 + market)}, index=index
    )


@pytest.fixture
def offline(monkeypatch):
    """Replace every network tool with fixed data."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.setenv("SENTIMENT_MODEL", "vader")
    monkeypatch.setattr(nodes, "get_news", lambda query, limit=8: [
        {"title": "Refiners slump as storm halts shipments", "source": "Wire", "url": "u1", "published_at": None},
        {"title": "Ports reopen after cyclone passes", "source": "Wire", "url": "u2", "published_at": None},
    ])
    monkeypatch.setattr(nodes, "get_weather_outlook", lambda: [
        {"name": "Jamnagar", "relevance": "refining hub", "max_rain_mm": 140.0,
         "max_gust_kmh": 95.0, "max_temp_c": 31.0, "flags": ["heavy rain", "gale-force gusts"]},
    ])
    monkeypatch.setattr(nodes, "get_macro", lambda: (
        [{"ticker": "^INDIAVIX", "label": "India VIX", "value": 24.0, "change_1m_pct": 30.0}], "now"))

    def use(closes):
        monkeypatch.setattr(nodes, "get_history", lambda tickers: (closes, "live" if closes is not None else "unavailable"))

    return use


def answer_for(query: str, holdings=None) -> dict:
    events = list(graph.run(query, holdings))
    assert events[0]["type"] == "start"
    assert [e["agent"] for e in events if e["type"] == "agent"][0] == "supervisor"
    assert {e["agent"] for e in events if e["type"] == "agent"} == set(graph.AGENTS)
    assert events[-1]["type"] == "answer"
    return events[-1]


HOLDING = [{"name": "Alpha Refining", "symbol": "AAA", "units": 100, "type": "STOCK"}]


def test_event_window_returns_measures_from_the_close_before_the_event():
    closes = pd.DataFrame({"X": [100.0, 100.0, 90.0, 99.0]}, index=pd.bdate_range("2024-01-01", periods=4))
    assert metrics.event_window_returns(closes, "2024-01-03", 1)["X"] == pytest.approx(-0.10)
    assert metrics.event_window_returns(closes, "2024-01-03", 2)["X"] == pytest.approx(-0.01)
    assert metrics.event_window_returns(closes, "2024-01-03", 3) is None
    assert metrics.event_window_returns(closes, "2023-12-01", 1) is None


def test_var_and_beta_on_known_series():
    market = pd.Series(np.tile([0.01, -0.01, 0.02, -0.02], 50))
    assert metrics.beta(2 * market, market) == pytest.approx(2.0)
    var, shortfall = metrics.historical_var(pd.Series(np.linspace(-0.10, 0.10, 201)))
    assert var == pytest.approx(0.09)
    assert shortfall >= var


def test_inr_uses_indian_grouping():
    assert inr(416646) == "₹4,16,646"
    assert inr(-4643.4) == "-₹4,643"
    assert inr(950) == "₹950"
    assert inr(12345678) == "₹1,23,45,678"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("How will a severe cyclone on the Gujarat coast affect my energy holdings?", "cyclone"),
        ("What if RBI hikes rates unexpectedly?", "rate_hike"),
        ("What if the RBI cuts the repo rate?", "rate_cut"),
        ("What if a war pushes crude oil prices up sharply?", "oil_up"),
        ("India Pakistan border conflict impact", "geopolitical"),
        ("Is my portfolio risky right now?", None),
        ("Should I increase my holdings?", None),
    ],
)
def test_classify_event(query, expected):
    assert nodes.classify_event(query) == expected


def test_event_search_filters_by_type_and_ranks_by_similarity():
    found = find_similar_events("cyclone hitting Gujarat refineries", "cyclone", limit=3)
    assert {e["type"] for e in found} == {"cyclone"}
    assert "Gujarat" in found[0]["title"]
    assert find_similar_events("is my portfolio risky") == []


def test_holdings_without_symbol_or_units_fall_back_to_sample():
    holdings, source = normalise_holdings([{"name": "Fund", "symbol": "", "units": 5}])
    assert source == "sample" and len(holdings) > 0
    holdings, source = normalise_holdings(HOLDING)
    assert source == "user" and holdings[0]["ticker"] == "AAA.NS"


def test_grounding_guard_flags_numbers_missing_from_evidence():
    rows = [{"id": "R1", "claim": "Portfolio value ₹4,16,646, VaR 1.6%", "source": "x"}]
    assert nodes.ungrounded_numbers("Worth ₹4,16,646 with VaR 1.6% [R1]", rows) == set()
    assert nodes.ungrounded_numbers("Expect a 12% loss [R1]", rows) == {"12"}


def test_large_event_losses_produce_a_sized_hedge(offline):
    offline(synthetic_closes(event_drop=-0.12, market_drop=-0.03))
    answer = answer_for("What does a cyclone do to my portfolio?", HOLDING)

    assert answer["action"] == "hedge"
    sides = {h["side"] for h in answer["hedges"]}
    assert sides == {"short", "reduce"}
    assert all(h["notional"] > 0 for h in answer["hedges"])
    assert any("Jamnagar" in e["claim"] for e in answer["evidence"])


def test_no_hedge_when_events_were_harmless(offline):
    offline(synthetic_closes(event_drop=0.0, market_drop=0.0))
    answer = answer_for("What does a cyclone do to my portfolio?", HOLDING)
    assert answer["action"] in ("monitor", "no_hedge")
    assert not [h for h in answer["hedges"] if not h["optional"]]


def test_answer_cites_only_real_evidence_and_only_grounded_numbers(offline):
    offline(synthetic_closes(event_drop=-0.12, market_drop=-0.03))
    answer = answer_for("What does a cyclone do to my portfolio?", HOLDING)

    known = {e["id"] for e in answer["evidence"]}
    assert len(known) == len(answer["evidence"])
    cited = set(re.findall(r"[A-Z]\d+", " ".join(nodes.CITATION.findall(answer["text"]))))
    assert cited and cited <= known
    assert nodes.ungrounded_numbers(answer["text"], answer["evidence"]) == set()


def test_holdings_left_out_are_reported(offline):
    offline(synthetic_closes())
    mixed = [*HOLDING, {"name": "Some Fund", "symbol": "", "units": 10, "type": "MF"}]
    answer = answer_for("What does a cyclone do to my portfolio?", mixed)
    assert any("1 holding(s)" in gap for gap in answer["gaps"])


def test_general_question_reports_risk_without_inventing_a_scenario(offline):
    offline(synthetic_closes())
    answer = answer_for("Is my portfolio risky right now?", HOLDING)
    assert "no comparable past events" in answer["text"].lower()
    assert any(e["id"].startswith("R") for e in answer["evidence"])
    assert not any(e["id"].startswith("H") for e in answer["evidence"])


def test_pipeline_survives_missing_prices(offline):
    offline(None)
    answer = answer_for("What does a cyclone do to my portfolio?", HOLDING)
    assert "could not be fetched" in answer["text"]
    assert answer["hedges"] == []
    assert any("unavailable" in g.lower() for g in answer["gaps"])


def test_ungrounded_llm_wording_is_rejected(offline, monkeypatch):
    offline(synthetic_closes(event_drop=-0.12, market_drop=-0.03))
    monkeypatch.setattr(nodes.llm, "complete", lambda system, user, schema=None: (
        None if schema else "**Bottom line:** you will lose 73% [R1]."))
    answer = answer_for("What does a cyclone do to my portfolio?", HOLDING)
    assert answer["writer"] == "template"
    assert "73%" not in answer["text"]


def test_chat_endpoint_streams_ndjson(offline):
    offline(synthetic_closes(event_drop=-0.12, market_drop=-0.03))
    response = TestClient(app).post("/api/chat", json={"query": "cyclone risk?", "holdings": HOLDING})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "start" and events[0]["portfolio_source"] == "user"
    assert events[-1]["type"] == "answer"
    assert TestClient(app).post("/api/chat", json={"query": ""}).status_code == 422


class FakeHit:
    def __init__(self, id, score):
        self.id, self.score = id, score


class FakeIndex:
    """Stands in for a Pinecone index; records what it was asked."""

    def __init__(self, hits=None, fail=False):
        self.hits, self.fail, self.calls = hits or [], fail, []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError("pinecone unreachable")
        result = type("Result", (), {"hits": self.hits})()
        return type("Response", (), {"result": result})()


@pytest.fixture
def pinecone_ready(monkeypatch):
    def use(index):
        monkeypatch.setitem(vector_store._state, "index", index)
        monkeypatch.setitem(vector_store._state, "ready", True)
        return index

    return use


def test_vector_search_uses_pinecone_ranking_and_type_filter(pinecone_ready):
    index = pinecone_ready(FakeIndex([FakeHit("EV07", 0.91), FakeHit("EV09", 0.88), FakeHit("ZZ99", 0.5)]))
    events, backend = vector_store.search_events("storm near Mumbai offshore rigs", "cyclone", 3)

    assert backend == vector_store.PINECONE
    assert [e["id"] for e in events] == ["EV07", "EV09"]
    assert events[0]["similarity"] == 0.91 and events[0]["date"] == "2021-05-17"
    assert index.calls[0]["filter"] == {"type": {"$eq": "cyclone"}}
    assert index.calls[0]["inputs"] == {"text": "storm near Mumbai offshore rigs"}


def test_vector_search_falls_back_to_local_when_pinecone_fails(pinecone_ready):
    pinecone_ready(FakeIndex(fail=True))
    events, backend = vector_store.search_events("cyclone hitting Gujarat refineries", "cyclone", 3)
    assert backend == vector_store.LOCAL
    assert events and "Gujarat" in events[0]["title"]


def test_general_question_matches_no_events_even_with_pinecone(pinecone_ready):
    index = pinecone_ready(FakeIndex([FakeHit("EV07", 0.80)]))
    assert vector_store.search_events("is my portfolio risky", None, 3) == ([], vector_store.LOCAL)
    assert index.calls == []


def test_evidence_names_the_search_backend(offline, pinecone_ready):
    offline(synthetic_closes(event_drop=-0.12, market_drop=-0.03))
    pinecone_ready(FakeIndex([FakeHit("EV07", 0.91), FakeHit("EV09", 0.88)]))
    answer = answer_for("What does a cyclone do to my portfolio?", HOLDING)
    sources = [e["source"] for e in answer["evidence"] if e["id"] in ("H1", "H2")]
    assert all("Pinecone vector search (similarity 0." in s for s in sources)
    assert nodes.ungrounded_numbers(answer["text"], answer["evidence"]) == set()


def test_vector_status_reports_local_backend_without_a_key(offline):
    body = TestClient(app).get("/api/vector/status").json()
    assert body["backend"] == "local" and body["configured"] is False


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code, self._payload, self.text = status_code, payload or {}, json.dumps(payload or {})

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def gemini_reply(text, finish="STOP"):
    return FakeResponse(payload={"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]})


@pytest.fixture
def gemini(monkeypatch):
    """Fake the Gemini HTTP API; returns the list of requests made."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setitem(llm._state, "model", None)
    monkeypatch.setattr(llm, "RETRY_DELAYS_SECONDS", (0,))
    requests = []

    def install(replies):
        queue = list(replies)

        def post(url, json=None, headers=None, timeout=None):
            requests.append({"url": url, "body": json, "headers": headers})
            return queue.pop(0)

        monkeypatch.setattr(llm.httpx, "post", post)
        return requests

    return install


def test_gemini_text_reply_and_key_stays_out_of_the_url(gemini):
    requests = gemini([gemini_reply("All good.")])
    assert llm.complete("system", "user") == "All good."
    assert "test-key" not in requests[0]["url"]
    assert requests[0]["headers"]["x-goog-api-key"] == "test-key"
    assert requests[0]["body"]["system_instruction"]["parts"][0]["text"] == "system"


def test_gemini_structured_reply_uses_converted_schema(gemini):
    requests = gemini([gemini_reply('{"event_type": "cyclone", "news_query": "cyclone gujarat"}')])
    result = llm.complete("system", "user", nodes.PLAN_SCHEMA)
    assert result == {"event_type": "cyclone", "news_query": "cyclone gujarat"}
    schema = requests[0]["body"]["generationConfig"]["responseSchema"]
    assert schema["type"] == "OBJECT" and "additionalProperties" not in schema
    assert schema["properties"]["event_type"]["type"] == "STRING"


def test_gemini_failures_return_none(gemini):
    gemini([FakeResponse(429, {"error": "quota"})] * 6
           + [gemini_reply("", finish="SAFETY"), gemini_reply("not json")])
    assert llm.complete("s", "u") is None
    assert llm.complete("s", "u") is None
    assert llm.complete("s", "u", nodes.PLAN_SCHEMA) is None
    assert llm.status()["last_error"]


def test_gemini_falls_back_to_the_next_model(gemini):
    busy = FakeResponse(503, {"error": "overloaded"})
    requests = gemini([busy, busy, gemini_reply("ok")])
    assert llm.complete("s", "u") == "ok"
    assert requests[0]["url"].endswith(f"/models/{llm.DEFAULT_MODELS[0]}:generateContent")
    assert requests[2]["url"].endswith(f"/models/{llm.DEFAULT_MODELS[1]}:generateContent")
    assert llm.status()["model"] == llm.DEFAULT_MODELS[1]


def test_pipeline_uses_llm_plan_and_grounded_llm_wording(offline, gemini):
    offline(synthetic_closes(event_drop=-0.12, market_drop=-0.03))
    plan = gemini_reply('{"event_type": "cyclone", "news_query": "cyclone coast"}')
    monkeypatch_text = "**Bottom line:** a hedge is recommended [G1].\n- Your portfolio beta is shown in the evidence [R4]."
    gemini([plan, gemini_reply(monkeypatch_text)])
    answer = answer_for("Big storm coming, what should I do with my shares?", HOLDING)
    assert answer["planner"] == "llm" and answer["writer"] == "llm"
    assert answer["text"] == monkeypatch_text
    assert answer["action"] == "hedge"


def test_gemini_retries_when_the_model_is_overloaded(gemini):
    requests = gemini([FakeResponse(503, {"error": "overloaded"}), gemini_reply("ok")])
    assert llm.complete("s", "u") == "ok"
    assert len(requests) == 2


def test_sentiment_falls_back_to_vader_when_forced(monkeypatch):
    monkeypatch.setenv("SENTIMENT_MODEL", "vader")
    assert sentiment_tool.backend() == sentiment_tool.VADER
    assert sentiment_tool.score("Shares plunge after fraud probe") < 0
    assert "VADER" in sentiment_tool.source_label()


@pytest.mark.skipif(not sentiment_tool.MODEL_FILE.exists(), reason="FinBERT model not downloaded")
def test_finbert_scores_financial_headlines(monkeypatch):
    monkeypatch.delenv("SENTIMENT_MODEL", raising=False)
    assert sentiment_tool.backend() == sentiment_tool.FINBERT
    good, bad, flat, missed_by_lexicon = sentiment_tool.score_many([
        "Refiner posts record profit and raises dividend",
        "Cyclone forces shutdown of Gujarat ports, oil shipments halted",
        "Company to hold annual general meeting on Friday",
        "ONGC output falls for third straight quarter",
    ])
    assert good > 0.5 and bad < -0.5 and abs(flat) < 0.3
    # No negative word a lexicon would catch, but clearly bad news for the company.
    assert missed_by_lexicon < -0.5


def test_insights_portfolio_indexes_prices_and_marks_events(offline, monkeypatch):
    from app import insights

    closes = synthetic_closes(event_drop=-0.12, market_drop=-0.03)
    monkeypatch.setattr(insights, "get_history", lambda tickers: (closes, "live"))
    response = TestClient(app).post("/api/insights/portfolio", json={"holdings": HOLDING, "range": "3y"})
    assert response.status_code == 200
    body = response.json()

    series = body["prices"]["series"][0]
    assert series["ticker"] == "AAA.NS" and series["values"][0] == 100
    assert len(series["values"]) == len(body["prices"]["dates"]) == len(body["prices"]["benchmark"]["values"])
    assert body["stats"]["total"] == body["positions"][0]["value"]
    assert body["sectors"][0]["weight"] == 1

    first, last = body["prices"]["dates"][0], body["prices"]["dates"][-1]
    assert body["events"] and all(first <= e["date"] <= last for e in body["events"])
    cyclone = next(e for e in body["events"] if e["type"] == "cyclone")
    assert cyclone["moves"]["AAA.NS"] < -0.05
    assert body["prices"]["dates"][cyclone["index"]] >= cyclone["date"]


def test_insights_weather_includes_daily_values_and_thresholds(monkeypatch):
    from app import insights

    monkeypatch.setattr(insights, "get_weather_outlook", lambda: [
        {"name": "Jamnagar", "relevance": "refining hub", "sectors": ["Energy"], "flags": ["heavy rain"],
         "max_rain_mm": 140.0, "max_gust_kmh": 40.0, "max_temp_c": 31.0,
         "days": [{"date": "2026-10-04", "rain_mm": 140.0, "gust_kmh": 40.0, "temp_c": 31.0}]}])
    body = TestClient(app).get("/api/insights/weather").json()
    assert body["sites"][0]["days"][0]["rain_mm"] == 140.0
    assert body["thresholds"] == {"rain_mm": 64.5, "gust_kmh": 62, "temp_c": 40}

    monkeypatch.setattr(insights, "get_weather_outlook", lambda: None)
    assert TestClient(app).get("/api/insights/weather").status_code == 503
