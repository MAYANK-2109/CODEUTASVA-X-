"""Where listed companies have physical assets, for linking a localised event
to the holdings it can reach.

Power stations come from the WRI Global Power Plant Database (coordinates and
capacity), matched to their listed owners by station name. Refineries, ports,
plants and hubs are a short curated list in data/industrial_sites.json.
"""

import json
import math
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SITES_FILE = DATA_DIR / "industrial_sites.json"
POWER_FILE = DATA_DIR / "trained" / "power_assets.json"
DEFAULT_RADIUS_KM = 300

# Station-name patterns for the listed owner of each large Indian power station.
# The database's own "owner" column is empty for most of them.
PLANT_OWNERS = {
    "NTPC": r"^(VINDH_CHAL|RIHAND|TALCHER STPS|SIPAT|KORBA STPS|R_GUNDEM STPS|KUDGI|KAHALGAON|MOUDA|FARAKKA|"
            r"SIMHADRI|SINGRAULI STPS|DADRI|UNCHAHAR|VALLUR|BARH|INDRA GANDHI STPP|TANDA|BONGAIGAON|LARA|"
            r"GADARWARA|KAWAS|ANTA|AURAIYA|JHANOR|FARIDABAD CCPP|RAJIV GANDHI CCPP|TALCHER \(OLD\)|BADARPUR)",
    "ADANIPOWER": r"^(MUNDRA TPP|TIRORA|KAWAI|UDUPI)",
    "TATAPOWER": r"^(MUNDRA UMPP|MAITHON RB|TROMBAY|JOJOBERA|BHIRA|KHOPOLI|BHIVPURI)",
    "RPOWER": r"^(SASAN UMPP|ROSA TPP)",
    "JSWENERGY": r"^(JSW RATNAGIRI|JALLIPPA KAPURDI|TORANGALLU|KARCHAM WANGTOO|BASPA)",
    "TORNTPOWER": r"^(SUGEN|DGEN|SABARMATI)",
    "NLCINDIA": r"^(NEYVELI|TUTICORIN JV|BARSINGSAR)",
    "SJVN": r"^(NATHPA JHAKRI|RAMPUR)",
    "NHPC": r"^(CHAMERA|DULHASTI|URI|SALAL|TEESTA V|TEESTA LOW DAM|PARBATI|DHAULI GANGA|TANAKPUR|"
            r"BAIRA SIUL|LOKTAK|RANGIT|SEWA|CHUTAK|NIMOO BAZGO|KISHANGANGA|INDIRA SAGAR|OMKARESHWAR)",
    "CESC": r"^(BUDGE BUDGE|TITAGARH|SOUTHERN REPL|HALDIA TPP)",
    "JINDALSTEL": r"^(TAMNAR)",
    "VEDL": r"^(TALWANDI SABO|JHARSUGUDA)",
    "LT": r"^(RAJPURA TPP)",
}
# India's land area, to drop database rows with mistyped coordinates.
INDIA_BOX = (6.0, 36.0, 68.0, 98.0)


def owner_of(plant_name: str) -> str | None:
    name = plant_name.strip().upper()
    for ticker, pattern in PLANT_OWNERS.items():
        if re.match(pattern, name):
            return ticker
    return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def load_assets() -> list[dict]:
    """Every mapped asset: {ticker, name, lat, lon, kind, mw?}."""
    assets = json.loads(SITES_FILE.read_text())
    try:
        assets = assets + json.loads(POWER_FILE.read_text())["plants"]
    except (OSError, ValueError, KeyError):
        pass  # the curated sites still work without the power stations
    return assets


def exposure(ticker: str, lat: float, lon: float, radius_km: float = DEFAULT_RADIUS_KM) -> dict | None:
    """A company's assets within reach of a point, or None if it has none there.

    `share` is the fraction of the company's mapped generating capacity inside
    the radius; it is None for companies mapped by site rather than by capacity.
    """
    symbol = ticker.split(".")[0]
    owned = [a for a in load_assets() if a["ticker"] == symbol]
    near = [{**a, "km": round(distance_km(lat, lon, a["lat"], a["lon"]))} for a in owned]
    near = sorted((a for a in near if a["km"] <= radius_km), key=lambda a: a["km"])
    if not near:
        return None
    total_mw = sum(a.get("mw", 0) for a in owned)
    near_mw = sum(a.get("mw", 0) for a in near)
    return {
        "assets": near,
        "share": round(near_mw / total_mw, 3) if total_mw and near_mw else None,
        "mw": round(near_mw) if near_mw else None,
    }


def describe(found: dict) -> str:
    names = ", ".join(f'{a["name"]} ({a["km"]} km)' for a in found["assets"][:3])
    more = f' and {len(found["assets"]) - 3} more' if len(found["assets"]) > 3 else ""
    share = (f'; {found["share"] * 100:.0f}% of its mapped generating capacity, {found["mw"]:,} MW'
             if found["share"] else "")
    return f"{names}{more}{share}"
