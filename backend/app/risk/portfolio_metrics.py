"""Portfolio maths for the suggestion engine: shrunk covariance and returns,
risk contributions, the effect of adding a candidate, a capped optimizer and a
walk-forward check. All of it is deterministic arithmetic on daily returns;
nothing here is a trained model."""

import numpy as np
import pandas as pd

TRADING_DAYS = 250
CVAR_LEVEL = 0.95


def ledoit_wolf(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Covariance shrunk towards a scaled identity (Ledoit and Wolf, 2004).
    Returns the matrix and the shrinkage intensity it chose, between 0 and 1."""
    x = returns - returns.mean(axis=0)
    n, p = x.shape
    sample = x.T @ x / n
    mu = np.trace(sample) / p
    delta = ((sample - mu * np.eye(p)) ** 2).sum() / p
    beta = sum(((np.outer(row, row) - sample) ** 2).sum() for row in x) / p / n**2
    shrinkage = 0.0 if delta == 0 else float(min(beta, delta) / delta)
    return shrinkage * mu * np.eye(p) + (1 - shrinkage) * sample, shrinkage


def shrunk_means(returns: np.ndarray, intensity: float = 0.5) -> np.ndarray:
    """Each asset's mean daily return pulled towards the average of all of them."""
    means = returns.mean(axis=0)
    return (1 - intensity) * means + intensity * means.mean()


def series_stats(series: pd.Series, risk_free: float) -> dict:
    """Annualised volatility and Sharpe ratio, daily CVaR and the worst drawdown of a return series."""
    clean = series.dropna()
    if len(clean) < 60:
        return {"n": int(len(clean)), "vol": None, "sharpe": None, "cvar": None, "max_drawdown": None}
    vol = float(clean.std()) * np.sqrt(TRADING_DAYS)
    excess = float(clean.mean()) * TRADING_DAYS - risk_free
    cutoff = np.percentile(clean, (1 - CVAR_LEVEL) * 100)
    curve = (1 + clean).cumprod()
    return {
        "n": int(len(clean)),
        "vol": vol,
        "sharpe": excess / vol if vol else None,
        "cvar": float(-clean[clean <= cutoff].mean()),
        "max_drawdown": float((curve / curve.cummax() - 1).min()),
    }


def sharpe_interval(series: pd.Series, risk_free: float, draws: int = 300, block: int = 10,
                    seed: int = 7) -> tuple[float, float] | None:
    """A 90% interval for the Sharpe ratio from a block bootstrap, which keeps runs of days together."""
    clean = series.dropna().to_numpy()
    if len(clean) < 120:
        return None
    rng = np.random.default_rng(seed)
    blocks = len(clean) // block
    starts = rng.integers(0, len(clean) - block + 1, size=(draws, blocks))
    samples = clean[starts[:, :, None] + np.arange(block)].reshape(draws, -1)
    vol = samples.std(axis=1) * np.sqrt(TRADING_DAYS)
    sharpe = (samples.mean(axis=1) * TRADING_DAYS - risk_free) / np.where(vol == 0, np.nan, vol)
    return float(np.nanpercentile(sharpe, 5)), float(np.nanpercentile(sharpe, 95))


def risk_contributions(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Each asset's share of portfolio variance; the shares sum to 1."""
    marginal = cov @ weights
    total = float(weights @ marginal)
    return weights * marginal / total if total > 0 else np.zeros_like(weights)


def effective_n(shares: np.ndarray) -> float:
    """How many equal pieces would be this concentrated: 1 / sum of squared shares."""
    squares = float((np.asarray(shares) ** 2).sum())
    return 1 / squares if squares > 0 else 0.0


def correlation_clusters(correlation: pd.DataFrame, threshold: float = 0.6) -> list[list[str]]:
    """Groups of assets linked, directly or through others, by a correlation at or above the threshold."""
    names, seen, clusters = list(correlation.columns), set(), []
    for name in names:
        if name in seen:
            continue
        group, queue = [], [name]
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            group.append(current)
            queue.extend(other for other in names
                         if other not in seen and correlation.loc[current, other] >= threshold)
        clusters.append(sorted(group))
    return sorted(clusters, key=len, reverse=True)


def blend(weights: dict[str, float], candidate: str, share: float) -> dict[str, float]:
    """The portfolio with `share` moved into the candidate, taken pro rata from everything else."""
    scaled = {name: weight * (1 - share) for name, weight in weights.items() if name != candidate}
    scaled[candidate] = share + weights.get(candidate, 0.0) * (1 - share)
    return scaled


def portfolio_series(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    columns = [name for name in weights if name in returns.columns]
    frame = returns[columns].dropna(how="any")
    return frame @ pd.Series({name: weights[name] for name in columns})


def candidate_improvement(returns: pd.DataFrame, weights: dict[str, float], candidate: str,
                          share: float, risk_free: float) -> dict | None:
    """What adding the candidate at `share` does to the portfolio's volatility,
    CVaR, Sharpe and effective N, on the days all of them have prices. None when
    there is too little shared history to say."""
    if candidate not in returns.columns:
        return None
    columns = list(dict.fromkeys([*weights, candidate]))
    frame = returns[[c for c in columns if c in returns.columns]].dropna(how="any")
    if len(frame) < 120:
        return None
    before = series_stats(portfolio_series(frame, weights), risk_free)
    mixed = blend(weights, candidate, share)
    after = series_stats(portfolio_series(frame, mixed), risk_free)
    current = portfolio_series(frame, weights)
    return {
        "n": before["n"], "before": before, "after": after,
        "vol_change": after["vol"] - before["vol"], "cvar_change": after["cvar"] - before["cvar"],
        "sharpe_change": after["sharpe"] - before["sharpe"],
        "effective_n_change": effective_n(np.array(list(mixed.values()))) - effective_n(np.array(list(weights.values()))),
        "correlation": float(frame[candidate].corr(current)) if candidate not in weights else None,
    }


def project_capped(vector: np.ndarray, caps: np.ndarray, floors: np.ndarray | None = None) -> np.ndarray:
    """The closest weights to `vector` that lie between each floor (0 by default) and each cap and sum to 1."""
    floors = np.zeros_like(caps) if floors is None else floors
    low, high = vector.min() - caps.max() - 1, vector.max() - floors.min() + 1
    for _ in range(80):
        middle = (low + high) / 2
        if np.clip(vector - middle, floors, caps).sum() > 1:
            low = middle
        else:
            high = middle
    return np.clip(vector - (low + high) / 2, floors, caps)


def limit_group(weights: np.ndarray, group: np.ndarray, limit: float, caps: np.ndarray) -> np.ndarray:
    """Scale the group's weights down to `limit` in total and hand what is freed
    to the other assets, in proportion to the room each has under its cap."""
    total = weights[group].sum()
    if total <= limit:
        return weights
    out = weights.copy()
    out[group] *= limit / total
    room = np.where(group, 0.0, caps - out)
    if room.sum() > 0:
        out += room / room.sum() * (total - limit)
    return out


def optimise(means: np.ndarray, cov: np.ndarray, caps: np.ndarray, start: np.ndarray,
             floors: np.ndarray | None = None, group: np.ndarray | None = None, group_limit: float = 1.0,
             risk_aversion: float = 4.0, steps: int = 400) -> np.ndarray:
    """Mean-variance weights by projected gradient ascent on annualised return
    minus half the risk aversion times variance. Each weight stays between its
    floor and its cap, and the assets marked in `group` together stay under `group_limit`."""
    if caps.sum() < 1:
        raise ValueError("the caps do not allow a fully invested portfolio")
    annual_cov, annual_means = cov * TRADING_DAYS, means * TRADING_DAYS
    rate = 1 / (risk_aversion * np.linalg.eigvalsh(annual_cov).max())

    def feasible(vector: np.ndarray) -> np.ndarray:
        weights = project_capped(vector, caps, floors)
        return weights if group is None else limit_group(weights, group, group_limit, caps)

    weights = feasible(start)
    for _ in range(steps):
        weights = feasible(weights + rate * (annual_means - risk_aversion * annual_cov @ weights))
    return weights


def walk_forward(returns: pd.DataFrame, weigh, lookback: int, hold: int = 21, periods: int = 24) -> pd.Series:
    """Out-of-sample daily returns of a rule. At each rebalance `weigh` sees only
    the `lookback` days before it, and its weights are then held for `hold` days."""
    frame = returns.dropna(how="any")
    first = max(lookback, len(frame) - periods * hold)
    pieces = []
    for start in range(first, len(frame), hold):
        weights = weigh(frame.iloc[start - lookback:start])
        pieces.append(frame.iloc[start:start + hold] @ weights)
    return pd.concat(pieces) if pieces else pd.Series(dtype=float)
