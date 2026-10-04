import hashlib
import json
import math
import time

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel

from app.advisor.suggest import suggest

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

CACHE_SECONDS = 600
_cache: dict[str, tuple[float, dict]] = {}


class SuggestRequest(BaseModel):
    holdings: list[dict] | None = None
    refresh: bool = False


def clean(value):
    """Plain JSON: numpy numbers become Python numbers and a NaN becomes null, never a made-up figure."""
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


@router.post("/suggestions")
def suggestions(request: SuggestRequest) -> dict:
    """Sector and stock suggestions for the holdings: gaps, sector ranking, picks, sizing and a walk-forward check."""
    key = hashlib.sha1(json.dumps(request.holdings, sort_keys=True, default=str).encode()).hexdigest()
    hit = _cache.get(key)
    if hit and not request.refresh and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    result = clean(suggest(request.holdings))
    if result["status"] == "ok":
        _cache[key] = (time.time(), result)
    return result
