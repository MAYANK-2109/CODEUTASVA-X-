import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

import httpx

from app.tools import health, resolver

FEED_URL = "https://news.google.com/rss/search"
CACHE_TTL_SECONDS = 300

HOLDINGS_WINDOW_DAYS = 7
HOLDINGS_PER_QUERY = 6   # a longer query is cut short by the feed
MAX_HOLDINGS_SEARCHED = 30
HEADLINES_PER_QUERY = 40

WORD_START = r"(?<![\w-])"
SMALL_WORDS = {"of", "and", "the", "for"}

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


def mention_patterns(name: str, ticker: str = "") -> list[re.Pattern]:
    """Whole-word patterns that identify a company in a headline: the first two
    words of its name ("HDFC Bank"), a one-word name ("NTPC"), and its exchange
    symbol ("ONGC", "INDIGO"). A symbol must be written as a name, with a capital
    first letter and all capitals if it has three letters, and not after a hyphen,
    so "ITC" and "Reliance" match but "itch" and "self-reliance" do not."""
    words = resolver.clean_name(name).split()
    phrase = " ".join(words[:2]) if len(words) >= 2 else "".join(words)
    patterns = []
    if len(phrase) >= 3:
        patterns.append(re.compile(rf"{WORD_START}{re.escape(phrase)}\b", re.IGNORECASE))
    symbol = ticker.split(".")[0].upper()
    if len(symbol) == 3 and symbol.isalpha():
        patterns.append(re.compile(rf"{WORD_START}{symbol}\b"))
    elif len(symbol) > 3 and symbol.isalpha():
        # Written as a name ("Reliance", "IndiGo"), so the first letter is a capital.
        patterns.append(re.compile(rf"{WORD_START}{symbol[0]}(?i:{symbol[1:]})\b"))
    short = initials(name)
    if short and short != symbol:
        patterns.append(re.compile(rf"{WORD_START}{short}\b"))
    return patterns


def initials(name: str) -> str | None:
    """The abbreviation headlines use for a long name: "State Bank of India" is
    "SBI". Only for names of three or more words, so it is at least three letters."""
    words = [w for w in resolver.clean_name(name).split() if w.lower() not in SMALL_WORDS and w[0].isalpha()]
    return "".join(w[0] for w in words).upper() if len(words) >= 3 else None


def names_holding(title: str, name: str, ticker: str = "") -> bool:
    return any(pattern.search(title) for pattern in mention_patterns(name, ticker))


def _search_terms(holding: dict) -> list[str]:
    terms = [f'"{resolver.clean_name(holding["name"])}"']
    symbol = holding["ticker"].split(".")[0]
    if len(symbol) >= 4 and symbol.isalpha():
        terms.append(symbol)
    short = initials(holding["name"])
    if short and short not in terms:
        terms.append(short)
    return terms


def get_holdings_news(holdings: list[dict], limit: int = 20) -> list[dict]:
    """Headlines from the last week that name one of the holdings, newest
    first. Each item lists the holdings it names. A search result that only
    mentions a holding in the article body is left out. When there are more
    headlines than `limit`, holdings take turns, so one company in the news
    all week does not crowd out the others."""
    searched = holdings[:MAX_HOLDINGS_SEARCHED]
    queries = [
        " OR ".join(term for h in searched[start:start + HOLDINGS_PER_QUERY] for term in _search_terms(h))
        + f" when:{HOLDINGS_WINDOW_DAYS}d"
        for start in range(0, len(searched), HOLDINGS_PER_QUERY)
    ]
    if not queries:
        return []
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        batches = list(pool.map(lambda query: get_news(query, HEADLINES_PER_QUERY), queries))

    patterns = [(h["name"], mention_patterns(h["name"], h["ticker"])) for h in holdings]
    found: dict[str, dict] = {}
    for item in (item for batch in batches for item in batch):
        named = [name for name, tests in patterns if any(test.search(item["title"]) for test in tests)]
        if named and item["title"] not in found:
            found[item["title"]] = {**item, "holdings": named}
    newest_first = sorted(found.values(), key=lambda i: i["published_at"] or "", reverse=True)
    if len(newest_first) <= limit:
        return newest_first

    waiting = {name: [i for i in newest_first if name in i["holdings"]] for name, _ in patterns}
    waiting = {name: items for name, items in waiting.items() if items}
    chosen: dict[str, dict] = {}
    while len(chosen) < limit and waiting:
        # Each round, every holding adds its newest unused headline, freshest holding first.
        for name in sorted(waiting, key=lambda n: waiting[n][0]["published_at"] or "", reverse=True):
            item = waiting[name].pop(0)
            if len(chosen) < limit:
                chosen.setdefault(item["title"], item)
        waiting = {name: [i for i in items if i["title"] not in chosen] for name, items in waiting.items()}
        waiting = {name: items for name, items in waiting.items() if items}
    return sorted(chosen.values(), key=lambda i: i["published_at"] or "", reverse=True)
