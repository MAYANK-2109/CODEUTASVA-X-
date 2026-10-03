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
