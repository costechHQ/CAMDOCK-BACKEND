# Operations runbook

Day-to-day running of the portal on the Docker Compose set-up from
[DEPLOYMENT.md](DEPLOYMENT.md). `dc` is the shortcut for
`docker compose -f docker-compose.yml -f docker-compose.prod.yml`. Commands run from the
repository root. Every command here was tried on a test copy.

## Is it healthy?

| Check | Meaning |
|---|---|
| `dc ps` | All three services (`db`, `api`, `proxy`) should be `running`, and `db` and `api` `healthy`. |
| `GET /api/v1/health` | 200: the API is up and reaches its database. 503: it cannot. |
| `GET /api/v1/health/ready` | 200 only if the database is also at the migration this code expects. 503 after an update means the migrations were not run. |

Neither check needs a login, and neither counts against the rate limit. A monitoring
service (UptimeRobot or similar) should call `/health` every minute.

## Reading the logs

```bash
dc logs --tail 100 api          # recent lines
dc logs -f api                  # follow live
```

Each request is one JSON line:

```json
{"ts": "2026-11-09T18:08:28+00:00", "level": "INFO", "logger": "app.request",
 "message": "request_id=469f... method=GET path=/api/v1/public/stats status=200 duration_ms=31 client=203.0.113.7",
 "request_id": "469f..."}
```

- The line has the path, status, duration and client address. It never has the query
  string, request body, headers, passwords or tokens. Emails and secrets are masked if they
  ever appear in a message.
- `level` is `ERROR` for any 5xx. Unexpected errors log their type and the code location,
  not the message, so personal data never lands in the log.
- `/health` lines are hidden at the default `LOG_LEVEL`.

### Finding what happened to one request

Every response carries an `X-Request-ID` header. Ask the person who reports a problem for
it (the frontend should show it on error screens), then:

```bash
dc logs api | grep 469f5169e73d42818ddfd5a2703b7f8c
```

### Common log messages

| You see | It means |
|---|---|
| `status=401` on `/auth/login` | Wrong email or password. |
| A warning "Sign-in attempts blocked for client ...", then `status=429` on `/auth/login` | Too many failed sign-ins from that address or email; see below. |
| `status=429`, `rate_limited` | One address sent more than 300 requests (or 30 to `/public/stats`) in a minute. |
| `status=413` | A request body over `MAX_REQUEST_BODY_KB`. |
| `level: ERROR` with "Unexpected error" | A bug. Note the `request_id` and the time, and report it. |
| "Readiness check failed: schema is not at the expected revision" | Migrations were not run after an update. |
| "Readiness check failed: database error" | The database is down or unreachable. |

## A user is blocked from signing in

After 5 wrong passwords for one email (or 20 failures from one address) in 15 minutes,
sign-in is refused with HTTP 429 for 15 minutes, **even with the right password**. The
screen should say to wait and try again; the response carries a `Retry-After` header with
the seconds left.

- **Normal answer:** wait 15 minutes. The block ends by itself.
- **Urgent:** restart the API, which clears every block at once:

  ```bash
  dc restart api
  ```

  It takes a few seconds, and signed-in users are not logged out. There is no way to lift
  one user's block alone; blocks are held in memory.

If everyone is being blocked together, the API is probably seeing all users as one
address: check that `TRUSTED_PROXIES` matches the proxy (see
[ENVIRONMENT.md](ENVIRONMENT.md)). In the supplied set-up the access log shows the real
visitor address in `client=`; if it shows `172.28.0.10` (the proxy), the setting is wrong.

## Accounts

- **Create an administrator** (also the way to get back in if no admin can sign in):

  ```bash
  dc run --rm api python -m app.cli create-admin --email deputy@school.example
  ```

  It refuses an email that already has an account. Teachers and parents are created by an
  administrator inside the portal.

- **Disable an account** (a leaver, or a lost password you do not want used). The change
  takes effect immediately, including for people already signed in. Replace the email:

  ```bash
  dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "UPDATE users SET is_active = false WHERE email = '"'"'someone@school.example'"'"'"'
  ```

  Set it back to `true` to re-enable. Never delete user rows: payments, results and the
  audit log refer to them.

- **Reset a forgotten password:** *this version has no password-reset feature.* The
  workable route today is to disable the old account and create a new one with a new
  email. See "Known limits" below.

## Storage you should look after

| What | Where | Action |
|---|---|---|
| Container logs | Docker, rotated at 5 × 10 MB per service | Nothing; bounded automatically. |
| Old sign-in sessions | table `auth_sessions` | Expired rows are harmless but accumulate. Once a year: `DELETE FROM auth_sessions WHERE expires_at < now() - interval '30 days'`. |
| Database | Docker volume `pgdata` | Back up daily ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)). Check disk space: `df -h`. |
| Audit log | table `audit_logs` | Append-only; the database refuses edits and deletes. Do not try to trim it. |

### Log retention and personal data

Access lines include the visitor's IP address, which can identify a person. Docker keeps
only the last 50 MB per service, which for a school is typically days to weeks. If you copy
logs elsewhere (a log service, a ticket), keep them no longer than you need, for example
90 days, and do not paste them into public places. Request ids, paths and statuses carry no
names.

## Limits you should know about

- **Rate limits and sign-in blocks are in memory, per API process.** A restart resets
  them. This set-up runs one process, so that is simply how it works. Running several API
  processes or servers would give each its own counters (the allowance multiplies); a
  shared store such as Redis would be needed first.
- **No payment void or refund.** A wrongly entered payment cannot be reversed in the
  portal. Record the correction on paper and ask the developer for a supported fix;
  do not edit `fee_payments` by hand, because the receipts and audit log would then
  disagree.
- **Request bodies need a length.** A request sent in chunks without `Content-Length` is
  refused with 411. Ordinary browsers and tools send it.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| The site does not load; browser says certificate error or timeout | DNS for `SITE_ADDRESS` does not point at the server, or ports 80/443 are closed. `dc logs proxy` shows certificate attempts. |
| `/health/ready` is 503 but `/health` is 200 | Migrations not applied. Run `dc run --rm api alembic upgrade head`. |
| Both are 503 | The database is down or its password changed. `dc ps`, `dc logs db`. |
| `api` keeps restarting; log says "Refusing to start" | A production check failed: placeholder database password, or a non-`https://` CORS origin. Fix `.env`, then `dc up -d`. |
| The frontend shows a CORS error | Its exact origin (scheme, host, port; no trailing path) is not in `CORS_ALLOWED_ORIGINS`. Edit `.env`, then `dc up -d`. |
| Everyone gets 429 | All users appear as one address: see `TRUSTED_PROXIES` above. |
| `dc up` says "Pool overlaps with other one" | `172.28.0.0/24` is already in use. Set `PROXY_SUBNET` and `PROXY_IP` in `.env` to an unused private range (for example `172.29.0.0/24` and `172.29.0.10`). |
| Disk is full | Check `docker system df` and the backup folder; delete old backups beyond retention. |

## Known limits of this version

These are not faults, but you should know them:

1. No password reset and no admin screen to disable accounts (use the commands above).
2. No payment void or refund.
3. In-memory rate limiting and sign-in blocking (see above).
4. Receipt numbers (`RCP-YYYY-NNNNNN`) are a proposal the school has not confirmed.
5. A receipt shows the student's current class, not the class at the time of payment.
6. The rules for when results are published, and whether past terms are locked against
   edits, are not defined yet.
