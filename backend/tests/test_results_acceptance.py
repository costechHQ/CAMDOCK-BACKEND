"""SRS section 8, "Results": one test per checklist item, over the HTTP API.

Students, teachers, teacher assignments and every score are created through the real
endpoints. Only what has no endpoint is inserted directly: classes, subjects, class
subject links, terms, the parent-to-student link, and the accounts of the shared `world`
(the end-to-end story creates its accounts through `POST /users`). Logins are real.

Two cross-cutting groups repeat the Phase 3 scope checks on every results endpoint.
"""

import logging
from datetime import date

import pytest
from sqlalchemy import func, insert, select

from app.models import AuditLog, ClassSubject, Result, Subject, User
from tests.test_access_scope import add, link, make_class
from tests.test_auth_api import PASSWORD, add_user, bearer, login_token
from tests.test_terms_api import make_terms, make_year

API = "/api/v1"
RESULTS = f"{API}/results"
STUDENTS = f"{API}/students"

# SRS FR-RES-005 grade table: (score, expected grade) around every boundary.
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


def login(db, client, role, email):
    """Insert an account, log in through the API; returns (user id, auth headers)."""
    user_id = add_user(db, email=email, role=role)
    return user_id, bearer(login_token(client, email=email))


def put_score(client, headers, world, student, subject, score, term="term"):
    return client.put(
        RESULTS,
        headers=headers,
        json={
            "student_id": world[student],
            "subject_id": world[subject],
            "term_id": world[term],
            "score": score,
        },
    )


def seed_score(client, headers, world, student, subject, score):
    response = put_score(client, headers, world, student, subject, score)
    assert response.status_code == 201, response.text


def get_results(client, headers, world, student="ada"):
    return client.get(
        f"{STUDENTS}/{world[student]}/results",
        headers=headers,
        params={"term_id": world["term"]},
    )


def get_sheet(client, headers, world, student="ada"):
    return client.get(
        f"{STUDENTS}/{world[student]}/result-sheet",
        headers=headers,
        params={"term_id": world["term"]},
    )


def get_class_averages(client, headers, world, class_key="p1"):
    return client.get(
        f"{API}/classes/{world[class_key]}/averages",
        headers=headers,
        params={"term_id": world["term"]},
    )


def get_listing(client, headers, world, **extra):
    return client.get(
        RESULTS, headers=headers, params={"term_id": world["term"], **extra}
    )


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


@pytest.fixture
def world(db, api_client):
    """A small school with the scores below, all entered through `PUT /results`.

    Primary 1, first term:
      Mathematics  Ada 80, Bola 70, Dave 75    (class mean 75.0)
      English      Ada 55.5, Bola 60           (class mean 57.75 -> 57.8)
      Art          Dave 50                     (class mean 50.0)
    Primary 2 (Chi): English 70.04, Art 70.05  (overall 70.045 -> 70.0, not 70.1)
    """
    ids = {
        "p1": make_class(db, "Primary 1"),
        "p2": make_class(db, "Primary 2"),
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
        "art": add(db, Subject, name="Art"),
    }
    for class_key in ("p1", "p2"):
        for subject in ("maths", "english", "art"):
            db.execute(
                insert(ClassSubject).values(
                    class_id=ids[class_key], subject_id=ids[subject]
                )
            )
    ids["term"] = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))[
        "First Term"
    ]

    ids["admin_id"], admin = login(db, api_client, "admin", "admin@camdock.test")
    for name, class_key in [
        ("ada", "p1"),
        ("bola", "p1"),
        ("dave", "p1"),
        ("chi", "p2"),
    ]:
        response = api_client.post(
            STUDENTS,
            headers=admin,
            json={"full_name": name.capitalize(), "class_id": ids[class_key]},
        )
        assert response.status_code == 201, response.text
        ids[name] = response.json()["id"]

    def teacher(email, class_keys, subject_keys):
        user_id, headers = login(db, api_client, "teacher", email)
        response = api_client.post(
            f"{API}/teachers",
            headers=admin,  # only an administrator may create the teacher record
            json={
                "user_id": user_id,
                "full_name": email.split("@")[0],
                "class_ids": [ids[k] for k in class_keys],
                "subject_ids": [ids[k] for k in subject_keys],
            },
        )
        assert response.status_code == 201, response.text
        return headers

    def parent(email, children):
        user_id, headers = login(db, api_client, "parent", email)
        for child in children:
            link(db, User(id=user_id, role="parent"), ids[child])
        return headers

    headers = {
        "anonymous": {},
        "admin": admin,
        "teacher_maths": teacher("tmaths@camdock.test", ["p1"], ["maths"]),
        "teacher_class_only": teacher("tclass@camdock.test", ["p1"], []),
        "teacher_other_class": teacher("tother@camdock.test", ["p2"], ["maths"]),
        "parent_ada": parent("pada@camdock.test", ["ada"]),
        "parent_two": parent("ptwo@camdock.test", ["bola", "dave"]),
        "parent_unlinked": parent("pnone@camdock.test", []),
    }
    ids["headers"] = headers
    ids["teacher_maths_id"] = db.execute(
        select(User.id).where(User.email == "tmaths@camdock.test")
    ).scalar_one()

    for student, subject, score in [
        ("ada", "maths", "80"),
        ("bola", "maths", "70"),
        ("dave", "maths", "75"),
        ("ada", "english", "55.5"),
        ("bola", "english", "60"),
        ("dave", "art", "50"),
        ("chi", "english", "70.04"),
        ("chi", "art", "70.05"),
    ]:
        seed_score(api_client, admin, ids, student, subject, score)
    return ids


# --- Authorized staff can create and update scores ---------------------------------


def test_authorized_staff_can_create_and_update_scores(db, api_client, world):
    teacher = world["headers"]["teacher_maths"]
    admin = world["headers"]["admin"]
    before = count(db, Result)

    new_student = api_client.post(
        STUDENTS, headers=admin, json={"full_name": "Emeka", "class_id": world["p1"]}
    ).json()["id"]
    created_by_teacher = put_score(
        api_client, teacher, world | {"emeka": new_student}, "emeka", "maths", "64.5"
    )
    created_by_admin = put_score(api_client, admin, world, "bola", "art", "64.5")
    updated_by_teacher = put_score(api_client, teacher, world, "ada", "maths", "85")
    updated_by_admin = put_score(api_client, admin, world, "ada", "maths", "86.25")

    assert (created_by_teacher.status_code, created_by_admin.status_code) == (201, 201)
    assert updated_by_teacher.status_code == 200
    assert updated_by_teacher.json()["score"] == "85.00"
    assert updated_by_admin.status_code == 200
    assert updated_by_admin.json()["score"] == "86.25"
    assert count(db, Result) == before + 2  # the two creations; updates add no rows

    audit = db.execute(
        select(AuditLog.__table__)
        .where(AuditLog.action == "result.update")
        .order_by(AuditLog.id)
    ).all()
    assert [(row.actor_user_id, row.changes) for row in audit] == [
        (world["teacher_maths_id"], {"score": {"from": "80.00", "to": "85.00"}}),
        (world["admin_id"], {"score": {"from": "85.00", "to": "86.25"}}),
    ]


# --- Scores below 0 or above 100 are rejected --------------------------------------


@pytest.mark.parametrize(
    "score", ["-0.01", "-1", "100.01", "101", "1000", "70.123", "abc", "NaN"]
)
def test_scores_outside_0_to_100_are_rejected(db, api_client, world, score):
    before = count(db, Result)

    response = put_score(
        api_client, world["headers"]["admin"], world, "bola", "art", score
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(d["field"].endswith("score") for d in error["details"])
    assert count(db, Result) == before


def test_the_limits_themselves_are_accepted(api_client, world):
    admin = world["headers"]["admin"]

    low = put_score(api_client, admin, world, "bola", "art", "0")
    high = put_score(api_client, admin, world, "bola", "art", "100")

    assert (low.status_code, high.status_code) == (201, 200)


# --- The correct grade is returned for each score boundary -------------------------


def test_the_correct_grade_is_returned_for_each_score_boundary(api_client, world):
    admin = world["headers"]["admin"]
    for score, grade in GRADE_BOUNDARIES:
        written = put_score(api_client, admin, world, "ada", "maths", score)
        results = get_results(api_client, admin, world).json()["items"]
        sheet = get_sheet(api_client, admin, world).json()["subjects"]
        listing = get_listing(api_client, admin, world)

        in_results = next(i for i in results if i["subject_id"] == world["maths"])
        on_sheet = next(i for i in sheet if i["subject_id"] == world["maths"])
        in_listing = next(
            i
            for i in listing.json()["items"]
            if i["student_id"] == world["ada"] and i["subject_id"] == world["maths"]
        )
        assert (
            written.json()["grade"]
            == in_results["grade"]
            == on_sheet["grade"]
            == in_listing["grade"]
            == grade
        ), score


# --- Student and class averages match the documented formulas ----------------------


def test_averages_match_the_documented_formulas_on_every_endpoint(api_client, world):
    admin = world["headers"]["admin"]

    student = get_results(api_client, admin, world).json()
    sheet = get_sheet(api_client, admin, world).json()
    p1 = get_class_averages(api_client, admin, world).json()
    p2 = get_class_averages(api_client, admin, world, "p2").json()

    # Student average: (80 + 55.5) / 2 = 67.75, rounded half up to one place.
    assert student["average"] == sheet["overall_average"] == "67.8"
    # Subject class averages, 57.75 rounded half up.
    assert {s["subject_name"]: s["average"] for s in p1["subjects"]} == {
        "Art": "50.0",
        "English Language": "57.8",
        "Mathematics": "75.0",
    }
    assert {s["subject_name"]: s["class_average"] for s in sheet["subjects"]} == {
        "English Language": "57.8",
        "Mathematics": "75.0",
    }
    # Overall class average: mean of the subject means, (75 + 57.75 + 50) / 3 = 60.9166...
    # A mean weighted by the number of scores would be 65.1.
    assert p1["overall_average"] == "60.9"
    # Rounding happens once: 70.045 -> 70.0, whereas averaging 70.0 and 70.1 gives 70.1.
    assert p2["overall_average"] == "70.0"


# --- Missing scores are not silently treated as zero --------------------------------


def test_missing_scores_are_not_silently_treated_as_zero(api_client, world):
    admin = world["headers"]["admin"]

    p1 = get_class_averages(api_client, admin, world).json()
    dave = get_sheet(api_client, admin, world, "dave").json()
    dave_results = get_results(api_client, admin, world, "dave").json()

    english = next(s for s in p1["subjects"] if s["subject_name"] == "English Language")
    # Three students, two English scores: 57.8 (not 38.3, which a zero would give).
    assert (english["average"], english["students_scored"]) == ("57.8", 2)
    assert p1["students_with_results"] == 3
    assert [s["subject_name"] for s in dave["subjects"]] == ["Art", "Mathematics"]
    assert [i["subject_name"] for i in dave_results["items"]] == ["Art", "Mathematics"]
    # Dave's average covers the two subjects he has: (50 + 75) / 2, not / 3.
    assert (dave["overall_average"], dave["subjects_recorded"]) == ("62.5", 2)


# --- Parents can only retrieve results for linked students --------------------------


def test_parents_can_only_retrieve_results_for_linked_students(api_client, world):
    parent = world["headers"]["parent_ada"]

    own = get_results(api_client, parent, world)
    own_sheet = get_sheet(api_client, parent, world)
    other = get_results(api_client, parent, world, "bola")
    other_sheet = get_sheet(api_client, parent, world, "bola")
    unknown = api_client.get(
        f"{STUDENTS}/999999/results",
        headers=parent,
        params={"term_id": world["term"]},
    )

    assert (own.status_code, own_sheet.status_code) == (200, 200)
    assert own.json()["student"]["id"] == world["ada"]
    # Another child and a nonexistent id are indistinguishable.
    assert (other.status_code, other_sheet.status_code, unknown.status_code) == (
        404,
        404,
        404,
    )
    assert other.json() == unknown.json()
    assert "Bola" not in other.text and "Bola" not in other_sheet.text
    # Every class-level door is shut.
    assert get_class_averages(api_client, parent, world).status_code == 403
    assert get_listing(api_client, parent, world).status_code == 403
    assert put_score(api_client, parent, world, "ada", "maths", "99").status_code == 403


def test_a_parent_of_two_children_sees_exactly_those_two(api_client, world):
    parent = world["headers"]["parent_two"]

    seen = {
        name: get_results(api_client, parent, world, name).status_code
        for name in ("ada", "bola", "dave", "chi")
    }

    assert seen == {"ada": 404, "bola": 200, "dave": 200, "chi": 404}


# --- Result-sheet data contains the required fields ---------------------------------


def test_result_sheet_data_contains_the_required_fields(api_client, world):
    sheet = get_sheet(api_client, world["headers"]["admin"], world).json()

    assert set(sheet) == {
        "school",
        "student",
        "class",
        "term",
        "subjects",
        "subjects_recorded",
        "overall_average",
    }
    assert sheet["school"] == {
        "name": "Camdock International School",
        "location": "Enugu State, Nigeria",
    }
    assert sheet["student"]["full_name"] == "Ada"
    assert sheet["class"]["name"] == "Primary 1"
    assert sheet["term"] == {
        "id": world["term"],
        "name": "First Term",
        "academic_year": "2025/2026",
    }
    for line in sheet["subjects"]:
        assert set(line) == {
            "subject_id",
            "subject_name",
            "score",
            "grade",
            "class_average",
        }
        assert line["grade"] and line["score"].count(".") == 1
    assert isinstance(sheet["overall_average"], str)


# --- Scope matrix: every results endpoint x every caller -----------------------------

ENDPOINTS = {
    "put_score": lambda c, h, w: put_score(c, h, w, "ada", "maths", "50"),
    "class_listing": lambda c, h, w: get_listing(c, h, w),
    "student_results": lambda c, h, w: get_results(c, h, w),
    "result_sheet": lambda c, h, w: get_sheet(c, h, w),
    "class_averages": lambda c, h, w: get_class_averages(c, h, w),
}
#                        put  list  results sheet averages
EXPECTED = {
    "anonymous": (401, 401, 401, 401, 401),
    "parent_unlinked": (403, 403, 404, 404, 403),
    "parent_ada": (403, 403, 200, 200, 403),
    "teacher_class_only": (403, 200, 200, 403, 200),
    "teacher_maths": (200, 200, 200, 403, 200),
    "teacher_other_class": (404, 200, 404, 403, 404),
    "admin": (200, 200, 200, 200, 200),
}
MATRIX = [
    (caller, endpoint, status)
    for caller, statuses in EXPECTED.items()
    for endpoint, status in zip(ENDPOINTS, statuses, strict=True)
]


@pytest.mark.parametrize(("caller", "endpoint", "status"), MATRIX)
def test_scope_matrix(api_client, world, caller, endpoint, status):
    response = ENDPOINTS[endpoint](api_client, world["headers"][caller], world)

    assert response.status_code == status, response.text
    if status >= 400:
        error = response.json()["error"]
        assert isinstance(error["code"], str) and isinstance(error["message"], str)


def test_restricted_callers_see_only_what_they_are_scoped_to(api_client, world):
    h = world["headers"]

    class_only = get_listing(api_client, h["teacher_class_only"], world).json()
    maths = get_listing(api_client, h["teacher_maths"], world).json()
    other = get_listing(api_client, h["teacher_other_class"], world).json()
    maths_view = get_results(api_client, h["teacher_maths"], world).json()
    class_only_view = get_results(api_client, h["teacher_class_only"], world).json()

    assert (class_only["items"], class_only["total"]) == ([], 0)
    assert {i["subject_name"] for i in maths["items"]} == {"Mathematics"}
    assert {i["class_id"] for i in maths["items"]} == {world["p1"]}
    assert maths["total"] == 3
    assert other["items"] == []  # Primary 2 teacher, but no Primary 2 maths score
    # A teacher sees only their own subject's scores, yet the aggregate covers all.
    assert [i["subject_name"] for i in maths_view["items"]] == ["Mathematics"]
    assert maths_view["average"] == "67.8" and maths_view["subjects_recorded"] == 2
    assert class_only_view["items"] == []


def test_a_teacher_cannot_modify_outside_assigned_classes_and_subjects(
    db, api_client, world
):
    h = world["headers"]
    before = db.execute(select(Result.score).order_by(Result.id)).scalars().all()

    wrong_subject = put_score(
        api_client, h["teacher_maths"], world, "ada", "english", "1"
    )
    wrong_class = put_score(api_client, h["teacher_maths"], world, "chi", "maths", "1")
    class_only = put_score(
        api_client, h["teacher_class_only"], world, "ada", "maths", "1"
    )

    assert wrong_subject.status_code == 403
    assert wrong_class.status_code == 404
    assert class_only.status_code == 403
    assert (
        db.execute(select(Result.score).order_by(Result.id)).scalars().all() == before
    )


# --- Error contract and secrets -------------------------------------------------------


def test_no_response_or_log_contains_secrets_or_other_students_data(
    api_client, world, caplog
):
    caplog.set_level(logging.DEBUG)
    h = world["headers"]
    responses = [
        ENDPOINTS[name](api_client, h[caller], world)
        for caller in EXPECTED
        for name in ENDPOINTS
    ]
    tokens = [v["Authorization"].split()[1] for v in h.values() if v]

    for response in responses:
        for forbidden in ("argon2", "password_hash", "token_hash", PASSWORD):
            assert forbidden not in response.text, response.text
    assert PASSWORD not in caplog.text
    assert not any(token in caplog.text for token in tokens)
    # Another child's name never appears in a parent's responses.
    parent_texts = " ".join(
        r.text
        for r in (
            get_results(api_client, h["parent_ada"], world, "bola"),
            get_sheet(api_client, h["parent_ada"], world, "dave"),
            get_results(api_client, h["parent_ada"], world),
        )
    )
    assert "Bola" not in parent_texts and "Dave" not in parent_texts


# --- End to end, creating accounts through the API ------------------------------------


def test_end_to_end_enter_correct_and_read_results(db, api_client):
    class_id = make_class(db, "Primary 3")
    maths = add(db, Subject, name="Mathematics")
    english = add(db, Subject, name="English Language")
    for subject in (maths, english):
        db.execute(insert(ClassSubject).values(class_id=class_id, subject_id=subject))
    term = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))["First Term"]
    _, admin = login(db, api_client, "admin", "head@camdock.test")

    def create_account(email, role):
        response = api_client.post(
            f"{API}/users",
            headers=admin,
            json={"email": email, "role": role, "password": PASSWORD},
        )
        assert response.status_code == 201, response.text
        token = login_token(api_client, email=email, password=PASSWORD)
        return response.json()["id"], bearer(token)

    teacher_id, teacher = create_account("t.eze@camdock.test", "teacher")
    parent_id, parent = create_account("o.obi@camdock.test", "parent")
    student = api_client.post(
        STUDENTS, headers=admin, json={"full_name": "Ada Obi", "class_id": class_id}
    ).json()["id"]
    assert (
        api_client.post(
            f"{API}/teachers",
            headers=admin,
            json={
                "user_id": teacher_id,
                "full_name": "Ngozi Eze",
                "class_ids": [class_id],
                "subject_ids": [maths, english],
            },
        ).status_code
        == 201
    )
    link(db, User(id=parent_id, role="parent"), student)

    def score(headers, subject, value):
        return api_client.put(
            RESULTS,
            headers=headers,
            json={
                "student_id": student,
                "subject_id": subject,
                "term_id": term,
                "score": value,
            },
        )

    assert score(teacher, maths, "72").status_code == 201
    assert score(teacher, english, "61.5").status_code == 201
    assert score(admin, maths, "74.5").status_code == 200  # the admin corrects a score

    params = {"term_id": term}
    results = api_client.get(
        f"{STUDENTS}/{student}/results", headers=parent, params=params
    )
    sheet = api_client.get(
        f"{STUDENTS}/{student}/result-sheet", headers=parent, params=params
    )

    assert results.status_code == sheet.status_code == 200
    assert [
        (i["subject_name"], i["score"], i["grade"]) for i in results.json()["items"]
    ] == [
        ("English Language", "61.50", "C4"),
        ("Mathematics", "74.50", "B2"),
    ]
    assert results.json()["average"] == sheet.json()["overall_average"] == "68.0"
    assert [s["class_average"] for s in sheet.json()["subjects"]] == ["61.5", "74.5"]
    assert (
        api_client.get(
            f"{API}/classes/{class_id}/averages", headers=parent, params=params
        ).status_code
        == 403
    )
    teacher_class_view = api_client.get(
        f"{API}/classes/{class_id}/averages", headers=teacher, params=params
    )
    assert teacher_class_view.json()["overall_average"] == "68.0"
