from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.core.config import get_settings
from app.models import Result, Student, Subject, User
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


def sheet_url(student_id) -> str:
    return f"/api/v1/students/{student_id}/result-sheet"


@pytest.fixture
def admin_login(db, api_client):
    return login_headers(db, api_client, "admin")


@pytest.fixture
def admin(admin_login):
    return admin_login[1]


@pytest.fixture
def school(db, admin_login):
    """Primary 1, first term (Eve is inactive and the second-term score is another term;
    neither may reach any figure):

    - Mathematics: Ada 80, Bola 70, Dave 75 (class mean 75.0)
    - English:     Ada 55.5, Bola 60        (class mean 57.75 -> 57.8)
    - Art:         Dave 50                  (class mean 50.0)

    Ada's average is (80 + 55.5) / 2 = 67.75 -> 67.8.
    """
    admin_id = admin_login[0]
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    ids = {
        "p1": p1,
        "p2": p2,
        "term": terms["First Term"],
        "term2": terms["Second Term"],
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
        "art": add(db, Subject, name="Art"),
    }
    for name in ("ada", "bola", "dave", "eve", "frank"):
        ids[name] = make_student(db, p1, f"S-{name.upper()}")
        db.execute(
            update(Student)
            .where(Student.id == ids[name])
            .values(full_name=name.capitalize())
        )
    db.execute(update(Student).where(Student.id == ids["eve"]).values(is_active=False))

    def put(student, subject, value, term="term", class_key="p1"):
        add(
            db,
            Result,
            student_id=ids[student],
            subject_id=ids[subject],
            term_id=ids[term],
            class_id=ids[class_key],
            score=D(value),
            created_by=admin_id,
        )

    ids["put"] = put
    put("ada", "maths", "80")
    put("bola", "maths", "70")
    put("dave", "maths", "75")
    put("ada", "english", "55.5")
    put("bola", "english", "60")
    put("dave", "art", "50")
    put("eve", "maths", "0")
    put("ada", "maths", "10", term="term2")
    return ids


def parent(db, api_client, children=()):
    user_id, headers = login_headers(db, api_client, "parent")
    for child in children:
        link(db, User(id=user_id, role="parent"), child)
    return headers


def get_sheet(api_client, headers, school, student="ada", term="term"):
    return api_client.get(
        sheet_url(school[student]), headers=headers, params={"term_id": school[term]}
    )


def test_admin_gets_the_full_sheet(api_client, admin, school):
    response = get_sheet(api_client, admin, school)

    assert response.status_code == 200
    assert response.json() == {
        "school": {
            "name": "Camdock International School",
            "location": "Enugu State, Nigeria",
        },
        "student": {"id": school["ada"], "student_number": "S-ADA", "full_name": "Ada"},
        "class": {"id": school["p1"], "name": "Primary 1"},
        "term": {
            "id": school["term"],
            "name": "First Term",
            "academic_year": "2025/2026",
        },
        "subjects": [
            {
                "subject_id": school["english"],
                "subject_name": "English Language",
                "score": "55.50",
                "grade": "C5",
                "class_average": "57.8",
            },
            {
                "subject_id": school["maths"],
                "subject_name": "Mathematics",
                "score": "80.00",
                "grade": "A1",
                "class_average": "75.0",
            },
        ],
        "subjects_recorded": 2,
        "overall_average": "67.8",
    }


def test_grades_follow_the_stored_scores(api_client, admin, school):
    subjects = get_sheet(api_client, admin, school, "bola").json()["subjects"]

    assert [(s["subject_name"], s["score"], s["grade"]) for s in subjects] == [
        ("English Language", "60.00", "C4"),
        ("Mathematics", "70.00", "B2"),
    ]


def test_class_averages_match_the_class_endpoint(api_client, admin, school):
    by_subject = {
        s["subject_id"]: s["average"]
        for s in api_client.get(
            f"/api/v1/classes/{school['p1']}/averages",
            headers=admin,
            params={"term_id": school["term"]},
        ).json()["subjects"]
    }

    for student in ("ada", "bola", "dave"):
        for line in get_sheet(api_client, admin, school, student).json()["subjects"]:
            assert line["class_average"] == by_subject[line["subject_id"]]


def test_missing_subjects_are_absent_not_zero(api_client, admin, school):
    body = get_sheet(api_client, admin, school, "dave").json()

    assert [s["subject_name"] for s in body["subjects"]] == ["Art", "Mathematics"]
    assert (body["subjects_recorded"], body["overall_average"]) == (2, "62.5")


def test_other_terms_and_inactive_students_do_not_reach_any_figure(
    api_client, admin, school
):
    first = get_sheet(api_client, admin, school).json()
    second = get_sheet(api_client, admin, school, term="term2").json()

    assert (
        first["subjects"][1]["class_average"] == "75.0"
    )  # not dragged down by Eve's 0
    assert [(s["score"], s["class_average"]) for s in second["subjects"]] == [
        ("10.00", "10.0")
    ]


def test_a_student_with_no_scores_gets_an_empty_sheet(db, api_client, admin, school):
    nobody = make_student(db, school["p2"], "S-NEW")

    body = api_client.get(
        sheet_url(nobody), headers=admin, params={"term_id": school["term"]}
    ).json()

    assert body["subjects"] == []
    assert body["subjects_recorded"] == 0
    assert body["overall_average"] is None
    assert body["class"] == {"id": school["p2"], "name": "Primary 2"}


def test_a_promoted_students_old_sheet_keeps_the_recorded_class(
    db, api_client, admin, school
):
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(class_id=school["p2"])
    )

    body = get_sheet(api_client, admin, school).json()

    assert body["class"] == {"id": school["p1"], "name": "Primary 1"}
    assert [s["class_average"] for s in body["subjects"]] == ["57.8", "75.0"]


def test_a_mid_term_move_uses_the_class_with_the_most_scores(api_client, admin, school):
    put = school["put"]
    put("frank", "maths", "60")
    put("frank", "english", "62")
    put("frank", "art", "90", class_key="p2")  # recorded after the move

    body = get_sheet(api_client, admin, school, "frank").json()

    assert body["class"]["id"] == school["p1"]
    art = next(s for s in body["subjects"] if s["subject_name"] == "Art")
    # Each line is measured against the class it was recorded in: Dave's art score is
    # in Primary 1, Frank's is the only one in Primary 2.
    assert art["class_average"] == "90.0"
    maths = next(s for s in body["subjects"] if s["subject_name"] == "Mathematics")
    assert maths["class_average"] == "71.3"  # (80 + 70 + 75 + 60) / 4 = 71.25


def test_a_tie_between_classes_goes_to_the_latest_entry(api_client, admin, school):
    put = school["put"]
    put("frank", "maths", "60")
    put("frank", "art", "90", class_key="p2")

    body = get_sheet(api_client, admin, school, "frank").json()

    assert body["class"]["id"] == school["p2"]


def test_linked_parent_gets_the_whole_sheet(db, api_client, school):
    headers = parent(db, api_client, [school["ada"]])

    response = get_sheet(api_client, headers, school)

    assert response.status_code == 200
    assert response.json()["overall_average"] == "67.8"
    assert len(response.json()["subjects"]) == 2


@pytest.mark.parametrize("target", ["bola", "frank"])
def test_parent_cannot_fetch_another_childs_sheet(db, api_client, school, target):
    headers = parent(db, api_client, [school["ada"]])

    response = get_sheet(api_client, headers, school, target)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Student not found."


def test_parent_with_no_children_cannot_guess_ids(db, api_client, school):
    headers = parent(db, api_client)

    assert get_sheet(api_client, headers, school).status_code == 404


def test_teachers_are_forbidden(db, api_client, school):
    user_id, headers = login_headers(db, api_client, "teacher")
    make_teacher(
        db,
        User(id=user_id, role="teacher"),
        [school["p1"]],
        [school["maths"], school["english"], school["art"]],
    )

    response = get_sheet(api_client, headers, school)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_sheet_requires_authentication(api_client, school):
    response = api_client.get(
        sheet_url(school["ada"]), params={"term_id": school["term"]}
    )

    assert response.status_code == 401


def test_unknown_and_inactive_students_are_not_found(api_client, admin, school):
    params = {"term_id": school["term"]}

    assert (
        api_client.get(sheet_url(9999), headers=admin, params=params).status_code == 404
    )
    assert get_sheet(api_client, admin, school, "eve").status_code == 404
    assert (
        api_client.get(sheet_url("abc"), headers=admin, params=params).status_code
        == 422
    )


@pytest.mark.parametrize(
    "params", [{}, {"term_id": 0}, {"term_id": "x"}, {"term_id": 9999}]
)
def test_sheet_needs_a_valid_term(api_client, admin, school, params):
    response = api_client.get(sheet_url(school["ada"]), headers=admin, params=params)

    assert response.status_code == 422


def test_school_name_and_location_come_from_settings(
    monkeypatch, api_client, admin, school
):
    monkeypatch.setenv("SCHOOL_NAME", "Camdock Test Academy")
    monkeypatch.setenv("SCHOOL_LOCATION", "Enugu, Nigeria")
    get_settings.cache_clear()

    body = get_sheet(api_client, admin, school).json()

    assert body["school"] == {
        "name": "Camdock Test Academy",
        "location": "Enugu, Nigeria",
    }
