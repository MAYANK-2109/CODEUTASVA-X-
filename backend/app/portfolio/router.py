"""
Portfolio router — FastAPI endpoints for:
  POST /api/portfolio/upload-file   → extract holdings from PDF or Excel
  GET  /api/portfolio/holdings      → list saved holdings
  POST /api/portfolio/holdings      → save holdings (batch upsert)
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
async def upload_pdf(file: UploadFile = File(...)):
    """
    Accept a PDF, extract holdings, return them as JSON.
    Accept a PDF, XLSX, or XLS file, extract holdings, return them as JSON.
    The file is processed in memory and deleted after extraction.
    """
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

    return {"holdings": holdings, "count": len(holdings)}


@router.get("/holdings")
async def list_holdings(user_id: str | None = None):
    """Return all holdings for a user."""
    db = _get_db()
    query = db.table("portfolio_holdings").select("*").order("created_at", desc=True)
    if user_id:
        query = query.eq("user_id", user_id)
    result = query.execute()
    return {"holdings": result.data or []}


@router.post("/holdings")
async def save_holdings(holdings: list[HoldingIn], user_id: str | None = None):
    """Batch insert holdings into the database."""
    db = _get_db()
    rows = [
        {
            "user_id": user_id,
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
