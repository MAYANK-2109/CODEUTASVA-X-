"""
Portfolio holdings extractor — supports PDF and Excel files.

PDF formats:
  - Zerodha Contract Note / P&L Statement
  - CDSL Consolidated Account Statement (CAS)
  - Groww Statement
  - Generic tabular PDFs (best-effort)

Excel formats (.xlsx / .xls):
  - Any spreadsheet with column headers containing
    name / symbol / qty / price / date keywords
  - Zerodha Holdings export (.xlsx)
  - Groww Holdings / Transaction export
  - Generic broker exports

Returns a list of dicts with keys:
  name, symbol, isin, type (STOCK/MF/ETF/BOND),
  buy_date, units, buy_price, current_price (None – filled later)
"""

import re
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
# Public API
# ---------------------------------------------------------------------------

def extract_holdings(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Unified dispatcher: routes to PDF or Excel extractor based on filename.
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return extract_holdings_from_pdf(file_bytes)
    if ext in ('xlsx', 'xls'):
        return extract_holdings_from_excel(file_bytes, ext)
    raise ValueError(f"Unsupported file type: .{ext}. Only PDF, XLSX, and XLS are accepted.")


def extract_holdings_from_pdf(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Extract holding rows from an uploaded PDF file (raw bytes)."""
    if not _HAS_PDFPLUMBER:
        raise RuntimeError("pdfplumber is not installed. Run `pip install pdfplumber`.")

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        tables = []
        for page in pdf.pages:
            page_tables = page.extract_tables()
            if page_tables:
                tables.extend(page_tables)

    holdings: list[dict[str, Any]] = []

    # 1. Try structured table extraction
    if tables:
        holdings = _extract_from_tables(tables)

    # 2. Fall back to text parsing
    if not holdings:
        holdings = _extract_from_text(text)

    # Deduplicate by (name, buy_date, units)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for h in holdings:
        key = (h.get("name", "").upper(), h.get("buy_date", ""), h.get("units", ""))
        if key not in seen:
            seen.add(key)
            unique.append(h)

    return unique


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_number(s: str | None) -> float | None:
    if not s:
        return None
    s = re.sub(r"[^\d.\-]", "", str(s).replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return None


def _clean_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    # Normalise DD-MM-YYYY, DD/MM/YYYY → YYYY-MM-DD
    m = re.match(r"(\d{2})[/\-](\d{2})[/\-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m2 = re.match(r"(\d{4})[/\-](\d{2})[/\-](\d{2})", s)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
    return s


def _infer_type(name: str) -> str:
    n = name.upper()
    if any(k in n for k in ("FUND", "SCHEME", "DIRECT", "GROWTH", "DIVIDEND", "SIP", "MF")):
        return "MF"
    if "ETF" in n:
        return "ETF"
    if any(k in n for k in ("BOND", "NCD", "DEBENTURE", "GSEC", "T-BILL")):
        return "BOND"
    return "STOCK"


# ---------------------------------------------------------------------------
# Table-based extraction
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"\d{2}[/\-]\d{2}[/\-]\d{4}|\d{4}[/\-]\d{2}[/\-]\d{2}")
_NUMBER_RE = re.compile(r"^\s*[\d,]+(\.\d+)?\s*$")


def _find_header_row(rows: list[list]) -> tuple[int, dict[str, int]]:
    """Return (header_idx, column_map) where column_map maps field→col index."""
    KEYWORDS = {
        "name":       ["name", "scrip", "security", "instrument", "scheme", "stock", "symbol", "description"],
        "symbol":     ["symbol", "ticker", "isin"],
        "buy_date":   ["date", "trade date", "purchase date", "buy date", "transaction date"],
        "units":      ["qty", "quantity", "units", "shares", "holding"],
        "buy_price":  ["price", "avg cost", "average cost", "avg price", "purchase price", "rate", "nav", "cost"],
    }

    for idx, row in enumerate(rows[:10]):  # Look only in first 10 rows
        row_str = [str(c).lower().strip() if c else "" for c in row]
        col_map: dict[str, int] = {}
        for field, kws in KEYWORDS.items():
            for ci, cell in enumerate(row_str):
                if any(kw in cell for kw in kws):
                    col_map.setdefault(field, ci)
        if "name" in col_map or "symbol" in col_map:
            return idx, col_map

    return -1, {}


def _extract_from_tables(tables: list[list[list]]) -> list[dict]:
    holdings: list[dict] = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        header_idx, col_map = _find_header_row(table)
        if header_idx == -1:
            continue
        for row in table[header_idx + 1:]:
            if not row:
                continue
            def gcell(field: str) -> str | None:
                idx = col_map.get(field)
                if idx is None or idx >= len(row):
                    return None
                return str(row[idx]).strip() if row[idx] else None

            name = gcell("name") or gcell("symbol") or ""
            if not name or len(name) < 2:
                continue
            # Skip pure-number names (likely a total row)
            if re.match(r"^[\d,.\s]+$", name):
                continue

            symbol = gcell("symbol") or ""
            raw_isin = symbol if re.match(r"^IN[A-Z0-9]{10}$", symbol.upper()) else ""
            buy_date = _clean_date(gcell("buy_date"))
            units = _clean_number(gcell("units"))
            buy_price = _clean_number(gcell("buy_price"))

            holdings.append({
                "name": name,
                "symbol": symbol if not raw_isin else "",
                "isin": raw_isin,
                "type": _infer_type(name),
                "buy_date": buy_date,
                "units": units,
                "buy_price": buy_price,
                "current_price": None,
            })
    return holdings


# ---------------------------------------------------------------------------
# Text-based extraction (fallback)
# ---------------------------------------------------------------------------

# Patterns for common broker/depot statement formats
_CDSL_PATTERN = re.compile(
    r"(?P<isin>IN[A-Z0-9]{10})\s+"
    r"(?P<name>[A-Za-z0-9\s\-&\.]+?)\s+"
    r"(?P<units>[\d,]+\.?\d*)\s+"
    r"(?P<price>[\d,]+\.?\d*)",
    re.IGNORECASE,
)

_ZERODHA_PATTERN = re.compile(
    r"(?P<date>\d{2}[/-]\d{2}[/-]\d{4})\s+"
    r"(?P<name>[A-Za-z0-9\s\-&\.]+?)\s+"
    r"(?P<qty>[\d,]+)\s+"
    r"(?P<price>[\d,]+\.?\d*)",
    re.IGNORECASE,
)


def _extract_from_text(text: str) -> list[dict]:
    holdings: list[dict] = []

    # CDSL CAS pattern (ISIN-first)
    for m in _CDSL_PATTERN.finditer(text):
        name = m.group("name").strip()
        if len(name) < 2:
            continue
        holdings.append({
            "name": name,
            "symbol": "",
            "isin": m.group("isin").upper(),
            "type": _infer_type(name),
            "buy_date": None,
            "units": _clean_number(m.group("units")),
            "buy_price": _clean_number(m.group("price")),
            "current_price": None,
        })

    if holdings:
        return holdings

    # Zerodha contract note / generic date-based rows
    for m in _ZERODHA_PATTERN.finditer(text):
        name = m.group("name").strip()
        if len(name) < 2:
            continue
        holdings.append({
            "name": name,
            "symbol": "",
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
    if ext == 'xlsx':
        if not _HAS_OPENPYXL:
            raise RuntimeError("openpyxl is not installed. Run `pip install openpyxl`.")
        return _extract_xlsx(file_bytes)
    else:
        if not _HAS_XLRD:
            raise RuntimeError("xlrd is not installed. Run `pip install xlrd`.")
        return _extract_xls(file_bytes)


def _sheet_to_rows(ws_rows: list[list]) -> list[dict[str, Any]]:
    """
    Given a list-of-list table (sheet rows), detect header row and
    return normalised holding dicts using the same logic as PDF tables.
    """
    if not ws_rows or len(ws_rows) < 2:
        return []

    header_idx, col_map = _find_header_row(ws_rows)
    if header_idx == -1:
        return []

    holdings: list[dict[str, Any]] = []
    for row in ws_rows[header_idx + 1:]:
        if not any(cell for cell in row):   # skip blank rows
            continue

        def gcell(field: str) -> str | None:
            idx = col_map.get(field)
            if idx is None or idx >= len(row):
                return None
            v = row[idx]
            return str(v).strip() if v is not None else None

        name = gcell('name') or gcell('symbol') or ''
        if not name or len(name.strip()) < 2:
            continue
        if re.match(r'^[\d,.\s\-]+$', name):   # skip pure-number total rows
            continue

        symbol_raw = gcell('symbol') or ''
        isin = symbol_raw if re.match(r'^IN[A-Z0-9]{10}$', symbol_raw.upper()) else ''

        holdings.append({
            'name':          name,
            'symbol':        '' if isin else symbol_raw,
            'isin':          isin,
            'type':          _infer_type(name),
            'buy_date':      _clean_date(gcell('buy_date')),
            'units':         _clean_number(gcell('units')),
            'buy_price':     _clean_number(gcell('buy_price')),
            'current_price': None,
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
    unique: list[dict] = []
    for h in holdings:
        key = (h.get('name', '').upper(), h.get('buy_date', ''), str(h.get('units', '')))
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique
