"""Deterministic risk maths. Every number the chat answer quotes comes from here."""

import numpy as np
import pandas as pd

TRADING_DAYS_1Y = 250


def event_window_returns(
    closes: pd.DataFrame, event_date: str, horizon: int
) -> pd.Series | None:
    """Return per column from the last close before `event_date` to the close of
    the `horizon`-th trading session on or after it. None if out of range."""
    pos = closes.index.searchsorted(pd.Timestamp(event_date))
    if pos == 0 or pos + horizon - 1 >= len(closes.index):
        return None
    return closes.iloc[pos + horizon - 1] / closes.iloc[pos - 1] - 1


def daily_returns(closes: pd.DataFrame, days: int = TRADING_DAYS_1Y) -> pd.DataFrame:
    return closes.pct_change(fill_method=None).iloc[-days:]


def beta(asset_returns: pd.Series, market_returns: pd.Series) -> float | None:
    pair = pd.concat([asset_returns, market_returns], axis=1).dropna()
    if len(pair) < 30:
        return None
    variance = pair.iloc[:, 1].var()
    if not variance:
        return None
    return float(pair.iloc[:, 0].cov(pair.iloc[:, 1]) / variance)


def historical_var(returns: pd.Series, level: float = 0.95) -> tuple[float, float] | None:
    """(VaR, CVaR) as positive loss fractions from the empirical distribution."""
    clean = returns.dropna()
    if len(clean) < 60:
        return None
    cutoff = float(np.percentile(clean, (1 - level) * 100))
    tail = clean[clean <= cutoff]
    return max(-cutoff, 0.0), max(-float(tail.mean()), 0.0)


def portfolio_returns(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Daily returns of a portfolio held at fixed `weights` (which sum to 1)."""
    columns = [t for t in weights if t in returns.columns]
    w = pd.Series({t: weights[t] for t in columns})
    return (returns[columns].dropna(how="any") * w).sum(axis=1)
