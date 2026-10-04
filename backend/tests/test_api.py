import pytest
from fastapi.testclient import TestClient

from app import api
from app.ingestion import news
from app.ingestion.news import names_holding, parse_feed
from app.main import app

client = TestClient(app)


def test_portfolio_computes_values_from_prices(monkeypatch):
    tickers = [h["ticker"] for h in api.load_portfolio()["holdings"]]
    monkeypatch.setattr(
        api, "get_latest_prices", lambda t: ({x: 100.0 for x in tickers}, "now")
    )
    body = client.get("/api/portfolio").json()
    first = body["holdings"][0]
    assert first["invested_value"] == first["shares"] * first["avg_price"]
    assert first["current_value"] == first["shares"] * 100.0
    assert body["totals"]["current_value"] == sum(
        h["shares"] * 100.0 for h in body["holdings"]
    )


def test_portfolio_reports_unknown_when_a_price_is_missing(monkeypatch):
    monkeypatch.setattr(api, "get_latest_prices", lambda t: ({t[0]: 100.0}, "now"))
    body = client.get("/api/portfolio").json()
    assert body["holdings"][0]["current_value"] is not None
    assert body["holdings"][1]["current_price"] is None
    assert body["holdings"][1]["current_value"] is None
    assert body["totals"]["current_value"] is None


def test_parse_feed_strips_source_and_sorts_newest_first():
    xml = """<rss><channel>
      <item><title>Old story - Mint</title><link>http://a</link>
        <pubDate>Fri, 02 Oct 2026 08:00:00 GMT</pubDate><source>Mint</source></item>
      <item><title>New story - ET</title><link>http://b</link>
        <pubDate>Sat, 03 Oct 2026 08:00:00 GMT</pubDate><source>ET</source></item>
    </channel></rss>"""
    items = parse_feed(xml, limit=5)
    assert [i["title"] for i in items] == ["New story", "Old story"]
    assert items[0]["source"] == "ET"
    assert items[0]["url"] == "http://b"


def test_market_overview_reports_levels_changes_and_trend(monkeypatch):
    import pandas as pd

    from app.tools import market

    index = pd.bdate_range(end="2026-10-01", periods=30)
    nifty = [100.0] * 8 + [110.0] * 20 + [100.0, 99.0]      # -1% on the day, -10% vs 21 sessions ago
    sensex = [200.0] * 28 + [200.0, 202.0]                    # +1% on the day, +1% over the month
    frame = pd.DataFrame({"^NSEI": nifty, "^BSESN": sensex}, index=index)
    monkeypatch.setattr(market, "_download", lambda tickers, **kwargs: frame)
    monkeypatch.setattr(market, "_memory", {})

    body = client.get("/api/market/overview").json()
    by_key = {i["key"]: i for i in body["indices"]}
    assert by_key["nifty"]["value"] == 99.0 and by_key["nifty"]["change_1d_pct"] == -1.0
    assert by_key["nifty"]["change_1m_pct"] == -10.0
    assert by_key["sensex"]["change_1d_pct"] == 1.0
    assert body["trend"] == {"label": "Downtrend", "detail": "Nifty 50 -10.0% over one month"}
    assert body["as_of"] == "2026-10-01"


def test_market_overview_is_unavailable_without_data(monkeypatch):
    from app.tools import market

    def fail(tickers, **kwargs):
        raise RuntimeError("source down")

    monkeypatch.setattr(market, "_download", fail)
    monkeypatch.setattr(market, "_memory", {})
    assert client.get("/api/market/overview").status_code == 503
    assert market.market_trend([{"key": "nifty", "change_1m_pct": 0.5}])["label"] == "Sideways"
    assert market.market_trend([{"key": "nifty", "change_1m_pct": 3.0}])["label"] == "Uptrend"
    assert market.market_trend([]) is None


def test_headline_matching_needs_the_company_named_as_a_whole_word():
    assert names_holding("Reliance Industries to raise funds", "Reliance Industries Ltd", "RELIANCE.NS")
    assert names_holding("Reliance shares slip 2%", "Reliance Industries Ltd", "RELIANCE.NS")   # symbol as a word
    assert names_holding("IndiGo adds new routes", "InterGlobe Aviation", "INDIGO.NS")
    assert names_holding("ITC posts higher profit", "ITC", "ITC.NS")
    assert names_holding("ONGC finds new gas reserves", "Oil & Natural Gas Corp", "ONGC.NS")
    assert not names_holding("Switch to smartwatches gathers pace", "ITC", "ITC.NS")           # inside another word
    assert not names_holding("Analysts itch for a rate cut", "ITC", "ITC.NS")
    assert not names_holding("Swadeshi products can strengthen self-reliance", "Reliance Industries", "RELIANCE.NS")
    assert not names_holding("Growing reliance on imports worries planners", "Reliance Industries", "RELIANCE.NS")
    assert not names_holding("Oil prices rise on supply fears", "Oil & Natural Gas Corp", "ONGC.NS")
    assert not names_holding("Banks rally after policy", "HDFC Bank", "HDFCBANK.NS")
    assert names_holding("SBI raises lending rates", "State Bank of India", "SBIN.NS")       # initials of a long name
    assert not names_holding("ED cracks down on GST ITC fraud", "ITC", "ITC.NS")             # input tax credit
    assert not names_holding("Authority upholds ITC demand in input tax dispute", "ITC", "ITC.NS")
    assert not names_holding("Rabi sowing picks up", "State Bank of India", "SBIN.NS")


HELD = [
    {"name": "Tata Steel", "symbol": "TATASTEEL", "units": 5, "type": "STOCK"},
    {"name": "HDFC Bank", "symbol": "HDFCBANK", "units": 10, "type": "STOCK"},
    {"name": "Some Bluechip Fund", "symbol": "", "units": 3, "type": "MF"},
]


def headline(title: str, when: str, url: str | None = None) -> dict:
    return {"title": title, "source": "Wire", "url": url or title, "published_at": f"2026-10-{when}:00:00+00:00"}


@pytest.fixture
def no_lookups(monkeypatch):
    """No ticker search and no sector lookup; a test that needs a sector supplies it."""
    monkeypatch.setattr(api.resolver, "search", lambda query: None)
    monkeypatch.setattr(api.sectors, "resolve", lambda tickers: {t: "Other" for t in tickers})


def test_news_trail_keeps_only_headlines_about_the_sectors_held(monkeypatch, no_lookups):
    feed = [
        headline("Sensex ends flat ahead of policy", "03T09"),
        headline("Steel prices rise as China cuts output", "03T10"),
        headline("JSW Steel to add capacity in Odisha", "03T08"),            # a peer: same sector
        headline("RBI keeps repo rate unchanged", "02T09"),
        headline("HDFC Bank appoints new chief", "02T08"),                   # names a holding
        headline("Car sales jump 12% in September", "03T11"),                # a sector not held
        headline("Some Bluechip Fund cuts expense ratio", "03T12"),          # funds are not stocks
        headline("Steel prices rise as China cuts output", "01T09", "dup"),
    ]
    asked = []
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: asked.append(query) or feed)

    body = client.post("/api/news", json={"holdings": HELD}).json()
    by_title = {i["title"]: i for i in body["items"]}
    assert list(by_title) == ["Steel prices rise as China cuts output", "JSW Steel to add capacity in Odisha",
                              "RBI keeps repo rate unchanged", "HDFC Bank appoints new chief"]   # newest first
    assert by_title["Steel prices rise as China cuts output"]["sectors"] == ["Steel"]
    assert by_title["HDFC Bank appoints new chief"] == {**by_title["HDFC Bank appoints new chief"],
                                                         "sectors": ["Banking"], "holdings": ["HDFC Bank"]}
    assert by_title["RBI keeps repo rate unchanged"]["holdings"] == []

    assert body["sectors"] == [{"sector": "Steel", "holdings": ["Tata Steel"]},
                               {"sector": "Banking", "holdings": ["HDFC Bank"]}]
    assert body["unmapped"] == [] and body["portfolio_source"] == "user" and body["holdings"] == 2
    # One search per sector held and one for the companies; nothing about cars or funds.
    assert len(asked) == 3 and all(query.endswith("when:7d") for query in asked)
    assert any('"steel sector"' in q and q.endswith("India when:7d") for q in asked)   # kept to India
    assert any('"Tata Steel"' in q for q in asked)
    assert not any("car sales" in q or "Bluechip" in q for q in asked)


def test_a_holding_outside_the_sector_map_is_looked_up(monkeypatch, no_lookups):
    held = [{"name": "Pidilite Industries", "symbol": "PIDILITIND", "units": 4, "type": "STOCK"},
            {"name": "Mystery Corp", "symbol": "MYSTERY", "units": 1, "type": "STOCK"}]
    monkeypatch.setattr(api.sectors, "resolve", lambda tickers: {"PIDILITIND.NS": "Chemicals", "MYSTERY.NS": "Other"})
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: [
        headline("Specialty chemicals makers see export recovery", "03T09"),
        headline("Mystery Corp wins an award", "03T10")])
    body = client.post("/api/news", json={"holdings": held}).json()
    assert [i["title"] for i in body["items"]] == ["Specialty chemicals makers see export recovery"]
    assert body["sectors"] == [{"sector": "Chemicals", "holdings": ["Pidilite Industries"]}]
    assert body["unmapped"] == ["Mystery Corp"]          # no sector found: said so, not guessed


def test_a_sector_in_the_news_all_week_does_not_crowd_out_the_others(monkeypatch, no_lookups):
    steel = [headline(f"Steel output update {n}", f"03T0{n}") for n in range(6)]
    bank = [headline("Banks report steady credit growth", "01T09")]
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: steel + bank)
    titles = [i["title"] for i in client.post("/api/news", json={"holdings": HELD, "limit": 3}).json()["items"]]
    assert titles == ["Steel output update 5", "Steel output update 4", "Banks report steady credit growth"]


def test_news_without_holdings_uses_the_sample_portfolio(monkeypatch, no_lookups):
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: [
        headline("Power demand hits a record high", "03T09"), headline("Monsoon withdraws from Kerala", "03T08")])
    body = client.get("/api/news").json()
    assert body["portfolio_source"] == "sample"
    assert [i["title"] for i in body["items"]] == ["Power demand hits a record high"]
    assert {"sector": "Power", "holdings": ["NTPC"]} in body["sectors"]
    assert client.post("/api/news", json={}).json()["portfolio_source"] == "sample"


def test_company_news_search_still_takes_turns_between_holdings(monkeypatch):
    held = [{"name": "Reliance Industries", "ticker": "RELIANCE.NS"}, {"name": "ITC", "ticker": "ITC.NS"}]
    busy = [headline(f"Reliance Industries update {n}", f"03T0{n}") for n in range(6)]
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: busy + [headline("ITC opens a new plant", "01T09")])
    titles = [i["title"] for i in news.get_holdings_news(held, limit=3)]
    assert titles == ["Reliance Industries update 5", "Reliance Industries update 4", "ITC opens a new plant"]


def test_sector_words_must_be_about_the_sector():
    cases = [("Energy", "Crude oil climbs above 100 dollars", True), ("Energy", "Edible oil imports jump", False),
             ("IT", "Nifty IT index slips 2%", True), ("IT", "Is it sector rotation time?", False),
             ("Utilities", "Power demand hits record high", True), ("Utilities", "The power of compounding", False),
             ("Steel", "Steelmakers raise prices", True), ("Auto", "Autonomous region votes", False)]
    for sector, title, expected in cases:
        assert bool(news.SECTOR_PATTERNS[sector].search(title)) == expected, title


def test_sector_classification_from_yahoo_names():
    from app.tools import sectors

    assert sectors.classify("Basic Materials", "Steel") == "Metals"
    assert sectors.classify("Basic Materials", "Specialty Chemicals") == "Chemicals"
    assert sectors.classify("Financial Services", "Banks - Regional") == "Banking"
    assert sectors.classify("Industrials", "Aerospace & Defense") == "Defence"
    assert sectors.classify("Consumer Cyclical", "Travel Services") == "Travel"
    assert sectors.classify("Technology", "Something New") == "IT"          # falls back to the broad sector
    assert sectors.classify(None, None) is None
    assert sectors.known("TATASTEEL.NS") == "Metals"                         # the built-in map needs no lookup
    assert news.news_topic({"ticker": "TATASTEEL.NS", "sector": "Metals"}) == "Steel"
    assert news.news_topic({"ticker": "HINDALCO.NS", "sector": "Metals"}) == "Metals"
    assert news.news_topic({"ticker": "X.NS", "sector": "Other"}) is None


# --------------------------------------------------------------------------- bad input

def test_holdings_with_nonsense_quantities_are_left_out():
    from app.tools.market import normalise_holdings

    rows = [
        {"symbol": "TATASTEEL", "units": "lots", "buy_price": 140},      # not a number
        {"symbol": "TATASTEEL", "units": 1e15, "buy_price": 140},        # absurd size
        {"symbol": "TATASTEEL", "units": float("nan"), "buy_price": 140},
        {"symbol": "TATASTEEL", "units": -5, "buy_price": 140},
        "not a row",
        {"symbol": "INFY", "units": "12", "buy_price": "cheap"},         # usable quantity, unusable price
    ]
    holdings, source = normalise_holdings(rows)
    assert source == "user"
    assert [(h["ticker"], h["units"], h["buy_price"]) for h in holdings] == [("INFY.NS", 12.0, None)]


def test_an_unhandled_error_is_a_json_500_with_cors_headers(monkeypatch):
    from fastapi.testclient import TestClient

    from app import terminal
    from app.main import app

    def boom(holdings):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(terminal, "_overview", boom)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/terminal/overview", json={"holdings": []}, headers={"Origin": "https://codeutasva-x.vercel.app"})
    assert response.status_code == 500
    assert response.json() == {"detail": "The server hit an error handling this request."}
    assert response.headers["access-control-allow-origin"] == "https://codeutasva-x.vercel.app"
