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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
