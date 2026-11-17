# ruff: noqa: F811
"""Every screen must show the same paid, balance and status for a bill.

How much has been paid is worked out in `app/services/paid.py` only. These tests put
bills in several states and check the bill, the list and its totals, the status filter,
the receipts, the overpayment check and both dashboards against the same expected
figures, so a rule that drifts on one surface is caught.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.fees import FEE_TYPES
from tests.test_averages import admin, admin_login, school  # noqa: F401
from tests.test_dashboard_parent import parent_for
from tests.test_fee_receipts import bill, pay
from tests.test_payment_refund import refund
from tests.test_payment_void import void

FEES = "/api/v1/fees"
DASH = "/api/v1/dashboard/overview"
APP = Path(__file__).resolve().parent.parent / "app"


def m(value) -> str:
    return f"{Decimal(str(value)):.2f}"


def status_of(billed, paid) -> str:
    if Decimal(str(paid)) == Decimal(str(billed)):
        return "paid"
    return "partial" if Decimal(str(paid)) > 0 else "due"


def make_bills(api_client, admin, school):
    """Ada's five first-term bills, each in a different state. Returns
    {fee_id: (billed, paid)} with the paid figure written out by hand."""
    expected = {}

    due = bill(api_client, admin, school, "ada", "1000", fee_type=FEE_TYPES[0])
    expected[due["id"]] = ("1000", "0")

    partial = bill(
        api_client,
        admin,
        school,
        "ada",
        "1000",
        fee_type=FEE_TYPES[1],
        amount_paid="400",
    )
    expected[partial["id"]] = ("1000", "400")

    settled = bill(
        api_client,
        admin,
        school,
        "ada",
        "600",
        fee_type=FEE_TYPES[2],
        amount_paid="600",
    )
    expected[settled["id"]] = ("600", "600")

    instalments = bill(api_client, admin, school, "ada", "1000", fee_type=FEE_TYPES[3])
    for amount in ("100", "250.50", "149.50"):
        pay(api_client, admin, instalments["id"], amount)
    expected[instalments["id"]] = ("1000", "500")

    mixed = bill(
        api_client,
        admin,
        school,
        "ada",
        "1000",
        fee_type=FEE_TYPES[4],
        amount_paid="250",
    )
    pay(api_client, admin, mixed["id"], "250")
    expected[mixed["id"]] = ("1000", "500")
    return expected


def assert_same_everywhere(api_client, admin, parent, school, expected):
    """`expected` is {fee_id: (billed, paid)}. Every surface must agree with it."""
    total_billed = sum(Decimal(b) for b, _ in expected.values())
    total_paid = sum(Decimal(p) for _, p in expected.values())

    # the bill, and its receipt
    for fee_id, (billed, paid) in expected.items():
        detail = api_client.get(f"{FEES}/{fee_id}", headers=admin).json()
        assert detail["amount_paid"] == m(paid), f"bill {fee_id}"
        assert detail["balance"] == m(Decimal(billed) - Decimal(paid)), f"bill {fee_id}"
        assert detail["status"] == status_of(billed, paid), f"bill {fee_id}"
        receipt = api_client.get(f"{FEES}/{fee_id}/receipt", headers=admin).json()
        assert receipt["amount_paid"] == m(paid), f"bill receipt {fee_id}"
        assert receipt["balance"] == detail["balance"]
        assert receipt["status"] == detail["status"]
        # each instalment's receipt shows the running total up to that instalment:
        # live payments so far, less the refunds given on them
        refunds_on = {}
        for given in detail["refunds"]:
            refunds_on[given["payment_id"]] = refunds_on.get(
                given["payment_id"], Decimal(0)
            ) + Decimal(given["amount"])
        # A printed receipt is the same refunds without the acting administrator.
        shown = [{**r, "refunded_by": None} for r in detail["refunds"]]
        assert receipt["refunds"] == shown, f"bill receipt {fee_id}"
        assert m(sum(refunds_on.values(), Decimal(0))) == detail["amount_refunded"]
        running = Decimal(0)
        shown = []
        for payment in detail["payments"]:
            if payment["status"] == "valid":  # a voided payment never counts
                running += Decimal(payment["amount"]) - refunds_on.get(
                    payment["id"], Decimal(0)
                )
                shown.append(payment["id"])
            one = api_client.get(
                f"{FEES}/{fee_id}/payments/{payment['id']}/receipt", headers=admin
            ).json()
            assert one["amount_paid"] == m(running), f"receipt {payment['id']}"
            assert one["balance"] == m(Decimal(billed) - running)
            assert one["status"] == status_of(billed, running)
            assert {r["payment_id"] for r in one["refunds"]} <= set(shown)
        assert running == Decimal(paid)

    # the list, its totals, and the status filter
    page = api_client.get(
        FEES, headers=admin, params={"student_id": school["ada"], "limit": 100}
    ).json()
    assert {i["id"]: i["amount_paid"] for i in page["items"]} == {
        fee_id: m(paid) for fee_id, (_, paid) in expected.items()
    }
    assert page["totals"] == {
        "billed": m(total_billed),
        "paid": m(total_paid),
        "balance": m(total_billed - total_paid),
    }
    for wanted in ("paid", "partial", "due"):
        listed = api_client.get(
            FEES,
            headers=admin,
            params={"student_id": school["ada"], "status": wanted, "limit": 100},
        ).json()
        assert {i["id"] for i in listed["items"]} == {
            fee_id for fee_id, (b, p) in expected.items() if status_of(b, p) == wanted
        }, wanted
        assert listed["totals"]["paid"] == m(
            sum(Decimal(p) for b, p in expected.values() if status_of(b, p) == wanted)
        )

    # the admin dashboard, for the term and for all time
    figures = {
        "billed": m(total_billed),
        "collected": m(total_paid),
        "outstanding": m(total_billed - total_paid),
    }
    only_ada = api_client.get(
        DASH, headers=admin, params={"term_id": school["term"]}
    ).json()["fees"]
    assert only_ada["term"] == figures
    assert only_ada["all_time"] == figures

    # the parent dashboard
    child = api_client.get(
        DASH, headers=parent, params={"term_id": school["term"]}
    ).json()["children"][0]
    assert child["fees"]["term"] == figures
    assert child["fees"]["all_time"] == figures


@pytest.fixture
def parent(db, api_client, school):
    return parent_for(db, api_client, [school["ada"]])[1]


def test_every_surface_agrees_for_bills_in_every_state(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_every_surface_still_agrees_after_more_payments(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)
    fee_ids = list(expected)
    pay(api_client, admin, fee_ids[0], "1000")  # a due bill becomes paid
    pay(api_client, admin, fee_ids[1], "0.01")  # a partial bill gains a little
    expected[fee_ids[0]] = ("1000", "1000")
    expected[fee_ids[1]] = ("1000", "400.01")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_every_surface_agrees_after_payments_are_voided(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)
    _due, _partial, settled, instalments, mixed = list(expected)

    def payments_of(fee_id):
        return api_client.get(f"{FEES}/{fee_id}", headers=admin).json()["payments"]

    # a fully paid bill whose only payment was a mistake goes back to due
    void(api_client, admin, settled, payments_of(settled)[0]["id"])
    expected[settled] = ("600", "0")
    # the middle instalment (250.50 of 100 + 250.50 + 149.50) is voided
    void(api_client, admin, instalments, payments_of(instalments)[1]["id"])
    expected[instalments] = ("1000", "249.50")
    # the first payment of a bill with two (250 + 250)
    void(api_client, admin, mixed, payments_of(mixed)[0]["id"])
    expected[mixed] = ("1000", "250")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_voiding_everything_leaves_every_surface_at_zero(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)
    for fee_id in list(expected):
        for payment in api_client.get(f"{FEES}/{fee_id}", headers=admin).json()[
            "payments"
        ]:
            void(api_client, admin, fee_id, payment["id"])
        expected[fee_id] = (expected[fee_id][0], "0")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_a_payment_after_a_void_is_counted_everywhere(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)
    _due, partial, _settled, _instalments, _mixed = list(expected)
    first = api_client.get(f"{FEES}/{partial}", headers=admin).json()["payments"][0]
    void(api_client, admin, partial, first["id"])
    pay(api_client, admin, partial, "700")
    expected[partial] = ("1000", "700")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_every_surface_agrees_after_refunds(db, api_client, admin, school, parent):
    expected = make_bills(api_client, admin, school)
    _due, partial, settled, instalments, _mixed = list(expected)

    def payments_of(fee_id):
        return api_client.get(f"{FEES}/{fee_id}", headers=admin).json()["payments"]

    # a fully paid bill gets part of its money back: paid -> partial
    refund(api_client, admin, settled, payments_of(settled)[0]["id"], "150.50")
    expected[settled] = ("600", "449.50")
    # two partial refunds on one instalment, a third on another
    inst = payments_of(instalments)
    refund(api_client, admin, instalments, inst[1]["id"], "50")
    refund(api_client, admin, instalments, inst[1]["id"], "100.25")
    refund(api_client, admin, instalments, inst[2]["id"], "149.50")
    expected[instalments] = ("1000", "200.25")
    # the first payment of a partial bill is refunded in full: partial -> due
    refund(api_client, admin, partial, payments_of(partial)[0]["id"], "400")
    expected[partial] = ("1000", "0")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_voids_and_refunds_together_agree_everywhere(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)
    _due, _partial, _settled, instalments, mixed = list(expected)

    def payments_of(fee_id):
        return api_client.get(f"{FEES}/{fee_id}", headers=admin).json()["payments"]

    mix = payments_of(mixed)  # two payments of 250
    void(api_client, admin, mixed, mix[0]["id"])
    refund(api_client, admin, mixed, mix[1]["id"], "75")
    expected[mixed] = ("1000", "175")
    inst = payments_of(instalments)  # 100, 250.50, 149.50
    void(api_client, admin, instalments, inst[0]["id"])
    refund(api_client, admin, instalments, inst[2]["id"], "49.50")
    expected[instalments] = ("1000", "350.50")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_a_payment_after_a_refund_is_counted_everywhere(
    db, api_client, admin, school, parent
):
    expected = make_bills(api_client, admin, school)
    _due, _partial, settled, _instalments, _mixed = list(expected)
    first = api_client.get(f"{FEES}/{settled}", headers=admin).json()["payments"][0]
    refund(api_client, admin, settled, first["id"], "200")
    pay(api_client, admin, settled, "100")
    expected[settled] = ("600", "500")

    assert_same_everywhere(api_client, admin, parent, school, expected)


def test_an_empty_school_agrees_too(db, api_client, admin, school, parent):
    page = api_client.get(FEES, headers=admin).json()

    assert page["totals"] == {"billed": "0.00", "paid": "0.00", "balance": "0.00"}
    assert api_client.get(DASH, headers=parent).json()["children"][0]["fees"][
        "all_time"
    ] == {"billed": "0.00", "collected": "0.00", "outstanding": "0.00"}


def test_the_overpayment_check_uses_the_same_figure(db, api_client, admin, school):
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="400")
    too_much = api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=admin, json={"amount": "600.01"}
    )
    exact = api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=admin, json={"amount": "600.00"}
    )

    assert too_much.status_code == 422
    assert "outstanding balance of 600.00" in too_much.json()["error"]["message"]
    assert exact.status_code == 201
    assert exact.json()["fee"]["status"] == "paid"
    again = api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=admin, json={"amount": "0.01"}
    )
    assert again.status_code == 422
    assert "already been paid in full" in again.json()["error"]["message"]


def test_a_parent_sees_the_same_figures_on_receipts(
    db, api_client, admin, school, parent
):
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="300")
    pay(api_client, admin, fee["id"], "200")

    mine = api_client.get(f"{FEES}/{fee['id']}/receipt", headers=parent).json()

    assert mine["amount_paid"] == "500.00"
    assert mine["balance"] == "500.00"
    assert mine["status"] == "partial"


def test_another_childs_bill_does_not_leak_into_the_totals(
    db, api_client, admin, school, parent
):
    bill(api_client, admin, school, "ada", "1000", amount_paid="100")
    bill(api_client, admin, school, "bola", "5000", amount_paid="4000")

    child = api_client.get(
        DASH, headers=parent, params={"term_id": school["term"]}
    ).json()["children"][0]

    assert child["fees"]["term"] == {
        "billed": "1000.00",
        "collected": "100.00",
        "outstanding": "900.00",
    }


# --- structure: nothing else may work out a paid figure --------------------------------


def python_files():
    return [p for p in APP.rglob("*.py") if p.name != "paid.py"]


def test_only_the_paid_module_adds_up_payment_amounts():
    column = re.compile(r"Fee(Payment|Refund)\.amount\b")
    python_sum = re.compile(r"sum\([^\n]*\.amount\b")

    offenders = []
    for path in python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if column.search(line) or python_sum.search(line):
                offenders.append(
                    f"{path.relative_to(APP.parent)}:{number}: {line.strip()}"
                )

    assert not offenders, (
        "Work out paid figures in app/services/paid.py only:\n" + "\n".join(offenders)
    )


def test_the_paid_module_is_where_the_sums_live():
    text = (APP / "services" / "paid.py").read_text(encoding="utf-8")

    assert "func.sum(FeePayment.amount)" in text  # one bill
    assert "func.sum(FeeRefund.amount)" in text  # refunds, per payment and per bill
    assert "union_all(" in text  # many bills: payments less refunds, in one place
