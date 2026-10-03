import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

from app.tools import health

FEED_URL = "https://news.google.com/rss/search"
CACHE_TTL_SECONDS = 300

_cache: dict[str, tuple[float, list]] = {}


def parse_feed(xml_text: str, limit: int) -> list[dict[str, str | None]]:
    items = []
    for item in ET.fromstring(xml_text).iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google News appends " - Source" to every headline.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        published = None
        if item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(item.findtext("pubDate")).isoformat()
            except (TypeError, ValueError):
                published = None
        items.append(
            {
                "title": title,
                "source": source or None,
                "url": item.findtext("link"),
                "published_at": published,
            }
        )
    items.sort(key=lambda i: i["published_at"] or "", reverse=True)
    return items[:limit]


def get_news(query: str, limit: int = 8) -> list[dict[str, str | None]]:
    """Recent headlines for the query, newest first. Empty list if the feed is down."""
    key = f"{query}|{limit}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    started = time.perf_counter()
    try:
        response = httpx.get(
            FEED_URL,
            params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
            timeout=8,
            follow_redirects=True,
        )
        response.raise_for_status()
        items = parse_feed(response.text, limit)
    except (httpx.HTTPError, ET.ParseError) as exc:
        health.record("news", False, detail=type(exc).__name__)
        return []
    health.record("news", True, ms=(time.perf_counter() - started) * 1000, items=len(items),
                  detail=f"{len(items)} headlines")
    _cache[key] = (time.time(), items)
    return items
