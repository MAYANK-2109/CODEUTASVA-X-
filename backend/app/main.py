import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import router

load_dotenv()

app = FastAPI(title="Financial Intelligence Terminal API")

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

from app.tools import gdelt, market, sentiment, vector_store  # noqa: E402


@app.on_event("startup")
def start_background_work() -> None:
    """Runs when the server starts, not when a test imports the app: connect
    to the vector database, begin the news scan and fetch price history."""
    vector_store.warm_up_in_background()
    gdelt.start_background_scan()
    market.warm_up_in_background()
    # Loading the sentiment model takes seconds; do it now, not inside the first request.
    threading.Thread(target=sentiment.backend, daemon=True, name="warm-sentiment").start()


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root() -> dict[str, str]:
    """The host's uptime check asks for this path."""
    return {"status": "ok", "service": "Financial Intelligence Terminal API"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
