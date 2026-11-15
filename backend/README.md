# Camdock International School Portal: Backend

REST API for the Camdock International School Portal (Enugu State, Nigeria), built with
FastAPI and PostgreSQL. Requirements are in
[`../DOCS/Camdock_International_School_Backend_SRS.md`](../DOCS/Camdock_International_School_Backend_SRS.md).

## Status

The backend is feature-complete for the SRS scope and has been hardened for release.

| Area | What exists |
|---|---|
| Accounts | Sign-in with bearer tokens stored as hashes, sign-out, three roles (admin, teacher, parent), failed-sign-in blocking, an account directory with disable and enable, changing your own password, and password recovery with one-time codes an administrator issues (plus a server command for a locked-out administrator). |
| School records | Classes, subjects, academic years and terms, students, teachers and their assignments, parent-to-child links. |
| Results | Score entry per student, subject and term, grades and averages, printable result sheets. |
| Fees | Fee types, bills, instalment payments (with an idempotency key for safe retries), receipts, balances. |
| Dashboards | Per-role overview (admin, teacher, parent) and the public school statistics. |
| Security | Role checks on every endpoint, rate limits, security headers, CORS allowlist, request size cap, production start-up checks, audit log. |
| Operations | Request ids and safe JSON logs, `/health` and `/health/ready`, Docker image running as a non-root user, backup and restore guide. |

The full endpoint list, with every field, is the OpenAPI file
[`docs/openapi.json`](docs/openapi.json). Rules the schema cannot say (money format,
errors, status codes, retries) are in [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).

## Documentation

| Document | For |
|---|---|
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | Frontend developers: how to call the API. |
| [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) | Every setting and what it does. |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Putting the system on a server. |
| [`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md) | Saving and recovering the school's data. |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Running it day to day: logs, blocked users, troubleshooting. |
| [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) | The SRS Section 8 criteria, where each is proved, and the recorded run. |

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Python 3.12 (pinned in `.python-version`)
- Docker, for PostgreSQL and the containerised API

Use `uv` for all Python dependency management. Do not use `pip`.

## Run it for development

From the repository root (`CAMDOCK/`), start PostgreSQL:

```bash
cp .env.example .env          # set POSTGRES_PASSWORD (letters and digits only)
docker compose up -d db --wait
```

Then the API, from `backend/`:

```bash
cd backend
uv sync
cp .env.example .env          # set DATABASE_URL to match the database password above
uv run alembic upgrade head
uv run python -m app.db.seed --academic-year 2026/2027 --start-date 2026-09-07 --end-date 2027-07-23 --current
uv run python -m app.cli create-admin --email head@school.example
uv run uvicorn app.main:create_app --factory --reload
```

- The database is published on `127.0.0.1:5433`, so a local `DATABASE_URL` is
  `postgresql+psycopg://camdock:<password>@127.0.0.1:5433/camdock`. Use `127.0.0.1`, not
  `localhost`: on Windows `localhost` tries IPv6 first and is very slow.
- `uv run fastapi dev app/main.py` also works.
- Interactive docs are at <http://127.0.0.1:8000/docs> in development only.
- `create-admin` asks for a password twice (at least 12 characters). There is no
  `--password` option on purpose; set `CAMDOCK_ADMIN_PASSWORD` for a non-interactive run.
- The seed is safe to run again. It loads classes, subjects (the prototype's defaults, not
  the school's confirmed curriculum) and the terms; it creates no users or money records.

To run the API in Docker too: `docker compose up -d --build --wait`, then
`docker compose run --rm api alembic upgrade head`. The container does not run migrations
by itself. Stop with `docker compose down`; add `-v` only if you want to **delete** the
database.

## Deploy to a server

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). It uses `docker-compose.prod.yml` for
production mode, HTTPS through a proxy, no public database port and log rotation.

## Health checks

| Endpoint | Meaning |
|---|---|
| `GET /api/v1/health` | 200 when the API reaches its database, otherwise 503. |
| `GET /api/v1/health/ready` | 200 only when the database is also at the migration this code expects; otherwise 503. |

Neither needs a login or counts against the rate limit, and a 503 never reveals connection
details.

## Database migrations

```bash
uv run alembic upgrade head                           # apply
uv run alembic revision --autogenerate -m "message"   # create after changing a model
uv run alembic check                                  # fails if models and migrations differ
```

Alembic reads `DATABASE_URL` from the same settings as the app; no URL is stored in
`alembic.ini`. Any change to a model needs a migration, and CI-style checks (`alembic check`
and the OpenAPI drift test) fail otherwise.

## Tests, lint and formatting

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
```

Most tests need a real PostgreSQL database and are skipped unless `TEST_DATABASE_URL` is
set. Use a throwaway database, never real data: the name must end in `_test`, or the run
refuses to start, because the tests apply and revert migrations on it.

```bash
docker run -d --name camdock-test-pg -e POSTGRES_PASSWORD=<random> -e POSTGRES_DB=camdock_test \
  -p 127.0.0.1:58432:5432 postgres:16-alpine
export TEST_DATABASE_URL="postgresql+psycopg://postgres:<random>@127.0.0.1:58432/camdock_test"
export DATABASE_URL="$TEST_DATABASE_URL"
uv run alembic upgrade head
uv run pytest                 # the full suite takes several minutes
docker rm -f camdock-test-pg  # when finished
```

(Some Windows machines block certain ports, for example 55432; pick another if Docker
refuses to bind.) Every test runs with `ENVIRONMENT=test` and cleared settings caches; see
`tests/conftest.py`.

If you change an endpoint, regenerate the contract and commit it:
`uv run python scripts/export_openapi.py`. A test fails when `docs/openapi.json` is stale.

## How a request is handled

Outermost layer first:

1. **Request log**: assigns `X-Request-ID` and writes one access line per request.
2. **Security headers**: `nosniff`, no framing, no caching, HSTS in production.
3. **CORS**: only if `CORS_ALLOWED_ORIGINS` is set.
4. **Global rate limit**: per client address, so refused requests still count.
5. **Body size cap**: 413 above `MAX_REQUEST_BODY_KB`.
6. **Unexpected-error catch-all**: turns a bug into a generic 500 that carries the headers above.
7. The application: authentication, role checks, then the endpoint.

All errors share one JSON shape, `{"error": {"code", "message", "details?"}}`. Domain
errors are raised as `app.core.errors.AppError(message, status_code=..., code=...)`.
Unexpected errors return a generic 500; details go only to the server log, and never include
the exception message or submitted values.

## Layout

```
backend/
  app/
    main.py        create_app() factory and middleware order
    cli.py         operator commands (create-admin)
    dependencies.py  authentication, roles and rate-limit dependencies
    routers/       one module per area (auth, students, results, fees, ...)
    services/      the business rules the routers call
    schemas/       request and response models
    models/        SQLAlchemy tables
    core/          settings, errors, logging, rate limiting, HTTP hardening
    db/            SQLAlchemy base and session, reference-data seed
  migrations/      Alembic revisions
  scripts/         export_openapi.py
  docs/            the documents listed above, and openapi.json
  tests/
  Dockerfile
```
