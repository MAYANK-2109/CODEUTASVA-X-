import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router

load_dotenv()

app = FastAPI(title="Financial Intelligence Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

from app.tools import vector_store  # noqa: E402
vector_store.warm_up_in_background()

from app.tools import gdelt  # noqa: E402
gdelt.start_background_scan()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
