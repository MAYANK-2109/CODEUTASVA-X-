"""Turns an alert's recommended action into paper trades.

Every execution walks the same states and each one is written to the ledger:
PROPOSED (the alert's solution) -> APPROVED (by the user's click or by their
policy) -> SUBMITTED (to the broker) -> FILLED, or REJECTED with the reason.
After a fill the portfolio's VaR is measured again to check the trade did what
the alert said it would.
"""

import hashlib
import math
from datetime import date

import numpy as np

from app.broker import ledger
from app.broker.paper import get_broker
from app.ml.alerts import build_alerts
from app.ml.features import HORIZON
from app.ml.radar import daily_turnover
from app.risk import metrics
from app.tools.market import get_history, normalise_holdings

EXECUTABLE = ("hedge", "trim", "rebalance")
AUTO_ACTIONS = ("hedge", "trim")
VAR_LEVEL = 0.95


class NotExecutable(Exception):
    """The alert is gone, or there is nothing in it to trade."""


def _positions(raw_holdings: list[dict] | None) -> tuple[list[dict], object]:
    holdings, _ = normalise_holdings(raw_holdings)
    closes, _ = get_history([h["ticker"] for h in holdings])
    if closes is None:
        return [], None
    latest = closes.iloc[-1]
    positions = []
    for h in holdings:
        price = latest.get(h["ticker"])
        if price is not None and not math.isnan(price):
            positions.append({**h, "price": float(price), "value": float(price) * h["units"]})
    return positions, closes


def horizon_var(positions: list[dict], closes, fills: list[dict]) -> dict | None:
    """The portfolio's loss over the horizon that only 5% of past windows
    exceeded, before and after the fills. A sale moves that value into cash; a
    put held to expiry removes the fall on the amount it protects and costs its premium."""
    tickers = [p["ticker"] for p in positions if p["ticker"] in closes.columns]
    moves = closes[tickers].pct_change(HORIZON, fill_method=None).iloc[-metrics.TRADING_DAYS_1Y:].dropna()
    if len(moves) < 60:
        return None
    value = {p["ticker"]: p["value"] for p in positions if p["ticker"] in tickers}

    def var(values: dict[str, float], puts: list[dict]) -> float:
        pnl = sum(moves[t] * v for t, v in values.items())
        for put in puts:
            if put["ticker"] in moves.columns:
                pnl = pnl + put["notional"] * (-moves[put["ticker"]]).clip(lower=0) - put["fill_price"]
        return max(-float(np.percentile(pnl, (1 - VAR_LEVEL) * 100)), 0.0)

    after = dict(value)
    for fill in fills:
        if fill["type"] == "sell" and fill["ticker"] in after:
            after[fill["ticker"]] = max(after[fill["ticker"]] - fill["quantity"] * fill["price"], 0.0)
    before_var = var(value, [])
    after_var = var(after, [f for f in fills if f["type"] == "buy_put"])
    return {
        "horizon_sessions": HORIZON, "level": VAR_LEVEL, "windows": int(len(moves)),
        "var_before": round(before_var), "var_after": round(after_var),
        "reduction": round(before_var - after_var),
        "method": f"Historical simulation over {len(moves)} overlapping {HORIZON}-session windows. A sale is held "
                  f"as cash; a put is held to expiry.",
    }


def execution_id(user_id: str, alert_id: str) -> str:
    """One execution per user, alert and day, so a second click cannot trade twice."""
    return hashlib.sha1(f"{user_id}|{alert_id}|{date.today().isoformat()}".encode()).hexdigest()[:16]


def _execute(user_id: str, alert: dict, positions: list[dict], closes, mode: str, approval: str) -> dict:
    existing = ledger.find(execution_id(user_id, alert["id"]))
    if existing:
        return {**existing, "duplicate": True}

    solution, broker = alert["solution"], get_broker()
    record = {"id": execution_id(user_id, alert["id"]), "user_id": user_id, "alert_id": alert["id"],
              "title": alert["title"], "action": solution["action"], "mode": mode, "broker": broker.name,
              "detail": solution["headline"]}
    events = [("PROPOSED", solution["headline"]), ("APPROVED", approval)]

    def rejected(reason: str) -> dict:
        return ledger.record({**record, "state": "REJECTED"}, [*events, ("REJECTED", reason)], [])

    orders = solution.get("orders") or []
    if (alert.get("hedge") or {}).get("drill"):
        return rejected("A drill is a rehearsal of a hypothetical event, so nothing is traded.")
    if solution["action"] not in EXECUTABLE or not orders:
        return rejected("This alert's action has no order to place.")
    held = {p["ticker"]: p["units"] for p in positions}
    for order in orders:
        if order["type"] == "sell":
            left = held.get(order["ticker"], 0) - ledger.shares_sold(user_id, order["ticker"])
            if order["quantity"] > left:
                return rejected(f'The order sells {order["quantity"]:,} shares of {order["name"]}, but only '
                                f"{max(left, 0):,.0f} are left after earlier paper sales.")

    turnover = daily_turnover(list({order["ticker"] for order in orders}))
    events.append(("SUBMITTED", f"{len(orders)} order(s) sent to {broker.name}"))
    fills = [broker.place(order, turnover.get(order["ticker"])) for order in orders]
    slippage = sum(fill["slippage_amount"] for fill in fills)
    verification = horizon_var(positions, closes, fills) if closes is not None else None
    events.append(("FILLED", f"{len(fills)} fill(s); slippage cost ₹{slippage:,.0f}"
                   + (f'; {HORIZON}-session 95% VaR ₹{verification["var_before"]:,} -> ₹{verification["var_after"]:,}'
                      if verification else "")))
    return ledger.record({**record, "state": "FILLED", "verification": verification}, events, fills)


def execute(user_id: str, alert_id: str, raw_holdings: list[dict] | None, scenario: str | None = None) -> dict:
    """One-click execution of an alert that is active right now. The orders are
    rebuilt on the server from the holdings, never taken from the request."""
    alert = next((a for a in build_alerts(raw_holdings, scenario)["alerts"] if a["id"] == alert_id), None)
    if alert is None:
        raise NotExecutable("This alert is no longer active, so there is nothing to execute.")
    positions, closes = _positions(raw_holdings)
    return _execute(user_id, alert, positions, closes, "manual", "Approved by you with one click")


def policy_allows(alert: dict, policy: dict) -> str | None:
    """Why the policy approves this alert, or None when it does not."""
    solution = alert["solution"]
    measured = solution.get("metrics") or {}
    if solution["action"] not in AUTO_ACTIONS or not solution.get("orders") or "downside" not in measured:
        return None
    if alert.get("hedge") and measured.get("confidence") != "High":
        return None  # a weather or news signal must be corroborated before it trades by itself
    if measured["downside"] < policy["min_downside"]:
        return None
    if solution["action"] == "hedge" and measured["put_cost"] > policy["max_put_cost"]:
        return None
    reason = f'Approved by your policy: downside {measured["downside"]:.1%} is at least {policy["min_downside"]:.1%}'
    if solution["action"] == "hedge":
        reason += f' and the put costs {measured["put_cost"]:.1%}, within {policy["max_put_cost"]:.1%}'
    return reason


def run_policy(user_id: str, raw_holdings: list[dict] | None) -> dict:
    """Autonomous mode: execute every active alert the user's policy approves."""
    policy = ledger.get_policy(user_id)
    if not policy["enabled"]:
        return {"enabled": False, "executed": []}
    positions, closes = _positions(raw_holdings)
    executed = []
    for alert in build_alerts(raw_holdings)["alerts"]:
        approval = policy_allows(alert, policy)
        if approval and not ledger.find(execution_id(user_id, alert["id"])):
            executed.append(_execute(user_id, alert, positions, closes, "auto", approval))
    return {"enabled": True, "executed": executed}


def account(user_id: str) -> dict:
    """The paper account: its policy, its executions and what they add up to."""
    rows = ledger.executions(user_id)
    filled = [r for r in rows if r["state"] == "FILLED"]
    fills = [fill for r in filled for fill in r["fills"]]
    return {
        "broker": get_broker().name,
        "policy": ledger.get_policy(user_id),
        "summary": {
            "filled": len(filled), "rejected": sum(r["state"] == "REJECTED" for r in rows),
            "cash_from_sales": round(sum(f["cash_flow"] for f in fills if f["cash_flow"] > 0), 2),
            "premium_paid": round(-sum(f["cash_flow"] for f in fills if f["cash_flow"] < 0), 2),
            "slippage_cost": round(sum(f["slippage_amount"] for f in fills), 2),
            "var_reduction": sum((r["verification"] or {}).get("reduction", 0) for r in filled),
        },
        "executions": rows,
    }
