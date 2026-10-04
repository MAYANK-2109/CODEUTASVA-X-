import logging
import os

from app.tools import memory

memory.cap_arenas()  # before any thread starts, so every thread shares the same few heaps

from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app.api import router  # noqa: E402

load_dotenv()

app = FastAPI(title="Financial Intelligence Terminal API")


class SafeErrors:
    """Turn an unhandled error into a JSON 500. It sits inside the CORS layer, so
    the browser gets a readable error instead of reporting a CORS failure."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        started = False

        async def watch(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.inner(scope, receive, watch)
        except Exception:
            if started:  # the reply is already on its way; nothing can be put in its place
                raise
            logging.getLogger("uvicorn.error").exception("Unhandled error on %s", scope.get("path"))
            reply = JSONResponse({"detail": "The server hit an error handling this request."}, status_code=500)
            await reply(scope, receive, send)


app.add_middleware(SafeErrors)  # added first, so every later layer (CORS, compression) wraps it

origins = [
    "https://codeutasva-x.vercel.app",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https://.*\.vercel\.app$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Large JSON answers (insights, ideas, alerts) travel compressed.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Register routers
from app.portfolio.router import router as portfolio_router  # noqa: E402
app.include_router(portfolio_router)


app.include_router(router)

from app.chat import router as chat_router  # noqa: E402
app.include_router(chat_router)

from app.insights import router as insights_router  # noqa: E402
app.include_router(insights_router)

from app.terminal import router as terminal_router  # noqa: E402
app.include_router(terminal_router)

from app.broker.router import router as broker_router  # noqa: E402
app.include_router(broker_router)

from app.advisor.router import router as advisor_router  # noqa: E402
app.include_router(advisor_router)

import threading  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402

from app.ingestion import stream  # noqa: E402
from app.tools import gdelt, market, sentiment, vector_store  # noqa: E402

KEEP_AWAKE_SECONDS = 600   # the free host stops a service after 15 minutes without a request


def _keep_awake(url: str) -> None:
    """Ask for our own public address so the host never counts the service as idle."""
    while True:
        time.sleep(KEEP_AWAKE_SECONDS)
        try:
            httpx.get(f"{url}/health", timeout=20)
        except httpx.HTTPError:
            pass


@app.on_event("startup")
def start_background_work() -> None:
    """Runs when the server starts, not when a test imports the app: connect
    to the vector database, begin the news scan and fetch price history."""
    vector_store.warm_up_in_background()
    gdelt.start_background_scan()
    market.warm_up_in_background()
    stream.start_in_background()
    # Loading the sentiment model takes seconds; do it now, not inside the first request.
    threading.Thread(target=sentiment.backend, daemon=True, name="warm-sentiment").start()
    # Render sets RENDER_EXTERNAL_URL; KEEP_AWAKE_URL does the same job on another host.
    public_url = os.getenv("KEEP_AWAKE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if public_url:
        threading.Thread(target=_keep_awake, args=(public_url.rstrip("/"),), daemon=True, name="keep-awake").start()


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root() -> dict[str, str]:
    """The host's uptime check asks for this path."""
    return {"status": "ok", "service": "Financial Intelligence Terminal API"}


@app.get("/health")
def health() -> dict:
    """Up, and how much of the memory limit is in use (null where it cannot be read)."""
    return {"status": "ok", "memory": memory.status()}
