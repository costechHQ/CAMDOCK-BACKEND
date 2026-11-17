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

- **A user wants a new password they know the old one of:** they do it themselves in
  the portal (`POST /auth/change-password`). Their other sessions end. Five wrong
  current passwords block them like five wrong sign-ins (15 minutes).

- **Create an administrator** (also the way to get back in if no admin can sign in):

  ```bash
  dc run --rm api python -m app.cli create-admin --email deputy@school.example
  ```

  It refuses an email that already has an account. Teachers and parents are created by an
  administrator inside the portal.

- **Disable or re-enable an account** (a leaver, or an account you do not want used).
  An administrator does this in the portal, which calls `PATCH /api/v1/users/{id}` with
  `{"is_active": false}`; `GET /api/v1/users?q=...` finds the account. Disabling ends the
  account's sessions at once, and re-enabling does not bring them back, so the person
  signs in again. You cannot disable yourself or the last active administrator. Linked
  teacher records are not changed. Every change is in the audit log.

  If the portal itself is unreachable, the same can be done in the database (replace the
  email; this does not end sessions, but a disabled account is refused on every request):

  ```bash
  dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "UPDATE users SET is_active = false WHERE email = '"'"'someone@school.example'"'"'"'
  ```

  Never delete user rows: payments, results and the audit log refer to them.

- **Reset a forgotten password.** Another administrator issues a one-time code in
  the portal (`POST /users/{id}/password-reset`), after confirming who is asking,
  in person or on a number the school already holds. They give the person the code;
  the person enters their email, the code and a new password on the reset screen
  (`POST /auth/reset-password`). The code works once and expires after
  `PASSWORD_RESET_TTL_HOURS` (24 by default). Using it ends all that account's
  sessions, and the person signs in with the new password. Every issue and use is in
  the audit log. Never put a code in a group chat or an email subject.

- **The last administrator is locked out** (no one is left to issue a code). On the
  server:

  ```bash
  dc run --rm api python -m app.cli reset-password --email head@school.example
  ```

  It shows the account and asks to confirm, then asks for the new password twice
  (hidden, at least 12 characters). It ends that account's sessions, cancels any
  unused reset code and records the action in the audit log with no signed-in user.
  For a script, set `CAMDOCK_ADMIN_PASSWORD` (the name says admin, but it is the
  password to set for whichever account you name) and pass `--yes`. It refuses a
  disabled account; enable it first (see "Disable or re-enable an account"). If
  the account had been blocked for too many wrong passwords, the block ends by
  itself after 15 minutes, or immediately with `dc restart api`; the command cannot
  clear it because blocks live in the API's memory.

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
- **Reversing a payment is done in the portal, never in the database.** An administrator
  voids a payment entered by mistake, or refunds money given back (see
  [API_CONTRACT.md](API_CONTRACT.md)). Both keep the original entry, are audited, and cannot
  be edited or deleted. Do not edit `fee_payments` or `fee_refunds` by hand: the receipts
  and audit log would then disagree. A refund entered by mistake cannot be undone; record a
  new payment for the same amount. There is no limit on how old a payment may be, because
  the school has not set a rule for closed terms.
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
| A user says their reset code "does not work" | Codes work once, expire after `PASSWORD_RESET_TTL_HOURS`, and a newer code replaces an older one. The screen deliberately never says which. Check the email typed is the account's own, then issue a new code. |
| A user gets 429 after typing codes | Five wrong codes block that email for 15 minutes. Wait, or `dc restart api` to clear it. |
| No administrator can sign in | On the server, use `reset-password` (see "The last administrator is locked out" above). |
| `dc up` says "Pool overlaps with other one" | `172.28.0.0/24` is already in use. Set `PROXY_SUBNET` and `PROXY_IP` in `.env` to an unused private range (for example `172.29.0.0/24` and `172.29.0.10`). |
| Disk is full | Check `docker system df` and the backup folder; delete old backups beyond retention. |

## Known limits of this version

These are not faults, but you should know them:

1. There is no self-service "forgot password" by email: a reset always needs an
   administrator to hand over a code (email and SMS are out of the SRS scope).
2. No rule yet for reversing a payment from a closed term, and the `REF-` refund receipt
   wording is a proposal the school has not seen.
3. In-memory rate limiting and sign-in blocking (see above).
4. Receipt numbers (`RCP-YYYY-NNNNNN`) are a proposal the school has not confirmed.
5. A receipt shows the student's current class, not the class at the time of payment.
6. The rules for when results are published, and whether past terms are locked against
   edits, are not defined yet.
