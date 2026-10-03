import json
from collections.abc import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import llm
from app.agents.graph import run
from app.ml.alerts import build_alerts
from app.tools import sentiment, vector_store

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    holdings: list[dict] | None = None


def _stream(request: ChatRequest) -> Iterator[str]:
    try:
        for event in run(request.query.strip(), request.holdings):
            yield json.dumps(event, ensure_ascii=False) + "\n"
    except Exception as exc:  # the stream has started, so report in-band
        yield json.dumps({"type": "error", "message": f"Analysis failed: {exc}"}) + "\n"


class AlertsRequest(BaseModel):
    holdings: list[dict] | None = None
    # A named drill adds one hypothetical signal, clearly labelled, to rehearse the response.
    scenario: str | None = None


@router.post("/alerts")
def alerts(request: AlertsRequest) -> dict:
    """Live alerts for the holdings: sudden moves, elevated risk, losses,
    concentration, negative news, weather and market stress."""
    return build_alerts(request.holdings, request.scenario)


@router.get("/alerts/drills")
def alert_drills() -> dict:
    """The hypothetical signals that can be rehearsed, and the state of the news scan."""
    from app.ml.radar import DRILLS
    from app.tools import gdelt

    return {"drills": [{"id": key, "title": drill["title"]} for key, drill in DRILLS.items()],
            "news_scan": gdelt.signals()}


@router.get("/vector/status")
def vector_status() -> dict:
    """Which search backend is live, and the latest embed-and-index timings."""
    return vector_store.status()


@router.get("/sentiment/status")
def sentiment_status() -> dict:
    """Which sentiment model is scoring headlines."""
    return sentiment.status()


@router.get("/llm/status")
def llm_status() -> dict:
    """Whether the LLM is configured, which model answered, and the last failure if any."""
    return llm.status()


@router.post("/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    """Newline-delimited JSON: a start event, one event per agent, then the answer."""
    return StreamingResponse(_stream(request), media_type="application/x-ndjson")
