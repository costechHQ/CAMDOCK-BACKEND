# Camdock International School Portal

Backend for the Camdock International School portal (Enugu State, Nigeria), built from an
HTML prototype and a written specification. It manages accounts, students, teachers,
results, fees and receipts, with separate views for administrators, teachers and parents.

## What is here

| Path | Contents |
|---|---|
| [`backend/`](backend) | The API (FastAPI and PostgreSQL). Start with [`backend/README.md`](backend/README.md). |
| [`DOCS/`](DOCS) | The requirements ([SRS](DOCS/Camdock_International_School_Backend_SRS.md)) and the original HTML prototype. The prototype's location text ("Port Harcourt") is wrong; the school is in Enugu State. |
| [`docker-compose.yml`](docker-compose.yml) | PostgreSQL and the API for development. |
| [`docker-compose.prod.yml`](docker-compose.prod.yml), [`deploy/`](deploy) | Production overrides and the HTTPS proxy configuration. |

## Where to read next

- **Building the frontend:** [`backend/docs/API_CONTRACT.md`](backend/docs/API_CONTRACT.md)
  and [`backend/docs/openapi.json`](backend/docs/openapi.json).
- **Running it on a server:** [`backend/docs/DEPLOYMENT.md`](backend/docs/DEPLOYMENT.md),
  then [`BACKUP_RESTORE.md`](backend/docs/BACKUP_RESTORE.md) and
  [`OPERATIONS.md`](backend/docs/OPERATIONS.md) in the same folder.
- **Every setting:** [`backend/docs/ENVIRONMENT.md`](backend/docs/ENVIRONMENT.md).

## Try it in five steps

You need Docker and [`uv`](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                         # set POSTGRES_PASSWORD (letters and digits)
docker compose up -d --build --wait
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.cli create-admin --email head@school.example
curl http://127.0.0.1:8000/api/v1/health/ready
```

The API is then on <http://127.0.0.1:8000> (interactive docs at `/docs`). This is the
development set-up and is not for real school data on the internet; use
[`DEPLOYMENT.md`](backend/docs/DEPLOYMENT.md) for that.

## Status

The backend covers the SRS scope: authentication and roles, students and teachers, results,
fees and receipts, and dashboards, plus rate limiting, security headers, safe logging,
health checks, and backup and deployment guides. Some things are deliberately not built
yet, such as payment void or refund and password reset; the list is in
[`OPERATIONS.md`](backend/docs/OPERATIONS.md#known-limits-of-this-version). The frontend
is a separate piece of work.
