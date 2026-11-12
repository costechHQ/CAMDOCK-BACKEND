"""Shows the SRS Section 8 acceptance criteria against a RUNNING API, over HTTP only.

It signs in as an existing administrator, creates its own teachers, parents, students,
scores, bills and payments, and checks each criterion from the outside, the way a
frontend would. It prints one PASS/FAIL line per criterion and exits 1 if any fails.

THIS WRITES DATA. Use it on a fresh or throwaway database, never on the school's real one.
It refuses to run unless you pass --confirm-writes, and refuses a database that already
has students unless you also pass --allow-existing-data.

    export ACCEPTANCE_ADMIN_PASSWORD=...        # the existing admin's password
    uv run python scripts/acceptance_run.py --base-url http://127.0.0.1:8000 \
        --admin-email head@school.example --confirm-writes

Criteria that cannot be shown through HTTP (tests, migrations, the development docs)
are listed as EXTERNAL with where they are shown. See docs/ACCEPTANCE.md.
"""

import argparse
import os
import secrets
import sys
import time
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal

import httpx

API = "/api/v1"
PASS, FAIL, EXTERNAL = "PASS", "FAIL", "EXTERNAL"

# Section 8 of the SRS, word for word. A test keeps this list and the SRS in step.
AUTH = [
    "A valid registered account can authenticate.",
    "Invalid credentials are rejected.",
    "Anonymous requests cannot read protected data or mutate records.",
    "A parent cannot read another student's results or fee records by changing IDs.",
    "A teacher cannot read or modify records outside assigned classes/subjects.",
    "An administrator-only endpoint rejects teacher and parent accounts.",
    "Logout/session invalidation behaves according to the approved authentication design.",
]
STUDENTS = [
    "An administrator can create a valid student.",
    "Invalid or missing required fields return documented validation errors.",
    "Student identifiers are unique.",
    "Teachers see only students in their assigned classes.",
    "An administrator can create a teacher and assign classes.",
    "Class names are stored consistently.",
]
RESULTS = [
    "Authorized staff can create and update scores.",
    "Scores below 0 or above 100 are rejected unless the scoring policy is changed.",
    "The correct grade is returned for each score boundary.",
    "Student averages and class averages match the documented formulas.",
    "Missing scores are not silently treated as zero.",
    "Parents can only retrieve results for linked students.",
    "Result-sheet data contains the required student, term, subject, score, grade, and average fields.",
]
FEES = [
    "Only authorized administrators can record fee entries.",
    "Invalid billed or paid amounts are rejected.",
    "Paid, Partial, and Due statuses are calculated correctly.",
    "Outstanding balances are calculated correctly.",
    "Parents can retrieve fee records only for linked students.",
    "Receipt numbers are unique.",
    "Receipt data includes the fields specified in FR-FEE-008.",
    "Financial writes are attributable to an authenticated user.",
]
QUALITY = [
    "Automated tests cover successful flows, validation failures, unauthorized access, and forbidden cross-user access.",
    "API documentation is available in the development environment.",
    "Database migrations can create the schema on a clean database.",
    "No production workflow depends on browser `localStorage` as its source of truth.",
    "No production account can authenticate merely by selecting a role or name.",
]
EXTERNAL_CRITERIA = {
    QUALITY[0]: "pytest (the full suite, run in CI and in docs/ACCEPTANCE.md)",
    QUALITY[1]: "GET /docs on a development stack (docs/ACCEPTANCE.md)",
    QUALITY[2]: "alembic upgrade head on an empty database (docs/ACCEPTANCE.md, CI)",
}
GRADE_BOUNDARIES = [
    ("100", "A1"),
    ("75", "A1"),
    ("74.99", "B2"),
    ("70", "B2"),
    ("69.99", "B3"),
    ("65", "B3"),
    ("64.99", "C4"),
    ("60", "C4"),
    ("59.99", "C5"),
    ("55", "C5"),
    ("54.99", "C6"),
    ("50", "C6"),
    ("49.99", "D7"),
    ("45", "D7"),
    ("44.99", "E8"),
    ("40", "E8"),
    ("39.99", "F9"),
    ("0", "F9"),
]


class Run:
    """Talks to the API and records what each criterion saw."""

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=20)
        self.outcomes: dict[str, list[str]] = {}
        self.cookies_seen = False
        self.non_json_seen = False
        self.requests = 0

    def call(
        self,
        method: str,
        path: str,
        token: str | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        while True:
            response = self.http.request(method, API + path, headers=headers, **kwargs)
            self.requests += 1
            if (
                response.status_code == 429
                and response.json().get("error", {}).get("code") == "rate_limited"
            ):
                time.sleep(int(response.headers.get("retry-after", "5")) + 1)
                continue
            break
        if "set-cookie" in response.headers:
            self.cookies_seen = True
        # An empty body (204) has no type; anything with a body must be JSON.
        if response.content and "application/json" not in response.headers.get(
            "content-type", ""
        ):
            self.non_json_seen = True
        return response

    def check(self, criterion: str, condition: bool, detail: str) -> None:
        """One observation for a criterion. It passes only if all its observations do."""
        self.outcomes.setdefault(criterion, []).append("" if condition else f"{detail}")

    def verdict(self, criterion: str) -> tuple[str, str]:
        if criterion in EXTERNAL_CRITERIA:
            return EXTERNAL, EXTERNAL_CRITERIA[criterion]
        seen = self.outcomes.get(criterion)
        if not seen:
            return FAIL, "nothing was checked"
        failures = [s for s in seen if s]
        if failures:
            return FAIL, "; ".join(failures)
        return PASS, f"{len(seen)} observations"


def login(run: Run, email: str, password: str) -> httpx.Response:
    return run.call("POST", "/auth/login", json={"email": email, "password": password})


def token_of(response: httpx.Response) -> str:
    return response.json()["access_token"]


def error_code(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return "?"


def quantize(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def run_checks(run: Run, admin_email: str, admin_password: str) -> None:
    """The scenario. Each block records observations against SRS criteria."""
    # --- accounts ------------------------------------------------------------------
    signed_in = login(run, admin_email, admin_password)
    admin = token_of(signed_in)
    me = run.call("GET", "/auth/me", admin)
    run.check(
        AUTH[0],
        signed_in.status_code == 200
        and me.status_code == 200
        and me.json()["role"] == "admin",
        f"login {signed_in.status_code}, me {me.status_code}",
    )

    wrong = login(run, admin_email, "not-the-password-" + secrets.token_hex(4))
    unknown = login(
        run, f"nobody-{secrets.token_hex(3)}@school.example", "whatever-12345"
    )
    run.check(
        AUTH[1], wrong.status_code == 401, f"wrong password gave {wrong.status_code}"
    )
    run.check(
        AUTH[1], unknown.status_code == 401, f"unknown email gave {unknown.status_code}"
    )
    run.check(
        AUTH[1],
        wrong.json() == unknown.json(),
        "wrong password and unknown email give different messages",
    )

    classes = run.call("GET", "/classes", admin).json()
    subjects = run.call("GET", "/subjects", admin).json()
    terms = run.call("GET", "/terms", admin).json()["items"]
    class_a, class_b = classes[0], classes[1]
    term = terms[0]
    tag = secrets.token_hex(3)

    def make_user(role: str) -> tuple[int, str, str]:
        email = f"{role}-{tag}-{secrets.token_hex(2)}@school.example"
        pw = secrets.token_urlsafe(18)
        created = run.call(
            "POST", "/users", admin, json={"email": email, "role": role, "password": pw}
        )
        return created.json()["id"], email, pw

    # --- teachers (FR: admin creates a teacher and assigns classes) ----------------
    t1_user, t1_email, t1_pw = make_user("teacher")
    t2_user, t2_email, t2_pw = make_user("teacher")
    p1_user, p1_email, p1_pw = make_user("parent")
    p2_user, p2_email, p2_pw = make_user("parent")
    # Scores need subjects that belong to the class; find the first three that do.
    subject_ids = [s["id"] for s in subjects]
    teacher_one = run.call(
        "POST",
        "/teachers",
        admin,
        json={
            "user_id": t1_user,
            "full_name": "Teacher One",
            "class_ids": [class_a["id"]],
            "subject_ids": subject_ids[:2],
        },
    )
    run.call(
        "POST",
        "/teachers",
        admin,
        json={
            "user_id": t2_user,
            "full_name": "Teacher Two",
            "class_ids": [class_b["id"]],
            "subject_ids": subject_ids[:2],
        },
    )
    listing = run.call("GET", "/teachers", admin, params={"limit": 100}).json()["items"]
    mine = [t for t in listing if t["user_id"] == t1_user]
    run.check(
        STUDENTS[4],
        teacher_one.status_code == 201
        and bool(mine)
        and [c["id"] for c in mine[0]["classes"]] == [class_a["id"]],
        f"teacher create {teacher_one.status_code}, assigned classes {mine and mine[0]['classes']}",
    )
    t1 = token_of(login(run, t1_email, t1_pw))
    t2 = token_of(login(run, t2_email, t2_pw))
    p1 = token_of(login(run, p1_email, p1_pw))
    p2 = token_of(login(run, p2_email, p2_pw))
    run.check(AUTH[0], all([t1, t2, p1, p2]), "a teacher or parent could not sign in")

    # --- students ------------------------------------------------------------------
    def make_student(name: str, class_id: int) -> dict:
        created = run.call(
            "POST", "/students", admin, json={"full_name": name, "class_id": class_id}
        )
        run.check(
            STUDENTS[0],
            created.status_code == 201 and bool(created.json().get("student_number")),
            f"creating {name} gave {created.status_code}",
        )
        return created.json()

    s1 = make_student("Ada Obi", class_a["id"])
    s2 = make_student("Chike Eze", class_a["id"])
    s3 = make_student("Ngozi Okafor", class_b["id"])
    s4 = make_student("Emeka Nwosu", class_b["id"])
    s5 = make_student("Ifeoma Udo", class_a["id"])  # no scores at all

    numbers = [s["student_number"] for s in (s1, s2, s3, s4, s5)]
    run.check(
        STUDENTS[2], len(set(numbers)) == len(numbers), f"duplicate numbers {numbers}"
    )
    forced = run.call(
        "POST",
        "/students",
        admin,
        json={
            "full_name": "Chosen Number",
            "class_id": class_a["id"],
            "student_number": numbers[0],
        },
    )
    run.check(
        STUDENTS[2],
        forced.status_code == 422,
        f"a caller-chosen student_number gave {forced.status_code}",
    )

    bad_bodies = [
        {"class_id": class_a["id"]},
        {"full_name": "", "class_id": class_a["id"]},
        {"full_name": "No Class"},
        {"full_name": "Wrong Class", "class_id": 999999},
        {"full_name": "Extra Field", "class_id": class_a["id"], "is_admin": True},
        {"full_name": 123, "class_id": class_a["id"]},
    ]
    for body in bad_bodies:
        response = run.call("POST", "/students", admin, json=body)
        shaped = error_code(response) == "validation_error"
        run.check(
            STUDENTS[1],
            response.status_code == 422 and shaped,
            f"{body} gave {response.status_code} {error_code(response)}",
        )
    missing_detail = run.call(
        "POST", "/students", admin, json={"class_id": class_a["id"]}
    )
    run.check(
        STUDENTS[1],
        bool(missing_detail.json()["error"].get("details")),
        "a 422 carried no field details",
    )

    visible_t1 = run.call("GET", "/students", t1, params={"limit": 100}).json()["items"]
    ids_t1 = {s["id"] for s in visible_t1}
    run.check(
        STUDENTS[3],
        {s1["id"], s2["id"], s5["id"]} <= ids_t1
        and not ({s3["id"], s4["id"]} & ids_t1),
        f"teacher one saw students {sorted(ids_t1)}",
    )
    run.check(
        STUDENTS[3],
        {s["class_id"] for s in visible_t1} == {class_a["id"]},
        "teacher one saw a student from another class",
    )

    all_classes = run.call("GET", "/classes", admin).json()
    names = [c["name"] for c in all_classes]
    stored = run.call("GET", f"/students/{s1['id']}", admin).json()
    run.check(
        STUDENTS[5],
        len(names) == len(set(names)) and all(n == n.strip() and n for n in names),
        f"class names not unique or not trimmed: {names}",
    )
    run.check(
        STUDENTS[5],
        stored["class_name"] == class_a["name"] and s1["class_name"] == class_a["name"],
        "a student's class name differs from the class list",
    )

    # --- results -------------------------------------------------------------------
    def put_score(
        token: str, student: int, subject: int, score: object
    ) -> httpx.Response:
        return run.call(
            "PUT",
            "/results",
            token,
            json={
                "student_id": student,
                "subject_id": subject,
                "term_id": term["id"],
                "score": score,
            },
        )

    usable: list[int] = []
    for subject_id in subject_ids:
        probe = put_score(admin, s1["id"], subject_id, "80")
        if probe.status_code in (200, 201):
            usable.append(subject_id)
        if len(usable) == 3:
            break
    sub1, sub2, sub3 = usable
    run.check(RESULTS[0], len(usable) == 3, "fewer than three subjects accept scores")
    put_score(admin, s1["id"], sub2, "71")
    put_score(admin, s1["id"], sub3, "62")

    teacher_made = put_score(t1, s2["id"], sub1, "88")
    teacher_updated = put_score(t1, s2["id"], sub1, "90")
    run.check(
        RESULTS[0],
        teacher_made.status_code in (200, 201) and teacher_updated.status_code == 200,
        f"teacher create {teacher_made.status_code}, update {teacher_updated.status_code}",
    )
    run.check(
        RESULTS[0],
        teacher_made.json().get("id") == teacher_updated.json().get("id")
        and teacher_updated.json().get("score") == "90.00",
        "an update created a second row or kept the old score",
    )
    put_score(admin, s2["id"], sub2, "55")

    outside_student = put_score(t1, s3["id"], sub1, "70")
    outside_subject = put_score(t1, s1["id"], sub3, "99")
    run.check(
        AUTH[4],
        outside_student.status_code == 404,
        f"teacher one writing to another class's student gave {outside_student.status_code}",
    )
    run.check(
        AUTH[4],
        outside_subject.status_code == 403,
        f"teacher one writing an unassigned subject gave {outside_subject.status_code}",
    )
    run.check(
        AUTH[4],
        run.call("GET", f"/students/{s3['id']}", t1).status_code == 404
        and run.call(
            "GET", f"/students/{s3['id']}/results", t1, params={"term_id": term["id"]}
        ).status_code
        == 404,
        "teacher one could read a student of another class",
    )
    kept = run.call(
        "GET", f"/students/{s1['id']}/results", admin, params={"term_id": term["id"]}
    ).json()
    run.check(
        AUTH[4],
        {i["subject_id"]: i["score"] for i in kept["items"]}.get(sub3) == "62.00",
        "a refused write changed a stored score",
    )
    other_class_list = run.call(
        "GET", "/results", t2, params={"term_id": term["id"], "class_id": class_a["id"]}
    )
    run.check(
        AUTH[4],
        other_class_list.status_code in (403, 404)
        or other_class_list.json().get("items") == [],
        f"teacher two listed class A scores: {other_class_list.status_code}",
    )

    for bad in ("-0.01", "100.01", "101", "-1", "abc", True, None, "1e3", "50.123"):
        response = put_score(admin, s4["id"], sub1, bad)
        run.check(
            RESULTS[1],
            response.status_code == 422,
            f"score {bad!r} gave {response.status_code}",
        )
    nothing = run.call(
        "GET", f"/students/{s4['id']}/results", admin, params={"term_id": term["id"]}
    ).json()
    run.check(RESULTS[1], nothing["items"] == [], "a rejected score was stored")

    for score, grade in GRADE_BOUNDARIES:
        response = put_score(admin, s3["id"], sub1, score)
        got = (
            response.json().get("grade") if response.status_code in (200, 201) else None
        )
        run.check(
            RESULTS[2], got == grade, f"score {score}: expected {grade}, got {got}"
        )

    # Averages: s1 = 80, 71, 62; s2 = 90, 55; s5 none. Class A means per subject.
    s1_results = run.call(
        "GET", f"/students/{s1['id']}/results", admin, params={"term_id": term["id"]}
    ).json()
    s2_results = run.call(
        "GET", f"/students/{s2['id']}/results", admin, params={"term_id": term["id"]}
    ).json()
    s5_results = run.call(
        "GET", f"/students/{s5['id']}/results", admin, params={"term_id": term["id"]}
    ).json()
    expect_s1 = quantize(Decimal(80 + 71 + 62) / 3, "0.1")
    expect_s2 = quantize(Decimal(90 + 55) / 2, "0.1")
    run.check(
        RESULTS[3],
        s1_results["average"] == str(expect_s1)
        and s2_results["average"] == str(expect_s2),
        f"student averages {s1_results['average']}, {s2_results['average']} "
        f"expected {expect_s1}, {expect_s2}",
    )
    class_avg = run.call(
        "GET",
        f"/classes/{class_a['id']}/averages",
        admin,
        params={"term_id": term["id"]},
    ).json()
    means = [Decimal(80 + 90) / 2, Decimal(71 + 55) / 2, Decimal(62)]
    expect_class = quantize(sum(means) / 3, "0.1")
    by_subject = {s["subject_id"]: s["average"] for s in class_avg["subjects"]}
    run.check(
        RESULTS[3],
        class_avg["overall_average"] == str(expect_class)
        and by_subject.get(sub1) == "85.0"
        and by_subject.get(sub2) == "63.0"
        and by_subject.get(sub3) == "62.0",
        f"class averages {class_avg['overall_average']} {by_subject}; expected {expect_class}",
    )
    # An average that needs the half-up rule: 60.10 and 60.00 give 60.05, shown as 60.1.
    put_score(admin, s4["id"], sub1, "60.10")
    put_score(admin, s4["id"], sub2, "60.00")
    rounded = run.call(
        "GET", f"/students/{s4['id']}/results", admin, params={"term_id": term["id"]}
    ).json()
    run.check(
        RESULTS[3],
        rounded["average"] == "60.1",
        f"60.10 and 60.00 gave {rounded['average']}, expected half-up 60.1",
    )

    run.check(
        RESULTS[4],
        s5_results["average"] is None and s5_results["subjects_recorded"] == 0,
        f"a student with no scores had average {s5_results['average']}",
    )
    run.check(
        RESULTS[4],
        s2_results["subjects_recorded"] == 2 and s2_results["average"] == "72.5",
        f"two scores of 90 and 55 gave {s2_results['average']} over "
        f"{s2_results['subjects_recorded']} subjects (a missing score counted as zero?)",
    )

    # --- parents -------------------------------------------------------------------
    link1 = run.call(
        "POST",
        f"/students/{s1['id']}/parents",
        admin,
        json={"parent_user_id": p1_user, "relationship": "Mother"},
    )
    link2 = run.call(
        "POST", f"/students/{s2['id']}/parents", admin, json={"parent_user_id": p2_user}
    )
    run.check(
        RESULTS[5],
        link1.status_code == 201 and link2.status_code == 201,
        f"linking gave {link1.status_code}, {link2.status_code}",
    )
    own = run.call(
        "GET", f"/students/{s1['id']}/results", p1, params={"term_id": term["id"]}
    )
    run.check(
        RESULTS[5],
        own.status_code == 200 and own.json()["average"] == "71.0",
        "parent could not read their own child",
    )
    nonexistent = run.call(
        "GET", "/students/999999/results", p1, params={"term_id": term["id"]}
    )
    for path in (
        f"/students/{s2['id']}",
        f"/students/{s2['id']}/results",
        f"/students/{s2['id']}/result-sheet",
        f"/students/{s3['id']}/results",
    ):
        response = run.call("GET", path, p1, params={"term_id": term["id"]})
        run.check(
            RESULTS[5],
            response.status_code == 404,
            f"parent one got {response.status_code} for {path}",
        )
        run.check(
            AUTH[3],
            response.status_code == 404 and response.json() == nonexistent.json(),
            f"parent one got a different answer for {path} than for an unknown id",
        )
    run.check(
        AUTH[3],
        run.call("GET", "/students", p1).status_code == 403,
        "a parent could list students",
    )
    overview = run.call("GET", "/dashboard/overview", p1).json()
    children = [c["student"]["id"] for c in overview.get("children", [])]
    run.check(
        AUTH[3],
        children == [s1["id"]],
        f"parent one's dashboard listed children {children}",
    )

    sheet = run.call(
        "GET",
        f"/students/{s1['id']}/result-sheet",
        admin,
        params={"term_id": term["id"]},
    ).json()
    needed = (
        sheet.get("school", {}).get("name"),
        sheet.get("student", {}).get("full_name"),
        sheet.get("student", {}).get("student_number"),
        sheet.get("class", {}).get("name"),
        sheet.get("term", {}).get("name"),
        sheet.get("term", {}).get("academic_year"),
        sheet.get("overall_average"),
    )
    rows = sheet.get("subjects", [])
    run.check(RESULTS[6], all(needed), f"sheet is missing a header field: {needed}")
    run.check(
        RESULTS[6],
        len(rows) == 3
        and all(
            r.get("subject_name")
            and r.get("score")
            and r.get("grade")
            and r.get("class_average")
            for r in rows
        ),
        f"sheet subject rows incomplete: {rows}",
    )
    run.check(
        RESULTS[6], sheet.get("overall_average") == "71.0", "sheet average is wrong"
    )
    parent_sheet = run.call(
        "GET", f"/students/{s1['id']}/result-sheet", p1, params={"term_id": term["id"]}
    )
    run.check(
        RESULTS[6],
        parent_sheet.status_code == 200,
        "a parent could not open their child's sheet",
    )

    # --- fees ----------------------------------------------------------------------
    def bill(token: str | None, body: dict) -> httpx.Response:
        return run.call("POST", "/fees", token, json=body)

    def fee_body(
        student: dict, amount: object, paid: object = None, kind: str = "Tuition Fee"
    ) -> dict:
        body: dict = {
            "student_id": student["id"],
            "term_id": term["id"],
            "fee_type": kind,
            "amount_billed": amount,
        }
        if paid is not None:
            body["amount_paid"] = paid
        return body

    for who, token in (("teacher", t1), ("parent", p1), ("anonymous", None)):
        attempt = bill(token, fee_body(s1, "1000", kind="Uniform & Materials"))
        run.check(
            FEES[0],
            attempt.status_code == (401 if token is None else 403),
            f"{who} creating a bill gave {attempt.status_code}",
        )
    for bad in ("0", "-5", "abc", "1000.123", "", None, "99999999999999"):
        response = bill(admin, fee_body(s1, bad, kind="PTA Levy"))
        run.check(
            FEES[1],
            response.status_code == 422,
            f"billed {bad!r} gave {response.status_code}",
        )
    for paid in ("-1", "1500", "abc", "10.999"):
        response = bill(admin, fee_body(s1, "1000", paid, kind="PTA Levy"))
        run.check(
            FEES[1],
            response.status_code == 422,
            f"paid {paid!r} gave {response.status_code}",
        )

    due = bill(admin, fee_body(s1, "1000"))
    fee1 = due.json()
    run.check(
        FEES[2],
        due.status_code == 201
        and fee1["status"] == "due"
        and fee1["balance"] == "1000.00",
        f"a bill with nothing paid: {due.status_code} {fee1.get('status')}",
    )
    first = run.call(
        "POST", f"/fees/{fee1['id']}/payments", admin, json={"amount": "400"}
    )
    run.check(
        FEES[2],
        first.status_code == 201 and first.json()["fee"]["status"] == "partial",
        "a part payment did not make the bill partial",
    )
    run.check(
        FEES[3],
        first.json()["fee"]["amount_paid"] == "400.00"
        and first.json()["fee"]["balance"] == "600.00",
        f"after 400 of 1000: {first.json()['fee']['amount_paid']} paid, {first.json()['fee']['balance']} left",
    )
    over = run.call(
        "POST", f"/fees/{fee1['id']}/payments", admin, json={"amount": "600.01"}
    )
    run.check(
        FEES[1],
        over.status_code == 422,
        f"paying more than is owed gave {over.status_code}",
    )
    second = run.call(
        "POST", f"/fees/{fee1['id']}/payments", admin, json={"amount": "600"}
    )
    run.check(
        FEES[2],
        second.status_code == 201 and second.json()["fee"]["status"] == "paid",
        "paying the balance did not make the bill paid",
    )
    run.check(
        FEES[3],
        second.json()["fee"]["balance"] == "0.00"
        and second.json()["fee"]["amount_paid"] == "1000.00",
        "a fully paid bill still shows a balance",
    )
    fee2 = bill(admin, fee_body(s2, "2500.50", "500.25", "Examination Fee")).json()
    run.check(
        FEES[3],
        fee2["amount_paid"] == "500.25"
        and fee2["balance"] == "2000.25"
        and fee2["status"] == "partial",
        f"a bill with a first payment: {fee2.get('amount_paid')} / {fee2.get('balance')}",
    )

    for who, token in (("teacher", t1), ("parent", p1), ("anonymous", None)):
        attempt = run.call(
            "POST", f"/fees/{fee1['id']}/payments", token, json={"amount": "1"}
        )
        run.check(
            FEES[0],
            attempt.status_code == (401 if token is None else 403),
            f"{who} recording a payment gave {attempt.status_code}",
        )
    unchanged = run.call("GET", f"/fees/{fee1['id']}", admin).json()
    run.check(
        FEES[7],
        unchanged["amount_paid"] == "1000.00" and len(unchanged["payments"]) == 2,
        "a refused payment changed the bill",
    )

    own_fees = run.call("GET", "/fees", p1, params={"limit": 100}).json()
    run.check(
        FEES[4],
        {f["id"] for f in own_fees["items"]} == {fee1["id"]},
        f"parent one's fee list: {[f['id'] for f in own_fees['items']]}",
    )
    other_fee = run.call("GET", f"/fees/{fee2['id']}", p1)
    unknown_fee = run.call("GET", "/fees/999999", p1)
    run.check(
        FEES[4],
        other_fee.status_code == 404 and other_fee.json() == unknown_fee.json(),
        "parent one could tell another child's bill from an unknown one",
    )
    run.check(
        AUTH[3],
        other_fee.status_code == 404
        and run.call("GET", f"/fees/{fee2['id']}/receipt", p1).status_code == 404
        and run.call("GET", "/fees", p1, params={"student_id": s2["id"]}).status_code
        in (200, 404)
        and run.call("GET", "/fees", p1, params={"student_id": s2["id"]})
        .json()
        .get("items", [])
        == [],
        "parent one could reach another child's fee records",
    )
    run.check(
        FEES[4],
        run.call("GET", f"/fees/{fee1['id']}", p1).status_code == 200,
        "a parent could not read their child's bill",
    )

    receipts: list[str] = []
    for fee in (fee1, fee2):
        detail = run.call("GET", f"/fees/{fee['id']}", admin).json()
        receipts.append(detail["receipt_number"])
        receipts.extend(p["receipt_number"] for p in detail["payments"])
    run.check(
        FEES[5],
        len(receipts) == len(set(receipts)) and len(receipts) == 5,
        f"receipt numbers: {receipts}",
    )

    receipt = run.call("GET", f"/fees/{fee1['id']}/receipt", admin).json()
    fields = (
        receipt.get("receipt_number"),
        receipt.get("date"),
        receipt.get("student", {}).get("full_name"),
        receipt.get("student", {}).get("class_name"),
        receipt.get("fee_type"),
        receipt.get("term", {}).get("name"),
        receipt.get("amount_billed"),
        receipt.get("amount_paid"),
        receipt.get("balance"),
        receipt.get("status"),
        receipt.get("school", {}).get("name"),
        receipt.get("signatures", {}).get("bursar_label"),
        receipt.get("signatures", {}).get("stamp_label"),
    )
    run.check(
        FEES[6],
        all(f is not None and f != "" for f in fields),
        f"receipt fields: {fields}",
    )
    one_payment = run.call(
        "GET",
        f"/fees/{fee1['id']}/payments/{first.json()['payment']['id']}/receipt",
        admin,
    )
    run.check(
        FEES[6],
        one_payment.status_code == 200
        and one_payment.json()["receipt_number"]
        == first.json()["payment"]["receipt_number"],
        "an instalment receipt did not carry its own number",
    )

    # Attribution: every money write above needed a signed-in administrator, and a
    # refused write left no trace. Which user each payment is stored against is a
    # database fact, shown by the SQL query in docs/ACCEPTANCE.md.
    for token in (None, t1, p1):
        before = run.call("GET", f"/fees/{fee1['id']}", admin).json()["payments"]
        run.call("POST", f"/fees/{fee1['id']}/payments", token, json={"amount": "1"})
        after_payments = run.call("GET", f"/fees/{fee1['id']}", admin).json()[
            "payments"
        ]
        run.check(
            FEES[7], before == after_payments, "an unauthenticated write was recorded"
        )

    # --- sessions and role selection -------------------------------------------------
    protected = [
        ("GET", "/students"),
        ("GET", "/teachers"),
        ("GET", "/fees"),
        ("GET", "/results"),
        ("GET", "/classes"),
        ("GET", "/subjects"),
        ("GET", "/terms"),
        ("GET", "/dashboard/overview"),
        ("GET", f"/students/{s1['id']}"),
        ("GET", f"/fees/{fee1['id']}"),
        ("POST", "/students"),
        ("POST", "/fees"),
        ("POST", "/teachers"),
        ("POST", "/users"),
        ("PUT", "/results"),
    ]
    for method, path in protected:
        anonymous = run.call(method, path, json={} if method != "GET" else None)
        forged = run.call(
            method,
            path,
            "not-a-real-token-" + "x" * 30,
            json={} if method != "GET" else None,
        )
        run.check(
            AUTH[2],
            anonymous.status_code == 401 and forged.status_code == 401,
            f"{method} {path}: no token {anonymous.status_code}, forged token {forged.status_code}",
        )

    admin_only = [
        ("POST", "/students", {"full_name": "X", "class_id": class_a["id"]}),
        ("POST", "/teachers", {"user_id": t1_user, "full_name": "X"}),
        (
            "POST",
            "/users",
            {"email": "x@school.example", "role": "parent", "password": "x" * 14},
        ),
        ("POST", "/fees", fee_body(s1, "10")),
        ("GET", "/teachers", None),
        ("POST", f"/students/{s1['id']}/parents", {"parent_user_id": p1_user}),
    ]
    for method, path, body in admin_only:
        for who, token in (("teacher", t1), ("parent", p1)):
            response = run.call(method, path, token, json=body)
            run.check(
                AUTH[5],
                response.status_code == 403,
                f"{who} on {method} {path} gave {response.status_code}",
            )

    fresh = token_of(login(run, t1_email, t1_pw))
    run.call("POST", "/auth/logout", fresh)
    after = run.call("GET", "/auth/me", fresh)
    run.check(
        AUTH[6],
        after.status_code == 401,
        f"a logged-out token gave {after.status_code}",
    )
    again = login(run, t1_email, t1_pw)
    run.check(AUTH[6], again.status_code == 200, "signing in again after logout failed")
    run.check(
        AUTH[6],
        run.call("GET", "/auth/me", t1).status_code == 200,
        "logging out one session ended another",
    )

    # Choosing a role or name is never enough.
    as_admin = run.call(
        "POST",
        "/auth/login",
        json={"email": t1_email, "password": t1_pw, "role": "admin"},
    )
    if as_admin.status_code == 200:
        role = as_admin.json()["user"]["role"]
        run.check(
            QUALITY[4], role == "teacher", f"asking for the admin role gave role {role}"
        )
    else:
        run.check(
            QUALITY[4],
            as_admin.status_code == 422,
            f"role in login gave {as_admin.status_code}",
        )
    for body in (
        {"email": t1_email},
        {"name": "Teacher One"},
        {"role": "admin"},
        {"email": admin_email, "password": ""},
    ):
        response = run.call("POST", "/auth/login", json=body)
        run.check(
            QUALITY[4],
            response.status_code in (401, 422),
            f"login with {sorted(body)} gave {response.status_code}",
        )

    # Nothing in the API stores or reads browser state.
    run.check(QUALITY[3], not run.cookies_seen, "the API set a cookie")
    run.check(QUALITY[3], not run.non_json_seen, "the API returned a non-JSON page")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:8000")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument(
        "--confirm-writes",
        action="store_true",
        help="acknowledge that this creates data in the target database",
    )
    parser.add_argument(
        "--allow-existing-data",
        action="store_true",
        help="run even though the database already has students",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None, runner: Callable[..., None] = run_checks
) -> int:
    args = parse_args(argv)
    if not args.confirm_writes:
        print(
            "Refusing to run: this writes data. Re-run with --confirm-writes on a throwaway database."
        )
        return 2
    password = os.environ.get("ACCEPTANCE_ADMIN_PASSWORD", "")
    if not password:
        print("Set ACCEPTANCE_ADMIN_PASSWORD to the administrator's password.")
        return 2
    run = Run(args.base_url)
    stats = run.call("GET", "/public/stats")
    if stats.status_code != 200:
        print(f"The API did not answer /public/stats ({stats.status_code}).")
        return 2
    if stats.json().get("students", 0) > 0 and not args.allow_existing_data:
        print(
            "Refusing to run: the database already has students. Use a fresh database."
        )
        return 2

    crashed = ""
    try:
        runner(run, args.admin_email, password)
    except Exception as error:  # noqa: BLE001  (any crash is a failed run, reported below)
        crashed = f"{type(error).__name__}: {error}"

    criteria = [*AUTH, *STUDENTS, *RESULTS, *FEES, *QUALITY]
    failed = 0
    for criterion in criteria:
        verdict, detail = run.verdict(criterion)
        failed += verdict == FAIL
        print(f"{verdict:8} {criterion}")
        if verdict != PASS:
            print(f"         {detail}")
    if crashed:
        print(f"FAIL     the run stopped before it finished ({crashed})")
        failed += 1
    shown = sum(run.verdict(c)[0] == PASS for c in criteria)
    external = sum(run.verdict(c)[0] == EXTERNAL for c in criteria)
    print(
        f"\n{shown} passed, {failed} failed, {external} shown elsewhere "
        f"({len(criteria)} criteria, {run.requests} requests)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
