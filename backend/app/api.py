import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ingestion.news import get_news
from app.ingestion.prices import get_latest_prices
from app.tools import resolver

PORTFOLIO_FILE = Path(__file__).resolve().parent.parent / "data" / "portfolio.json"

router = APIRouter(prefix="/api")


def load_portfolio() -> dict:
    return json.loads(PORTFOLIO_FILE.read_text())


@router.get("/portfolio")
def portfolio() -> dict:
    data = load_portfolio()
    prices, as_of = get_latest_prices([h["ticker"] for h in data["holdings"]])

    holdings = []
    for h in data["holdings"]:
        current_price = prices.get(h["ticker"])
        holdings.append(
            {
                **h,
                "invested_value": round(h["shares"] * h["avg_price"], 2),
                "current_price": current_price,
                "current_value": (
                    round(h["shares"] * current_price, 2)
                    if current_price is not None
                    else None
                ),
            }
        )

    # A total that silently skipped unpriced holdings would understate the
    # portfolio, so it is only reported when every holding has a price.
    all_priced = all(h["current_value"] is not None for h in holdings)
    return {
        "currency": data["currency"],
        "holdings": holdings,
        "totals": {
            "invested_value": round(sum(h["invested_value"] for h in holdings), 2),
            "current_value": (
                round(sum(h["current_value"] for h in holdings), 2)
                if all_priced
                else None
            ),
        },
        "prices_as_of": as_of,
    }


@router.get("/news")
def news(limit: int = 8) -> dict:
    names = [h["name"] for h in load_portfolio()["holdings"]]
    query = " OR ".join(f'"{name}"' for name in names) + " when:2d"
    return {"items": get_news(query, limit)}


class HoldingRef(BaseModel):
    key: str
    symbol: str = ""
    isin: str = ""
    name: str = ""


class PricesRequest(BaseModel):
    holdings: list[HoldingRef] = Field(default_factory=list, max_length=300)
    symbols: list[str] = Field(default_factory=list, max_length=300)


@router.post("/prices")
def prices(request: PricesRequest) -> dict:
    """Latest market price per holding, found by symbol, ISIN or company name.

    Holdings that cannot be priced are left out, never given a stand-in value.
    """
    refs = request.holdings + [HoldingRef(key=s, symbol=s) for s in request.symbols if s.strip()]
    options = {ref.key: resolver.candidates(ref.symbol, ref.isin, ref.name) for ref in refs}
    if not any(options.values()):
        return {"prices": {}, "tickers": {}, "as_of": None}

    # First choice for everyone in one request, then the alternatives only
    # for holdings the first choice could not price.
    latest, as_of = get_latest_prices(sorted({o[0] for o in options.values() if o}))
    unpriced = {key: o[1:] for key, o in options.items() if o and o[0] not in latest}
    # A symbol that prices nothing may simply be wrong in the file, so the
    # company name gets a turn as well.
    names = {ref.key: ref.name for ref in refs}
    for key, alternatives in unpriced.items():
        by_name = resolver.search_name(names[key]) if names[key] else None
        if by_name and by_name not in options[key]:
            alternatives.append(by_name)
            options[key].append(by_name)
    unpriced = {key: alts for key, alts in unpriced.items() if alts}
    if unpriced:
        more, more_as_of = get_latest_prices(sorted({t for alts in unpriced.values() for t in alts}))
        latest = {**latest, **more}
        as_of = as_of or more_as_of

    result, tickers = {}, {}
    for key, tried in options.items():
        ticker = next((t for t in tried if t in latest), None)
        if ticker:
            result[key], tickers[key] = latest[ticker], ticker
    return {"prices": result, "tickers": tickers, "as_of": as_of}
