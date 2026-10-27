from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.models import ClassSubject, Result, SchoolClass, Student, Subject, User
from tests.test_access_scope import (
    add,
    link,
    make_class,
    make_student,
    make_teacher,
)
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

D = Decimal


def averages_url(class_id) -> str:
    return f"/api/v1/classes/{class_id}/averages"


def student_url(student_id) -> str:
    return f"/api/v1/students/{student_id}/results"


@pytest.fixture
def admin_login(db, api_client):
    return login_headers(db, api_client, "admin")


@pytest.fixture
def admin(admin_login):
    return admin_login[1]


@pytest.fixture
def school(db, admin_login):
    """Primary 1 scores in the first term (inactive Eve and a second-term score are
    noise that must not count):

    - Mathematics: Ada 80, Bola 70, Dave 75 (mean 75.0, 3 students)
    - English:     Ada 55.5, Bola 60        (mean 57.75, 2 students)
    - Art:         Dave 50                  (mean 50.0, 1 student)

    Overall = mean(75, 57.75, 50) = 60.9166... -> 60.9. A mean weighted by the number of
    scores would be 65.08, so the two rules cannot be confused.

    Primary 2 (Chi): English 70.04 and Art 70.05. The overall is 70.045 -> 70.0, but
    averaging the *rounded* subject figures (70.0 and 70.1) would give 70.1.
    """
    admin_id = admin_login[0]
    p1, p2, p3 = (make_class(db, f"Primary {n}") for n in (1, 2, 3))
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    ids = {
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "term": terms["First Term"],
        "term2": terms["Second Term"],
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
        "art": add(db, Subject, name="Art"),
    }
    for number, class_id in [("ADA", p1), ("BOLA", p1), ("DAVE", p1), ("EVE", p1)]:
        ids[number.lower()] = make_student(db, class_id, f"S-{number}")
    ids["chi"] = make_student(db, p2, "S-CHI")
    db.execute(update(Student).where(Student.id == ids["eve"]).values(is_active=False))

    def put(student, subject, value, term="term", class_key=None):
        add(
            db,
            Result,
            student_id=ids[student],
            subject_id=ids[subject],
            term_id=ids[term],
            class_id=ids[class_key or ("p2" if student == "chi" else "p1")],
            score=D(value),
            created_by=admin_id,
        )

    put("ada", "maths", "80")
    put("bola", "maths", "70")
    put("dave", "maths", "75")
    put("ada", "english", "55.5")
    put("bola", "english", "60")
    put("dave", "art", "50")
    put("eve", "maths", "0")  # inactive student
    put("ada", "maths", "10", term="term2")  # another term
    put("chi", "english", "70.04")
    put("chi", "art", "70.05")
    return ids


def teacher(db, api_client, classes, subjects, email="teach@camdock.test"):
    user_id, headers = login_headers(db, api_client, "teacher", email)
    make_teacher(db, User(id=user_id, role="teacher"), classes, subjects)
    return headers


def parent(db, api_client, children=()):
    user_id, headers = login_headers(db, api_client, "parent")
    for child in children:
        link(db, User(id=user_id, role="parent"), child)
    return headers


def get_averages(api_client, headers, school, class_key="p1", term="term"):
    return api_client.get(
        averages_url(school[class_key]),
        headers=headers,
        params={"term_id": school[term]},
    )


# --- GET /classes/{id}/averages ---------------------------------------------------


def test_class_averages_follow_the_srs_formulas(api_client, admin, school):
    response = get_averages(api_client, admin, school)

    assert response.status_code == 200
    assert response.json() == {
        "class": {"id": school["p1"], "name": "Primary 1"},
        "term": {
            "id": school["term"],
            "name": "First Term",
            "academic_year": "2025/2026",
        },
        "students_with_results": 3,
        "subjects": [
            {
                "subject_id": school["art"],
                "subject_name": "Art",
                "average": "50.0",
                "students_scored": 1,
            },
            {
                "subject_id": school["english"],
                "subject_name": "English Language",
                "average": "57.8",  # 57.75 rounds half up
                "students_scored": 2,
            },
            {
                "subject_id": school["maths"],
                "subject_name": "Mathematics",
                "average": "75.0",
                "students_scored": 3,
            },
        ],
        "overall_average": "60.9",
    }


def test_overall_is_the_mean_of_unrounded_subject_means(api_client, admin, school):
    body = get_averages(api_client, admin, school, "p2").json()

    assert [s["average"] for s in body["subjects"]] == ["70.1", "70.0"]
    assert (
        body["overall_average"] == "70.0"
    )  # not 70.1, which rounding first would give


def test_missing_scores_are_not_counted_as_zero(api_client, admin, school):
    english = next(
        s
        for s in get_averages(api_client, admin, school).json()["subjects"]
        if s["subject_name"] == "English Language"
    )

    # Three students are in the class but only two have an English score.
    assert english["average"] == "57.8"
    assert english["students_scored"] == 2


def test_other_terms_and_inactive_students_do_not_count(api_client, admin, school):
    first = get_averages(api_client, admin, school).json()
    second = get_averages(api_client, admin, school, term="term2").json()

    maths = next(s for s in first["subjects"] if s["subject_name"] == "Mathematics")
    assert maths["average"] == "75.0"  # Eve's 0 and Ada's second-term 10 are left out
    assert second["overall_average"] == "10.0"
    assert second["students_with_results"] == 1


def test_a_score_counts_for_the_class_it_was_recorded_in(db, api_client, admin, school):
    db.execute(
        update(Student)
        .where(Student.id == school["dave"])
        .values(class_id=school["p2"])
    )

    p1 = get_averages(api_client, admin, school, "p1").json()
    p2 = get_averages(api_client, admin, school, "p2").json()

    assert p1["students_with_results"] == 3
    assert p1["overall_average"] == "60.9"
    assert p2["students_with_results"] == 1


def test_a_class_with_no_scores_has_null_averages(api_client, admin, school):
    body = get_averages(api_client, admin, school, "p3").json()

    assert body["subjects"] == []
    assert body["students_with_results"] == 0
    assert body["overall_average"] is None
    assert body["class"]["name"] == "Primary 3"


def test_teacher_sees_every_subject_of_an_assigned_class(db, api_client, admin, school):
    headers = teacher(db, api_client, [school["p1"]], [school["english"]])

    teacher_view = get_averages(api_client, headers, school)

    assert teacher_view.status_code == 200
    assert teacher_view.json() == get_averages(api_client, admin, school).json()


def test_teacher_with_no_subjects_still_sees_class_averages(db, api_client, school):
    headers = teacher(db, api_client, [school["p1"]], [])

    assert get_averages(api_client, headers, school).status_code == 200


def test_teacher_cannot_read_an_unassigned_class(db, api_client, school):
    headers = teacher(db, api_client, [school["p2"]], [school["maths"]])

    response = get_averages(api_client, headers, school, "p1")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Class not found."


def test_teacher_without_a_teacher_record_gets_not_found(db, api_client, school):
    headers = login_headers(db, api_client, "teacher")[1]

    assert get_averages(api_client, headers, school).status_code == 404


def test_parent_cannot_use_the_class_endpoint(db, api_client, school):
    headers = parent(db, api_client, [school["ada"]])

    response = get_averages(api_client, headers, school)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_unknown_and_inactive_classes_are_not_found(db, api_client, admin, school):
    db.execute(
        update(SchoolClass)
        .where(SchoolClass.id == school["p3"])
        .values(is_active=False)
    )
    params = {"term_id": school["term"]}

    assert (
        api_client.get(averages_url(9999), headers=admin, params=params).status_code
        == 404
    )
    assert (
        api_client.get(
            averages_url(school["p3"]), headers=admin, params=params
        ).status_code
        == 404
    )
    assert (
        api_client.get(averages_url("abc"), headers=admin, params=params).status_code
        == 422
    )


@pytest.mark.parametrize(
    "params", [{}, {"term_id": 0}, {"term_id": "x"}, {"term_id": 9999}]
)
def test_class_averages_need_a_valid_term(api_client, admin, school, params):
    response = api_client.get(averages_url(school["p1"]), headers=admin, params=params)

    assert response.status_code == 422


def test_class_averages_require_authentication(api_client, school):
    response = api_client.get(
        averages_url(school["p1"]), params={"term_id": school["term"]}
    )

    assert response.status_code == 401


# --- the average on GET /students/{id}/results -------------------------------------


def get_student(api_client, headers, school, student="ada"):
    return api_client.get(
        student_url(school[student]),
        headers=headers,
        params={"term_id": school["term"]},
    )


def test_student_average_rounds_half_up(api_client, admin, school):
    body = get_student(api_client, admin, school).json()

    assert body["average"] == "67.8"  # (80 + 55.5) / 2 = 67.75
    assert body["subjects_recorded"] == 2


def test_student_average_covers_only_that_student_and_term(api_client, admin, school):
    assert get_student(api_client, admin, school, "bola").json()["average"] == "65.0"
    assert get_student(api_client, admin, school, "dave").json()["average"] == "62.5"
    second = api_client.get(
        student_url(school["ada"]), headers=admin, params={"term_id": school["term2"]}
    ).json()
    assert (second["average"], second["subjects_recorded"]) == ("10.0", 1)


def test_a_student_without_scores_has_a_null_average(db, api_client, admin, school):
    nobody = make_student(db, school["p1"], "S-NEW")

    body = api_client.get(
        student_url(nobody), headers=admin, params={"term_id": school["term"]}
    ).json()

    assert (body["average"], body["subjects_recorded"], body["items"]) == (None, 0, [])


def test_parent_sees_the_average_for_a_linked_child(db, api_client, school):
    headers = parent(db, api_client, [school["ada"]])

    body = get_student(api_client, headers, school).json()

    assert body["average"] == "67.8"
    assert len(body["items"]) == 2


def test_teacher_sees_a_full_average_but_only_their_own_subjects(
    db, api_client, school
):
    headers = teacher(db, api_client, [school["p1"]], [school["english"]])

    body = get_student(api_client, headers, school).json()

    assert [i["subject_name"] for i in body["items"]] == ["English Language"]
    assert body["average"] == "67.8"
    assert body["subjects_recorded"] == 2


def test_the_average_is_still_hidden_behind_student_scope(db, api_client, school):
    headers = parent(db, api_client, [school["ada"]])

    assert get_student(api_client, headers, school, "bola").status_code == 404


def test_unused_class_subject_rows_do_not_affect_averages(
    db, api_client, admin, school
):
    db.execute(
        ClassSubject.__table__.insert().values(
            class_id=school["p1"], subject_id=school["art"]
        )
    )

    assert get_averages(api_client, admin, school).json()["overall_average"] == "60.9"
