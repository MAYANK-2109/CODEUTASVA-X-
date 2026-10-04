import numpy as np
import pandas as pd
import pytest

from app.risk import portfolio_metrics as pm


def frame(days: int = 600, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0004, 0.01, days)
    return pd.DataFrame({
        "A": market + rng.normal(0, 0.004, days),            # A and B move together
        "B": market + rng.normal(0, 0.004, days),
        "C": rng.normal(0.0003, 0.008, days),                # C is on its own
        "D": -0.5 * market + rng.normal(0.0005, 0.006, days),
    }, index=pd.bdate_range("2024-01-01", periods=days))


def test_ledoit_wolf_shrinks_towards_identity_and_stays_valid():
    data = frame().to_numpy()
    cov, shrinkage = pm.ledoit_wolf(data)
    sample = np.cov(data, rowvar=False, bias=True)
    assert 0 < shrinkage < 1 and np.allclose(cov, cov.T) and np.linalg.eigvalsh(cov).min() > 0
    assert np.trace(cov) == pytest.approx(np.trace(sample))                 # total variance is kept
    assert abs(cov[0, 1]) < abs(sample[0, 1])                                # off-diagonals are pulled in
    short, more = pm.ledoit_wolf(data[:40])
    assert more > shrinkage                                                   # less data, more shrinkage


def test_risk_contributions_sum_to_one_and_effective_n():
    cov, _ = pm.ledoit_wolf(frame().to_numpy())
    shares = pm.risk_contributions(np.array([0.4, 0.3, 0.2, 0.1]), cov)
    assert shares.sum() == pytest.approx(1)
    assert pm.effective_n(np.array([0.25] * 4)) == pytest.approx(4)
    assert pm.effective_n(np.array([1.0, 0, 0, 0])) == pytest.approx(1)


def test_correlation_clusters_group_what_moves_together():
    clusters = pm.correlation_clusters(frame().corr(), threshold=0.6)
    assert clusters[0] == ["A", "B"] and ["C"] in clusters and ["D"] in clusters


def test_adding_a_diversifier_lowers_volatility_and_a_twin_does_not():
    data, weights = frame(), {"A": 0.6, "B": 0.4}
    diversifier = pm.candidate_improvement(data, weights, "D", 0.10, 0.0)
    assert diversifier["vol_change"] < 0 and diversifier["effective_n_change"] > 0
    assert diversifier["correlation"] < 0 and diversifier["n"] == 600
    twin = pm.candidate_improvement(data.assign(E=data["A"]), weights, "E", 0.10, 0.0)
    assert twin["vol_change"] > diversifier["vol_change"] and twin["correlation"] > 0.9
    assert pm.candidate_improvement(data, weights, "missing", 0.05, 0.0) is None
    assert pm.candidate_improvement(data.iloc[:50], weights, "D", 0.05, 0.0) is None    # too little history


def test_blend_funds_the_addition_pro_rata():
    mixed = pm.blend({"A": 0.6, "B": 0.4}, "D", 0.05)
    assert mixed == pytest.approx({"A": 0.57, "B": 0.38, "D": 0.05}) and sum(mixed.values()) == pytest.approx(1)


def test_optimiser_respects_caps_and_full_investment():
    data = frame().to_numpy()
    cov, _ = pm.ledoit_wolf(data)
    caps = np.array([0.5, 0.5, 0.05, 0.05])
    weights = pm.optimise(pm.shrunk_means(data), cov, caps, np.array([0.5, 0.5, 0, 0]))
    assert weights.sum() == pytest.approx(1, abs=1e-6) and (weights >= -1e-9).all() and (weights <= caps + 1e-9).all()
    projected = pm.project_capped(np.array([2.0, -1.0, 0.3]), np.array([0.6, 0.6, 0.6]))
    assert projected.sum() == pytest.approx(1, abs=1e-6) and projected[0] == pytest.approx(0.6) and projected[1] == 0
    with pytest.raises(ValueError):
        pm.optimise(pm.shrunk_means(data), cov, np.array([0.2] * 4), np.array([0.25] * 4))


def test_walk_forward_never_sees_the_future():
    data = frame()
    seen = []

    def weigh(history: pd.DataFrame) -> np.ndarray:
        seen.append(history.index[-1])
        return np.full(4, 0.25)

    out = pm.walk_forward(data, weigh, lookback=250, hold=21, periods=6)
    assert len(seen) == 6 and len(out) == 6 * 21
    # Each rebalance uses only days before the first day it is then held for.
    assert all(last < out.index[i * 21] for i, last in enumerate(seen))

    # Changing the last month cannot change any earlier out-of-sample return.
    changed = data.copy()
    changed.iloc[-21:] *= 5
    momentum = lambda history: pm.project_capped(history.mean().to_numpy() * 1000, np.ones(4))  # noqa: E731
    before, after = (pm.walk_forward(d, momentum, 250, 21, 6) for d in (data, changed))
    pd.testing.assert_series_equal(before.iloc[:-21], after.iloc[:-21])
    assert not np.allclose(before.iloc[-21:], after.iloc[-21:])


def test_series_stats_and_sharpe_interval():
    series = pm.portfolio_series(frame(), {"A": 0.5, "C": 0.5})
    stats = pm.series_stats(series, 0.0)
    assert stats["vol"] > 0 and stats["cvar"] > 0 and stats["max_drawdown"] < 0 and stats["n"] == 600
    low, high = pm.sharpe_interval(series, 0.0)
    assert low < stats["sharpe"] < high
    assert pm.sharpe_interval(series, 0.0) == (low, high)                    # seeded, so repeatable
    assert pm.series_stats(series.iloc[:30], 0.0)["vol"] is None and pm.sharpe_interval(series.iloc[:30], 0.0) is None
