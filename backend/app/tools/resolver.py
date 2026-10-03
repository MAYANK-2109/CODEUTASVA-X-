"""Find the market ticker for a holding from whatever a broker file gave us:
a symbol, an ISIN, or only a company name.

ISINs and exact company names are looked up in the NSE's own securities list
(data/nse_isins.csv); anything else goes to a search service.
Refresh the list with:  uv run python -m app.tools.resolver --refresh
"""

import csv
import io
import json
import re
import threading
import time
from pathlib import Path

import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_FILE = DATA_DIR / "cache" / "tickers.json"
NSE_LIST_FILE = DATA_DIR / "nse_isins.csv"
# Published by the exchange: every listed equity and every listed ETF.
NSE_SOURCES = {
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv": ("SYMBOL", "ISIN NUMBER", "NAME OF COMPANY"),
    "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv": ("Symbol", "ISINNumber", "SecurityName"),
}
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
_nse: tuple[dict[str, str], dict[str, str]] | None = None


def _nse_list() -> tuple[dict[str, str], dict[str, str]]:
    """(ISIN -> ticker, cleaned lower-case name -> ticker) from the exchange's list."""
    global _nse
    if _nse is None:
        by_isin: dict[str, str] = {}
        by_name: dict[str, str] = {}
        try:
            with NSE_LIST_FILE.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    ticker = f'{row["symbol"]}.NS'
                    by_isin[row["isin"]] = ticker
                    by_name.setdefault(clean_name(row["name"]).lower(), ticker)
        except OSError:
            pass  # without the list, every lookup goes to the search service
        _nse = (by_isin, by_name)
    return _nse


def refresh_nse_list() -> int:
    """Download the exchange's lists and rewrite data/nse_isins.csv. Returns the row count."""
    import httpx

    rows: dict[str, tuple[str, str]] = {}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept": "text/csv,*/*"}
    for url, (symbol_col, isin_col, name_col) in NSE_SOURCES.items():
        response = httpx.get(url, headers=headers, timeout=40, follow_redirects=True)
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig", errors="replace")))
        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            isin, symbol = row.get(isin_col, "").upper(), row.get(symbol_col, "")
            if ISIN.match(isin) and symbol:
                rows[isin] = (symbol, row.get(name_col, ""))
    if len(rows) < 1000:
        raise RuntimeError(f"Only {len(rows)} securities downloaded; keeping the existing list")
    with NSE_LIST_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["isin", "symbol", "name"])
        for isin in sorted(rows):
            writer.writerow([isin, *rows[isin]])
    global _nse
    _nse = None
    return len(rows)


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
    by_isin, by_name = _nse_list()
    known = by_isin.get(query.upper()) or by_name.get(clean_name(query).lower()) or _load().get(query)
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


if __name__ == "__main__":
    import sys

    if "--refresh" in sys.argv:
        print(f"Saved {refresh_nse_list()} securities to {NSE_LIST_FILE}")
    isins, names = _nse_list()
    print(f"{len(isins)} ISINs and {len(names)} names in the exchange list")
