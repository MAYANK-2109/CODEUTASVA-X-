import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

FEED_URL = "https://news.google.com/rss/search"
CACHE_TTL_SECONDS = 300

_cache: dict[str, object] = {"key": None, "at": 0.0, "value": None}


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
    if _cache["key"] == key and time.time() - _cache["at"] < CACHE_TTL_SECONDS:
        return _cache["value"]
    try:
        response = httpx.get(
            FEED_URL,
            params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
            timeout=8,
            follow_redirects=True,
        )
        response.raise_for_status()
        items = parse_feed(response.text, limit)
    except (httpx.HTTPError, ET.ParseError):
        return []
    _cache.update(key=key, at=time.time(), value=items)
    return items
