"""Where a portfolio stands at the latest close: positions, sector weights, VaR and beta."""

import math

import numpy as np
import pandas as pd

from app.risk import metrics, portfolio_metrics
from app.tools.market import NIFTY


MIN_SESSIONS_FOR_RISK = 60


def _number(value) -> float | None:
    return None if value is None or math.isnan(value) else float(value)


def snapshot(holdings: list[dict], closes: pd.DataFrame) -> dict | None:
    """Priced positions (largest first) with weights and betas, sector rows, and
    portfolio VaR, beta and volatility. None when no holding has a price."""
    latest = closes.iloc[-1]
    positions, unpriced = [], []
    for h in holdings:
        price = _number(latest.get(h["ticker"]))
        if price is None:
            unpriced.append(h["name"])
        else:
            positions.append({**h, "price": price, "value": price * h["units"]})
    if not positions:
        return None
    positions.sort(key=lambda p: -p["value"])
    total = sum(p["value"] for p in positions)
    weights = {p["ticker"]: p["value"] / total for p in positions}

    sectors: dict[str, dict] = {}
    for p in positions:
        entry = sectors.setdefault(p["sector"], {"sector": p["sector"], "value": 0.0, "holdings": []})
        entry["value"] += p["value"]
        entry["holdings"].append(p["name"])
    sector_rows = sorted(({**s, "weight": s["value"] / total} for s in sectors.values()), key=lambda s: -s["value"])

    returns = metrics.daily_returns(closes)
    portfolio = metrics.portfolio_returns(returns, weights)

    # Each holding's share of portfolio risk, which differs from its share of value
    # when it is more volatile or moves with the rest. None without enough shared history.
    shared = returns[list(weights)].dropna(how="any")
    risk_share: dict[str, float | None] = {t: None for t in weights}
    if len(shared) >= MIN_SESSIONS_FOR_RISK:
        cov, _ = portfolio_metrics.ledoit_wolf(shared.to_numpy())
        shares = portfolio_metrics.risk_contributions(np.array(list(weights.values())), cov)
        risk_share = dict(zip(weights, (float(share) for share in shares)))
    for p in positions:
        p["risk_share"] = risk_share[p["ticker"]]
    for row in sector_rows:
        parts = [p["risk_share"] for p in positions if p["sector"] == row["sector"]]
        row["risk_share"] = sum(parts) if all(part is not None for part in parts) else None
    betas = {t: metrics.beta(returns[t], returns[NIFTY]) for t in weights}
    known = {t: b for t, b in betas.items() if b is not None}
    for p in positions:
        p["weight"] = weights[p["ticker"]]
        p["beta"] = betas.get(p["ticker"])
    spread = _number(portfolio.std()) if len(portfolio.dropna()) >= 60 else None
    return {
        "positions": positions, "unpriced": unpriced, "total": total, "weights": weights,
        "sectors": sector_rows,
        "var": metrics.historical_var(portfolio),
        "beta": sum(weights[t] * b for t, b in known.items()) if known else None,
        "volatility": spread * math.sqrt(metrics.TRADING_DAYS_1Y) if spread else None,
        "sessions": len(returns),
        # 1 / sum of squared weights: how many equal positions would be this concentrated.
        "effective_holdings": 1 / sum(w * w for w in weights.values()),
    }
