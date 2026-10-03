import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
SAMPLE_PORTFOLIO = DATA_DIR / "portfolio.json"

NIFTY = "^NSEI"
HISTORY_START = "2013-01-01"
HISTORY_TTL_SECONDS = 6 * 3600
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
}

_memory: dict[str, tuple[float, object]] = {}


def to_ticker(symbol: str) -> str:
    symbol = symbol.strip().upper()
    return symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.NS"


def normalise_holdings(raw: list[dict] | None) -> tuple[list[dict], str]:
    """Holdings as {ticker, name, units, buy_price, sector} and where they came from.

    Rows without a tradable symbol or a quantity cannot be priced, so they are
    dropped. With nothing usable, the sample portfolio stands in and the source
    says so.
    """
    holdings = []
    for row in raw or []:
        symbol = (row.get("symbol") or "").strip()
        units = row.get("units")
        if not symbol or not units or units <= 0:
            continue
        if (row.get("type") or "STOCK").upper() not in ("STOCK", "ETF"):
            continue
        ticker = to_ticker(symbol)
        holdings.append(
            {
                "ticker": ticker,
                "name": row.get("name") or symbol,
                "units": float(units),
                "buy_price": row.get("buy_price"),
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
        if closes.empty or NIFTY not in closes.columns:
            raise ValueError("empty price history")
        closes = closes.ffill()
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
