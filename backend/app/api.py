import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ingestion.news import get_news
from app.ingestion.prices import get_latest_prices
from app.tools.market import to_ticker

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


class PricesRequest(BaseModel):
    symbols: list[str] = Field(max_length=200)


@router.post("/prices")
def prices(request: PricesRequest) -> dict:
    """Latest market price per symbol. Symbols that cannot be priced are left out."""
    tickers = {symbol: to_ticker(symbol) for symbol in request.symbols if symbol.strip()}
    if not tickers:
        return {"prices": {}, "as_of": None}
    latest, as_of = get_latest_prices(sorted(set(tickers.values())))
    return {
        "prices": {symbol: latest[ticker] for symbol, ticker in tickers.items() if ticker in latest},
        "as_of": as_of,
    }
