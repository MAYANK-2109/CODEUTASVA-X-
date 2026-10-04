import threading
import time

import pandas as pd
import pytest

from app.tools import cache, market
from app.tools.market import NIFTY


@pytest.fixture
def caching(monkeypatch):
    monkeypatch.setattr(cache, "enabled", lambda: True)


def test_cache_is_off_during_tests_so_each_test_sees_its_own_data():
    calls = []
    work = cache.cached(60)(lambda x: calls.append(x) or len(calls))
    assert (work(1), work(1)) == (1, 2)


def test_identical_requests_share_one_answer_until_it_expires(caching):
    calls = []
    work = cache.cached(0.2)(lambda holdings, scenario=None: calls.append(holdings) or len(calls))
    assert work([{"symbol": "A"}]) == 1 and work([{"symbol": "A"}]) == 1          # same request: stored answer
    assert work([{"symbol": "B"}]) == 2 and work([{"symbol": "A"}], scenario="x") == 3
    time.sleep(0.25)
    assert work([{"symbol": "A"}]) == 4                                            # expired: worked out again


def test_requests_that_arrive_together_wait_for_the_first(caching):
    calls = []

    def slow(x):
        calls.append(x)
        time.sleep(0.2)
        return x * 2

    work, answers = cache.cached(60)(slow), []
    threads = [threading.Thread(target=lambda: answers.append(work(21))) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert answers == [42] * 5 and calls == [21]


def test_a_failure_is_not_stored(caching):
    attempts = []

    def flaky(x):
        attempts.append(x)
        if len(attempts) == 1:
            raise RuntimeError("source down")
        return "ok"

    work = cache.cached(60)(flaky)
    with pytest.raises(RuntimeError):
        work(1)
    assert work(1) == "ok" and len(attempts) == 2


def test_price_history_downloads_only_tickers_not_fetched_recently(monkeypatch, tmp_path):
    index = pd.bdate_range("2026-01-01", periods=30)
    asked = []

    def download(tickers, **kwargs):
        asked.append(sorted(tickers))
        return pd.DataFrame({t: range(100, 130) for t in tickers}, index=index, dtype=float)

    monkeypatch.setattr(market, "_download", download)
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(market, "_memory", {})

    first, source = market.get_history(["A.NS", "B.NS"])
    assert source == "live" and list(first.columns) == ["A.NS", "B.NS", NIFTY] and asked == [["A.NS", "B.NS", NIFTY]]
    second, _ = market.get_history(["A.NS", "C.NS"])
    assert asked[1] == ["C.NS"]                                    # A and the index are reused
    assert list(second.columns) == ["A.NS", "C.NS", NIFTY] and second["A.NS"].equals(first["A.NS"])
    market.get_history(["A.NS", "B.NS", "C.NS"])
    assert len(asked) == 2                                          # nothing new to fetch

    monkeypatch.setattr(market, "HISTORY_TTL_SECONDS", 0)           # everything is stale: all fetched again
    market.get_history(["A.NS"])
    assert asked[2] == ["A.NS", NIFTY]


def test_a_ticker_with_no_prices_stays_as_an_empty_column(monkeypatch, tmp_path):
    index = pd.bdate_range("2026-01-01", periods=10)

    def download(tickers, **kwargs):
        return pd.DataFrame({t: (float("nan") if t == "GONE.NS" else 100.0) for t in tickers}, index=index)

    monkeypatch.setattr(market, "_download", download)
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(market, "_memory", {})
    closes, _ = market.get_history(["A.NS", "GONE.NS"])
    assert closes["GONE.NS"].isna().all() and closes["A.NS"].notna().all()
