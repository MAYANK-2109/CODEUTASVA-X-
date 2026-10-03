from fastapi.testclient import TestClient

from app import api
from app.ingestion.news import parse_feed
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
