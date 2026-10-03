import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from app.tools import resolver

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
SAMPLE_PORTFOLIO = DATA_DIR / "portfolio.json"

NIFTY = "^NSEI"
HISTORY_START = "2013-01-01"
# Short enough that an alert on today's move is not hours stale.
HISTORY_TTL_SECONDS = 15 * 60
MACRO_TTL_SECONDS = 600

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
                "sector": SECTORS.get(ticker.split(".")[0], "Other"),
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


def _download(tickers: list[str], **kwargs) -> pd.DataFrame:
    closes = yf.download(
        tickers, progress=False, auto_adjust=True, timeout=15, **kwargs
    )["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])
    return closes.dropna(how="all")


def get_history(tickers: list[str]) -> tuple[pd.DataFrame | None, str]:
    """Daily adjusted closes since 2013 for the tickers plus the Nifty.

    Returns (closes, source) where source is "live", "cache" (saved copy used
    because the live fetch failed) or "unavailable".
    """
    wanted = sorted(set(tickers) | {NIFTY})
    key = hashlib.sha1(",".join(wanted).encode()).hexdigest()[:16]
    hit = _memory.get(key)
    if hit and time.time() - hit[0] < HISTORY_TTL_SECONDS:
        return hit[1], "live"

    cache_file = CACHE_DIR / f"history_{key}.csv"
    try:
        closes = _download(wanted, start=HISTORY_START)
        # On a poor connection a batch download can come back with some
        # tickers blank. Ask once more for just those before accepting it.
        blank = [t for t in wanted if t not in closes.columns or closes[t].notna().sum() == 0]
        if blank and len(blank) < len(wanted):
            retry = _download(blank, start=HISTORY_START)
            for ticker in blank:
                if ticker in retry.columns and retry[ticker].notna().sum() > 0:
                    closes[ticker] = retry[ticker]
        if closes.empty or NIFTY not in closes.columns or closes[NIFTY].notna().sum() == 0:
            raise ValueError("price history is missing the index")
        closes = closes.sort_index().ffill()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        closes.to_csv(cache_file)
        _memory[key] = (time.time(), closes)
        return closes, "live"
    except Exception:
        if cache_file.exists():
            closes = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            return closes, "cache"
        return None, "unavailable"


def get_macro() -> tuple[list[dict], str | None]:
    """Latest level and one-month change for each macro series."""
    hit = _memory.get("macro")
    if hit and time.time() - hit[0] < MACRO_TTL_SECONDS:
        return hit[1]
    try:
        closes = _download(list(MACRO_SERIES), period="3mo").ffill()
    except Exception:
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
    try:
        closes = _download(list(INDEX_SERIES), period="3mo")
    except Exception:
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
