import hashlib
import json
import math
import time

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel

from app.advisor.suggest import suggest
from app.tools import memory

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
    asked_at = time.time()

    def stored(newer_than: float = 0.0) -> dict | None:
        hit = _cache.get(key)
        return hit[1] if hit and hit[0] >= newer_than and time.time() - hit[0] < CACHE_SECONDS else None

    answer = None if request.refresh else stored()
    if answer:
        return answer
    with memory.heavy():
        # Whoever waited here for an identical request takes its answer instead of repeating the work.
        answer = stored(asked_at if request.refresh else 0.0)
        if answer:
            return answer
        result = clean(suggest(request.holdings))
        if result["status"] == "ok":
            _cache[key] = (time.time(), result)
        return result
