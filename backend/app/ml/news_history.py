"""Weekly market-news sentiment back to 2015, for testing whether news adds
anything to the risk model.

Each week's headlines are searched on Google News by date, scored with the
sentiment model, and averaged. A week with too few headlines stays empty
rather than being filled in.

Build it with:  uv run python -m app.ml.news_history
"""

import json
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.tools import sentiment

OUT_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "trained" / "news_sentiment.json"
RAW_FILE = OUT_FILE.parent.parent / "cache" / "news_history_raw.json"
QUERY = "Sensex Nifty"
FIRST_WEEK = date(2015, 1, 5)        # a Monday
MIN_HEADLINES = 5                    # fewer than this is not a reading of the week's mood
FETCH_WORKERS = 6
FEED = "https://news.google.com/rss/search"


def _week_headlines(start: date) -> list[str] | None:
    """Headlines published in the seven days from `start`. None when the feed did not answer."""
    end = start + timedelta(days=7)
    for attempt in range(3):
        try:
            response = httpx.get(FEED, params={"q": f"{QUERY} after:{start} before:{end}", "hl": "en-IN",
                                               "gl": "IN", "ceid": "IN:en"}, timeout=20, follow_redirects=True)
            response.raise_for_status()
            return [item.findtext("title") or "" for item in ET.fromstring(response.content).findall(".//item")]
        except (httpx.HTTPError, ET.ParseError):
            time.sleep(2 + 4 * attempt)
    return None


def fetch(until: date) -> dict[str, list[str]]:
    """Every week's headlines, resuming from what is already saved."""
    try:
        raw = json.loads(RAW_FILE.read_text())
    except (OSError, ValueError):
        raw = {}
    due, week = [], FIRST_WEEK
    while week + timedelta(days=7) <= until:
        if week.isoformat() not in raw:
            due.append(week)
        week += timedelta(days=7)
    RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for done, (start, titles) in enumerate(zip(due, pool.map(_week_headlines, due)), 1):
            if titles is not None:
                raw[start.isoformat()] = titles
            if done % 25 == 0:
                RAW_FILE.write_text(json.dumps(raw))
                print(f"{start}: {len(raw)} weeks fetched", flush=True)
    RAW_FILE.write_text(json.dumps(raw))
    return raw


def build(until: date | None = None) -> dict:
    raw = fetch(until or date.today())
    scorer = sentiment.backend()
    negative_cutoff = sentiment.cutoffs()[1]
    weeks = []
    for key in sorted(raw):
        titles = [title for title in dict.fromkeys(raw[key]) if title]
        if len(titles) < MIN_HEADLINES:
            weeks.append({"week": key, "headlines": len(titles), "mean": None, "negative_share": None})
            continue
        scores = sentiment.score_many(titles)
        weeks.append({"week": key, "headlines": len(titles), "mean": round(sum(scores) / len(scores), 4),
                      "negative_share": round(sum(s <= negative_cutoff for s in scores) / len(scores), 4)})
    if sentiment.backend() != scorer:
        raise RuntimeError(f"the scorer changed from {scorer} to {sentiment.backend()} part-way; nothing was written")
    scored = [w for w in weeks if w["mean"] is not None]
    out = {
        "built_on": date.today().isoformat(),
        "source": f'Google News RSS, search "{QUERY}" by week',
        "scorer": scorer,
        "weeks_scored": len(scored),
        "weeks_too_thin": len(weeks) - len(scored),
        "headlines": sum(w["headlines"] for w in scored),
        "min_headlines": MIN_HEADLINES,
        "weeks": weeks,
    }
    OUT_FILE.write_text(json.dumps(out))
    return out


def load() -> dict | None:
    try:
        return json.loads(OUT_FILE.read_text())
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    result = build()
    print({k: v for k, v in result.items() if k != "weeks"}, file=sys.stderr)
