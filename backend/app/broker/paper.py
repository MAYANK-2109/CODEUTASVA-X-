"""Broker adapters. Only a paper broker exists: it fills orders at the last
close moved against the trader by a slippage model, and touches no real
account. A live adapter (Zerodha Kite Connect, Alpaca) would implement the same
`place` method and be chosen in `get_broker`."""

import math
from typing import Protocol

HALF_SPREAD_BPS = 5.0          # paid on every share trade
IMPACT_BPS_AT_ONE_PERCENT = 10.0  # extra cost of an order that is 1% of a day's trading
UNKNOWN_LIQUIDITY_BPS = 25.0   # used when the day's trading value is not known
OPTION_SPREAD = 0.03           # a put is bought 3% above its estimated price


def share_slippage_bps(order_value: float, daily_turnover: float | None) -> float:
    """Basis points lost to the spread and to moving the price. The impact part
    grows with the square root of the order's share of a day's trading."""
    if not daily_turnover or daily_turnover <= 0:
        return UNKNOWN_LIQUIDITY_BPS
    participation = order_value / daily_turnover
    return HALF_SPREAD_BPS + IMPACT_BPS_AT_ONE_PERCENT * math.sqrt(participation / 0.01)


class Broker(Protocol):
    name: str

    def place(self, order: dict, daily_turnover: float | None) -> dict: ...


class PaperBroker:
    """Fills every valid order at once, at a price worse than the reference by the slippage model."""

    name = "Paper account (simulated fills, no real orders)"

    def place(self, order: dict, daily_turnover: float | None = None) -> dict:
        if order["type"] == "sell":
            value = order["quantity"] * order["price"]
            bps = share_slippage_bps(value, daily_turnover)
            fill_price = order["price"] * (1 - bps / 10_000)
            proceeds = order["quantity"] * fill_price
            return {**order, "fill_price": round(fill_price, 2), "slippage_bps": round(bps, 1),
                    "slippage_amount": round(value - proceeds, 2), "cash_flow": round(proceeds, 2),
                    "liquidity_known": bool(daily_turnover)}
        if order["type"] == "buy_put":
            paid = order["premium"] * (1 + OPTION_SPREAD)
            return {**order, "fill_price": round(paid, 2), "slippage_bps": round(OPTION_SPREAD * 10_000, 1),
                    "slippage_amount": round(paid - order["premium"], 2), "cash_flow": round(-paid, 2),
                    "liquidity_known": True}
        raise ValueError(f'unsupported order type {order["type"]}')


def get_broker() -> Broker:
    return PaperBroker()
