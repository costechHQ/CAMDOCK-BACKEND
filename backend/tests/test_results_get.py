from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

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

LIST_URL = "/api/v1/results"


def student_url(student_id) -> str:
    return f"/api/v1/students/{student_id}/results"


@pytest.fixture
def admin_login(db, api_client):
    return login_headers(db, api_client, "admin")


@pytest.fixture
def admin(admin_login):
    return admin_login[1]


@pytest.fixture
def admin_id(admin_login):
    return admin_login[0]


@pytest.fixture
def school(db):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    ada = make_student(db, p1, "S-ADA")
    bola = make_student(db, p1, "S-BOLA")
    chi = make_student(db, p2, "S-CHI")
    for student_id, name in [(ada, "Ada Obi"), (bola, "Bola Eze"), (chi, "Chi Nwa")]:
        db.execute(
            update(Student).where(Student.id == student_id).values(full_name=name)
        )
    return {
        "p1": p1,
        "p2": p2,
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
        "art": add(db, Subject, name="Art"),
        "term": terms["First Term"],
        "term2": terms["Second Term"],
        "ada": ada,
        "bola": bola,
        "chi": chi,
    }


def score(db, admin_id, school, student, subject, value, term="term", class_id=None):
    """Insert a result directly, as it would exist after a PUT."""
    class_id = class_id or (school["p2"] if student == "chi" else school["p1"])
    return add(
        db,
        Result,
        student_id=school[student],
        subject_id=school[subject],
        term_id=school[term],
        class_id=class_id,
        score=Decimal(value),
        created_by=admin_id,
    )


def teacher(db, api_client, classes, subjects, email="teach@camdock.test"):
    user_id, headers = login_headers(db, api_client, "teacher", email)
    make_teacher(db, User(id=user_id, role="teacher"), classes, subjects)
    return headers


def parent(db, api_client, children=()):
    user_id, headers = login_headers(db, api_client, "parent")
    for child in children:
        link(db, User(id=user_id, role="parent"), child)
    return headers


@pytest.fixture
def scores(db, admin_id, school):
    """Ada: maths 80, english 55.5; Bola: maths 39.99; Chi (P2): art 70."""
    return {
        "ada_maths": score(db, admin_id, school, "ada", "maths", "80"),
        "ada_english": score(db, admin_id, school, "ada", "english", "55.5"),
        "bola_maths": score(db, admin_id, school, "bola", "maths", "39.99"),
        "chi_art": score(db, admin_id, school, "chi", "art", "70"),
        "ada_maths_t2": score(db, admin_id, school, "ada", "maths", "10", term="term2"),
    }


def ids(response) -> list[int]:
    return [item["id"] for item in response.json()["items"]]


# --- GET /results ---------------------------------------------------------------


def test_admin_lists_a_terms_scores_in_name_order(
    db, api_client, admin, school, scores
):
    response = api_client.get(
        LIST_URL, headers=admin, params={"term_id": school["term"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert (body["limit"], body["offset"]) == (50, 0)
    # Student name, then subject name: Ada (English, Maths), Bola, Chi.
    assert ids(response) == [
        scores["ada_english"],
        scores["ada_maths"],
        scores["bola_maths"],
        scores["chi_art"],
    ]
    assert body["items"][0] == {
        "id": scores["ada_english"],
        "student_id": school["ada"],
        "student_number": "S-ADA",
        "student_name": "Ada Obi",
        "subject_id": school["english"],
        "subject_name": "English Language",
        "term_id": school["term"],
        "class_id": school["p1"],
        "score": "55.50",
        "grade": "C5",
    }
    assert body["items"][2]["grade"] == "F9"


def test_terms_do_not_mix(db, api_client, admin, school, scores):
    response = api_client.get(
        LIST_URL, headers=admin, params={"term_id": school["term2"]}
    )

    assert ids(response) == [scores["ada_maths_t2"]]


def test_pagination(db, api_client, admin, school, scores):
    params = {"term_id": school["term"], "limit": 2}

    first = api_client.get(LIST_URL, headers=admin, params=params).json()
    second = api_client.get(
        LIST_URL, headers=admin, params=params | {"offset": 2}
    ).json()

    assert first["total"] == second["total"] == 4
    assert [i["id"] for i in first["items"] + second["items"]] == [
        scores["ada_english"],
        scores["ada_maths"],
        scores["bola_maths"],
        scores["chi_art"],
    ]


def test_filters_by_class_and_subject(db, api_client, admin, school, scores):
    by_class = api_client.get(
        LIST_URL,
        headers=admin,
        params={"term_id": school["term"], "class_id": school["p2"]},
    )
    by_subject = api_client.get(
        LIST_URL,
        headers=admin,
        params={"term_id": school["term"], "subject_id": school["maths"]},
    )

    assert ids(by_class) == [scores["chi_art"]]
    assert ids(by_subject) == [scores["ada_maths"], scores["bola_maths"]]
    assert by_subject.json()["total"] == 2


def test_a_term_with_no_scores_gives_an_empty_page(db, api_client, admin, school):
    response = api_client.get(
        LIST_URL, headers=admin, params={"term_id": school["term"]}
    )

    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"term_id": 0},
        {"term_id": "x"},
        {"term_id": 9999},
        {"term_id": 1, "limit": 501},
    ]
    + [{"term_id": 1, "limit": 0}, {"term_id": 1, "offset": -1}],
)
def test_bad_query_parameters_are_unprocessable(db, api_client, admin, school, params):
    if params.get("term_id") == 1:
        params = params | {"term_id": school["term"]}

    assert api_client.get(LIST_URL, headers=admin, params=params).status_code == 422


def test_the_largest_page_size_is_accepted(db, api_client, admin, school):
    response = api_client.get(
        LIST_URL, headers=admin, params={"term_id": school["term"], "limit": 500}
    )

    assert response.status_code == 200


def test_inactive_students_are_hidden(db, api_client, admin, school, scores):
    db.execute(
        update(Student).where(Student.id == school["bola"]).values(is_active=False)
    )

    response = api_client.get(
        LIST_URL, headers=admin, params={"term_id": school["term"]}
    )

    assert response.json()["total"] == 3
    assert scores["bola_maths"] not in ids(response)


def test_teacher_sees_only_scores_for_their_class_and_subject(
    db, api_client, school, scores
):
    headers = teacher(db, api_client, [school["p1"]], [school["maths"]])

    response = api_client.get(
        LIST_URL, headers=headers, params={"term_id": school["term"]}
    )

    assert ids(response) == [scores["ada_maths"], scores["bola_maths"]]
    assert response.json()["total"] == 2


def test_teacher_with_the_class_but_not_the_subject_sees_nothing(
    db, api_client, school, scores
):
    headers = teacher(db, api_client, [school["p1"]], [school["art"]])

    response = api_client.get(
        LIST_URL, headers=headers, params={"term_id": school["term"]}
    )

    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_teacher_with_the_subject_but_not_the_class_sees_nothing(
    db, api_client, school, scores
):
    headers = teacher(db, api_client, [school["p2"]], [school["maths"]])

    response = api_client.get(
        LIST_URL, headers=headers, params={"term_id": school["term"]}
    )

    assert response.json()["items"] == []


def test_teacher_filtering_on_someone_elses_class_gets_an_empty_page(
    db, api_client, school, scores
):
    headers = teacher(db, api_client, [school["p1"]], [school["maths"]])

    response = api_client.get(
        LIST_URL,
        headers=headers,
        params={"term_id": school["term"], "class_id": school["p2"]},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_teacher_without_a_teacher_record_sees_nothing(db, api_client, school, scores):
    headers = login_headers(db, api_client, "teacher")[1]

    response = api_client.get(
        LIST_URL, headers=headers, params={"term_id": school["term"]}
    )

    assert response.json()["total"] == 0


def test_a_score_keeps_showing_for_the_class_it_was_recorded_in(
    db, api_client, admin, school, scores
):
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(class_id=school["p2"])
    )
    old = teacher(db, api_client, [school["p1"]], [school["maths"]], "old@camdock.test")
    new = teacher(db, api_client, [school["p2"]], [school["maths"]], "new@camdock.test")
    params = {"term_id": school["term"]}

    assert scores["ada_maths"] in ids(
        api_client.get(LIST_URL, headers=old, params=params)
    )
    assert scores["ada_maths"] not in ids(
        api_client.get(LIST_URL, headers=new, params=params)
    )


def test_parent_cannot_use_the_class_listing(db, api_client, school, scores):
    headers = parent(db, api_client, [school["ada"]])

    response = api_client.get(
        LIST_URL, headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 403


def test_listing_requires_authentication(api_client, school):
    assert (
        api_client.get(LIST_URL, params={"term_id": school["term"]}).status_code == 401
    )


# --- GET /students/{id}/results ---------------------------------------------------


def test_admin_gets_a_students_scores_for_a_term(db, api_client, admin, school, scores):
    response = api_client.get(
        student_url(school["ada"]), headers=admin, params={"term_id": school["term"]}
    )

    assert response.status_code == 200
    assert response.json() == {
        "student": {
            "id": school["ada"],
            "student_number": "S-ADA",
            "full_name": "Ada Obi",
            "class_id": school["p1"],
            "class_name": "Primary 1",
        },
        "term": {
            "id": school["term"],
            "name": "First Term",
            "academic_year": "2025/2026",
        },
        "average": "67.8",
        "subjects_recorded": 2,
        "items": [
            {
                "result_id": scores["ada_english"],
                "subject_id": school["english"],
                "subject_name": "English Language",
                "score": "55.50",
                "grade": "C5",
            },
            {
                "result_id": scores["ada_maths"],
                "subject_id": school["maths"],
                "subject_name": "Mathematics",
                "score": "80.00",
                "grade": "A1",
            },
        ],
    }


def test_missing_scores_are_absent_not_zero(db, api_client, admin, school, scores):
    response = api_client.get(
        student_url(school["bola"]), headers=admin, params={"term_id": school["term"]}
    )

    assert [i["subject_name"] for i in response.json()["items"]] == ["Mathematics"]


def test_a_student_with_no_scores_gets_an_empty_list(db, api_client, admin, school):
    response = api_client.get(
        student_url(school["ada"]), headers=admin, params={"term_id": school["term"]}
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_the_student_endpoint_only_returns_the_requested_term(
    db, api_client, admin, school, scores
):
    response = api_client.get(
        student_url(school["ada"]), headers=admin, params={"term_id": school["term2"]}
    )

    assert [i["result_id"] for i in response.json()["items"]] == [
        scores["ada_maths_t2"]
    ]
    assert response.json()["term"]["name"] == "Second Term"


def test_parent_sees_all_subjects_for_a_linked_child(db, api_client, school, scores):
    headers = parent(db, api_client, [school["ada"]])

    response = api_client.get(
        student_url(school["ada"]), headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 2


@pytest.mark.parametrize("target", ["bola", "chi"])
def test_parent_cannot_read_another_childs_scores(
    db, api_client, school, scores, target
):
    headers = parent(db, api_client, [school["ada"]])

    response = api_client.get(
        student_url(school[target]), headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Student not found."


def test_parent_with_no_children_is_refused_by_id_guessing(
    db, api_client, school, scores
):
    headers = parent(db, api_client)

    response = api_client.get(
        student_url(school["ada"]), headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 404


def test_teacher_sees_only_their_own_subjects_for_a_student(
    db, api_client, school, scores
):
    headers = teacher(db, api_client, [school["p1"]], [school["english"]])

    response = api_client.get(
        student_url(school["ada"]), headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 200
    assert [i["subject_name"] for i in response.json()["items"]] == ["English Language"]


def test_teacher_with_the_class_but_no_matching_subject_sees_the_student_without_scores(
    db, api_client, school, scores
):
    headers = teacher(db, api_client, [school["p1"]], [school["art"]])

    response = api_client.get(
        student_url(school["ada"]), headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_teacher_outside_the_class_gets_not_found(db, api_client, school, scores):
    headers = teacher(db, api_client, [school["p2"]], [school["maths"]])

    response = api_client.get(
        student_url(school["ada"]), headers=headers, params={"term_id": school["term"]}
    )

    assert response.status_code == 404


def test_unknown_inactive_and_invalid_students(db, api_client, admin, school, scores):
    db.execute(
        update(Student).where(Student.id == school["bola"]).values(is_active=False)
    )
    params = {"term_id": school["term"]}

    assert (
        api_client.get(student_url(9999), headers=admin, params=params).status_code
        == 404
    )
    assert (
        api_client.get(
            student_url(school["bola"]), headers=admin, params=params
        ).status_code
        == 404
    )
    assert (
        api_client.get(student_url("abc"), headers=admin, params=params).status_code
        == 422
    )


@pytest.mark.parametrize("params", [{}, {"term_id": 0}, {"term_id": 9999}])
def test_student_results_need_a_valid_term(db, api_client, admin, school, params):
    response = api_client.get(student_url(school["ada"]), headers=admin, params=params)

    assert response.status_code == 422


def test_student_results_require_authentication(api_client, school):
    response = api_client.get(
        student_url(school["ada"]), params={"term_id": school["term"]}
    )

    assert response.status_code == 401
