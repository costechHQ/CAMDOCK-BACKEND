# Camdock International School Portal: API contract

The machine-readable contract is [`openapi.json`](openapi.json). A test fails if it
drifts from the code; regenerate it with `uv run python scripts/export_openapi.py`.
This note covers the rules that the schema alone does not say. All paths are under
`/api/v1`.

## Authentication and roles

- `POST /auth/login` returns a bearer token; send it as `Authorization: Bearer <token>`.
  `POST /auth/logout` ends the session, and `GET /auth/me` returns the signed-in account.
- Roles: `admin`, `teacher`, `parent`. Only `GET /health`, `POST /auth/login` and
  `GET /public/stats` need no token.
- Failed sign-ins are throttled. After 5 failures for one email (or 20 from one
  address) within 15 minutes, `POST /auth/login` returns **429** with code
  `too_many_attempts` and a `Retry-After` header (seconds) for 15 minutes, even if the
  password is then correct. Wrong password, unknown email and disabled account all count
  the same. A successful login clears the email's count.
- `POST /auth/change-password` lets any signed-in user change their own password:
  send `{"current_password", "new_password"}`; the answer is **204** with no body.
  Passwords are used exactly as sent (spaces included). The new one must be at
  least 12 characters, not blank, not the account's email and different from the
  current one (422 with the reason). A wrong current password is **422** with code
  `incorrect_password` (not 401, so a frontend does not mistake it for an expired
  session) and counts toward the same limit as a wrong sign-in password: after 5
  wrong attempts the endpoint, and sign-in for that email, answer **429**
  `too_many_attempts` with `Retry-After`. On success every *other* session of the
  account ends; the one that made the request stays signed in.
- Request rates are limited per client address, per minute. `GET /public/stats` allows
  30 and the whole API allows 300 (`GET /health` is exempt). Over the limit gives **429**
  with code `rate_limited` and a `Retry-After` header (seconds until the window ends).
  Login failures have their own 429 code, `too_many_attempts`.
- Request bodies are limited to 1 MiB (`MAX_REQUEST_BODY_KB`), judged from the
  `Content-Length` header: larger gives **413** (`payload_too_large`), and a body sent
  without `Content-Length` (chunked) gives **411** (`length_required`). Normal `fetch`
  calls with a JSON string body send the header automatically.
- Browsers may call the API only from the origins listed in `CORS_ALLOWED_ORIGINS`
  (none by default). Credentials are not used: send the token in `Authorization`.
  Allowed methods are GET, POST, PUT, PATCH and DELETE; allowed request headers are
  `Authorization` and `Content-Type`.
- Responses are never cacheable (`Cache-Control: no-store`) and carry the standard
  security headers.
- Every response carries an `X-Request-ID` header. Quote it when reporting a problem:
  it ties the response to the server log. You may send your own `X-Request-ID` (8 to
  64 letters, digits, `.`, `_` or `-`); anything else is replaced. Browsers can read
  `X-Request-ID` and `Retry-After` from allowed origins.
- A missing or invalid token gives **401**. A valid token whose role may not use the
  endpoint gives **403**.
- A record the caller may not reach gives **404**, identical to a record that does not
  exist, so ids cannot be probed.

## Errors

Every error has the same body. Validation errors (422) also carry a `details` list.

```json
{"error": {"code": "not_found", "message": "Class not found."}}
```

Status codes: 401, 403, 404, 409 (conflicts such as a duplicate bill), 422 (invalid
input, including an unknown `term_id`), 411 and 413 (request body without a length, or too large), 429 (too many failed sign-ins, or too many requests).

## Conventions

- **Money** is naira with exactly two decimals, as a string: `"25000.50"`.
- **Retrying a payment safely.** `POST /fees/{id}/payments` accepts an optional
  `Idempotency-Key` header (8 to 64 letters, digits, `.`, `_` or `-`; anything else is
  a 422). Generate a new key for each payment the admin enters and send the same key
  again if you have to retry. The first call returns **201**. A retry with the same
  key, bill and amount returns **200** with the original payment and the bill, and
  records nothing new, even if the bill has since been paid in full. The same key with
  a different bill or amount returns **409** `idempotency_key_reused`. Keys belong to
  the admin who sent them and never expire. Without the header every call records a
  new payment.
- **Averages** are strings with one decimal, rounded half up: `"60.9"`. `null` means
  nothing is recorded; it is never shown as zero.
- **Lists** return `{items, total, limit, offset}`. `limit` defaults to 20, maximum 100.
- Dates and times are ISO 8601.

## Accounts (admin only)

- `GET /users?q=&role=&is_active=&limit=&offset=` lists accounts by email, in the usual
  `{items, total, limit, offset}` shape. `q` matches part of the email, ignoring case;
  `role` is `admin`, `teacher` or `parent`. Each item has `id`, `email`, `role`,
  `is_active`, `last_login_at` and `created_at`. No credential is ever returned.
- `PATCH /users/{id}` with `{"is_active": true|false}` disables or re-enables an
  account and returns it. Disabling ends all the account's sessions immediately;
  re-enabling does not restore them. Setting the value the account already has changes
  nothing. Only `is_active` is accepted (anything else is a 422).
- **409** if an administrator tries to disable their own account, or an administrator
  whose disabling would leave no active administrator. **404** for an unknown id.

## Terms and fee totals

- `term_id` is optional on the dashboard. Omitted, the server uses the current academic
  year: the term containing today, otherwise the latest term that has started,
  otherwise the first term. With no current year or no terms, `term` is `null`.
- Fees are never mixed across periods. `fees.term` covers the selected term and is
  `null` when there is no term; `fees.all_time` covers every term. `collected` is the
  sum of payments against those bills, whenever they were paid; `outstanding` is
  `billed - collected`. Bills of deactivated students still count.

## Health checks (no login, not rate limited)

- `GET /health` answers `{"status": "ok", "database": "ok"}` when the API can reach its
  database, otherwise 503.
- `GET /health/ready` answers `{"status": "ok", "database": "ok", "migrations": "ok"}`
  only when the database schema is at the migration this version of the API expects.
  Otherwise it returns 503 with the standard error body and no detail. Use it as a
  deploy gate after running migrations; use `/health` for liveness probes.

## `GET /public/stats` (no login)

```json
{"students": 120, "teachers": 14, "classes": 9}
```

Active students, active teachers and active classes. `classes` counts every active
class, whether or not it has students. Counts only: no personal data.

## `GET /dashboard/overview?term_id=` (signed in)

The body depends on the caller; branch on `role`.

### admin

```json
{
  "role": "admin",
  "term": {"id": 1, "name": "First Term", "academic_year": "2025/2026"},
  "students": 120,
  "teachers": 14,
  "active_classes": 8,
  "fees": {
    "term": {"billed": "1250.50", "collected": "400.00", "outstanding": "850.50"},
    "all_time": {"billed": "1950.50", "collected": "500.00", "outstanding": "1450.50"}
  },
  "class_averages": [
    {"school_class": {"id": 1, "name": "Primary 1"}, "overall_average": "60.9"}
  ]
}
```

`active_classes` counts active classes holding at least one active student, so it can
be lower than the public `classes`. `class_averages` lists every active class.

### teacher ("My overview")

```json
{
  "role": "teacher",
  "title": "My overview",
  "term": {"id": 1, "name": "First Term", "academic_year": "2025/2026"},
  "students": 3,
  "classes": [
    {"school_class": {"id": 1, "name": "Primary 1"}, "students": 3, "overall_average": "60.9"}
  ]
}
```

Only the teacher's assigned classes. No fee figures.

### parent

```json
{
  "role": "parent",
  "term": {"id": 1, "name": "First Term", "academic_year": "2025/2026"},
  "children": [
    {
      "student": {"id": 7, "student_number": "S-0007", "full_name": "Ada Obi"},
      "school_class": {"id": 1, "name": "Primary 1"},
      "student_average": "67.8",
      "class_average": "60.9",
      "subjects_recorded": 2,
      "fees": {
        "term": {"billed": "1000.00", "collected": "400.00", "outstanding": "600.00"},
        "all_time": {"billed": "1700.00", "collected": "500.00", "outstanding": "1200.00"}
      },
      "links": {
        "results": "/api/v1/students/7/results?term_id=1",
        "fees": "/api/v1/fees?student_id=7"
      }
    }
  ]
}
```

Only the parent's linked, active children, ordered by name. `links` are API paths the
frontend turns into navigation to the results and fee screens; the results link
includes `term_id` when a term is selected.

## Empty states

Every dashboard response is valid on an empty school: counts are `0`, lists are `[]`,
averages and the term fee block are `null`, and all-time amounts are `"0.00"`. The
frontend should render these as empty states, not errors.
