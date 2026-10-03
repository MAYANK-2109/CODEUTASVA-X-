"""Scores holdings with the impact model trained by app.ml.train: the chance
of a sharp fall over the next sessions and how large the downside is.

The model reads price history, macro indicators, the holding's sector and
weather alert days at its sites. When the macro history is missing the
holdings are left unscored and the caller says so."""

import json
import math
from functools import lru_cache
from pathlib import Path

from app.ml import gbm
from app.ml.features import BRENT, IMPACT_FEATURES, RUPEE, VIX, impact_features, macro_features
from app.tools.market import NIFTY, SECTORS, get_cross_assets
from app.tools.weather import LOCATIONS, get_weather_history

MODEL_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "trained" / "impact_model.json"
UNKNOWN_SECTOR = -1


@lru_cache(maxsize=1)
def model() -> dict | None:
    try:
        return json.loads(MODEL_FILE.read_text())
    except (OSError, ValueError):
        return None


def sector_codes() -> dict[str, int]:
    return {sector: code for code, sector in enumerate(sorted(set(SECTORS.values())))}


def company_alert_dates() -> dict[str, list[str]]:
    """Weather alert days at the sites where each company has an operation."""
    history = get_weather_history() or {"sites": {}}
    dates: dict[str, list[str]] = {}
    for place in LOCATIONS:
        days = [day for day, _, _ in history["sites"].get(place["name"], [])]
        for symbol in place["companies"]:
            dates.setdefault(symbol, []).extend(days)
    return dates


def score_positions(positions: list[dict], closes) -> dict[str, dict] | None:
    """Per ticker: `fall` (chance of a sharp fall), `downside` and
    `usual_downside` (returns, negative), and `elevated`. None when the model
    or its macro inputs are unavailable; a holding with too little history is left out."""
    trained = model()
    macro = get_cross_assets() if trained else None
    if macro is None or any(column not in macro.columns for column in (VIX, BRENT, RUPEE)):
        return None
    macro_days = macro_features(closes[NIFTY], macro)
    alerts = company_alert_dates()
    scores = {}
    for p in positions:
        symbol = p["ticker"].split(".")[0]
        days = impact_features(closes[p["ticker"]], closes[NIFTY], macro_days,
                               trained["sectors"].get(p.get("sector"), UNKNOWN_SECTOR), alerts.get(symbol, []))
        row = {name: float(days[name].iloc[-1]) for name in IMPACT_FEATURES}
        if any(math.isnan(value) for value in row.values()):
            continue
        fall = gbm.predict(trained["models"]["fall"], row)
        scores[p["ticker"]] = {
            "fall": fall,
            "elevated": fall >= trained["fall"]["elevated_cutoff"],
            "downside": min(gbm.predict(trained["models"]["downside"], row), 0.0),
            "usual_downside": min(row["usual_downside"], 0.0),
        }
    return scores


def summary() -> dict | None:
    """How the model did on the years it was not trained on, for display."""
    trained = model()
    if trained is None:
        return None
    fall = trained["fall"]
    return {
        "name": "LightGBM impact model", "auc": fall["auc"],
        "auc_price_only": fall["auc_by_data_used"]["price history"],
        "tested_on": trained["test_period"], "trained_on": trained["trained_on"],
        "fall_size": trained["fall_size"], "horizon_sessions": trained["horizon_sessions"],
        "rate_when_elevated": fall["rate_when_elevated"], "rate_otherwise": fall["rate_otherwise"],
    }
