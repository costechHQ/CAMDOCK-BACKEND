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
input, including an unknown `term_id`).

## Conventions

- **Money** is naira with exactly two decimals, as a string: `"25000.50"`.
- **Averages** are strings with one decimal, rounded half up: `"60.9"`. `null` means
  nothing is recorded; it is never shown as zero.
- **Lists** return `{items, total, limit, offset}`. `limit` defaults to 20, maximum 100.
- Dates and times are ISO 8601.

## Terms and fee totals

- `term_id` is optional on the dashboard. Omitted, the server uses the current academic
  year: the term containing today, otherwise the latest term that has started,
  otherwise the first term. With no current year or no terms, `term` is `null`.
- Fees are never mixed across periods. `fees.term` covers the selected term and is
  `null` when there is no term; `fees.all_time` covers every term. `collected` is the
  sum of payments against those bills, whenever they were paid; `outstanding` is
  `billed - collected`. Bills of deactivated students still count.

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
