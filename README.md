# FinLens

Plain-English questions about NSE-listed Indian companies, answered from their
annual reports with every figure cited to a PDF page.

Status: Day 1 — repo, schema, company list and resolver.

## Setup

```bash
cp .env.example .env
docker compose up -d db                       # Postgres 16 + pgvector on :5433
uv sync
uv run alembic upgrade head                   # create / update the schema
uv run python -m finlens.companies.nse_master # load ~2,600 NSE companies
uv run uvicorn finlens.main:app --reload
curl "localhost:8000/companies/resolve?q=Tata%20Consultancy"
uv run pytest
```

## Layout

```
src/finlens/
  main.py                       app creation, routers, startup/shutdown (lifespan)
  api/deps.py                   shared dependencies (resolver, http, db session)
  api/routes/                   one APIRouter per resource
  schemas/                      Pydantic request/response models (public API shapes)
  config.py, db.py, models.py   settings, engine + get_session, the four tables
  http.py                       HttpService: the only way to make outbound requests
                                (robots.txt, per-host rate limit, retries, streaming)
  repositories/                 data access per table; never commits, the caller does
  companies/                    NSE company list + name resolver
  ingestion/ocr/                GLM-OCR on Modal + reconciliation (from the rag research)
docs/sources.md                 where reports come from and why
```

## Changing the schema

Edit `src/finlens/models.py`, then:

```bash
uv run alembic revision --autogenerate -m "add x to reports"   # writes migrations/versions/...
# read the generated file: autogenerate misses extensions, renames and data changes
uv run alembic upgrade head
```

`tests/test_migrations.py` fails if models and migrations drift apart.

The OCR scripts still run standalone from their folder (see their docstrings);
they become part of the ingestion job on Day 2.
