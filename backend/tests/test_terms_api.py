from datetime import date

import pytest

from app.models import AcademicYear, Term
from tests.test_access_scope import add
from tests.test_teachers_api import login_headers

URL = "/api/v1/terms"


def make_year(db, name, start, is_current=False) -> int:
    return add(
        db,
        AcademicYear,
        name=name,
        start_date=start,
        end_date=date(start.year + 1, 7, 31),
        is_current=is_current,
    )


def make_terms(db, year_id, names=("First Term", "Second Term", "Third Term")):
    return {name: add(db, Term, academic_year_id=year_id, name=name) for name in names}


@pytest.fixture
def two_years(db):
    old = make_year(db, "2024/2025", date(2024, 9, 1))
    new = make_year(db, "2025/2026", date(2025, 9, 1), is_current=True)
    # Inserted out of order on purpose: ordering must not depend on insertion order.
    return {
        "old": old,
        "new": new,
        "old_terms": make_terms(db, old, ("Third Term", "First Term", "Second Term")),
        "new_terms": make_terms(db, new, ("Second Term", "Third Term", "First Term")),
    }


@pytest.mark.parametrize("role", ["admin", "teacher", "parent"])
def test_every_role_can_list_terms(db, api_client, two_years, role):
    headers = login_headers(db, api_client, role)[1]

    response = api_client.get(URL, headers=headers)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 6


def test_terms_are_newest_year_first_then_term_order(db, api_client, two_years):
    headers = login_headers(db, api_client, "admin")[1]

    items = api_client.get(URL, headers=headers).json()["items"]

    assert [(i["academic_year"], i["name"]) for i in items] == [
        ("2025/2026", "First Term"),
        ("2025/2026", "Second Term"),
        ("2025/2026", "Third Term"),
        ("2024/2025", "First Term"),
        ("2024/2025", "Second Term"),
        ("2024/2025", "Third Term"),
    ]


def test_term_fields(db, api_client, two_years):
    headers = login_headers(db, api_client, "admin")[1]

    first = api_client.get(URL, headers=headers).json()["items"][0]

    assert first == {
        "id": two_years["new_terms"]["First Term"],
        "name": "First Term",
        "academic_year_id": two_years["new"],
        "academic_year": "2025/2026",
        "is_current_year": True,
        "start_date": None,
        "end_date": None,
    }


def test_filter_by_academic_year(db, api_client, two_years):
    headers = login_headers(db, api_client, "teacher")[1]

    items = api_client.get(
        URL, headers=headers, params={"academic_year_id": two_years["old"]}
    ).json()["items"]

    assert {i["academic_year"] for i in items} == {"2024/2025"}
    assert len(items) == 3


def test_unknown_year_gives_an_empty_list(db, api_client, two_years):
    headers = login_headers(db, api_client, "admin")[1]

    response = api_client.get(URL, headers=headers, params={"academic_year_id": 999999})

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_empty_database_gives_an_empty_list(db, api_client):
    headers = login_headers(db, api_client, "admin")[1]

    assert api_client.get(URL, headers=headers).json() == {"items": []}


def test_anonymous_request_is_unauthorized(api_client):
    assert api_client.get(URL).status_code == 401
