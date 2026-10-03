from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# VADER is a general-purpose lexicon; these market terms are missing from it or
# carry the wrong polarity for financial headlines.
FINANCE_TERMS = {
    "surge": 2.0, "surges": 2.0, "rally": 2.0, "rallies": 2.0, "soar": 2.5, "soars": 2.5,
    "jump": 1.5, "jumps": 1.5, "gain": 1.5, "gains": 1.5, "upgrade": 2.0, "upgrades": 2.0,
    "outperform": 2.0, "beat": 1.5, "beats": 1.5, "bullish": 2.5, "record high": 2.5,
    "plunge": -3.0, "plunges": -3.0, "slump": -2.5, "slumps": -2.5, "tumble": -2.5,
    "tumbles": -2.5, "selloff": -2.5, "sell-off": -2.5, "crash": -3.0, "downgrade": -2.0,
    "downgrades": -2.0, "underperform": -2.0, "miss": -1.5, "misses": -1.5,
    "bearish": -2.5, "default": -2.5, "shutdown": -2.0, "disruption": -1.5,
    "shortfall": -1.5, "probe": -1.5, "penalty": -1.5,
}
POSITIVE_CUTOFF = 0.05
NEGATIVE_CUTOFF = -0.05

_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(FINANCE_TERMS)


def score(text: str) -> float:
    """Compound sentiment in [-1, 1]."""
    return _analyzer.polarity_scores(text)["compound"]


def score_headlines(items: list[dict]) -> dict | None:
    """Aggregate sentiment over news items. None when there is nothing to score."""
    if not items:
        return None
    scored = [{**item, "score": round(score(item["title"]), 2)} for item in items]
    scores = [s["score"] for s in scored]
    return {
        "mean": round(sum(scores) / len(scores), 2),
        "count": len(scores),
        "positive": sum(s >= POSITIVE_CUTOFF for s in scores),
        "negative": sum(s <= NEGATIVE_CUTOFF for s in scores),
        "items": sorted(scored, key=lambda s: s["score"]),
    }


def label(mean: float) -> str:
    if mean >= 0.15:
        return "positive"
    if mean <= -0.15:
        return "negative"
    return "neutral"
