# Camdock International School Portal: Backend

REST API for the Camdock International School Portal (Enugu State, Nigeria). Requirements are in
[`../DOCS/Camdock_International_School_Backend_SRS.md`](../DOCS/Camdock_International_School_Backend_SRS.md).

## Status

Phase 1 (foundation) only. What exists:

- FastAPI app factory, environment-based settings, and a consistent error format.
- SQLAlchemy session handling and Alembic, **with no tables or migrations yet**.
- `GET /api/v1/health`.
- Docker Compose with PostgreSQL, and an automated test suite.

There are no school features yet (authentication, students, results, fees). They arrive in later phases.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Python 3.12 (pinned in `.python-version`)
- Docker, for PostgreSQL and the containerised API

Use `uv` for all Python dependency management. Do not use `pip`.

## Configuration

Settings are read from environment variables (and a local `.env` file). Never commit `.env`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ENVIRONMENT` | no | `development` | `development`, `test` or `production`. API docs are enabled only in `development`. |
| `DATABASE_URL` | **yes** | none | SQLAlchemy URL, for example `postgresql+psycopg://user:password@host:5432/dbname`. The app refuses to start without it. |
| `API_V1_PREFIX` | no | `/api/v1` | URL prefix for the API. |

Two separate `.env` files exist, each with a committed `.env.example`:

- `backend/.env.example`: settings for running the app directly (above).
- `../.env.example`: `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, used by Docker Compose.
  `POSTGRES_PASSWORD` has no default. Use a URL-safe password (letters and digits) because Compose
  embeds it in `DATABASE_URL`.

## Run with Docker Compose

From the repository root (`CAMDOCK/`):

```bash
cp .env.example .env          # then edit .env and set POSTGRES_PASSWORD
docker compose up -d --build --wait
curl http://127.0.0.1:8000/api/v1/health
```

| Service | Address (localhost only) |
|---|---|
| API | `127.0.0.1:8000` (docs at `/docs` in development) |
| PostgreSQL | `127.0.0.1:5433` |

The API container does not run migrations on its own. To apply them:

```bash
docker compose run --rm api alembic upgrade head
```

Stop with `docker compose down`. Add `-v` to also delete the database volume.

## Run locally without Docker for the API

```bash
cd backend
uv sync
cp .env.example .env          # set DATABASE_URL to a reachable PostgreSQL database
uv run uvicorn app.main:create_app --factory --reload
```

The app is created by a factory, so the `--factory` flag is required.

## API conventions

All errors use one JSON shape:

```json
{"error": {"code": "validation_error", "message": "Request validation failed.",
           "details": [{"field": "body.score", "message": "Input should be less than or equal to 100"}]}}
```

- `details` appears only for validation errors (422). It lists field paths and messages, never the submitted values.
- Unhandled errors return a generic 500. Details go to the server log only.
- Raise `app.core.errors.AppError(message, status_code=..., code=...)` for domain errors such as 409 conflicts.

`GET /api/v1/health` is public.

| Case | Response |
|---|---|
| Database reachable | `200 {"status": "ok", "database": "ok"}` |
| Database unreachable | `503 {"error": {"code": "service_unavailable", ...}}` with no connection details |

Database connections time out after 5 seconds.

## Database migrations

```bash
uv run alembic upgrade head                           # apply
uv run alembic revision --autogenerate -m "message"   # create (once models exist)
```

Alembic reads `DATABASE_URL` from the same settings as the app. No URL is stored in `alembic.ini`.

## Tests, lint and formatting

```bash
uv run pytest                 # runs without any database
uv run ruff check
uv run ruff format --check
```

Three tests need a real PostgreSQL database and are skipped unless `TEST_DATABASE_URL` is set:

```bash
TEST_DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5433/camdock_test" uv run pytest
```

Use `127.0.0.1`, not `localhost`, for the Compose database. Compose publishes PostgreSQL on IPv4
only, and on Windows `localhost` tries IPv6 first, which made the test run take minutes instead of seconds.
Create the test database first with `docker compose exec db createdb -U camdock camdock_test`.

Safety rule: the database name in `TEST_DATABASE_URL` must end in `_test`, or the test run refuses to
start. Tests run Alembic against this database, so never point it at real data.

Every test runs with `ENVIRONMENT=test` and a placeholder `DATABASE_URL`, and settings caches are
cleared between tests (see `tests/conftest.py`).

## Layout

```
backend/
  app/
    routers/       endpoint modules (health)
    core/          settings and error handling
    db/            SQLAlchemy base and session
    main.py        create_app() factory
  migrations/      Alembic environment (no revisions yet)
  tests/
  Dockerfile
```
