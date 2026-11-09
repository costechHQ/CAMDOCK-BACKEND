# Deployment: one server with Docker Compose

This guide puts the whole system on a single Linux server: PostgreSQL, the API, and a
reverse proxy that provides HTTPS. Every command below was run against a test copy of
this setup. Commands are run from the repository root (the folder holding
`docker-compose.yml`).

The set-up uses a second Compose file, `docker-compose.prod.yml`, on top of the
development one. It switches the API to production mode, publishes **no** database or API
port, adds a proxy ([Caddy](https://caddyserver.com/)) as the only public entry point,
rotates container logs and restarts services after a crash or reboot.

## What you need

- A Linux server with Docker and the Docker Compose plugin (`docker compose version`).
- A domain name (for example `portal.school.example`) pointing at the server's public IP.
  Without it the proxy cannot obtain an HTTPS certificate.
- Ports 80 and 443 open to the internet; nothing else except SSH. PostgreSQL and the API
  are not reachable from outside by design.
- A place off the server to keep backups (see [BACKUP_RESTORE.md](BACKUP_RESTORE.md)).

## 1. Get the code and configure

```bash
git clone <repository-url> camdock && cd camdock
cp .env.example .env
```

Edit `.env` and set:

```
POSTGRES_PASSWORD=<a long random password, letters and digits only>
SITE_ADDRESS=portal.school.example
CORS_ALLOWED_ORIGINS=https://<where the frontend is served from>
```

`openssl rand -hex 24` makes a suitable password. The API refuses to start in production
with a placeholder such as `change-me`. Every setting is described in
[ENVIRONMENT.md](ENVIRONMENT.md). Keep `.env` private (`chmod 600 .env`); it is not
committed to git.

To save typing, define a shortcut for the two-file command:

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'
```

(or set `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` in `.env`). The rest of
this guide writes `dc`.

## 2. Start

```bash
dc up -d --build --wait
```

The API container runs as a non-root user. It starts, but is **not ready** until the
database has its tables, which is the next step. `GET /api/v1/health` already answers;
`GET /api/v1/health/ready` answers 503 until the migrations are applied.

## 3. Create the tables

```bash
dc run --rm api alembic upgrade head
curl -s https://portal.school.example/api/v1/health/ready
# {"status":"ok","database":"ok","migrations":"ok"}
```

`/health/ready` returns 200 only when the database is reachable **and** at the migration
this version of the code expects. Use it as the "is this deployment good" check.
`/health` only says the database answers, and is what Compose uses to decide a container
is up.

## 4. Load the school's reference data and create the first administrator

```bash
dc run --rm api python -m app.db.seed \
  --academic-year 2026/2027 --start-date 2026-09-07 --end-date 2027-07-23 --current

dc run --rm api python -m app.cli create-admin --email head@school.example
```

The seed adds the classes, subjects and the three terms. It is safe to run again: it adds
only what is missing and never overwrites edits. The subjects and their class mapping are
the prototype's defaults, not the school's confirmed curriculum; check them with the
school. The second command asks for a password twice (at least 12 characters) and
creates the first administrator. Other accounts are created from inside the portal.

## 5. Check it from outside

From any computer:

```bash
curl -si https://portal.school.example/api/v1/public/stats
```

You should see 200, a JSON body with three counts, and the headers
`Strict-Transport-Security`, `X-Content-Type-Options`, `X-Request-ID` and no `Server`
header. `https://portal.school.example/docs` must answer 404: the interactive docs exist
only in development.

## How the proxy and `TRUSTED_PROXIES` fit together

All traffic enters through the proxy, which adds the real client address in
`X-Forwarded-For`. The compose file gives the proxy a fixed address on a private network
and tells the API to believe that header **only** from that address. If you replace the
proxy with your own (nginx, a cloud load balancer), set `TRUSTED_PROXIES` to its address
or range and keep the API unreachable from anywhere else. Without it the whole school
would share one rate limit and one sign-in block; see [ENVIRONMENT.md](ENVIRONMENT.md).

## Firewall

Allow inbound 22 (SSH), 80 and 443 only. Docker publishes only the proxy's ports in this
set-up, and the base file's `127.0.0.1` database and API ports are removed. Check with
`dc ps` and `ss -ltn`: only 80 and 443 should listen on a public address.

## Updating to a new version

```bash
# 1. back up first (the "Back up" command in BACKUP_RESTORE.md)
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "camdock-$(date +%F-%H%M).dump"

# 2. get the new code and build it
git pull
dc build

# 3. apply new migrations with the new code, then restart on it
dc run --rm api alembic upgrade head
dc up -d --wait

# 4. confirm
curl -s https://portal.school.example/api/v1/health/ready
```

Migrations in this project are additive, so the old API keeps working during the few
seconds between steps 3's two commands. Expect a brief restart of the API container.
People who are signed in stay signed in: sessions are stored in the database.

## Rolling back

If an update misbehaves, restore the backup you took in step 1 and run the previous
version of the code:

```bash
git checkout <previous-tag-or-commit>
dc build
# restore the database: BACKUP_RESTORE.md, "Restore"
dc up -d --wait
```

Do not try to undo migrations with `alembic downgrade` on live data; restoring a backup
is the tested route.

## Things to know

- **Restarting the API clears the in-memory sign-in blocks and rate-limit counters.**
  That is the only way to lift a block early; otherwise it ends by itself after
  `LOGIN_BLOCK_MINUTES`. These counters live in one process: this set-up runs one API
  process, which is right for one server. Running several API processes or servers would
  multiply the allowed attempts.
- **Logs** go to the container output (`dc logs api`) as JSON lines, rotated by Docker at
  5 files of 10 MB per service. They contain client addresses; see
  [OPERATIONS.md](OPERATIONS.md) for retention.
- **Time zone.** Timestamps are stored and logged in UTC.
- **Disk.** The database lives in the Docker volume `pgdata`, and the certificate in
  `caddy_data`. Both survive `dc down`. `dc down -v` **deletes them**; never use `-v` on
  the live server.
- **Reboot.** Services have `restart: unless-stopped`, so they return after a reboot once
  Docker starts.
