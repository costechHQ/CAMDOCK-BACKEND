from datetime import date

import pytest
from sqlalchemy import func, insert, select, update

from app.models import AuditLog, ClassSubject, Result, Student, Subject, User
from tests.test_access_scope import (
    add,
    link,
    make_class,
    make_student,
    make_teacher,
)
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

URL = "/api/v1/results"


@pytest.fixture
def school(db):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    maths = add(db, Subject, name="Mathematics")
    english = add(db, Subject, name="English Language")
    art = add(db, Subject, name="Art")  # exists but is taught in no class
    for class_id in (p1, p2):
        for subject_id in (maths, english):
            db.execute(
                insert(ClassSubject).values(class_id=class_id, subject_id=subject_id)
            )
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    return {
        "p1": p1,
        "p2": p2,
        "maths": maths,
        "english": english,
        "art": art,
        "term": terms["First Term"],
        "term2": terms["Second Term"],
        "ada": make_student(db, p1, "S-ADA"),
        "chi": make_student(db, p2, "S-CHI"),
    }


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


def teacher(db, api_client, classes, subjects, email="teach@camdock.test"):
    user_id, headers = login_headers(db, api_client, "teacher", email)
    make_teacher(db, User(id=user_id, role="teacher"), classes, subjects)
    return headers


def body(school, student="ada", subject="maths", term="term", score="75", **extra):
    return {
        "student_id": school[student],
        "subject_id": school[subject],
        "term_id": school[term],
        "score": score,
        **extra,
    }


def results(db):
    return db.execute(select(Result.__table__)).all()


def audits(db, action):
    return db.execute(select(AuditLog.__table__).where(AuditLog.action == action)).all()


def test_admin_creates_a_score(db, api_client, admin, school):
    response = api_client.put(URL, headers=admin, json=body(school, score="75.5"))

    assert response.status_code == 201
    out = response.json()
    assert out == {
        "id": out["id"],
        "student_id": school["ada"],
        "subject_id": school["maths"],
        "term_id": school["term"],
        "class_id": school["p1"],
        "score": "75.50",
        "grade": "A1",
    }
    (row,) = results(db)
    assert row.created_by is not None
    assert row.updated_by is None


def test_a_json_number_is_accepted(db, api_client, admin, school):
    response = api_client.put(URL, headers=admin, json=body(school, score=72.25))

    assert response.status_code == 201
    assert response.json()["score"] == "72.25"
    assert response.json()["grade"] == "B2"


def test_changing_a_score_updates_in_place(db, api_client, admin, school):
    first = api_client.put(URL, headers=admin, json=body(school, score="40")).json()

    response = api_client.put(URL, headers=admin, json=body(school, score="39.99"))

    assert response.status_code == 200
    assert response.json()["id"] == first["id"]
    assert response.json()["grade"] == "F9"
    (row,) = results(db)
    assert str(row.score) == "39.99"
    assert row.updated_by is not None


def test_repeating_the_same_score_is_a_no_op(db, api_client, admin, school):
    api_client.put(URL, headers=admin, json=body(school, score="60"))

    response = api_client.put(URL, headers=admin, json=body(school, score="60.00"))

    assert response.status_code == 200
    (row,) = results(db)
    assert row.updated_by is None
    assert len(audits(db, "result.create")) == 1
    assert audits(db, "result.update") == []


def test_audit_rows_record_actor_and_old_and_new_scores(db, api_client, admin, school):
    out = api_client.put(URL, headers=admin, json=body(school, score="50")).json()
    api_client.put(URL, headers=admin, json=body(school, score="55.5"))

    (created,) = audits(db, "result.create")
    assert (created.entity_type, created.entity_id) == ("result", out["id"])
    assert created.actor_user_id is not None
    assert created.changes == {
        "student_id": school["ada"],
        "subject_id": school["maths"],
        "term_id": school["term"],
        "class_id": school["p1"],
        "score": "50.00",
    }
    (updated,) = audits(db, "result.update")
    assert updated.changes == {"score": {"from": "50.00", "to": "55.50"}}


def test_one_row_per_student_subject_and_term(db, api_client, admin, school):
    for subject, term in [
        ("maths", "term"),
        ("english", "term"),
        ("maths", "term2"),
        ("maths", "term"),
    ]:
        api_client.put(
            URL, headers=admin, json=body(school, subject=subject, term=term)
        )

    assert len(results(db)) == 3


def test_class_id_stays_as_recorded_after_the_student_moves(
    db, api_client, admin, school
):
    api_client.put(URL, headers=admin, json=body(school, score="50"))
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(class_id=school["p2"])
    )

    api_client.put(URL, headers=admin, json=body(school, score="55"))

    (row,) = results(db)
    assert row.class_id == school["p1"]


@pytest.mark.parametrize(
    ("score", "grade"),
    [("0", "F9"), ("0.00", "F9"), ("39.99", "F9"), ("100", "A1"), ("100.00", "A1")],
)
def test_boundary_scores_are_accepted(db, api_client, admin, school, score, grade):
    response = api_client.put(URL, headers=admin, json=body(school, score=score))

    assert response.status_code == 201
    assert response.json()["grade"] == grade


@pytest.mark.parametrize(
    "score",
    [
        "-0.01",
        "100.01",
        "101",
        "70.123",
        "abc",
        "",
        "NaN",
        "Infinity",
        None,
        True,
        [75],
    ],
)
def test_invalid_scores_are_rejected_and_nothing_is_written(
    db, api_client, admin, school, score
):
    response = api_client.put(URL, headers=admin, json=body(school, score=score))

    assert response.status_code == 422
    assert results(db) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"student_id": 0},
        {"student_id": "1"},
        {"subject_id": -1},
        {"term_id": 1.5},
        {"unexpected": 1},
    ],
)
def test_invalid_ids_and_extra_fields_are_rejected(
    db, api_client, admin, school, payload
):
    response = api_client.put(URL, headers=admin, json=body(school) | payload)

    assert response.status_code == 422
    assert results(db) == []


def test_missing_fields_are_rejected(api_client, admin, school):
    assert api_client.put(URL, headers=admin, json={"score": "50"}).status_code == 422


def test_unknown_student_is_not_found(db, api_client, admin, school):
    response = api_client.put(
        URL, headers=admin, json=body(school) | {"student_id": 9999}
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Student not found."


def test_inactive_student_is_not_found(db, api_client, admin, school):
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(is_active=False)
    )

    assert api_client.put(URL, headers=admin, json=body(school)).status_code == 404


def test_unknown_term_or_subject_is_unprocessable(db, api_client, admin, school):
    bad_term = api_client.put(URL, headers=admin, json=body(school) | {"term_id": 9999})
    bad_subject = api_client.put(
        URL, headers=admin, json=body(school) | {"subject_id": 9999}
    )

    assert bad_term.status_code == bad_subject.status_code == 422
    assert results(db) == []


def test_inactive_subject_is_unprocessable(db, api_client, admin, school):
    db.execute(
        update(Subject).where(Subject.id == school["maths"]).values(is_active=False)
    )

    assert api_client.put(URL, headers=admin, json=body(school)).status_code == 422


def test_subject_must_be_taught_in_the_students_class(db, api_client, admin, school):
    response = api_client.put(URL, headers=admin, json=body(school, subject="art"))

    assert response.status_code == 422
    assert "not taught" in response.json()["error"]["message"]
    assert results(db) == []


def test_teacher_with_class_and_subject_can_write(db, api_client, school):
    headers = teacher(db, api_client, [school["p1"]], [school["maths"]])

    response = api_client.put(URL, headers=headers, json=body(school))

    assert response.status_code == 201


def test_teacher_with_the_class_but_not_the_subject_is_forbidden(
    db, api_client, school
):
    headers = teacher(db, api_client, [school["p1"]], [school["english"]])

    response = api_client.put(URL, headers=headers, json=body(school))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert results(db) == []


def test_teacher_outside_the_class_gets_not_found(db, api_client, school):
    headers = teacher(db, api_client, [school["p2"]], [school["maths"]])

    response = api_client.put(URL, headers=headers, json=body(school))

    assert response.status_code == 404
    assert results(db) == []


def test_teacher_without_a_teacher_record_gets_not_found(db, api_client, school):
    headers = login_headers(db, api_client, "teacher")[1]

    assert api_client.put(URL, headers=headers, json=body(school)).status_code == 404


def test_teacher_cannot_probe_subjects_in_other_classes(db, api_client, school):
    """Subject checks must not reveal anything before access is established."""
    headers = teacher(db, api_client, [school["p1"]], [school["english"]])

    response = api_client.put(URL, headers=headers, json=body(school, subject="art"))

    assert response.status_code == 403


def test_parent_is_forbidden_even_for_a_linked_child(db, api_client, school):
    user_id, headers = login_headers(db, api_client, "parent")
    link(db, User(id=user_id, role="parent"), school["ada"])

    response = api_client.put(URL, headers=headers, json=body(school))

    assert response.status_code == 403
    assert results(db) == []


def test_anonymous_request_is_unauthorized(db, api_client, school):
    assert api_client.put(URL, json=body(school)).status_code == 401


def test_other_methods_are_not_allowed(api_client, admin):
    assert api_client.post(URL, headers=admin, json={}).status_code == 405


def test_only_one_row_exists_when_the_insert_race_is_lost(
    db, api_client, admin, school, monkeypatch
):
    """The first lookup misses (as if another request had not committed yet), the insert
    then hits the unique key, and the retry must turn into a plain update."""
    from app.services import results as service

    api_client.put(URL, headers=admin, json=body(school, score="50"))
    real_find = service._find
    calls = []

    def find(db_, data):
        calls.append(1)
        return None if len(calls) == 1 else real_find(db_, data)

    monkeypatch.setattr(service, "_find", find)

    response = api_client.put(URL, headers=admin, json=body(school, score="65"))

    assert response.status_code == 200
    (row,) = results(db)
    assert str(row.score) == "65.00"
    assert db.execute(select(func.count()).select_from(Result)).scalar_one() == 1
