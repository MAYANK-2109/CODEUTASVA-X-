"""Features that describe a sudden price move. Used identically for training
on history and for scoring today's prices, so the two can never drift apart."""

import numpy as np
import pandas as pd

BETA_WINDOW = 250
VOL_WINDOW = 60
SHOCK_Z = 2.0           # an abnormal move at least this many daily standard deviations
HORIZON = 5             # sessions over which "what happened next" is measured
FEATURES = ["today", "recent_peak", "recent_shocks", "vol_regime", "market_today", "trend_gap"]


def abnormal_returns(stock: pd.Series, market: pd.Series) -> pd.DataFrame:
    """Daily return of the stock beyond what its beta to the index explains,
    with the trailing statistics needed to judge whether a day was unusual."""
    r = stock.pct_change(fill_method=None)
    m = market.pct_change(fill_method=None)
    beta = (r.rolling(BETA_WINDOW).cov(m) / m.rolling(BETA_WINDOW).var()).shift(1).clip(-1, 3)
    abnormal = r - beta * m
    vol = abnormal.rolling(VOL_WINDOW).std().shift(1)
    long_vol = abnormal.rolling(BETA_WINDOW).std().shift(1)
    market_vol = m.rolling(VOL_WINDOW).std().shift(1)
    frame = pd.DataFrame(
        {
            "abnormal": abnormal,
            "z": abnormal / vol,
            "prior_week_z": abnormal.rolling(HORIZON).sum().shift(1) / (vol * np.sqrt(HORIZON)),
            "market_z": m / market_vol,
            "vol_regime": vol / long_vol,
            "trend_gap": (stock / stock.rolling(50).mean() - 1).shift(1) / (vol * np.sqrt(50)),
            "return": r,
        }
    )
    # What the stock did, beyond the market, over the following sessions.
    frame["next"] = abnormal.rolling(HORIZON).sum().shift(-HORIZON)
    return frame


def risk_features(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day: what is known at that day's close, and whether
    a sudden move followed within the next sessions (`shock_ahead`)."""
    size = frame["z"].abs()
    shock = (size >= SHOCK_Z).astype(float)
    ahead = size.rolling(HORIZON).max().shift(-HORIZON)
    return pd.DataFrame(
        {
            "today": size.clip(upper=8),
            "recent_peak": size.rolling(HORIZON).max().clip(upper=8),
            "recent_shocks": shock.rolling(20).sum(),
            "vol_regime": frame["vol_regime"].clip(0.3, 3),
            "market_today": frame["market_z"].abs().clip(upper=8),
            "trend_gap": frame["trend_gap"].abs().clip(upper=6),
            "z": frame["z"],
            "return": frame["return"],
            "abnormal": frame["abnormal"],
            "next": frame["next"],
            "shock_ahead": (ahead >= SHOCK_Z).astype(float).where(ahead.notna()),
        }
    )
