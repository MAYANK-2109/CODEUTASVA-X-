"""Historical event corpus and a small TF-IDF search over it."""

import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

EVENTS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "events.json"

STOPWORDS = set(
    "a an and are as at be by for from how if in into is it its my of on or our "
    "that the this to was what when will with would does do did i we me can could "
    "should affect impact happen happens portfolio holdings stocks stock shares".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", text.lower()) if t not in STOPWORDS and len(t) > 2]


@lru_cache(maxsize=1)
def _index() -> tuple[list[dict], list[dict[str, float]], dict[str, float]]:
    events = json.loads(EVENTS_FILE.read_text())
    docs = [Counter(tokenize(f'{e["title"]} {e["description"]} {e["tags"]}')) for e in events]
    doc_freq = Counter(term for doc in docs for term in doc)
    idf = {term: math.log(len(docs) / freq) + 1 for term, freq in doc_freq.items()}
    vectors = [{term: count * idf[term] for term, count in doc.items()} for doc in docs]
    return events, vectors, idf


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(weight * b.get(term, 0.0) for term, weight in a.items())
    norm = math.sqrt(sum(w * w for w in a.values())) * math.sqrt(sum(w * w for w in b.values()))
    return dot / norm if norm else 0.0


def load_events() -> list[dict]:
    return _index()[0]


def find_similar_events(query: str, event_type: str | None = None, limit: int = 6) -> list[dict]:
    """Past events ranked by text similarity to the query.

    With an `event_type`, only events of that type are returned. Without one,
    only events sharing vocabulary with the query are.
    """
    events, vectors, idf = _index()
    query_vector = {
        term: count * idf.get(term, 0.0) for term, count in Counter(tokenize(query)).items()
    }
    ranked = []
    for event, vector in zip(events, vectors):
        if event_type and event["type"] != event_type:
            continue
        similarity = _cosine(query_vector, vector)
        if event_type or similarity > 0.05:
            ranked.append({**event, "similarity": round(similarity, 2)})
    ranked.sort(key=lambda e: (e["similarity"], e["date"]), reverse=True)
    return ranked[:limit]
