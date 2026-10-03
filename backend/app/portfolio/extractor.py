"""
Portfolio holdings extractor — supports PDF and Excel files.

PDF formats:
  - Zerodha Contract Note / P&L Statement
  - CDSL / NSDL Consolidated Account Statement (CAS)
  - Groww Statement / Holdings Export
  - Generic tabular PDFs (best-effort)

Excel formats (.xlsx / .xls):
  - Any spreadsheet with column headers containing
    name / symbol / qty / price / ltp / date keywords
  - Zerodha Holdings export (.xlsx)
  - Groww Holdings / Transaction export (.xlsx)
  - AngelOne, Upstox, ICICI Direct, Kotak, HDFC Sec exports
  - Generic broker exports

Returns a list of dicts with keys:
  name, symbol, isin, type (STOCK/MF/ETF/BOND),
  buy_date, units, buy_price, current_price
"""

import re
from datetime import date, datetime
from io import BytesIO
from typing import Any

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

try:
    import xlrd
    _HAS_XLRD = True
except ImportError:
    _HAS_XLRD = False


# ---------------------------------------------------------------------------
# Company Name -> Known NSE Ticker Mapping (for when symbol column is absent)
# ---------------------------------------------------------------------------

COMPANY_TICKER_MAP = {
    "reliance": "RELIANCE",
    "tata consultancy": "TCS",
    "tcs": "TCS",
    "infosys": "INFY",
    "infy": "INFY",
    "hdfc bank": "HDFCBANK",
    "hdfc": "HDFCBANK",
    "icici bank": "ICICIBANK",
    "icici": "ICICIBANK",
    "state bank": "SBIN",
    "sbi": "SBIN",
    "bharti airtel": "BHARTIARTL",
    "airtel": "BHARTIARTL",
    "itc": "ITC",
    "kotak": "KOTAKBANK",
    "larsen": "LT",
    "l&t": "LT",
    "hindustan unilever": "HINDUNILVR",
    "hul": "HINDUNILVR",
    "axis bank": "AXISBANK",
    "tata motors": "TATAMOTORS",
    "tata steel": "TATASTEEL",
    "maruti": "MARUTI",
    "sun pharma": "SUNPHARMA",
    "ntpc": "NTPC",
    "ongc": "ONGC",
    "power grid": "POWERGRID",
    "titan": "TITAN",
    "bajaj finance": "BAJFINANCE",
    "bajaj finserv": "BAJAJFINSV",
    "adani enterprises": "ADANIENT",
    "adani ports": "ADANIPORTS",
    "adani green": "ADANIGREEN",
    "adani power": "ADANIPOWER",
    "wipro": "WIPRO",
    "hcl tech": "HCLTECH",
    "asian paints": "ASIANPAINT",
    "ultratech": "ULTRACEMCO",
    "coal india": "COALINDIA",
    "interglobe": "INDIGO",
    "indigo": "INDIGO",
    "zomato": "ZOMATO",
    "swiggy": "SWIGGY",
    "paytm": "PAYTM",
    "one97": "PAYTM",
    "jio financial": "JIOFIN",
    "dr reddy": "DRREDDY",
    "cipla": "CIPLA",
    "divis": "DIVISLAB",
    "grasim": "GRASIM",
    "hindalco": "HINDALCO",
    "jsw steel": "JSWSTEEL",
    "eicher": "EICHERMOT",
    "hero moto": "HEROMOTOCO",
    "britannia": "BRITANNIA",
    "nestle": "NESTLEIND",
    "dabur": "DABUR",
    "tata consumer": "TATACONSUM",
    "tech mahindra": "TECHM",
    "ltimindtree": "LTIM",
    "vedanta": "VEDL",
    "dlf": "DLF",
}


def resolve_symbol(name: str, symbol: str = "") -> str:
    """Infer NSE ticker symbol from company name or existing symbol string."""
    if symbol and len(symbol.strip()) >= 2 and not symbol.strip().upper().startswith("IN"):
        return symbol.strip().upper()

    clean_name = (name or "").strip()
    if not clean_name:
        return ""

    # Check for ticker explicitly enclosed in parentheses: e.g. "Infosys (INFY)"
    m = re.search(r"\(([A-Z0-9\-]{2,12})\)", clean_name)
    if m:
        return m.group(1).upper()

    lower = clean_name.lower()

    # Do not treat generic mutual fund/bond words as tickers
    if any(k in lower for k in ("fund", "scheme", "direct", "growth", "sip", "mf", "bond", "cash")):
        return ""

    for key, ticker in COMPANY_TICKER_MAP.items():
        if key in lower:
            return ticker

    # If the name is already a single short alphanumeric uppercase ticker, e.g. "INFY", "TCS", "RELIANCE"
    token = re.sub(r"[^A-Za-z0-9\-]", "", clean_name)
    if 2 <= len(token) <= 12 and token.isupper() and not token.isdigit():
        return token

    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_holdings(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Unified dispatcher: routes to PDF or Excel extractor based on filename.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        return extract_holdings_from_pdf(file_bytes)
    if ext in ("xlsx", "xls"):
        return extract_holdings_from_excel(file_bytes, ext)
    raise ValueError(f"Unsupported file type: .{ext}. Only PDF, XLSX, and XLS are accepted.")


def extract_holdings_from_pdf(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Extract holding rows from an uploaded PDF file (raw bytes)."""
    if not _HAS_PDFPLUMBER:
        raise RuntimeError("pdfplumber is not installed. Run `pip install pdfplumber`.")

    tables = []
    full_text_pages = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            full_text_pages.append(t)
            page_tables = page.extract_tables()
            if page_tables:
                tables.extend(page_tables)

    text = "\n".join(full_text_pages)
    holdings: list[dict[str, Any]] = []

    # 1. Try structured table extraction
    if tables:
        holdings = _extract_from_tables(tables)

    # 2. Fall back to text parsing if no table holdings found
    if not holdings:
        holdings = _extract_from_text(text)

    return _deduplicate(holdings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_number(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val == val else None  # skip NaN
    s = str(val).strip()
    if not s or s.lower() in ("-", "--", "na", "null", "none"):
        return None
    # Handle negative numbers in accounting brackets: (100.5) -> -100.5
    is_neg = False
    if s.startswith("(") and s.endswith(")"):
        is_neg = True
        s = s[1:-1]
    # Remove currency symbols (INR, Rs, ₹, $) and commas
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if not s or s == "." or s == "-":
        return None
    try:
        n = float(s)
        return -n if is_neg else n
    except ValueError:
        return None


def _clean_date(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s or s.lower() in ("-", "--", "na", "null"):
        return None
    # Normalise DD-MM-YYYY, DD/MM/YYYY → YYYY-MM-DD
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        day = m.group(1).zfill(2)
        month = m.group(2).zfill(2)
        year = m.group(3)
        return f"{year}-{month}-{day}"
    # Normalise YYYY-MM-DD or YYYY/MM/DD
    m2 = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", s)
    if m2:
        year = m2.group(1)
        month = m2.group(2).zfill(2)
        day = m2.group(3).zfill(2)
        return f"{year}-{month}-{day}"
    return s.split(" ")[0] if " " in s else s


def _infer_type(name: str, isin: str = "") -> str:
    n = name.upper()
    if any(k in n for k in ("FUND", "SCHEME", "DIRECT", "GROWTH", "DIVIDEND", "SIP", "MF", "MUTUAL")):
        return "MF"
    if "ETF" in n or "BEES" in n:
        return "ETF"
    if any(k in n for k in ("BOND", "NCD", "DEBENTURE", "GSEC", "T-BILL", "GOI")):
        return "BOND"
    # ISINs starting INF are fund units; held in a demat account they are ETFs.
    if isin.upper().startswith("INF"):
        return "ETF"
    return "STOCK"


# ---------------------------------------------------------------------------
# Table-based extraction & Smart Header Detection
# ---------------------------------------------------------------------------

KEYWORDS: dict[str, list[str]] = {
    "name": [
        "company name", "scrip name", "stock name", "security name",
        "instrument name", "scheme name", "particulars", "description",
        "asset name", "company", "scrip", "security", "scheme", "stock",
        "instrument", "name"
    ],
    "symbol": [
        "tradingsymbol", "ticker", "scrip code", "stock symbol",
        "symbol", "isin", "code"
    ],
    "buy_date": [
        "trade date", "purchase date", "buy date", "transaction date",
        "txn date", "date"
    ],
    "units": [
        "quantity", "shares", "units", "holding", "balance",
        "total qty", "net qty", "avail qty", "available qty", "volume", "qty"
    ],
    "buy_price": [
        "avg. price", "avg price", "average price", "avg cost", "average cost",
        "buy price", "purchase price", "buy rate", "purchase rate", "cost price",
        "unit cost", "rate", "nav", "cost", "price"
    ],
    "current_price": [
        "ltp", "last price", "current price", "market price", "close price",
        "closing price", "cmp", "live price", "cur price", "current rate", "market rate"
    ],
    "invested_value": [
        "invested value", "invested amt", "invested amount", "total cost",
        "cost value", "buy value", "invested"
    ],
    "current_value": [
        "current value", "market value", "present value", "cur value", "total value",
        "closing value"
    ],
}


def _find_header_row(rows: list[list]) -> tuple[int, dict[str, int]]:
    """
    Score rows across the top of the table to find the TRUE table header row.
    Never gets fooled by top metadata lines like 'Client Name: ...' or 'Account No: ...'.
    """
    best_idx = -1
    best_score = 0
    best_map: dict[str, int] = {}

    for idx, row in enumerate(rows[:30]):
        if not row:
            continue
        # PDF table cells wrap ("Closing\nprice"), so collapse whitespace first.
        row_str = [" ".join(str(c).lower().split()) if c is not None else "" for c in row]
        if not any(row_str):
            continue

        col_map: dict[str, int] = {}
        for field, kws in KEYWORDS.items():
            for ci, cell in enumerate(row_str):
                if not cell:
                    continue
                # exact or substring match
                for kw in kws:
                    if kw == cell or kw in cell:
                        if field not in col_map:
                            col_map[field] = ci
                        break

        # A valid table header MUST identify the asset AND contain at least one numeric field
        has_identifier = ("name" in col_map or "symbol" in col_map)
        has_numeric = (
            "units" in col_map
            or "buy_price" in col_map
            or "current_price" in col_map
            or "invested_value" in col_map
            or "current_value" in col_map
        )

        score = len(col_map)
        if has_identifier and has_numeric and score > best_score:
            best_score = score
            best_idx = idx
            best_map = col_map

    return best_idx, best_map


def _is_ignorable_row(name: str) -> bool:
    """Filter out total, subtotal, disclaimer or empty rows."""
    if not name or len(name.strip()) < 2:
        return True
    n = name.strip().lower()
    if re.match(r"^[\d,.\s\-%]+$", n):
        return True
    if any(n.startswith(prefix) for prefix in (
        "total", "grand total", "subtotal", "sub total", "portfolio total",
        "disclaimer", "summary", "account value", "cash balance", "page "
    )):
        return True
    return False


def _extract_from_tables(tables: list[list[list]]) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []

    for table in tables:
        if not table or len(table) < 2:
            continue
        header_idx, col_map = _find_header_row(table)
        if header_idx == -1:
            continue

        for row in table[header_idx + 1:]:
            if not row:
                continue

            def gcell(field: str) -> Any:
                idx = col_map.get(field)
                if idx is None or idx >= len(row):
                    return None
                return row[idx]

            raw_name = " ".join(str(gcell("name") or "").split())
            raw_sym = str(gcell("symbol") or "").strip()

            name = raw_name if raw_name else raw_sym
            if _is_ignorable_row(name):
                continue

            # Determine ISIN or symbol
            isin = ""
            if re.match(r"^IN[A-Z0-9]{10}$", raw_sym.upper()):
                isin = raw_sym.upper()
                symbol = ""
            elif re.match(r"^IN[A-Z0-9]{10}$", raw_name.upper()):
                isin = raw_name.upper()
                symbol = ""
            else:
                symbol = raw_sym

            # Auto resolve symbol if missing
            if not symbol and not isin:
                symbol = resolve_symbol(name)

            units = _clean_number(gcell("units"))
            buy_price = _clean_number(gcell("buy_price"))
            current_price = _clean_number(gcell("current_price"))
            invested_val = _clean_number(gcell("invested_value"))
            current_val = _clean_number(gcell("current_value"))

            # Derive buy_price if missing
            if buy_price is None and invested_val is not None and units and units > 0:
                buy_price = round(invested_val / units, 2)

            # Derive current_price if missing
            if current_price is None and current_val is not None and units and units > 0:
                current_price = round(current_val / units, 2)

            # Only add rows with valid quantity or price
            if units is None and buy_price is None and current_price is None:
                continue

            holdings.append({
                "name": name,
                "symbol": symbol,
                "isin": isin,
                "type": _infer_type(name, isin),
                "buy_date": _clean_date(gcell("buy_date")),
                "units": units,
                "buy_price": buy_price,
                "current_price": current_price,
            })

    return holdings


# ---------------------------------------------------------------------------
# Text-based extraction (Fallback for unstructured PDFs)
# ---------------------------------------------------------------------------

_CDSL_PATTERN = re.compile(
    r"(?P<isin>IN[A-Z0-9]{10})\s+"
    r"(?P<name>[A-Za-z0-9\s\-&\.\(\)]+?)\s+"
    r"(?P<units>[\d,]+\.?\d*)\s+"
    r"(?P<price>[\d,]+\.?\d*)",
    re.IGNORECASE,
)

_ZERODHA_PATTERN = re.compile(
    r"(?P<date>\d{2}[/-]\d{2}[/-]\d{4})\s+"
    r"(?P<name>[A-Za-z0-9\s\-&\.\(\)]+?)\s+"
    r"(?P<qty>[\d,]+)\s+"
    r"(?P<price>[\d,]+\.?\d*)",
    re.IGNORECASE,
)

_GROWW_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9\s\-&\.]+?)\s+"
    r"(?P<qty>[\d,]+(?:\.\d+)?)\s+"
    r"(?:shares|units)?\s+"
    r"(?:avg\s+)?₹?(?P<buy_price>[\d,]+\.?\d*)\s+"
    r"(?:ltp\s+)?₹?(?P<ltp>[\d,]+\.?\d*)",
    re.IGNORECASE,
)


_NAME_THEN_ISIN = re.compile(r"^(?P<name>.+?)\s+(?P<isin>IN[A-Z0-9]{10})\s+(?P<rest>[-\d,.\s()₹]+)$")


def _extract_isin_rows(text: str) -> list[dict[str, Any]]:
    """Statement lines of the form: name, ISIN, quantity, average price,
    buy value, closing price, ... (the layout of a broker holdings statement)."""
    holdings: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _NAME_THEN_ISIN.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        numbers = [_clean_number(tok) for tok in m.group("rest").split()]
        numbers = [n for n in numbers if n is not None]
        if _is_ignorable_row(name) or len(numbers) < 2:
            continue
        isin = m.group("isin").upper()
        holdings.append({
            "name": name,
            "symbol": "",
            "isin": isin,
            "type": _infer_type(name, isin),
            "buy_date": None,
            "units": numbers[0],
            "buy_price": numbers[1],
            "current_price": numbers[3] if len(numbers) >= 4 else None,
        })
    return holdings


def _extract_from_text(text: str) -> list[dict[str, Any]]:
    holdings = _extract_isin_rows(text)
    if holdings:
        return holdings

    # 1. Groww / Modern Broker text pattern (name + qty + avg price + ltp)
    for m in _GROWW_PATTERN.finditer(text):
        name = m.group("name").strip()
        if _is_ignorable_row(name):
            continue
        units = _clean_number(m.group("qty"))
        buy_p = _clean_number(m.group("buy_price"))
        ltp = _clean_number(m.group("ltp"))
        holdings.append({
            "name": name,
            "symbol": resolve_symbol(name),
            "isin": "",
            "type": _infer_type(name),
            "buy_date": None,
            "units": units,
            "buy_price": buy_p,
            "current_price": ltp,
        })

    if holdings:
        return holdings

    # 2. CDSL CAS pattern (ISIN-first)
    for m in _CDSL_PATTERN.finditer(text):
        name = m.group("name").strip()
        if _is_ignorable_row(name):
            continue
        holdings.append({
            "name": name,
            "symbol": resolve_symbol(name),
            "isin": m.group("isin").upper(),
            "type": _infer_type(name, m.group("isin")),
            "buy_date": None,
            "units": _clean_number(m.group("units")),
            "buy_price": _clean_number(m.group("price")),
            "current_price": None,
        })

    if holdings:
        return holdings

    # 3. Zerodha contract note / generic date-based rows
    for m in _ZERODHA_PATTERN.finditer(text):
        name = m.group("name").strip()
        if _is_ignorable_row(name):
            continue
        holdings.append({
            "name": name,
            "symbol": resolve_symbol(name),
            "isin": "",
            "type": _infer_type(name),
            "buy_date": _clean_date(m.group("date")),
            "units": _clean_number(m.group("qty")),
            "buy_price": _clean_number(m.group("price")),
            "current_price": None,
        })

    return holdings


# ---------------------------------------------------------------------------
# Excel extraction (.xlsx / .xls)
# ---------------------------------------------------------------------------

def extract_holdings_from_excel(file_bytes: bytes, ext: str) -> list[dict[str, Any]]:
    """Extract holdings from an Excel workbook (.xlsx or .xls)."""
    if ext == "xlsx":
        if not _HAS_OPENPYXL:
            raise RuntimeError("openpyxl is not installed. Run `pip install openpyxl`.")
        return _extract_xlsx(file_bytes)
    else:
        if not _HAS_XLRD:
            raise RuntimeError("xlrd is not installed. Run `pip install xlrd`.")
        return _extract_xls(file_bytes)


def _sheet_to_rows(ws_rows: list[list]) -> list[dict[str, Any]]:
    """
    Given sheet rows, detect header row using smart scoring and
    return normalized holding dicts with full data preservation.
    """
    if not ws_rows or len(ws_rows) < 2:
        return []

    header_idx, col_map = _find_header_row(ws_rows)
    if header_idx == -1:
        return []

    holdings: list[dict[str, Any]] = []
    for row in ws_rows[header_idx + 1:]:
        if not any(cell for cell in row):  # skip blank rows
            continue

        def gcell(field: str) -> Any:
            idx = col_map.get(field)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        raw_name = " ".join(str(gcell("name") or "").split())
        raw_sym = str(gcell("symbol") or "").strip()

        name = raw_name if raw_name else raw_sym
        if _is_ignorable_row(name):
            continue

        # Determine ISIN or symbol
        isin = ""
        if re.match(r"^IN[A-Z0-9]{10}$", raw_sym.upper()):
            isin = raw_sym.upper()
            symbol = ""
        elif re.match(r"^IN[A-Z0-9]{10}$", raw_name.upper()):
            isin = raw_name.upper()
            symbol = ""
        else:
            symbol = raw_sym

        # Auto resolve symbol if missing
        if not symbol and not isin:
            symbol = resolve_symbol(name)

        units = _clean_number(gcell("units"))
        buy_price = _clean_number(gcell("buy_price"))
        current_price = _clean_number(gcell("current_price"))
        invested_val = _clean_number(gcell("invested_value"))
        current_val = _clean_number(gcell("current_value"))

        # Derive buy_price if missing
        if buy_price is None and invested_val is not None and units and units > 0:
            buy_price = round(invested_val / units, 2)

        # Derive current_price if missing
        if current_price is None and current_val is not None and units and units > 0:
            current_price = round(current_val / units, 2)

        if units is None and buy_price is None and current_price is None:
            continue

        holdings.append({
            "name": name,
            "symbol": symbol,
            "isin": isin,
            "type": _infer_type(name, isin),
            "buy_date": _clean_date(gcell("buy_date")),
            "units": units,
            "buy_price": buy_price,
            "current_price": current_price,
        })

    return holdings


def _extract_xlsx(file_bytes: bytes) -> list[dict[str, Any]]:
    """Extract from .xlsx using openpyxl."""
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    all_holdings: list[dict[str, Any]] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows: list[list] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))

        sheet_holdings = _sheet_to_rows(rows)
        all_holdings.extend(sheet_holdings)

    wb.close()
    return _deduplicate(all_holdings)


def _extract_xls(file_bytes: bytes) -> list[dict[str, Any]]:
    """Extract from legacy .xls using xlrd."""
    wb = xlrd.open_workbook(file_contents=file_bytes)
    all_holdings: list[dict[str, Any]] = []

    for sheet_idx in range(wb.nsheets):
        ws = wb.sheet_by_index(sheet_idx)
        rows: list[list] = []
        for r in range(ws.nrows):
            rows.append(ws.row_values(r))

        sheet_holdings = _sheet_to_rows(rows)
        all_holdings.extend(sheet_holdings)

    return _deduplicate(all_holdings)


def _deduplicate(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    unique: list[dict[str, Any]] = []
    for h in holdings:
        key = (
            h.get("name", "").strip().upper(),
            h.get("symbol", "").strip().upper(),
            str(h.get("units", "")),
            str(h.get("buy_price", ""))
        )
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique
