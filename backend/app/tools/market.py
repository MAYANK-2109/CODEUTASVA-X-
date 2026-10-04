import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from app.tools import health, resolver

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
SAMPLE_PORTFOLIO = DATA_DIR / "portfolio.json"

NIFTY = "^NSEI"
HISTORY_START = "2013-01-01"
# Short enough that an alert on today's move is not hours stale.
HISTORY_TTL_SECONDS = 15 * 60
MACRO_TTL_SECONDS = 600

# Assets outside the equity book that an event can move. Each trades on its
# own calendar, so their history is kept apart from the NSE price history.
CROSS_ASSETS = {
    "BZ=F": "Brent crude",
    "NG=F": "Natural gas (Henry Hub)",
    "GC=F": "Gold",
    "INR=X": "USD/INR",
}
CROSS_ASSET_TTL_SECONDS = 3600
INDIA_VIX = "^INDIAVIX"  # fetched with the cross assets; an input to the impact model, not a forecast target

MACRO_SERIES = {
    "BZ=F": "Brent crude (USD/bbl)",
    "INR=X": "USD/INR",
    "^INDIAVIX": "India VIX",
    NIFTY: "Nifty 50",
}

SECTORS = {
    "RELIANCE": "Energy", "ONGC": "Energy", "IOC": "Energy", "BPCL": "Energy",
    "HINDPETRO": "Energy", "GAIL": "Energy", "OIL": "Energy", "COALINDIA": "Energy",
    "NTPC": "Utilities", "POWERGRID": "Utilities", "TATAPOWER": "Utilities",
    "ADANIGREEN": "Utilities", "ADANIPOWER": "Utilities", "NHPC": "Utilities",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "KOTAKBANK": "Banking", "AXISBANK": "Banking", "INDUSINDBK": "Banking",
    "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials", "HDFCLIFE": "Financials",
    "SBILIFE": "Financials", "LICI": "Financials",
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT", "LTIM": "IT",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "DABUR": "FMCG", "TATACONSUM": "FMCG",
    "INDIGO": "Airlines", "SPICEJET": "Airlines",
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto",
    "EICHERMOT": "Auto", "HEROMOTOCO": "Auto",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "DIVISLAB": "Pharma",
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals", "VEDL": "Metals",
    "LT": "Infrastructure", "ADANIPORTS": "Infrastructure", "ULTRACEMCO": "Cement",
    "GRASIM": "Cement", "ASIANPAINT": "Paints", "BERGEPAINT": "Paints",
    "BHARTIARTL": "Telecom", "DLF": "Real Estate", "TITAN": "Consumer",
    "GOLDBEES": "Commodities", "SILVERBEES": "Commodities", "SETFGOLD": "Commodities",
    "TATAGOLD": "Commodities", "SILVERCASE": "Commodities", "NETFSILVER": "Commodities",
}

DOWNLOAD_DEADLINE_SECONDS = 30
_downloads = ThreadPoolExecutor(max_workers=8, thread_name_prefix="prices")
_memory: dict[str, tuple[float, object]] = {}


def to_ticker(symbol: str) -> str:
    symbol = symbol.strip().upper()
    return symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.NS"


def normalise_holdings(raw: list[dict] | None) -> tuple[list[dict], str]:
    """Holdings as {ticker, name, units, buy_price, sector} and where they came from.

    Rows without a tradable symbol or a quantity are auto-resolved where possible.
    With nothing usable, the sample portfolio stands in and the source says so.
    """
    from app.portfolio.extractor import resolve_symbol
    from app.tools import sectors

    holdings = []
    for row in raw or []:
        raw_sym = (row.get("symbol") or "").strip()
        raw_name = (row.get("name") or "").strip()
        raw_isin = (row.get("isin") or "").strip()

        # Units alias (supports 'units' or 'shares')
        units = row.get("units") if row.get("units") is not None else row.get("shares")
        if units is None or float(units) <= 0:
            continue

        row_type = (row.get("type") or "STOCK").upper()
        if row_type not in ("STOCK", "ETF", "EQUITY", "COMMODITY"):
            continue

        # Buy price alias (supports 'buy_price' or 'avg_price' or 'cost_price')
        buy_price = row.get("buy_price") if row.get("buy_price") is not None else row.get("avg_price")

        if not raw_sym or raw_sym.upper().startswith("IN"):
            resolved = resolve_symbol(raw_name, raw_sym)
            if resolved:
                raw_sym = resolved

        options = resolver.candidates(raw_sym, raw_isin, raw_name)
        if not options:
            continue
        ticker = options[0]

        holdings.append(
            {
                "ticker": ticker,
                "name": raw_name or raw_sym or ticker,
                "units": float(units),
                "buy_price": float(buy_price) if buy_price is not None else None,
                "sector": sectors.known(ticker) or sectors.UNKNOWN,
            }
        )
    if holdings:
        return holdings, "user"

    sample = json.loads(SAMPLE_PORTFOLIO.read_text())["holdings"]
    return [
        {
            "ticker": h["ticker"],
            "name": h["name"],
            "units": float(h["shares"]),
            "buy_price": h["avg_price"],
            "sector": h["sector"],
        }
        for h in sample
    ], "sample"


def _fetch(tickers: list[str], **kwargs) -> pd.DataFrame:
    closes = yf.download(
        tickers, progress=False, auto_adjust=True, timeout=15, **kwargs
    )["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    return closes.dropna(how="all")


def _download(tickers: list[str], **kwargs) -> pd.DataFrame:
    """Closing prices from Yahoo Finance, abandoned after a deadline. The
    source sometimes never answers, and a caller must not wait on it for ever:
    a TimeoutError here is handled like any other failed download."""
    return _downloads.submit(_fetch, tickers, **kwargs).result(timeout=DOWNLOAD_DEADLINE_SECONDS)


def _columns() -> dict[str, tuple[float, pd.Series]]:
    """Each ticker's downloaded closes with the time they arrived."""
    return _memory.setdefault("columns", {})


def get_history(tickers: list[str]) -> tuple[pd.DataFrame | None, str]:
    """Daily adjusted closes since 2013 for the tickers plus the Nifty.

    Prices are kept per ticker, so a request only downloads the tickers no
    earlier request fetched recently. Returns (closes, source) where source is
    "live", "cache" (saved copy used because the live fetch failed) or "unavailable".
    """
    wanted = sorted(set(tickers) | {NIFTY})
    key = hashlib.sha1(",".join(wanted).encode()).hexdigest()[:16]
    hit = _memory.get(key)
    if hit and time.time() - hit[0] < HISTORY_TTL_SECONDS:
        return hit[1], "live"

    cache_file = CACHE_DIR / f"history_{key}.csv"
    started = time.perf_counter()
    columns = _columns()
    need = [t for t in wanted if t not in columns or time.time() - columns[t][0] >= HISTORY_TTL_SECONDS]
    try:
        if need:
            fetched = _download(need, start=HISTORY_START)
            # On a poor connection a batch download can come back with some
            # tickers blank. Ask once more for just those before accepting it.
            blank = [t for t in need if t not in fetched.columns or fetched[t].notna().sum() == 0]
            if blank and len(blank) < len(need):
                retry = _download(blank, start=HISTORY_START)
                for ticker in blank:
                    if ticker in retry.columns and retry[ticker].notna().sum() > 0:
                        fetched[ticker] = retry[ticker]
            now = time.time()
            for ticker in need:
                if ticker in fetched.columns and fetched[ticker].notna().sum() > 0:
                    columns[ticker] = (now, fetched[ticker].dropna())
        if NIFTY not in columns:
            raise ValueError("price history is missing the index")
        closes = pd.DataFrame({t: columns[t][1] for t in wanted if t in columns})
        # A ticker with no prices stays in the frame as an empty column, as a download would return it.
        closes = closes.reindex(columns=wanted).sort_index().ffill()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        closes.to_csv(cache_file)
        _memory[key] = (time.time(), closes)
        if need:
            health.record("history", True, ms=(time.perf_counter() - started) * 1000, items=len(need),
                          detail=f"closes to {closes.index[-1].date()}")
        return closes, "live"
    except Exception as exc:
        if cache_file.exists():
            closes = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            health.record("history", False, detail=f"live fetch failed, saved copy to {closes.index[-1].date()} in use")
            return closes, "cache"
        health.record("history", False, detail=f"{type(exc).__name__}: no price history")
        return None, "unavailable"


def get_cross_assets() -> pd.DataFrame | None:
    """Daily closes since 2013 for crude, gas, gold, the rupee and India VIX. Gaps are left
    as they are, so each column can be read on its own trading calendar.
    None when neither the live source nor a saved copy is available."""
    hit = _memory.get("cross_assets")
    if hit and time.time() - hit[0] < CROSS_ASSET_TTL_SECONDS:
        return hit[1]
    cache_file = CACHE_DIR / "cross_assets.csv"
    try:
        closes = _download([*CROSS_ASSETS, INDIA_VIX], start=HISTORY_START).sort_index()
        if closes.empty:
            raise ValueError("no cross-asset history")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        closes.to_csv(cache_file)
        _memory["cross_assets"] = (time.time(), closes)
        return closes
    except Exception:
        if cache_file.exists():
            return pd.read_csv(cache_file, index_col=0, parse_dates=True)
        return None


def get_macro() -> tuple[list[dict], str | None]:
    """Latest level and one-month change for each macro series."""
    hit = _memory.get("macro")
    if hit and time.time() - hit[0] < MACRO_TTL_SECONDS:
        return hit[1]
    started = time.perf_counter()
    try:
        closes = _download(list(MACRO_SERIES), period="3mo").ffill()
    except Exception as exc:
        health.record("macro", False, detail=type(exc).__name__)
        return [], None

    indicators = []
    for ticker, label in MACRO_SERIES.items():
        if ticker not in closes.columns:
            continue
        series = closes[ticker].dropna()
        if len(series) < 22:
            continue
        indicators.append(
            {
                "ticker": ticker,
                "label": label,
                "value": round(float(series.iloc[-1]), 2),
                "change_1m_pct": round(float(series.iloc[-1] / series.iloc[-22] - 1) * 100, 1),
            }
        )
    result = (indicators, datetime.now(timezone.utc).isoformat() if indicators else None)
    health.record("macro", bool(indicators), ms=(time.perf_counter() - started) * 1000, items=len(indicators),
                  detail=f"{len(indicators)} of {len(MACRO_SERIES)} series")
    if indicators:
        _memory["macro"] = (time.time(), result)
    return result


INDEX_SERIES = {NIFTY: ("nifty", "Nifty 50"), "^BSESN": ("sensex", "BSE Sensex")}
INDEX_TTL_SECONDS = 300
TREND_THRESHOLD_PCT = 2.0


def get_indices() -> tuple[list[dict], str | None]:
    """Latest close, change on the day and change over one month for the
    headline indices, with the date of that close. Empty when unavailable."""
    hit = _memory.get("indices")
    if hit and time.time() - hit[0] < INDEX_TTL_SECONDS:
        return hit[1]
    started = time.perf_counter()
    try:
        closes = _download(list(INDEX_SERIES), period="3mo")
    except Exception as exc:
        health.record("indices", False, detail=type(exc).__name__)
        return [], None

    indices, last_date = [], None
    for ticker, (key, label) in INDEX_SERIES.items():
        if ticker not in closes.columns:
            continue
        series = closes[ticker].dropna()
        if len(series) < 22:
            continue
        indices.append(
            {
                "key": key,
                "label": label,
                "value": round(float(series.iloc[-1]), 2),
                "change_1d_pct": round(float(series.iloc[-1] / series.iloc[-2] - 1) * 100, 2),
                "change_1m_pct": round(float(series.iloc[-1] / series.iloc[-22] - 1) * 100, 2),
            }
        )
        last_date = series.index[-1].date().isoformat()
    result = (indices, last_date)
    health.record("indices", bool(indices), ms=(time.perf_counter() - started) * 1000, items=len(indices),
                  detail=f"index close of {last_date}" if last_date else "no index data")
    if indices:
        _memory["indices"] = (time.time(), result)
    return result


def market_trend(indices: list[dict]) -> dict | None:
    """A plain label for the Nifty's one-month move. None without Nifty data."""
    nifty = next((i for i in indices if i["key"] == "nifty"), None)
    if nifty is None:
        return None
    change = nifty["change_1m_pct"]
    if change >= TREND_THRESHOLD_PCT:
        label = "Uptrend"
    elif change <= -TREND_THRESHOLD_PCT:
        label = "Downtrend"
    else:
        label = "Sideways"
    return {"label": label, "detail": f"Nifty 50 {change:+.1f}% over one month"}


def warm_up_in_background() -> None:
    """Fetch the sample portfolio's price history and the cross-asset history
    once at startup, so the first question does not wait for 13 years of prices."""
    def warm() -> None:
        try:
            sample = json.loads(SAMPLE_PORTFOLIO.read_text())["holdings"]
            get_history([h["ticker"] for h in sample])
            get_cross_assets()
        except Exception:
            pass  # the first request will simply fetch what it needs

    threading.Thread(target=warm, daemon=True, name="warm-prices").start()
