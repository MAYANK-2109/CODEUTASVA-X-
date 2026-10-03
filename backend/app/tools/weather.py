import time

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL_SECONDS = 1800

# Sites chosen for their economic footprint, not population.
LOCATIONS = [
    {"name": "Mumbai", "lat": 19.08, "lon": 72.88, "relevance": "financial hub, Mumbai High offshore oil, JNPT port",
     "sectors": ["Energy", "Banking", "Financials", "Infrastructure"]},
    {"name": "Jamnagar", "lat": 22.47, "lon": 70.06, "relevance": "refining hub, Gujarat ports",
     "sectors": ["Energy", "Infrastructure"]},
    {"name": "Chennai", "lat": 13.08, "lon": 80.27, "relevance": "auto and electronics manufacturing, IT",
     "sectors": ["Auto", "IT"]},
    {"name": "Visakhapatnam", "lat": 17.69, "lon": 83.22, "relevance": "refinery, steel plant, east-coast port",
     "sectors": ["Energy", "Metals", "Infrastructure"]},
    {"name": "Kolkata", "lat": 22.57, "lon": 88.36, "relevance": "east-coast port and trade hub",
     "sectors": ["Metals", "Infrastructure"]},
    {"name": "Delhi NCR", "lat": 28.61, "lon": 77.21, "relevance": "power demand centre, auto manufacturing",
     "sectors": ["Utilities", "Auto", "Airlines"]},
]

# India Meteorological Department thresholds.
HEAVY_RAIN_MM = 64.5
GALE_GUST_KMH = 62
HEAT_C = 40

_cache: dict[str, object] = {"at": 0.0, "value": None}


def get_weather_outlook() -> list[dict] | None:
    """Seven-day peak rain, gust and temperature per site, with threshold flags.

    None when the forecast service cannot be reached.
    """
    if _cache["value"] and time.time() - _cache["at"] < CACHE_TTL_SECONDS:
        return _cache["value"]
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
    except (httpx.HTTPError, ValueError):
        return None

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
                "relevance": place["relevance"],
                "sectors": place["sectors"],
                "days": days,
                "max_rain_mm": rain,
                "max_gust_kmh": gust,
                "max_temp_c": heat,
                "flags": flags,
            }
        )
    _cache.update(at=time.time(), value=outlook)
    return outlook
