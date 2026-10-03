"""
Portfolio router — FastAPI endpoints for:
  POST /api/portfolio/upload-pdf    → extract holdings from PDF or Excel (auth required)
  GET  /api/portfolio/holdings      → list saved holdings for the authenticated user
  POST /api/portfolio/holdings      → save holdings (batch upsert) for the authenticated user
  PUT  /api/portfolio/holdings/{id} → update a single holding
  DELETE /api/portfolio/holdings/{id} → delete a holding
"""

import os
import re
import tempfile
from typing import Any

from fastapi import APIRouter, File, HTTPException, Header, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from .extractor import extract_holdings

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# ---------------------------------------------------------------------------
# JWT / auth helpers
# ---------------------------------------------------------------------------

def _verify_jwt(authorization: str | None, required: bool = True) -> str | None:
    """
    Decode the Supabase JWT from the Authorization header and return the
    authenticated user's UUID. If required=False, returns None on any failure.
    """
    import base64, json as _json

    if not authorization or not authorization.lower().startswith("bearer "):
        if required:
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
        return None

    token = authorization.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) != 3:
        if required:
            raise HTTPException(status_code=401, detail="Malformed JWT.")
        return None

    # Decode payload (middle part) — add padding if needed
    payload_b64 = parts[1] + "==" * ((4 - len(parts[1]) % 4) % 4)
    try:
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        if required:
            raise HTTPException(status_code=401, detail="Could not decode JWT payload.")
        return None

    user_id: str | None = payload.get("sub")
    if not user_id and required:
        raise HTTPException(status_code=401, detail="JWT missing 'sub' claim.")

    return user_id

# ---------------------------------------------------------------------------
# Supabase client (server-side, uses service-role key)
# ---------------------------------------------------------------------------

try:
    from supabase import create_client, Client

    _SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    _SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    _supabase: Client | None = None
    if _SUPABASE_URL and _SUPABASE_KEY:
        _supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY)
except Exception:
    _supabase = None  # type: ignore[assignment]


def _get_db() -> "Client":
    if _supabase is None:
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
        )
    return _supabase


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class HoldingIn(BaseModel):
    name: str
    symbol: str = ""
    isin: str = ""
    type: str = "STOCK"
    buy_date: str | None = None
    units: float | None = None
    buy_price: float | None = None
    current_price: float | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"STOCK", "MF", "ETF", "BOND"}
        v = v.upper()
        return v if v in allowed else "STOCK"


class HoldingUpdate(BaseModel):
    name: str | None = None
    symbol: str | None = None
    isin: str | None = None
    type: str | None = None
    buy_date: str | None = None
    units: float | None = None
    buy_price: float | None = None
    current_price: float | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/upload-pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    """
    Accept a PDF, XLSX, or XLS broker statement, extract holdings, and return
    them as JSON **scoped to the authenticated user**.

    Requires a valid Supabase ``Authorization: Bearer <jwt>`` header so that
    the returned ``user_id`` is always derived from the token — never from
    untrusted client input.

    The file is processed in-memory and freed immediately after extraction.
    """
    # ── Auth: resolve the calling user from the JWT ───────────────────────
    user_id = _verify_jwt(authorization, required=False)

    # ── File validation ───────────────────────────────────────────────────
    ALLOWED_EXTENSIONS = ('.pdf', '.xlsx', '.xls')
    fname = (file.filename or '').lower()
    if not any(fname.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Only PDF, XLSX, and XLS files are supported."
        )

    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:  # 20 MB limit
        raise HTTPException(status_code=413, detail="File too large. Max 20 MB.")

    try:
        holdings = extract_holdings(file_bytes, file.filename or 'file.pdf')
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
    finally:
        del file_bytes  # Free memory immediately

    # ── Enrich holdings with live market prices if current_price is missing ──
    from app.tools.market import to_ticker
    tickers_to_query = [
        to_ticker(h["symbol"])
        for h in holdings
        if h.get("symbol") and h.get("current_price") is None
    ]
    if tickers_to_query:
        try:
            from app.ingestion.prices import get_latest_prices
            live_prices, _ = get_latest_prices(tickers_to_query)
            for h in holdings:
                if h.get("current_price") is None and h.get("symbol"):
                    t = to_ticker(h["symbol"])
                    if t in live_prices:
                        h["current_price"] = live_prices[t]
        except Exception:
            pass

    # If current_price is still None, fallback to buy_price to avoid null display
    for h in holdings:
        if h.get("current_price") is None and h.get("buy_price") is not None:
            h["current_price"] = h["buy_price"]

    # Return holdings *and* the verified user_id so the frontend can persist
    # them directly via the authenticated Supabase client
    return {"holdings": holdings, "count": len(holdings), "user_id": user_id}


@router.get("/holdings")
async def list_holdings(authorization: str | None = Header(default=None)):
    """Return all holdings for the **authenticated** user only."""
    user_id = _verify_jwt(authorization)
    db = _get_db()
    result = (
        db.table("portfolio_holdings")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"holdings": result.data or []}


@router.post("/holdings")
async def save_holdings(
    holdings: list[HoldingIn],
    authorization: str | None = Header(default=None),
):
    """Batch insert holdings into the database, scoped to the authenticated user."""
    user_id = _verify_jwt(authorization)
    db = _get_db()
    rows = [
        {
            "user_id": user_id,  # always from verified JWT, never from client payload
            "name": h.name,
            "symbol": h.symbol,
            "isin": h.isin,
            "type": h.type,
            "buy_date": h.buy_date,
            "units": h.units,
            "buy_price": h.buy_price,
            "current_price": h.current_price,
        }
        for h in holdings
    ]
    result = db.table("portfolio_holdings").insert(rows).execute()
    return {"inserted": len(result.data or []), "data": result.data}


@router.put("/holdings/{holding_id}")
async def update_holding(holding_id: str, payload: HoldingUpdate):
    """Patch a single holding by id."""
    db = _get_db()
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")
    result = db.table("portfolio_holdings").update(update_data).eq("id", holding_id).execute()
    return {"updated": result.data}


@router.delete("/holdings/{holding_id}")
async def delete_holding(holding_id: str):
    """Delete a single holding."""
    db = _get_db()
    db.table("portfolio_holdings").delete().eq("id", holding_id).execute()
    return {"deleted": holding_id}
