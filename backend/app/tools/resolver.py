"""Find the market ticker for a holding from whatever a broker file gave us:
a symbol, an ISIN, or only a company name."""

import json
import re
import threading
import time
from pathlib import Path

import yfinance as yf

CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "tickers.json"
EXCHANGE_RANK = {"NSI": 0, "BSE": 1}  # prefer the NSE listing
MISS_TTL_SECONDS = 600
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
NAME_NOISE = re.compile(
    r"\b(limited|ltd|equity shares?|eq|ord(inary)?|shares?|fully paid|new|fv\s*\d+|rs\.?\s*\d+)\b|[^a-z0-9 ]",
    re.IGNORECASE,
)

_lock = threading.Lock()
_found: dict[str, str] | None = None
_missed: dict[str, float] = {}


def _load() -> dict[str, str]:
    global _found
    if _found is None:
        try:
            _found = json.loads(CACHE_FILE.read_text())
        except (OSError, ValueError):
            _found = {}
    return _found


def _remember(query: str, ticker: str) -> None:
    with _lock:
        _load()[query] = ticker
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(_found, indent=0, sort_keys=True))
        except OSError:
            pass  # the lookup still works without the saved copy


def search(query: str) -> str | None:
    """Best Indian-listed match for an ISIN or a company name, or None."""
    query = query.strip()
    if not query:
        return None
    known = _load().get(query)
    if known:
        return known
    if time.time() - _missed.get(query, 0) < MISS_TTL_SECONDS:
        return None
    try:
        quotes = yf.Search(query, max_results=8, news_count=0).quotes
    except Exception:
        return None  # a failed lookup is not a miss; try again next time
    listed = [q for q in quotes if q.get("exchange") in EXCHANGE_RANK and q.get("symbol")]
    if not listed:
        _missed[query] = time.time()
        return None
    best = min(listed, key=lambda q: EXCHANGE_RANK[q["exchange"]])
    _remember(query, best["symbol"])
    return best["symbol"]


def clean_symbol(symbol: str) -> str:
    """'nse:reliance-eq' -> 'RELIANCE'. A full ticker such as 'RELIANCE.BO' is kept."""
    text = re.sub(r"^(NSE|BSE)\s*[:\-]\s*", "", symbol.strip().upper())
    text = re.sub(r"[-\s](EQ|BE|BZ|SM|ST)$", "", text)
    return text.replace(" ", "")


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", NAME_NOISE.sub(" ", name)).strip()


def search_name(name: str) -> str | None:
    """Search by company name, dropping trailing words that broker files
    abbreviate ("Inds", "Corp") until something matches. Never fewer than two
    words, so a single common word cannot pick an unrelated company."""
    words = clean_name(name).split()
    for length in range(len(words), max(0, len(words) - 3), -1):
        if length < 2 and len(words) > 1:
            break
        ticker = search(" ".join(words[:length]))
        if ticker:
            return ticker
    return None


def candidates(symbol: str = "", isin: str = "", name: str = "") -> list[str]:
    """Tickers to try for a holding, most trustworthy first, without duplicates."""
    found: list[str] = []
    cleaned = clean_symbol(symbol or "")
    if ISIN.match(cleaned):  # some files put the ISIN in the symbol column
        isin, cleaned = isin or cleaned, ""
    if cleaned.isdigit():  # a BSE scrip code; the price source does not accept those
        cleaned = ""
    if cleaned:
        if "." in cleaned or cleaned.startswith("^"):
            found.append(cleaned)
        else:
            found += [f"{cleaned}.NS", f"{cleaned}.BO"]
    if isin and ISIN.match(isin.strip().upper()):
        found.append(search(isin.strip().upper()))
    if not found and name:
        found.append(search_name(name))
    return list(dict.fromkeys(t for t in found if t))
