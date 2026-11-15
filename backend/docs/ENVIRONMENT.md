# Environment settings

The API reads its settings from environment variables, and from a `.env` file in the
directory it starts in. Never commit `.env`. Values are checked at start-up: a bad value
stops the API with a message instead of running with a wrong setting.

A test (`tests/test_docs.py`) fails if a setting is added to the code and not listed here.

## API settings (`backend/.env`, or the `environment:` block in Compose)

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `test` or `production`. Interactive API docs (`/docs`) exist only in `development`. `production` turns on HSTS and the start-up checks below. |
| `DATABASE_URL` | none, **required** | SQLAlchemy URL, `postgresql+psycopg://user:password@host:5432/dbname`. The API will not start without it. |
| `API_V1_PREFIX` | `/api/v1` | URL prefix of every endpoint. |
| `SESSION_TTL_HOURS` | `12` | How long a sign-in lasts before the user must log in again. |
| `PASSWORD_RESET_TTL_HOURS` | `24` | How long a password-reset code, issued by an administrator, stays valid. From 1 to 168. |
| `LOGIN_MAX_FAILURES` | `5` | Failed sign-ins for one email, inside the window, before that email is blocked. |
| `LOGIN_IP_MAX_FAILURES` | `20` | Failed sign-ins from one address, inside the window, before that address is blocked. |
| `LOGIN_WINDOW_MINUTES` | `15` | The period failures are counted over. |
| `LOGIN_BLOCK_MINUTES` | `15` | How long a block lasts. The block ends by itself. |
| `RATE_LIMIT_PUBLIC_PER_MINUTE` | `30` | Requests per minute, per client address, to the open `/public/stats` endpoint. |
| `RATE_LIMIT_GLOBAL_PER_MINUTE` | `300` | Requests per minute, per client address, to the whole API. `/health` and `/health/ready` are exempt. |
| `CORS_ALLOWED_ORIGINS` | empty | Browser origins allowed to call the API, comma-separated, for example `https://portal.school.example`. Empty means no browser on another origin may call it. In production every origin must be `https://`. No wildcards. |
| `MAX_REQUEST_BODY_KB` | `1024` | Largest accepted request body, in KB. Larger gets a 413. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Logs are JSON lines when `ENVIRONMENT=production`, plain text otherwise. |
| `TRUSTED_PROXIES` | empty | Addresses or CIDR ranges of reverse proxies whose `X-Forwarded-For` header is believed. See below. |
| `SCHOOL_NAME` | `Camdock International School` | Printed on result sheets. |
| `SCHOOL_LOCATION` | `Enugu State, Nigeria` | Printed on result sheets. |

### Refusals at start-up in production

With `ENVIRONMENT=production` the API refuses to start, with a fixed message that never
repeats the value, when:

- the database password is empty or a well-known placeholder (`change-me`, `changeme`,
  `password`, `postgres`, `secret`);
- any `CORS_ALLOWED_ORIGINS` entry is not `https://`.

### `TRUSTED_PROXIES`: why it matters

The rate limits and the sign-in block work per client address. Behind a reverse proxy
every request reaches the API from the proxy's own address, so without this setting **all
users share one rate limit and one sign-in block**: one person failing five logins would
block everyone using the same account, and a busy morning could trip the limit for the
whole school.

- Leave it **empty** when clients connect to the API directly. `X-Forwarded-For` is then
  ignored, so nobody can fake their address.
- Set it to the proxy's address when a proxy sits in front. The API then takes the client
  address from the rightmost `X-Forwarded-For` entry that is not itself a trusted proxy,
  and only when the connection comes from a trusted proxy.
- Never put a wide range such as `0.0.0.0/0` here: it lets any caller choose its own
  address and dodge both limits.

`docker-compose.prod.yml` sets it for you, to the address of its bundled proxy.

## Compose settings (the `.env` next to `docker-compose.yml`)

`LOG_LEVEL` and `CORS_ALLOWED_ORIGINS` from the table above are also read from this file
and passed to the API by `docker-compose.prod.yml`.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `camdock` | Database user. |
| `POSTGRES_PASSWORD` | none, **required** | Database password. Use letters and digits only: Compose places it inside `DATABASE_URL`. |
| `POSTGRES_DB` | `camdock` | Database name. |
| `SITE_ADDRESS` | none, **required in production** | The domain name the proxy serves, for example `portal.school.example`. A domain gets an HTTPS certificate automatically; the name must already point at the server. `:80` serves plain HTTP and is for tests only. |
| `HTTP_PORT` | `80` | Port the proxy listens on for HTTP. |
| `HTTPS_PORT` | `443` | Port the proxy listens on for HTTPS. |
| `PROXY_SUBNET` | `172.28.0.0/24` | Private Docker network the stack uses. Change it, together with `PROXY_IP`, only if the server already uses that range. |
| `PROXY_IP` | `172.28.0.10` | Fixed address of the proxy inside `PROXY_SUBNET`. The API trusts only this address. |

## Command-line only

| Variable | Purpose |
|---|---|
| `CAMDOCK_ADMIN_PASSWORD` | Lets `python -m app.cli create-admin` run without a prompt. There is deliberately no `--password` option, so a password never appears in a process list or shell history. |
