# ruff: noqa: F811
"""GET /fees/{id}: the history of payments, voids and refunds, and who did what."""

import pytest

from tests.test_averages import admin, admin_login, school  # noqa: F401
from tests.test_dashboard_parent import parent_for
from tests.test_fee_receipts import bill, pay
from tests.test_payment_refund import refund
from tests.test_payment_void import void
from tests.test_teachers_api import login_headers

FEES = "/api/v1/fees"
ADMIN_EMAIL = "admin@camdock.test"
VOID_REASON = "entered the wrong amount"
REFUND_REASON = "parent withdrew the child"


@pytest.fixture
def story(api_client, admin, school):
    """Bill of 1000: pay 400, pay 600, void the 600, refund 100 of the 400."""
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="400")
    first = fee["payments"][0]
    second = pay(api_client, admin, fee["id"], "600")
    assert void(api_client, admin, fee["id"], second["id"], VOID_REASON).status_code
    assert (
        refund(api_client, admin, fee["id"], first["id"], "100", REFUND_REASON)
    ).status_code == 201
    return fee["id"], first, second


def detail(api_client, headers, fee_id):
    response = api_client.get(f"{FEES}/{fee_id}", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_the_history_lists_every_event_in_order(api_client, admin, story):
    fee_id, first, second = story

    history = detail(api_client, admin, fee_id)["history"]

    assert [(h["kind"], h["payment_id"]) for h in history] == [
        ("payment", first["id"]),
        ("payment", second["id"]),
        ("void", second["id"]),
        ("refund", first["id"]),
    ]
    assert [h["amount"] for h in history] == ["400.00", "600.00", "600.00", "100.00"]
    assert history[0]["receipt_number"] == first["receipt_number"]
    assert history[2]["receipt_number"] == second["receipt_number"]
    assert history[3]["receipt_number"].startswith("REF-")
    assert history[3]["refund_id"] is not None and history[0]["refund_id"] is None
    times = [h["at"] for h in history]
    assert times == sorted(times)


def test_administrators_see_who_and_why(api_client, admin, story):
    fee_id, _, _ = story

    body = detail(api_client, admin, fee_id)

    history = body["history"]
    assert [h["by"] for h in history] == [ADMIN_EMAIL] * 4
    assert [h["reason"] for h in history] == [
        None,
        None,
        VOID_REASON,
        REFUND_REASON,
    ]
    assert body["payments"][1]["voided_by"] == ADMIN_EMAIL
    assert body["payments"][0]["voided_by"] is None
    assert body["refunds"][0]["refunded_by"] == ADMIN_EMAIL


def test_families_see_what_happened_but_not_who_or_why(
    db, api_client, admin, school, story
):
    fee_id, _, _ = story
    _, parent = parent_for(db, api_client, [school["ada"]])

    body = detail(api_client, parent, fee_id)

    assert [h["kind"] for h in body["history"]] == [
        "payment",
        "payment",
        "void",
        "refund",
    ]
    assert all(h["by"] is None and h["reason"] is None for h in body["history"])
    assert all(p["voided_by"] is None for p in body["payments"])
    assert all(r["refunded_by"] is None for r in body["refunds"])
    assert body["payments"][1]["void_reason"] is None
    assert body["amount_paid"] == "300.00"
    assert ADMIN_EMAIL not in str(body)


def test_an_unpaid_bill_has_an_empty_history(api_client, admin, school):
    fee = bill(api_client, admin, school, "ada", "1000")

    assert detail(api_client, admin, fee["id"])["history"] == []


def test_a_second_administrator_is_named_for_their_own_actions(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="500")
    _, other = login_headers(db, api_client, "admin", "bursar@camdock.test")
    assert void(api_client, other, fee["id"], fee["payments"][0]["id"]).status_code

    history = detail(api_client, admin, fee["id"])["history"]

    assert [(h["kind"], h["by"]) for h in history] == [
        ("payment", ADMIN_EMAIL),
        ("void", "bursar@camdock.test"),
    ]


def test_printed_receipts_never_carry_an_administrators_email(api_client, admin, story):
    fee_id, first, second = story
    refund_id = detail(api_client, admin, fee_id)["refunds"][0]["id"]

    receipts = [
        api_client.get(f"{FEES}/{fee_id}/receipt", headers=admin),
        api_client.get(
            f"{FEES}/{fee_id}/payments/{first['id']}/receipt", headers=admin
        ),
        api_client.get(
            f"{FEES}/{fee_id}/payments/{second['id']}/receipt", headers=admin
        ),
        api_client.get(f"{FEES}/{fee_id}/refunds/{refund_id}/receipt", headers=admin),
    ]

    for receipt in receipts:
        assert receipt.status_code == 200
        assert ADMIN_EMAIL not in receipt.text
