"""Weather at the sites where listed companies operate: the 7-day forecast, and
the days since 2013 that crossed an alert threshold.

Rebuild the history file with:  uv run python -m app.tools.weather --history
"""

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.tools import health

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CACHE_TTL_SECONDS = 1800
HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "weather_extremes.json"
HISTORY_START = "2013-01-01"
HISTORY_TOP_UP_SECONDS = 6 * 3600
HISTORY_SOURCE = "Open-Meteo historical weather (ERA5 reanalysis); India Meteorological Department thresholds"

# Sites chosen for their economic footprint, not population. `companies` are
# NSE symbols with a physical operation at or near the site; `regions` are the
# words a question might use for the area.
LOCATIONS = [
    {"name": "Mumbai", "lat": 19.08, "lon": 72.88,
     "relevance": "financial hub, Mumbai High offshore oil, JNPT port",
     "sectors": ["Energy", "Banking", "Financials", "Infrastructure"],
     "regions": ["mumbai", "maharashtra", "konkan", "west coast", "arabian sea"],
     "companies": {
         "ONGC": "Mumbai High offshore oil fields", "BPCL": "Mumbai refinery", "HINDPETRO": "Mumbai refinery",
         "RELIANCE": "headquarters", "HDFCBANK": "headquarters", "ICICIBANK": "headquarters",
         "SBIN": "headquarters", "KOTAKBANK": "headquarters", "AXISBANK": "headquarters",
         "TATAPOWER": "Mumbai power distribution", "INDIGO": "Mumbai airport hub",
     }},
    {"name": "Jamnagar", "lat": 22.47, "lon": 70.06,
     "relevance": "refining hub, Gujarat ports",
     "sectors": ["Energy", "Infrastructure"],
     "regions": ["jamnagar", "gujarat", "kutch", "saurashtra", "west coast", "arabian sea"],
     "companies": {"RELIANCE": "Jamnagar refinery complex", "ADANIPORTS": "Mundra port"}},
    {"name": "Chennai", "lat": 13.08, "lon": 80.27,
     "relevance": "auto and electronics manufacturing, IT",
     "sectors": ["Auto", "IT"],
     "regions": ["chennai", "tamil nadu", "east coast", "bay of bengal"],
     "companies": {
         "TCS": "Chennai delivery campuses", "INFY": "Chennai delivery campuses", "HCLTECH": "Chennai delivery campuses",
         "ASHOKLEY": "headquarters and plants", "TVSMOTOR": "headquarters", "EICHERMOT": "Royal Enfield plants",
         "IOC": "Chennai Petroleum refinery (subsidiary)",
     }},
    {"name": "Visakhapatnam", "lat": 17.69, "lon": 83.22,
     "relevance": "refinery, steel plant, east-coast port",
     "sectors": ["Energy", "Metals", "Infrastructure"],
     "regions": ["visakhapatnam", "vizag", "andhra", "east coast", "bay of bengal"],
     "companies": {"HINDPETRO": "Visakh refinery", "ADANIPORTS": "Gangavaram port", "NTPC": "Simhadri power station"}},
    {"name": "Kolkata", "lat": 22.57, "lon": 88.36,
     "relevance": "east-coast port and trade hub",
     "sectors": ["Metals", "Infrastructure"],
     "regions": ["kolkata", "west bengal", "bengal", "haldia", "east coast", "bay of bengal"],
     "companies": {"ITC": "headquarters", "COALINDIA": "headquarters", "IOC": "Haldia refinery"}},
    {"name": "Delhi NCR", "lat": 28.61, "lon": 77.21,
     "relevance": "power demand centre, auto manufacturing",
     "sectors": ["Utilities", "Auto", "Airlines"],
     "regions": ["delhi", "ncr", "gurugram", "gurgaon", "haryana", "north india"],
     "companies": {
         "MARUTI": "Gurugram and Manesar plants", "HEROMOTOCO": "Gurugram plant", "INDIGO": "Delhi airport hub",
         "NTPC": "headquarters and Dadri power station", "POWERGRID": "headquarters",
     }},
]

# India Meteorological Department thresholds.
HEAVY_RAIN_MM = 64.5
GALE_GUST_KMH = 62
HEAT_C = 40

# The measure behind each kind of alert day.
KINDS = {
    "rain": ("precipitation_sum", HEAVY_RAIN_MM),
    "wind": ("wind_gusts_10m_max", GALE_GUST_KMH),
    "heat": ("temperature_2m_max", HEAT_C),
}

_cache: dict[str, object] = {"at": 0.0, "value": None}
_history: dict[str, object] = {"at": 0.0, "value": None}


def get_weather_outlook() -> list[dict] | None:
    """Seven-day peak rain, gust and temperature per site, with threshold flags.

    None when the forecast service cannot be reached.
    """
    if _cache["value"] and time.time() - _cache["at"] < CACHE_TTL_SECONDS:
        return _cache["value"]
    started = time.perf_counter()
    try:
        response = httpx.get(
            FORECAST_URL,
            params={
                "latitude": ",".join(str(p["lat"]) for p in LOCATIONS),
                "longitude": ",".join(str(p["lon"]) for p in LOCATIONS),
                "daily": "temperature_2m_max,precipitation_sum,wind_gusts_10m_max",
                "forecast_days": 7,
                "timezone": "Asia/Kolkata",
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        health.record("weather", False, detail=type(exc).__name__)
        return None
    health.record("weather", True, ms=(time.perf_counter() - started) * 1000, items=len(LOCATIONS),
                  detail=f"7-day forecast for {len(LOCATIONS)} sites")

    outlook = []
    for place, forecast in zip(LOCATIONS, payload if isinstance(payload, list) else [payload]):
        daily = forecast.get("daily", {})
        rain = max((v for v in daily.get("precipitation_sum", []) if v is not None), default=None)
        gust = max((v for v in daily.get("wind_gusts_10m_max", []) if v is not None), default=None)
        heat = max((v for v in daily.get("temperature_2m_max", []) if v is not None), default=None)
        flags = []
        if rain is not None and rain >= HEAVY_RAIN_MM:
            flags.append("heavy rain")
        if gust is not None and gust >= GALE_GUST_KMH:
            flags.append("gale-force gusts")
        if heat is not None and heat >= HEAT_C:
            flags.append("extreme heat")
        days = [
            {"date": date, "rain_mm": r, "gust_kmh": g, "temp_c": t}
            for date, r, g, t in zip(
                daily.get("time", []), daily.get("precipitation_sum", []),
                daily.get("wind_gusts_10m_max", []), daily.get("temperature_2m_max", []),
            )
        ]
        outlook.append(
            {
                "name": place["name"],
                "lat": place["lat"],
                "lon": place["lon"],
                "relevance": place["relevance"],
                "sectors": place["sectors"],
                "regions": place["regions"],
                "companies": place["companies"],
                "days": days,
                "max_rain_mm": rain,
                "max_gust_kmh": gust,
                "max_temp_c": heat,
                "flags": flags,
            }
        )
    _cache.update(at=time.time(), value=outlook)
    return outlook


def holdings_at(site: dict, holdings: list[dict]) -> list[dict]:
    """The holdings with an operation at this site, each with what it has there."""
    companies = site.get("companies", {})
    return [
        {"name": h["name"], "ticker": h["ticker"], "operation": companies[h["ticker"].split(".")[0]]}
        for h in holdings
        if h["ticker"].split(".")[0] in companies
    ]


def sites_named_in(query: str, sites: list[dict]) -> list[dict]:
    """Sites whose region the question mentions."""
    text = query.lower()
    return [site for site in sites if any(region in text for region in site.get("regions", []))]


# --------------------------------------------------------------------------- history


def _threshold_days(daily: dict) -> list[list]:
    """[date, kind, value] for every day at or above a threshold."""
    days = []
    for kind, (field, threshold) in KINDS.items():
        for day, value in zip(daily.get("time", []), daily.get(field, [])):
            if value is not None and value >= threshold:
                days.append([day, kind, round(float(value), 1)])
    return sorted(days)


def _archive(places: list[dict], start: str, end: str) -> dict[str, list[list]]:
    """Threshold days per site between two dates, from the reanalysis archive."""
    response = httpx.get(
        ARCHIVE_URL,
        params={
            "latitude": ",".join(str(p["lat"]) for p in places),
            "longitude": ",".join(str(p["lon"]) for p in places),
            "start_date": start, "end_date": end,
            "daily": ",".join(field for field, _ in KINDS.values()),
            "timezone": "Asia/Kolkata",
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    return {place["name"]: _threshold_days(result.get("daily", {}))
            for place, result in zip(places, payload if isinstance(payload, list) else [payload])}


def build_history() -> dict:
    """Fetch every site since HISTORY_START, one request each, and save the alert days."""
    through = (date.today() - timedelta(days=1)).isoformat()
    sites = {}
    for place in LOCATIONS:
        sites.update(_archive([place], HISTORY_START, through))
        print(f'{place["name"]}: {len(sites[place["name"]])} alert days')
        time.sleep(2)
    history = {
        "built_at": datetime.now(timezone.utc).isoformat(), "start": HISTORY_START, "through": through,
        "source": HISTORY_SOURCE,
        "thresholds": {"rain_mm": HEAVY_RAIN_MM, "gust_kmh": GALE_GUST_KMH, "temp_c": HEAT_C},
        "sites": sites,
    }
    HISTORY_FILE.write_text(json.dumps(history, separators=(",", ":")))
    return history


def get_weather_history() -> dict | None:
    """Alert days per site since 2013: {through, source, sites: {name: [[date, kind, value]]}}.

    The saved file is extended in memory with the days since it was built. If
    that request fails the file is used as it is, and `through` says how far it goes.
    """
    if _history["value"] and time.time() - _history["at"] < HISTORY_TOP_UP_SECONDS:
        return _history["value"]
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return None
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if history["through"] < yesterday:
        try:
            start = (date.fromisoformat(history["through"]) + timedelta(days=1)).isoformat()
            for name, days in _archive(LOCATIONS, start, yesterday).items():
                history["sites"].setdefault(name, []).extend(days)
            history["through"] = yesterday
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    _history.update(at=time.time(), value=history)
    return history


if __name__ == "__main__":
    if "--history" in sys.argv:
        built = build_history()
        print(f'Saved {HISTORY_FILE.name}: {sum(len(d) for d in built["sites"].values())} alert days to {built["through"]}')
    else:
        for site in get_weather_outlook() or []:
            print(site["name"], site["flags"] or "no alert")
