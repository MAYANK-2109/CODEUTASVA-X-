import pytest
from fastapi.testclient import TestClient

from app.broker import ledger, paper, service
from app.main import app
from app.ml import alerts, impact, radar
from app.tools import resolver
from tests.test_alerts import HOLDINGS, closes

client = TestClient(app)
FLAGGED = {"fall": 0.21, "downside": -0.12, "usual_downside": -0.04, "elevated": True}


@pytest.fixture
def paper_account(monkeypatch, tmp_path):
    """A fresh ledger, fixed prices, no network; AAA is flagged by the impact model."""
    monkeypatch.setattr(ledger, "DB_FILE", tmp_path / "ledger.sqlite")
    monkeypatch.setenv("SENTIMENT_MODEL", "vader")
    monkeypatch.setattr(resolver, "search", lambda query: None)
    monkeypatch.setattr(alerts, "get_news", lambda query, limit=8: [])
    monkeypatch.setattr(alerts, "get_macro", lambda: ([], None))
    monkeypatch.setattr(radar, "get_weather_outlook", lambda: [])
    monkeypatch.setattr(radar, "get_macro", lambda: ([], None))
    monkeypatch.setattr(radar, "daily_turnover", lambda tickers: {})
    monkeypatch.setattr(radar.gdelt, "signals", lambda: {"themes": {}, "scanned_at": None, "stale": True, "error": None})
    monkeypatch.setattr(service, "daily_turnover", lambda tickers: {"AAA.NS": 50_000_000.0})
    frame = closes()
    monkeypatch.setattr(alerts, "get_history", lambda tickers: (frame, "live"))
    monkeypatch.setattr(service, "get_history", lambda tickers: (frame, "live"))
    monkeypatch.setattr(impact, "score_positions", lambda positions, closes: {"AAA.NS": FLAGGED})
    return frame


def active(category: str) -> dict:
    return next(a for a in alerts.build_alerts(HOLDINGS)["alerts"] if a["category"] == category)


def test_slippage_grows_with_the_order_and_is_flat_when_liquidity_is_unknown():
    small = paper.share_slippage_bps(100_000, 100_000_000)        # 0.1% of a day
    large = paper.share_slippage_bps(4_000_000, 100_000_000)      # 4% of a day
    assert small == pytest.approx(5 + 10 * 0.1 ** 0.5) and large == pytest.approx(5 + 10 * 2)
    assert paper.share_slippage_bps(100_000, None) == paper.UNKNOWN_LIQUIDITY_BPS

    sold = paper.PaperBroker().place({"type": "sell", "ticker": "X", "name": "X", "quantity": 100, "price": 200.0}, 20_000_000)
    assert sold["fill_price"] < 200 and sold["cash_flow"] == pytest.approx(100 * sold["fill_price"], abs=0.5)
    assert sold["slippage_amount"] == pytest.approx(20000 - sold["cash_flow"], abs=0.01)
    put = paper.PaperBroker().place({"type": "buy_put", "ticker": "X", "name": "X", "notional": 20000, "premium": 400.0}, None)
    assert put["cash_flow"] == -412.0 and put["slippage_amount"] == 12.0


def test_one_click_execution_fills_logs_every_state_and_checks_var(paper_account):
    risk = active("risk")
    assert risk["solution"]["action"] in ("hedge", "trim") and risk["solution"]["orders"]
    body = client.post("/api/broker/execute", json={"user_id": "u1", "alert_id": risk["id"], "holdings": HOLDINGS}).json()

    assert body["state"] == "FILLED" and body["mode"] == "manual"
    assert [e["state"] for e in body["events"]] == ["PROPOSED", "APPROVED", "SUBMITTED", "FILLED"]
    assert "one click" in body["events"][1]["detail"]
    fill = body["fills"][0]
    assert fill["ticker"] == "AAA.NS" and fill["slippage_amount"] > 0
    check = body["verification"]
    assert check["var_after"] < check["var_before"] and check["reduction"] == check["var_before"] - check["var_after"]

    # A second click the same day returns the same execution instead of trading again.
    again = client.post("/api/broker/execute", json={"user_id": "u1", "alert_id": risk["id"], "holdings": HOLDINGS}).json()
    assert again["id"] == body["id"] and again["duplicate"] is True

    account = client.get("/api/broker/account", params={"user_id": "u1"}).json()
    assert account["summary"]["filled"] == 1 and len(account["executions"]) == 1
    assert account["summary"]["slippage_cost"] == pytest.approx(fill["slippage_amount"])
    assert client.get("/api/broker/account", params={"user_id": "someone-else"}).json()["executions"] == []


def test_orders_come_from_the_server_and_an_inactive_alert_cannot_be_executed(paper_account):
    response = client.post("/api/broker/execute", json={"user_id": "u1", "alert_id": "not-an-alert", "holdings": HOLDINGS})
    assert response.status_code == 404 and "no longer active" in response.json()["detail"]


def test_selling_more_than_is_left_is_rejected(paper_account, monkeypatch):
    concentration = active("concentration")
    order = concentration["solution"]["orders"][0]
    assert order["type"] == "sell" and order["quantity"] >= 1
    monkeypatch.setattr(ledger, "shares_sold", lambda user_id, ticker: 100.0)      # all 100 already sold on paper
    body = client.post("/api/broker/execute",
                       json={"user_id": "u1", "alert_id": concentration["id"], "holdings": HOLDINGS}).json()
    assert body["state"] == "REJECTED" and body["fills"] == [] and body["verification"] is None
    assert [e["state"] for e in body["events"]] == ["PROPOSED", "APPROVED", "REJECTED"]
    assert "only 0 are left" in body["events"][-1]["detail"]


def test_watch_alerts_and_drills_have_nothing_to_execute(paper_account, monkeypatch):
    monkeypatch.setattr(impact, "score_positions", lambda positions, closes: {})
    drill = next(a for a in alerts.build_alerts(HOLDINGS, "cyclone_gujarat")["alerts"] if a["hedge"] and a["hedge"]["drill"])
    assert drill["solution"]["orders"] == []
    body = client.post("/api/broker/execute", json={"user_id": "u1", "alert_id": drill["id"], "holdings": HOLDINGS,
                                                    "scenario": "cyclone_gujarat"}).json()
    assert body["state"] == "REJECTED" and "rehearsal" in body["events"][-1]["detail"]


def test_policy_executes_only_what_it_approves(paper_account):
    assert client.post("/api/broker/auto", json={"user_id": "u2", "holdings": HOLDINGS}).json() == {
        "enabled": False, "executed": []}                                         # off by default

    strict = {"user_id": "u2", "enabled": True, "min_downside": 0.30, "max_put_cost": 0.05}
    assert client.put("/api/broker/policy", json=strict).json()["min_downside"] == 0.30
    assert client.post("/api/broker/auto", json={"user_id": "u2", "holdings": HOLDINGS}).json()["executed"] == []

    client.put("/api/broker/policy", json={**strict, "min_downside": 0.08})        # the 12% downside now qualifies
    (done,) = client.post("/api/broker/auto", json={"user_id": "u2", "holdings": HOLDINGS}).json()["executed"]
    assert done["state"] == "FILLED" and done["mode"] == "auto" and done["action"] in ("hedge", "trim")
    assert "Approved by your policy: downside 12.0% is at least 8.0%" in done["events"][1]["detail"]
    # Concentration is a rebalance, which the policy never does by itself; and nothing runs twice.
    assert client.post("/api/broker/auto", json={"user_id": "u2", "holdings": HOLDINGS}).json()["executed"] == []
    assert client.get("/api/broker/account", params={"user_id": "u2"}).json()["policy"]["enabled"] is True


def test_policy_rules():
    policy = {"enabled": True, "min_downside": 0.08, "max_put_cost": 0.02}
    order = [{"type": "buy_put"}]
    alert = lambda action, metrics, hedge=None: {"solution": {"action": action, "orders": order, "metrics": metrics}, "hedge": hedge}  # noqa: E731
    assert service.policy_allows(alert("hedge", {"downside": 0.10, "put_cost": 0.015}), policy)
    assert service.policy_allows(alert("hedge", {"downside": 0.10, "put_cost": 0.03}), policy) is None    # put too dear
    assert service.policy_allows(alert("trim", {"downside": 0.10, "put_cost": 0.03}), policy)             # no put bought
    assert service.policy_allows(alert("trim", {"downside": 0.05, "put_cost": 0.01}), policy) is None     # too small
    assert service.policy_allows(alert("rebalance", {"downside": 0.5, "put_cost": 0.0}), policy) is None
    weather = {"downside": 0.2, "put_cost": 0.01, "confidence": "Medium"}
    assert service.policy_allows(alert("hedge", weather, hedge={"drill": False}), policy) is None         # not corroborated
    assert service.policy_allows(alert("hedge", {**weather, "confidence": "High"}, hedge={"drill": False}), policy)
