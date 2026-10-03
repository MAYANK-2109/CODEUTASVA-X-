"""The sector of a stock. Symbols in the map in app.tools.market are answered
from it; any other is looked up on Yahoo Finance once and remembered on disk."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import yfinance as yf

from app.tools.market import SECTORS

CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "sectors.json"
LOOKUP_SECONDS = 8
MISS_TTL_SECONDS = 600
UNKNOWN = "Other"

# Yahoo's industry names, most specific first, then its broad sectors.
INDUSTRY_RULES = [
    ("steel", "Metals"), ("aluminum", "Metals"), ("copper", "Metals"), ("metal", "Metals"), ("mining", "Metals"),
    ("bank", "Banking"), ("insurance", "Financials"), ("credit services", "Financials"),
    ("capital markets", "Financials"), ("asset management", "Financials"), ("financial", "Financials"),
    ("oil & gas", "Energy"), ("coal", "Energy"),
    ("utilities", "Utilities"), ("solar", "Utilities"),
    ("information technology", "IT"), ("software", "IT"),
    ("auto", "Auto"), ("airlines", "Airlines"), ("airports", "Airlines"),
    ("drug", "Pharma"), ("biotech", "Pharma"), ("medical", "Pharma"), ("health", "Pharma"),
    ("telecom", "Telecom"), ("real estate", "Real Estate"),
    ("building materials", "Cement"), ("chemicals", "Chemicals"), ("agricultural inputs", "Chemicals"),
    ("aerospace & defense", "Defence"),
    ("engineering & construction", "Infrastructure"), ("infrastructure", "Infrastructure"),
    ("railroads", "Infrastructure"), ("marine shipping", "Infrastructure"),
    ("machinery", "Capital Goods"), ("electrical equipment", "Capital Goods"),
    ("packaged foods", "FMCG"), ("household", "FMCG"), ("beverages", "FMCG"), ("tobacco", "FMCG"),
    ("confectioners", "FMCG"),
    ("travel", "Travel"), ("lodging", "Travel"), ("restaurants", "Travel"), ("leisure", "Travel"),
    ("retail", "Consumer"), ("apparel", "Consumer"), ("luxury", "Consumer"), ("furnishings", "Consumer"),
]
SECTOR_FALLBACK = {
    "Basic Materials": "Metals", "Communication Services": "Telecom", "Consumer Cyclical": "Consumer",
    "Consumer Defensive": "FMCG", "Energy": "Energy", "Financial Services": "Financials", "Healthcare": "Pharma",
    "Industrials": "Infrastructure", "Real Estate": "Real Estate", "Technology": "IT", "Utilities": "Utilities",
}

_lock = threading.Lock()
_found: dict[str, str] | None = None
_missed: dict[str, float] = {}


def _cache() -> dict[str, str]:
    global _found
    with _lock:
        if _found is None:
            try:
                _found = json.loads(CACHE_FILE.read_text())
            except (OSError, ValueError):
                _found = {}
        return _found


def classify(yahoo_sector: str | None, industry: str | None) -> str | None:
    """Our sector name for what Yahoo reports. None when it reports nothing."""
    text = (industry or "").lower()
    for needle, sector in INDUSTRY_RULES:
        if needle in text:
            return sector
    return SECTOR_FALLBACK.get(yahoo_sector or "")


def known(ticker: str) -> str | None:
    """The sector if it is already known, without asking anyone."""
    return SECTORS.get(ticker.split(".")[0]) or _cache().get(ticker)


def _lookup(ticker: str) -> None:
    try:
        info = yf.Ticker(ticker).info
        sector = classify(info.get("sector"), info.get("industry"))
    except Exception:
        sector = None
    with _lock:
        if sector:
            _found[ticker] = sector
        else:
            _missed[ticker] = time.time()


def resolve(tickers: list[str]) -> dict[str, str]:
    """Sector per ticker, looking up the unknown ones together with a deadline.
    A ticker nobody can place is "Other"; it is asked about again later."""
    cache = _cache()
    now = time.time()
    due = [t for t in dict.fromkeys(tickers)
           if known(t) is None and now - _missed.get(t, 0) > MISS_TTL_SECONDS]
    if due:
        pool = ThreadPoolExecutor(max_workers=min(8, len(due)))
        wait([pool.submit(_lookup, ticker) for ticker in due], timeout=LOOKUP_SECONDS)
        pool.shutdown(wait=False)
        with _lock:
            try:
                CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                CACHE_FILE.write_text(json.dumps(cache, indent=0, sort_keys=True))
            except OSError:
                pass  # the lookups still stand for this run
    return {t: known(t) or UNKNOWN for t in tickers}
