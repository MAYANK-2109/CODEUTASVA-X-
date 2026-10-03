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

SECTOR_HEADLINES_PER_QUERY = 30
MAX_SECTORS_SEARCHED = 10

WORD_START = r"(?<![\w-])"
SMALL_WORDS = {"of", "and", "the", "for"}
# Symbols that are also a common abbreviation, and the words that give the other meaning away.
OTHER_MEANINGS = {"ITC": re.compile(r"\b(?:GST|CGST|SGST|IGST|input tax|tax credit)\b", re.IGNORECASE)}

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
    other_meaning = OTHER_MEANINGS.get(ticker.split(".")[0].upper())
    if other_meaning and other_meaning.search(title):
        return False
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

    found: dict[str, dict] = {}
    for item in (item for batch in batches for item in batch):
        named = [h["name"] for h in holdings if names_holding(item["title"], h["name"], h["ticker"])]
        if named and item["title"] not in found:
            found[item["title"]] = {**item, "holdings": named}
    newest_first = sorted(found.values(), key=lambda i: i["published_at"] or "", reverse=True)
    if len(newest_first) <= limit:
        return newest_first

    return _take_turns(newest_first, {h["name"]: [i for i in newest_first if h["name"] in i["holdings"]]
                                      for h in holdings}, limit)


# --------------------------------------------------------------------------- sector news

# Per sector: the name shown, what to search for, and the words a headline must
# contain to count as being about that sector. Matching ignores case unless noted.
SECTOR_TOPICS = {
    "Steel": ("Steel", '"steel sector" OR "steel prices" OR steelmakers OR "iron ore"',
              r"steel\w*|iron ore|coking coal"),
    "Metals": ("Metals and mining", '"metal stocks" OR aluminium OR copper OR "metal prices" OR mining',
               r"metals?|alumini?um|copper|zinc|mining|miners?|iron ore|steel\w*"),
    "Energy": ("Oil and gas", '"oil and gas" OR "crude oil" OR refinery OR "fuel prices" OR OPEC',
               r"crude|(?<!edible )(?<!palm )(?<!cooking )oil|gas|refiner\w*|petrol|diesel|fuel|OPEC\+?|LNG|petroleum"),
    "Utilities": ("Power", '"power sector" OR electricity OR "power demand" OR discoms OR "renewable energy"',
                  r"power (?:sector|demand|plants?|tariffs?|grid|generation|supply|producers?|ministry|cuts?|stocks)"
                  r"|electricity|discoms?|renewable\w*|solar|thermal|hydro\w*|coal"),
    "Banking": ("Banking", 'banks OR "banking sector" OR RBI OR "credit growth"',
                r"banks?|banking|RBI|NPAs?|lending|lenders?|credit growth|deposits?|repo rate"),
    "Financials": ("Financial services", 'NBFC OR insurers OR "insurance sector" OR "mutual funds" OR "financial services"',
                   r"NBFCs?|insur\w+|mutual funds?|financial services|fintech|brokerages?|SEBI|IRDAI"),
    "IT": ("IT services", '"IT sector" OR "IT services" OR "IT stocks" OR "Nifty IT" OR "software exports"',
           r"(?-i:IT) (?:sector|services|stocks|firms|companies|majors|industry|index)|Nifty IT|software"
           r"|tech (?:services|stocks|layoffs)|outsourcing|H-?1B"),
    "FMCG": ("FMCG", 'FMCG OR "consumer goods" OR "rural demand"',
             r"FMCG|consumer goods|rural demand|packaged foods?|staples"),
    "Airlines": ("Aviation", 'airlines OR aviation OR airfares OR DGCA OR "jet fuel"',
                 r"airlines?|aviation|airfares?|DGCA|air travel|flights?|airports?|ATF|jet fuel"),
    "Auto": ("Automobiles", '"auto sector" OR "car sales" OR "vehicle sales" OR automakers OR "two-wheeler"',
             r"auto(?:mobile|maker|motive)?s?|car sales|vehicles?|two-wheelers?|EVs?|SUVs?|SIAM|tractors?|carmakers?"),
    "Pharma": ("Pharma and healthcare", 'pharma OR drugmakers OR USFDA OR "healthcare sector"',
               r"pharma\w*|drug\w*|USFDA|healthcare|hospitals?|generics?|biotech\w*"),
    "Infrastructure": ("Infrastructure", 'infrastructure OR "capital expenditure" OR "order book" OR ports OR highways',
                       r"infrastructure|infra|capex|capital expenditure|order book|ports?|highways?|construction|railways?"),
    "Cement": ("Cement", '"cement sector" OR "cement prices" OR "cement companies"', r"cement"),
    "Paints": ("Paints", '"paint industry" OR "paint companies" OR "paint makers"', r"paints?|paintmakers?"),
    "Telecom": ("Telecom", 'telecom OR "tariff hike" OR spectrum OR TRAI OR 5G',
                r"telecom\w*|telcos?|spectrum|TRAI|5G|tariff hikes?|ARPU"),
    "Real Estate": ("Real estate", '"real estate" OR realty OR "housing sales" OR "home sales"',
                    r"real estate|realty|housing|home sales|property|developers?"),
    "Consumer": ("Retail and consumer", 'jewellery OR retailers OR "consumer durables" OR "festive demand"',
                 r"jewel\w+|retail\w*|consumer durables?|festive (?:demand|sales)|apparel"),
    "Commodities": ("Gold and silver", '"gold prices" OR "silver prices" OR bullion', r"gold|silver|bullion"),
    "Chemicals": ("Chemicals", '"chemical sector" OR "specialty chemicals" OR "chemical stocks"',
                  r"chemicals?|agrochem\w*|fertili[sz]ers?"),
    "Defence": ("Defence", '"defence stocks" OR "defence sector" OR "defence orders"',
                r"defen[cs]e|aerospace|missiles?"),
    "Capital Goods": ("Capital goods", '"capital goods" OR "engineering companies" OR "order inflows"',
                      r"capital goods|engineering|order inflows?|machinery"),
    "Travel": ("Travel and hotels", 'hotels OR tourism OR "travel demand" OR hospitality',
               r"hotels?|tourism|travel|hospitality|restaurants?|QSR"),
}
SECTOR_PATTERNS = {key: re.compile(rf"{WORD_START}(?:{words})\b", re.IGNORECASE)
                   for key, (_, _, words) in SECTOR_TOPICS.items()}
GLOBAL_TOPICS = {"Energy", "Commodities"}
# Companies whose news topic is narrower than their sector.
TOPIC_OF_SYMBOL = {"TATASTEEL": "Steel", "JSWSTEEL": "Steel", "SAIL": "Steel", "JINDALSTEL": "Steel",
                   "JSL": "Steel", "NMDC": "Steel"}


def news_topic(holding: dict) -> str | None:
    """The sector topic a holding brings news for; None when its sector is unknown."""
    topic = TOPIC_OF_SYMBOL.get(holding["ticker"].split(".")[0], holding.get("sector"))
    return topic if topic in SECTOR_TOPICS else None


def _take_turns(newest_first: list[dict], groups: dict[str, list[dict]], limit: int) -> list[dict]:
    """Up to `limit` items, each group adding its newest unused one per round."""
    waiting = {name: list(items) for name, items in groups.items() if items}
    chosen: dict[str, dict] = {}
    while len(chosen) < limit and waiting:
        for name in sorted(waiting, key=lambda n: waiting[n][0]["published_at"] or "", reverse=True):
            item = waiting[name].pop(0)
            if len(chosen) < limit:
                chosen.setdefault(item["title"], item)
        waiting = {name: [i for i in items if i["title"] not in chosen] for name, items in waiting.items()}
        waiting = {name: items for name, items in waiting.items() if items}
    return sorted(chosen.values(), key=lambda i: i["published_at"] or "", reverse=True)


def get_sector_news(holdings: list[dict], limit: int = 30) -> dict:
    """Headlines from the last week about the sectors the holdings are in.

    A headline counts when it uses that sector's vocabulary or names a holding
    in it; anything else the search returns is dropped. Returns the items
    (newest first, each tagged with its sectors and any holdings it names),
    the sectors searched with their holdings, and the holdings with no known sector.
    """
    topics: dict[str, list[dict]] = {}
    unmapped = []
    for h in holdings:
        topic = news_topic(h)
        if topic is None:
            unmapped.append(h["name"])
        else:
            topics.setdefault(topic, []).append(h)
    # Sectors with the most holdings first; beyond the cap they are not searched.
    ranked = sorted(topics, key=lambda t: -len(topics[t]))[:MAX_SECTORS_SEARCHED]
    if not ranked:
        return {"items": [], "sectors": [], "unmapped": unmapped}

    window = f" when:{HOLDINGS_WINDOW_DAYS}d"
    searched = [h for topic in ranked for h in topics[topic]][:MAX_HOLDINGS_SEARCHED]
    # World prices drive oil and bullion; for every other sector the search is kept to India.
    queries = [(SECTOR_TOPICS[topic][1] + ("" if topic in GLOBAL_TOPICS else " India") + window,
                SECTOR_HEADLINES_PER_QUERY) for topic in ranked] + [
        (" OR ".join(term for h in searched[start:start + HOLDINGS_PER_QUERY] for term in _search_terms(h)) + window,
         HEADLINES_PER_QUERY)
        for start in range(0, len(searched), HOLDINGS_PER_QUERY)
    ]
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        batches = list(pool.map(lambda query: get_news(*query), queries))

    found: dict[str, dict] = {}
    for item in (item for batch in batches for item in batch):
        if item["title"] in found:
            continue
        named = [h for topic in ranked for h in topics[topic] if names_holding(item["title"], h["name"], h["ticker"])]
        about = [topic for topic in ranked
                 if SECTOR_PATTERNS[topic].search(item["title"]) or any(h in topics[topic] for h in named)]
        if about:
            found[item["title"]] = {**item, "sectors": [SECTOR_TOPICS[t][0] for t in about],
                                    "holdings": [h["name"] for h in named]}
    newest_first = sorted(found.values(), key=lambda i: i["published_at"] or "", reverse=True)
    labels = [SECTOR_TOPICS[topic][0] for topic in ranked]
    items = newest_first if len(newest_first) <= limit else _take_turns(
        newest_first, {label: [i for i in newest_first if label in i["sectors"]] for label in labels}, limit)
    return {
        "items": items,
        "sectors": [{"sector": SECTOR_TOPICS[topic][0], "holdings": [h["name"] for h in topics[topic]]}
                    for topic in ranked],
        "unmapped": unmapped,
    }
