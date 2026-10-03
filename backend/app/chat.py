import json
from collections.abc import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.graph import run
from app.tools import vector_store

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


@router.get("/vector/status")
def vector_status() -> dict:
    """Which search backend is live, and the latest embed-and-index timings."""
    return vector_store.status()


@router.post("/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    """Newline-delimited JSON: a start event, one event per agent, then the answer."""
    return StreamingResponse(_stream(request), media_type="application/x-ndjson")
