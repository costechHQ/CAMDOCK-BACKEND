# Acceptance: SRS Section 8

Section 8 of the [SRS](../../DOCS/Camdock_International_School_Backend_SRS.md) lists 33
criteria the backend must meet before release. This page shows where each one is proved,
and records the run that demonstrated them on a brand-new database.

Two kinds of proof are used:

- **The acceptance run**: `scripts/acceptance_run.py` talks to a running API over HTTP
  only, like a frontend would, creates its own teachers, parents, students, scores, bills
  and payments, and prints one PASS or FAIL per criterion. It can be repeated at any time.
- **The automated tests**: the files named below, run on every push by CI. They cover the
  same rules from the inside, with many more cases (edge values, races, error shapes).

A test (`tests/test_acceptance_doc.py`) fails if the SRS gains a criterion that is missing
here, or if this page names a test file that no longer exists. It also runs the whole
acceptance run against the application inside the test suite.

## Criteria and where each is shown

Status **run** means the acceptance run checks it over HTTP and it passed. **elsewhere**
means it cannot be shown over HTTP; the proof is given in the section after the table.

### Authentication and authorization

| Criterion | Status | Also covered by |
|---|---|---|
| A valid registered account can authenticate. | run | `tests/test_auth_api.py`, `tests/test_auth_acceptance.py` |
| Invalid credentials are rejected. | run | `tests/test_auth_api.py`, `tests/test_login_throttle.py` |
| Anonymous requests cannot read protected data or mutate records. | run | `tests/test_authorization.py`, `tests/test_auth_acceptance.py` |
| A parent cannot read another student's results or fee records by changing IDs. | run | `tests/test_students_teachers_acceptance.py`, `tests/test_access_scope.py` |
| A teacher cannot read or modify records outside assigned classes/subjects. | run | `tests/test_access_scope.py`, `tests/test_results_acceptance.py` |
| An administrator-only endpoint rejects teacher and parent accounts. | run | `tests/test_authorization.py` |
| Logout/session invalidation behaves according to the approved authentication design. | run | `tests/test_auth_api.py`, `tests/test_auth_acceptance.py` |

### Students and teachers

| Criterion | Status | Also covered by |
|---|---|---|
| An administrator can create a valid student. | run | `tests/test_students_api.py` |
| Invalid or missing required fields return documented validation errors. | run | `tests/test_students_api.py`, `tests/test_errors.py` |
| Student identifiers are unique. | run | `tests/test_students_schema.py`, `tests/test_students_teachers_acceptance.py` |
| Teachers see only students in their assigned classes. | run | `tests/test_access_scope.py`, `tests/test_students_api.py` |
| An administrator can create a teacher and assign classes. | run | `tests/test_teachers_api.py` |
| Class names are stored consistently. | run | `tests/test_academic_schema.py`, `tests/test_seed.py` |

### Results

| Criterion | Status | Also covered by |
|---|---|---|
| Authorized staff can create and update scores. | run | `tests/test_results_put.py` |
| Scores below 0 or above 100 are rejected unless the scoring policy is changed. | run | `tests/test_results_put.py`, `tests/test_results_schema.py` |
| The correct grade is returned for each score boundary. | run | `tests/test_grading.py` |
| Student averages and class averages match the documented formulas. | run | `tests/test_averages.py`, `tests/test_results_acceptance.py` |
| Missing scores are not silently treated as zero. | run | `tests/test_averages.py`, `tests/test_results_get.py` |
| Parents can only retrieve results for linked students. | run | `tests/test_results_get.py`, `tests/test_parent_links.py` |
| Result-sheet data contains the required student, term, subject, score, grade, and average fields. | run | `tests/test_result_sheet.py` |

### Fees and receipts

| Criterion | Status | Also covered by |
|---|---|---|
| Only authorized administrators can record fee entries. | run | `tests/test_fees_create.py`, `tests/test_fee_payments.py` |
| Invalid billed or paid amounts are rejected. | run | `tests/test_fees_create.py`, `tests/test_money.py` |
| Paid, Partial, and Due statuses are calculated correctly. | run | `tests/test_money.py`, `tests/test_fees_acceptance.py` |
| Outstanding balances are calculated correctly. | run | `tests/test_money.py`, `tests/test_fees_read.py` |
| Parents can retrieve fee records only for linked students. | run | `tests/test_fees_read.py`, `tests/test_fees_acceptance.py` |
| Receipt numbers are unique. | run | `tests/test_fee_receipts.py`, `tests/test_fee_payments_concurrency.py` |
| Receipt data includes the fields specified in FR-FEE-008. | run | `tests/test_fee_receipts.py` |
| Financial writes are attributable to an authenticated user. | run + SQL | `tests/test_fees_schema.py`, `tests/test_fee_payments.py` |

### Quality

| Criterion | Status | Also covered by |
|---|---|---|
| Automated tests cover successful flows, validation failures, unauthorized access, and forbidden cross-user access. | elsewhere | the whole `tests/` folder |
| API documentation is available in the development environment. | elsewhere | `tests/test_config.py` |
| Database migrations can create the schema on a clean database. | elsewhere | `tests/test_migrations.py` |
| No production workflow depends on browser `localStorage` as its source of truth. | run | `tests/test_acceptance_doc.py` |
| No production account can authenticate merely by selecting a role or name. | run | `tests/test_auth_api.py`, `tests/test_authorization.py` |

## How to run the acceptance run yourself

It **writes data**, so use a fresh or throwaway database, never the school's real one. It
refuses to run without `--confirm-writes`, and refuses a database that already has students.

```bash
# on a database that is migrated, seeded, and has one administrator
export ACCEPTANCE_ADMIN_PASSWORD=...          # that administrator's password
cd backend
uv run python scripts/acceptance_run.py \
  --base-url http://127.0.0.1:8000 \
  --admin-email head@school.example \
  --confirm-writes
```

Exit status is 0 when nothing failed. Remove the test data afterwards by discarding the
throwaway database.

## The recorded run

- **Target:** the production stack from `docker-compose.prod.yml` (production mode,
  behind the HTTPS proxy, over plain HTTP for the test), built from this repository.
- **Database:** brand new volume. Migrations applied from empty to `0012`, then the
  reference-data seed and one administrator, nothing else.
- **Result:** `30 passed, 0 failed, 3 shown elsewhere (33 criteria, 234 requests)`.

```
PASS     A valid registered account can authenticate.
...        (28 more PASS lines, one for each criterion marked "run" above)
EXTERNAL Automated tests cover successful flows, validation failures, unauthorized access, and forbidden cross-user access.
EXTERNAL API documentation is available in the development environment.
EXTERNAL Database migrations can create the schema on a clean database.
```

### What the checker is not fooled by

To prove the run can fail, the API was deliberately broken nine ways and the run repeated
against each. Every break was reported as a FAIL on the matching criteria: a parent's
student scope opened up, a teacher's student scope opened up, a grade boundary moved, the
score cap raised to 101, logout that does not revoke the session, a balance off by one, a
teacher allowed to create bills, averages rounded half-even instead of half-up, and a
missing score counted as zero.

### Password recovery checks (added in Phase 9)

The same run also exercises the account-recovery features, filed under the Section 8
criteria they belong to (the list of 33 does not change):

- **Invalid credentials are rejected.** A wrong reset code, a code with an unknown
  email, and a code issued for someone else all get the same 422 `invalid_reset_code`;
  the password does not change; a used code is refused; the old password stops working
  after a reset or a change; a wrong current password is refused; a disabled account
  cannot sign in and cannot be issued a code.
- **A valid registered account can authenticate.** After a reset, the new password signs
  in (the code works typed in lower case with spaces).
- **Logout/session invalidation.** A reset ends every session of the account; a password
  change keeps the session that made it and ends the others; disabling ends sessions and
  re-enabling does not revive them.
- **Administrator-only endpoints.** Teachers and parents get 403 on listing accounts,
  disabling an account and issuing a reset code; an administrator cannot disable or reset
  themselves.

To prove these checks can fail, the API was broken eight more ways and the run repeated:
a reset that does not end sessions, a code that can be used twice, a reset that leaves the
old password working, a teacher allowed to issue a code, a teacher allowed to list accounts,
re-enabling that revives old sessions, a password change that ends no other session, and
a different message for a bad code. Seven were reported as FAIL; the eighth only changed
the shared message, so every bad code still got the same answer (the exact message is
asserted by unit tests instead).

Beyond the scripted run, the recovery features were tried by hand on the same
production stack:

- A code was issued, redeemed once (204), and refused when used again (422). The old
  password then failed (401) and the new one worked (200).
- Five wrong codes for one email gave 422 each time, and the sixth request, even with the
  right code, gave **429** with `Retry-After: 900`. After `docker compose restart api`
  (which clears the in-memory block) the right code was accepted.
- The server command `reset-password --yes` ran inside the API container and changed the
  password. Without `--yes` and without a terminal it refused ("Not a terminal: pass
  --yes to confirm the reset."), and for a disabled account it refused ("That account is
  disabled. Enable it first."); both exited with status 1.
- **No secret reached a log.** Afterwards the API, proxy and database logs (339 lines)
  were searched for every reset code (with and without hyphens) and every password used,
  including the administrator's: 0 matches for each.
- The audit trail held one row per action: `user.password_reset_issue`,
  `user.password_reset_complete`, `user.password_change`, `user.password_reset_cli`,
  `user.disable` and `user.enable`.
- **Upgrade from the previous release.** A database at the Phase 8 revision `0011`, holding
  an administrator, the seeded classes and terms and their audit rows, was upgraded to
  `0012` with every row intact, accepted the recovery command, and was downgraded back to
  `0011` cleanly (the new table disappeared and the data stayed).

### Proof for the criteria not shown by HTTP

**Financial writes are attributable to an authenticated user.** Over HTTP the run shows
that only a signed-in administrator can record a bill or payment and that refused attempts
leave no trace. Which user each row belongs to is a database fact, checked on the same
database right after the run:

```sql
select p.receipt_number, p.amount, u.email as recorded_by, u.role
from fee_payments p join users u on u.id = p.created_by order by p.id;
```

```
 receipt_number  | amount |     recorded_by     | role
 RCP-2026-000001 | 400.00 | head@school.example | admin
 RCP-2026-000002 | 600.00 | head@school.example | admin
 RCP-2026-000003 | 500.25 | head@school.example | admin
```

The same was true for both bills (`fee_records.created_by`), no payment had an empty
`created_by` (the column is also `NOT NULL` with a foreign key), and the audit log held a
`fee.create` row per bill and a `fee.payment.create` row per payment.

**API documentation is available in the development environment.** A development stack
(`docker compose up`, `ENVIRONMENT=development`) answered `GET /docs` and
`GET /openapi.json` with 200 and the Swagger UI page. The production stack answered 404 to
both, which is intended: the docs exist only in development.

**Database migrations can create the schema on a clean database.** Every run above, and
every CI run, applies `alembic upgrade head` to an empty database; `tests/test_migrations.py`
also upgrades revision by revision and downgrades back to nothing, and `alembic check`
confirms the models and migrations describe the same schema.

**Automated tests cover successful flows, validation failures, unauthorized access, and
forbidden cross-user access.** The full suite, run on a brand-new database for this
release: **1536 passed in 10m10s**, with `ruff`, `ruff format --check` and
`alembic check` clean.

**No production workflow depends on browser `localStorage`.** This is a backend, so it has
no browser storage to depend on. What can be shown is that the API never sets a cookie
and every response with a body is JSON, so it cannot hold state in a browser or serve a
page that does. The run observed 191 responses and recorded neither. The API keeps all
state in the database; the sign-in token is returned to the caller and sent back in the
`Authorization` header.

## Not covered by Section 8

These are known limits, not failures of a criterion. They are listed in
[OPERATIONS.md](OPERATIONS.md#known-limits-of-this-version): no payment void or refund, no
an email-based "forgot password" (a reset needs an administrator to hand over a code),
in-memory rate limiting, and the undefined result-publication and term-locking rules.

One observation from writing the run: the amount `"1e3"` is accepted as `1000.00`. Money
is documented as a decimal string with at most two decimals, and the value is exact, so
this is a leniency in parsing rather than a wrong figure.
