# CODEUTASVA-X-
CU 2026

## Structure

- `frontend/` - React + Vite + Tailwind dashboard (Supabase auth)
- `backend/` - FastAPI + LangGraph multi-agent API

## Run

Frontend (http://localhost:5173):

```bash
cd frontend
npm install
npm run dev
```

Backend (http://localhost:8000):

```bash
cd backend
cp .env.example .env   # then fill in keys
uv sync
uv run uvicorn app.main:app --reload
```

Backend tests:

```bash
cd backend
uv run pytest
```
