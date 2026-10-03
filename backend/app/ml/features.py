"""Features that describe a sudden price move. Used identically for training
on history and for scoring today's prices, so the two can never drift apart."""

import numpy as np
import pandas as pd

BETA_WINDOW = 250
VOL_WINDOW = 60
SHOCK_Z = 2.0           # an abnormal move at least this many daily standard deviations
HORIZON = 5             # sessions over which "what happened next" is measured
FEATURES = ["today", "recent_peak", "recent_shocks", "vol_regime", "market_today", "trend_gap"]

# The impact model reads four kinds of data. Every value is known by the close
# of the day it describes; prices from markets that close later are a day old.
PRICE_FEATURES = [*FEATURES, "today_signed", "week_signed", "return_5", "return_21", "beta", "vol_20", "vol_60",
                  "high_gap", "usual_downside"]
MACRO_FEATURES = ["vix", "vix_change_5", "brent_change_5", "brent_change_21", "rupee_change_21",
                  "nifty_return_21", "nifty_vol_60", "nifty_drawdown"]
SECTOR_FEATURES = ["sector"]
WEATHER_FEATURES = ["site_alert_days"]
IMPACT_FEATURES = [*PRICE_FEATURES, *MACRO_FEATURES, *SECTOR_FEATURES, *WEATHER_FEATURES]
DOWNSIDE_LEVEL = 0.05   # the downside is the move that only one week in twenty is worse than
FALL_SIZE = 0.05        # a "sharp fall" is a loss of at least this much over the horizon
USUAL_WINDOW = 500
WEATHER_WINDOW_DAYS = 7
VIX, BRENT, RUPEE = "^INDIAVIX", "BZ=F", "INR=X"


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
            "beta": beta,
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


def macro_features(nifty: pd.Series, macro: pd.DataFrame) -> pd.DataFrame:
    """Market-wide conditions on each NSE session. `macro` holds daily closes of
    India VIX, Brent and USD/INR, each on its own calendar."""
    days = nifty.dropna().index
    vix = macro[VIX].dropna().reindex(days, method="ffill")
    # Brent and the rupee close after the NSE does, so a session sees the previous day's.
    late = macro[[BRENT, RUPEE]].ffill().reindex(days, method="ffill").shift(1)
    market = nifty.reindex(days)
    change = market.pct_change(fill_method=None)
    return pd.DataFrame(
        {
            "vix": vix,
            "vix_change_5": vix / vix.shift(5) - 1,
            "brent_change_5": late[BRENT] / late[BRENT].shift(5) - 1,
            "brent_change_21": late[BRENT] / late[BRENT].shift(21) - 1,
            "rupee_change_21": late[RUPEE] / late[RUPEE].shift(21) - 1,
            "nifty_return_21": market / market.shift(21) - 1,
            "nifty_vol_60": change.rolling(VOL_WINDOW).std() * np.sqrt(250),
            "nifty_drawdown": market / market.rolling(BETA_WINDOW).max() - 1,
        }
    )


def site_alert_days(days: pd.DatetimeIndex, alert_dates: list[str]) -> pd.Series:
    """How many weather alert days the company's sites had in the week before each session."""
    if not alert_dates:
        return pd.Series(0.0, index=days)
    daily = pd.Series(1.0, index=pd.to_datetime(alert_dates)).groupby(level=0).sum()
    calendar = pd.date_range(min(days[0], daily.index[0]), days[-1])
    recent = daily.reindex(calendar, fill_value=0.0).rolling(WEATHER_WINDOW_DAYS).sum().shift(1)
    return recent.reindex(days).fillna(0.0)


def impact_features(stock: pd.Series, market: pd.Series, macro_days: pd.DataFrame,
                    sector_code: int, alert_dates: list[str]) -> pd.DataFrame:
    """One row per session: the model's inputs, and what followed (`forward_return`
    and `fall_ahead`), which is blank for the latest sessions."""
    frame = abnormal_returns(stock, market)
    days = risk_features(frame)
    week = stock / stock.shift(HORIZON) - 1
    out = days[[*FEATURES, "z", "return", "abnormal"]].copy()
    out["today_signed"] = frame["z"].clip(-8, 8)
    out["week_signed"] = frame["prior_week_z"].clip(-8, 8)
    out["return_5"] = week
    out["return_21"] = stock / stock.shift(21) - 1
    out["beta"] = frame["beta"]
    out["vol_20"] = frame["return"].rolling(20).std() * np.sqrt(250)
    out["vol_60"] = frame["return"].rolling(VOL_WINDOW).std() * np.sqrt(250)
    out["high_gap"] = stock / stock.rolling(BETA_WINDOW).max() - 1
    out["usual_downside"] = week.rolling(USUAL_WINDOW, min_periods=250).quantile(DOWNSIDE_LEVEL)
    out = out.join(macro_days.reindex(out.index))
    out["sector"] = float(sector_code)
    out["site_alert_days"] = site_alert_days(out.index, alert_dates)
    out["forward_return"] = stock.shift(-HORIZON) / stock - 1
    out["fall_ahead"] = (out["forward_return"] <= -FALL_SIZE).astype(float).where(out["forward_return"].notna())
    return out
