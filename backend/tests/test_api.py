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
    assert not names_holding("Rabi sowing picks up", "State Bank of India", "SBIN.NS")


HELD = [
    {"name": "Reliance Industries", "symbol": "RELIANCE", "units": 5, "type": "STOCK"},
    {"name": "ITC", "symbol": "ITC", "units": 10, "type": "STOCK"},
    {"name": "Some Bluechip Fund", "symbol": "", "units": 3, "type": "MF"},
]


def test_news_keeps_only_headlines_that_name_a_holding(monkeypatch):
    feed = [
        {"title": "Sensex ends flat ahead of policy", "source": "A", "url": "u1", "published_at": "2026-10-03T09:00:00+00:00"},
        {"title": "ITC posts higher profit", "source": "B", "url": "u2", "published_at": "2026-10-02T09:00:00+00:00"},
        {"title": "Reliance and ITC lead gains", "source": "C", "url": "u3", "published_at": "2026-10-03T10:00:00+00:00"},
        {"title": "Some Bluechip Fund cuts expense ratio", "source": "D", "url": "u4", "published_at": "2026-10-03T11:00:00+00:00"},
        {"title": "ITC posts higher profit", "source": "E", "url": "u5", "published_at": "2026-10-01T09:00:00+00:00"},
    ]
    asked = []
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: asked.append(query) or feed)
    monkeypatch.setattr(api.resolver, "search", lambda query: None)

    body = client.post("/api/news", json={"holdings": HELD}).json()
    assert [i["title"] for i in body["items"]] == ["Reliance and ITC lead gains", "ITC posts higher profit"]   # newest first, no repeat
    assert body["items"][0]["holdings"] == ["Reliance Industries", "ITC"]
    assert body["portfolio_source"] == "user" and body["holdings"] == 2      # the fund is not a stock
    assert len(asked) == 1 and '"Reliance Industries"' in asked[0] and asked[0].endswith("when:7d")
    assert "Bluechip" not in asked[0]


def test_news_for_many_holdings_is_searched_in_batches_and_limited(monkeypatch):
    many = [{"name": f"Company{n} Works", "symbol": f"CO{chr(65 + n)}X", "units": 1, "type": "STOCK"} for n in range(8)]
    asked = []

    def feed(query, limit=8):
        asked.append(query)
        return [{"title": f"Company{n} Works wins order", "source": "W", "url": f"u{n}",
                 "published_at": f"2026-10-0{n + 1}T09:00:00+00:00"} for n in range(8) if f'"Company{n} Works"' in query]

    monkeypatch.setattr(news, "get_news", feed)
    monkeypatch.setattr(api.resolver, "search", lambda query: None)
    body = client.post("/api/news", json={"holdings": many, "limit": 3}).json()
    assert len(asked) == 2                                                     # six holdings per search
    assert [i["holdings"] for i in body["items"]] == [["Company7 Works"], ["Company6 Works"], ["Company5 Works"]]


def test_news_without_holdings_uses_the_sample_portfolio(monkeypatch):
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: [
        {"title": "NTPC commissions new unit", "source": "W", "url": "u", "published_at": None},
        {"title": "Gold prices steady", "source": "W", "url": "v", "published_at": None}])
    body = client.get("/api/news").json()
    assert body["portfolio_source"] == "sample"
    assert [i["title"] for i in body["items"]] == ["NTPC commissions new unit"]
    assert client.post("/api/news", json={}).json()["portfolio_source"] == "sample"


def test_a_holding_in_the_news_all_week_does_not_crowd_out_the_others(monkeypatch):
    busy = [{"title": f"Reliance Industries update {n}", "source": "W", "url": f"r{n}",
             "published_at": f"2026-10-03T0{n}:00:00+00:00"} for n in range(6)]
    quiet = [{"title": "ITC opens a new plant", "source": "W", "url": "i", "published_at": "2026-09-29T09:00:00+00:00"}]
    monkeypatch.setattr(news, "get_news", lambda query, limit=8: busy + quiet)
    monkeypatch.setattr(api.resolver, "search", lambda query: None)
    titles = [i["title"] for i in client.post("/api/news", json={"holdings": HELD, "limit": 3}).json()["items"]]
    assert titles == ["Reliance Industries update 5", "Reliance Industries update 4", "ITC opens a new plant"]
