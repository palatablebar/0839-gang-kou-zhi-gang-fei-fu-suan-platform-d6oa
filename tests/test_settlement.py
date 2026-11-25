from datetime import date

import pytest

from app.errors import DomainIssue
from app.settlement.model import Bill, receipt_from_payload
from app.settlement.store import SettlementStore


def bill(bill_id, customer="Harbor", amount=10000, due="2026-10-01", signed=True, disputed=False):
    return {"bill_id": bill_id, "customer_id": customer, "amount_cents": amount,
            "due_date": date.fromisoformat(due),
            "signed": signed, "disputed": disputed, "label": "", "paid_cents": 0}


def receipt(**payload):
    return receipt_from_payload(payload)


def seeded(tmp_path):
    store = SettlementStore(tmp_path / "settlement.sqlite3")
    store.upsert_bills([
        Bill.parse(item)
        for item in [
            {"bill_id": "B1", "customer_id": "Harbor", "amount_cents": 10000, "due_date": "2026-09-01"},
            {"bill_id": "B2", "customer_id": "Harbor", "amount_cents": 12000, "due_date": "2026-10-01"},
            {"bill_id": "B3", "customer_id": "Harbor", "amount_cents": 8000, "due_date": "2026-08-01", "disputed": True},
            {"bill_id": "B4", "customer_id": "Other", "amount_cents": 5000, "due_date": "2026-09-01"},
        ]
    ])
    return store


def test_undesignated_money_pays_earliest_due_first(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 15000,
                                 "received_at": "2026-11-01T00:00:00", "bank_ref": "A1"})
    allocations = result["receipt"]["allocations"]
    assert [(item["bill_id"], item["amount_cents"]) for item in allocations] == [
        ("B1", 10000), ("B2", 5000)]
    snapshot = store.snapshot()
    assert {row["bill_id"]: row["outstanding_cents"] for row in snapshot["bills"]}["B2"] == 7000


def test_designated_disputed_money_stays_pending(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 8000,
                                 "received_at": "2026-11-02T00:00:00",
                                 "designated_bill_id": "B3", "designated_cents": 8000,
                                 "bank_ref": "A2"})
    assert result["receipt"]["allocations"] == []
    assert result["receipt"]["pending"] == [
        {"bill_id": "B3", "amount_cents": 8000, "reason": "DESIGNATED_BILL_DISPUTED"}]
    snapshot = store.snapshot()
    assert snapshot["totals"]["pending_cents"] == 8000
    # Even though B1 is overdue, the designated money is not redirected.
    assert {row["bill_id"]: row["paid_cents"] for row in snapshot["bills"]}["B1"] == 0


def test_designated_disputed_does_not_absorb_mixed_receipt(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 20000,
                                 "designations": [
                                     {"bill_id": "B3", "amount_cents": 8000},
                                     {"bill_id": "B1", "amount_cents": 5000},
                                 ],
                                 "bank_ref": "A3"})
    receipt_row = result["receipt"]
    # The 8000 ear-marked for the disputed bill is held, not redirected.
    assert receipt_row["pending"] == [
        {"bill_id": "B3", "amount_cents": 8000, "reason": "DESIGNATED_BILL_DISPUTED"}]
    # Designated 5000 pays B1; the remaining undesignated 7000 goes by priority,
    # finishing B1 first and then B2.
    assert receipt_row["allocations"] == [
        {"bill_id": "B1", "amount_cents": 5000, "source": "DESIGNATED"},
        {"bill_id": "B1", "amount_cents": 5000, "source": "PRIORITY"},
        {"bill_id": "B2", "amount_cents": 2000, "source": "PRIORITY"},
    ]
    assert receipt_row["allocated_cents"] + receipt_row["pending_cents"] == 20000


def test_only_signed_bills_and_same_customer(tmp_path):
    store = seeded(tmp_path)
    store.upsert_bills([Bill.parse(
        {"bill_id": "B5", "customer_id": "Harbor", "amount_cents": 9000, "due_date": "2026-01-01", "signed": True})])
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 40000, "bank_ref": "A4"})
    touched = {item["bill_id"] for item in result["receipt"]["allocations"]}
    assert "B4" not in touched  # other customer's bill untouched
    snapshot = store.snapshot()
    assert next(row for row in snapshot["bills"] if row["bill_id"] == "B4")["paid_cents"] == 0


def test_unsigned_bill_cannot_be_settled(tmp_path):
    store = seeded(tmp_path)
    store.upsert_bills([Bill.parse(
        {"bill_id": "B6", "customer_id": "Harbor", "amount_cents": 9000, "due_date": "2026-01-01", "signed": False})])
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 9000,
                                 "designated_bill_id": "B6", "designated_cents": 9000, "bank_ref": "A5"})
    assert result["receipt"]["pending"][0]["reason"] == "DESIGNATED_BILL_UNSIGNED"
    with pytest.raises(DomainIssue) as exc:
        store.claim(result["receipt"]["receipt_id"], "B6")
    assert exc.value.code == "BILL_UNSIGNED"


def test_replay_is_idempotent(tmp_path):
    store = seeded(tmp_path)
    payload = {"customer_id": "Harbor", "amount_cents": 15000,
               "received_at": "2026-11-01T00:00:00", "bank_ref": "A6"}
    first = store.post_receipt(dict(payload))
    second = store.post_receipt(dict(payload))
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]
    assert second["receipt"]["allocations"] == first["receipt"]["allocations"]
    snapshot = store.snapshot()
    assert snapshot["totals"]["received_active_cents"] == 15000
    assert snapshot["totals"]["paid_cents"] == 15000


def test_replay_without_timestamp_is_idempotent(tmp_path):
    store = seeded(tmp_path)
    first = store.post_receipt({"customer_id": "Harbor", "amount_cents": 15000, "bank_ref": "A7"})
    second = store.post_receipt({"customer_id": "Harbor", "amount_cents": 15000, "bank_ref": "A7"})
    assert first["receipt"]["receipt_id"] == second["receipt"]["receipt_id"]
    assert store.snapshot()["totals"]["received_active_cents"] == 15000


def test_replay_with_same_id_but_different_amount_conflicts(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R-X", "customer_id": "Harbor", "amount_cents": 1000})
    with pytest.raises(DomainIssue) as exc:
        store.post_receipt({"receipt_id": "R-X", "customer_id": "Harbor", "amount_cents": 2000})
    assert exc.value.code == "RECEIPT_CONFLICT"


def test_reversal_restores_debt_and_keeps_later_receipt(tmp_path):
    store = seeded(tmp_path)
    first = store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 10000,
                                "received_at": "2026-11-01T00:00:00"})
    second = store.post_receipt({"receipt_id": "R2", "customer_id": "Harbor", "amount_cents": 12000,
                                 "received_at": "2026-11-05T00:00:00"})
    assert {item["bill_id"]: item["amount_cents"] for item in first["receipt"]["allocations"]} == {"B1": 10000}
    assert {item["bill_id"]: item["amount_cents"] for item in second["receipt"]["allocations"]} == {"B2": 12000}
    store.reverse_receipt("R1")
    snapshot = store.snapshot()
    by_id = {row["bill_id"]: row for row in snapshot["bills"]}
    assert by_id["B1"]["paid_cents"] == 0
    assert by_id["B1"]["outstanding_cents"] == 10000
    assert by_id["B2"]["paid_cents"] == 12000
    assert snapshot["totals"]["received_active_cents"] == 12000
    assert snapshot["totals"]["received_reversed_cents"] == 10000
    assert snapshot["totals"]["allocated_cents"] == 12000
    assert snapshot["totals"]["allocated_cents"] + snapshot["totals"]["pending_cents"] == \
        snapshot["totals"]["received_active_cents"]
    assert snapshot["balanced"] is True


def test_reversal_twice_is_rejected(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 1000})
    store.reverse_receipt("R1")
    with pytest.raises(DomainIssue) as exc:
        store.reverse_receipt("R1")
    assert exc.value.code == "RECEIPT_ALREADY_REVERSED"


def test_new_receipt_after_reversal_uses_restored_debt(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 10000,
                        "received_at": "2026-11-01T00:00:00"})
    store.reverse_receipt("R1")
    later = store.post_receipt({"receipt_id": "R2", "customer_id": "Harbor", "amount_cents": 6000,
                                "received_at": "2026-11-10T00:00:00"})
    assert {item["bill_id"]: item["amount_cents"] for item in later["receipt"]["allocations"]} == {"B1": 6000}
    snapshot = store.snapshot()
    by_id = {row["bill_id"]: row for row in snapshot["bills"]}
    assert by_id["B1"]["paid_cents"] == 6000
    assert snapshot["balanced"] is True


def test_pending_can_be_claimed_after_dispute_clears(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 8000,
                                 "designated_bill_id": "B3", "designated_cents": 8000})
    assert result["receipt"]["pending_cents"] == 8000
    store.upsert_bills([Bill.parse(
        {"bill_id": "B3", "customer_id": "Harbor", "amount_cents": 8000,
         "due_date": "2026-08-01", "disputed": False})])
    store.claim("R1", "B3")
    snapshot = store.snapshot()
    by_id = {row["bill_id"]: row for row in snapshot["bills"]}
    assert by_id["B3"]["paid_cents"] == 8000
    assert snapshot["totals"]["pending_cents"] == 0
    assert snapshot["balanced"] is True


def test_reversal_of_receipt_with_pending_keeps_balance(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 20000,
                        "designated_bill_id": "B3", "designated_cents": 8000})
    store.reverse_receipt("R1")
    snapshot = store.snapshot()
    assert snapshot["totals"]["received_active_cents"] == 0
    assert snapshot["totals"]["pending_cents"] == 0
    assert snapshot["totals"]["allocated_cents"] == 0
    assert snapshot["balanced"] is True


def test_snapshot_identity_holds_for_every_receipt(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 15000})
    store.post_receipt({"receipt_id": "R2", "customer_id": "Harbor", "amount_cents": 9000,
                        "designated_bill_id": "B3", "designated_cents": 9000})
    store.post_receipt({"receipt_id": "R3", "customer_id": "Other", "amount_cents": 20000})
    snapshot = store.snapshot()
    for item in snapshot["receipts"]:
        if item["status"] == "ACTIVE":
            assert item["allocated_cents"] + item["pending_cents"] == item["amount_cents"]
    assert snapshot["totals"]["allocated_cents"] + snapshot["totals"]["pending_cents"] == \
        snapshot["totals"]["received_active_cents"]
    assert snapshot["balanced"] is True


def test_bill_customer_cannot_change_after_payment(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 1000})
    with pytest.raises(DomainIssue) as exc:
        store.upsert_bills([Bill.parse({"bill_id": "B1", "customer_id": "Other",
                                       "amount_cents": 10000, "due_date": "2026-09-01"})])
    assert exc.value.code == "BILL_CUSTOMER_LOCKED"


def test_bill_amount_cannot_drop_below_paid(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 5000})
    with pytest.raises(DomainIssue) as exc:
        store.upsert_bills([Bill.parse({"bill_id": "B1", "customer_id": "Harbor",
                                       "amount_cents": 4000, "due_date": "2026-09-01"})])
    assert exc.value.code == "BILL_AMOUNT_TOO_SMALL"


def test_designation_cannot_exceed_receipt(tmp_path):
    store = seeded(tmp_path)
    with pytest.raises(DomainIssue) as exc:
        store.post_receipt({"customer_id": "Harbor", "amount_cents": 1000,
                            "designated_bill_id": "B1", "designated_cents": 2000})
    assert exc.value.code == "DESIGNATION_EXCEEDS_RECEIPT"


def test_designated_unknown_bill_stays_pending(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                                 "designated_bill_id": "B-404", "designated_cents": 5000})
    assert result["receipt"]["pending"] == [
        {"bill_id": "B-404", "amount_cents": 5000, "reason": "DESIGNATED_BILL_UNKNOWN"}]


def test_designated_other_customer_stays_pending(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                                 "designated_bill_id": "B4", "designated_cents": 5000})
    assert result["receipt"]["pending"][0]["reason"] == "DESIGNATED_CUSTOMER_MISMATCH"


def test_designated_excess_falls_back_to_priority(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 15000,
                                 "designated_bill_id": "B1", "designated_cents": 15000})
    # B1 is settled by the designation; the excess clears the next oldest debt.
    assert result["receipt"]["allocations"] == [
        {"bill_id": "B1", "amount_cents": 10000, "source": "DESIGNATED"},
        {"bill_id": "B2", "amount_cents": 5000, "source": "PRIORITY"}]
    assert result["receipt"]["pending"] == []
    assert result["receipt"]["allocated_cents"] == 15000


def test_claim_requires_same_customer(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Other", "amount_cents": 8000})
    # B4 (Other, 5000) gets paid; the extra 3000 is loose pending.
    with pytest.raises(DomainIssue) as exc:
        store.claim("R1", "B1", 3000)  # B1 belongs to Harbor
    assert exc.value.code == "CUSTOMER_MISMATCH"


def test_loose_pending_can_be_claimed(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 30000})
    # All eligible debt is now paid; new money has nowhere to go but pending.
    extra = store.post_receipt({"receipt_id": "R2", "customer_id": "Harbor", "amount_cents": 5000})
    assert extra["receipt"]["pending"] == [{"bill_id": None, "amount_cents": 5000, "reason": "NO_ELIGIBLE_BILL"}]
    store.upsert_bills([Bill.parse({"bill_id": "B1", "customer_id": "Harbor",
                                    "amount_cents": 12000, "due_date": "2026-09-01"})])
    with pytest.raises(DomainIssue) as exc:
        store.claim("R2", "B1", 9000)
    assert exc.value.code == "BAD_CLAIM_AMOUNT"
    claimed = store.claim("R2", "B1", 2000)
    assert claimed["pending_cents"] == 3000
    assert claimed["allocations"][-1] == {"bill_id": "B1", "amount_cents": 2000, "source": "CLAIM"}


def test_designated_pending_cannot_be_redirected_by_claim(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 8000,
                        "designated_bill_id": "B3", "designated_cents": 8000})
    with pytest.raises(DomainIssue) as exc:
        store.claim("R1", "B1")
    assert exc.value.code == "NOTHING_PENDING"


# ---------------------------------------------------------------- HTTP layer

def post(client, url, payload):
    return client.post(url, json=payload)


def test_api_snapshot_is_empty(client):
    data = client.get("/api/settlement").get_json()
    assert data["bills"] == []
    assert data["receipts"] == []
    assert data["balanced"] is True


def test_api_receipt_flow(client):
    post(client, "/api/settlement/bills", {"bills": [
        {"bill_id": "B1", "customer_id": "Harbor", "amount_cents": 10000, "due_date": "2026-09-01"},
        {"bill_id": "B2", "customer_id": "Harbor", "amount_cents": 8000, "due_date": "2026-08-01",
         "disputed": True},
    ]})
    result = post(client, "/api/settlement/receipts", {
        "customer_id": "Harbor", "amount_cents": 12000,
        "designated_bill_id": "B2", "designated_cents": 8000, "bank_ref": "H1",
    }).get_json()
    assert result["replayed"] is False
    assert result["receipt"]["pending_cents"] == 8000
    assert result["receipt"]["allocations"] == [
        {"bill_id": "B1", "amount_cents": 4000, "source": "PRIORITY"}]
    snapshot = client.get("/api/settlement").get_json()
    assert snapshot["totals"]["allocated_cents"] + snapshot["totals"]["pending_cents"] == \
        snapshot["totals"]["received_active_cents"]


def test_api_replay_is_idempotent(client):
    payload = {"customer_id": "Harbor", "amount_cents": 5000, "bank_ref": "H2"}
    first = post(client, "/api/settlement/receipts", payload).get_json()
    second = post(client, "/api/settlement/receipts", payload).get_json()
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert client.get("/api/settlement").get_json()["totals"]["received_active_cents"] == 5000


def test_api_reverse_and_balance(client):
    post(client, "/api/settlement/receipts", {"receipt_id": "R1", "customer_id": "Harbor",
                                              "amount_cents": 5000})
    post(client, "/api/settlement/receipts", {"receipt_id": "R2", "customer_id": "Harbor",
                                              "amount_cents": 4000,
                                              "received_at": "2026-11-02T00:00:00"})
    response = post(client, "/api/settlement/receipts/R1/reverse", {})
    assert response.status_code == 200
    snapshot = response.get_json()["snapshot"]
    assert snapshot["totals"]["received_active_cents"] == 4000
    assert snapshot["totals"]["received_reversed_cents"] == 5000
    assert snapshot["balanced"] is True


def test_api_conflict_returns_409(client):
    post(client, "/api/settlement/receipts", {"receipt_id": "R1", "customer_id": "Harbor",
                                              "amount_cents": 5000})
    response = post(client, "/api/settlement/receipts", {"receipt_id": "R1", "customer_id": "Harbor",
                                                         "amount_cents": 6000})
    assert response.status_code == 409
    assert response.get_json()["code"] == "RECEIPT_CONFLICT"


def test_api_unknown_stage_still_404(client):
    assert client.post("/api/stages/nope").status_code == 404


def test_replaying_a_reversed_receipt_does_not_resurrect_it(tmp_path):
    store = seeded(tmp_path)
    payload = {"customer_id": "Harbor", "amount_cents": 9000, "bank_ref": "A8"}
    first = store.post_receipt(dict(payload))
    store.reverse_receipt(first["receipt"]["receipt_id"])
    replay = store.post_receipt(dict(payload))
    assert replay["replayed"] is True
    assert replay["receipt"]["status"] == "REVERSED"
    snapshot = store.snapshot()
    assert snapshot["totals"]["received_active_cents"] == 0
    assert snapshot["totals"]["allocated_cents"] == 0
    assert snapshot["balanced"] is True


def test_designated_to_unsigned_bill_stays_pending(tmp_path):
    store = seeded(tmp_path)
    store.upsert_bills([Bill.parse({"bill_id": "B7", "customer_id": "Harbor",
                                    "amount_cents": 7000, "due_date": "2026-07-01", "signed": False})])
    result = store.post_receipt({"customer_id": "Harbor", "amount_cents": 7000,
                                 "designated_bill_id": "B7", "designated_cents": 7000})
    assert result["receipt"]["pending"] == [
        {"bill_id": "B7", "amount_cents": 7000, "reason": "DESIGNATED_BILL_UNSIGNED"}]


def test_bill_registration_accepts_yuan_amount(client):
    response = post(client, "/api/settlement/bills", {"bill_id": "Y1", "customer_id": "Yard",
                                                      "amount": "520.00", "due_date": "2026-10-01"})
    assert response.status_code == 200
    bill = next(item for item in response.get_json()["bills"] if item["bill_id"] == "Y1")
    assert bill["amount_cents"] == 52000


def test_receipt_accepts_yuan_amount(client):
    post(client, "/api/settlement/bills", {"bill_id": "Y2", "customer_id": "Yard",
                                           "amount_cents": 52000, "due_date": "2026-10-01"})
    result = post(client, "/api/settlement/receipts", {"customer_id": "Yard", "amount": "520.00",
                                                       "bank_ref": "Y-1"}).get_json()
    assert result["receipt"]["amount_cents"] == 52000
    assert result["receipt"]["allocations"] == [
        {"bill_id": "Y2", "amount_cents": 52000, "source": "PRIORITY"}]


def test_bill_with_payments_cannot_become_disputed(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 10000})
    with pytest.raises(DomainIssue) as exc:
        store.upsert_bills([Bill.parse({"bill_id": "B1", "customer_id": "Harbor",
                                       "amount_cents": 10000, "due_date": "2026-09-01",
                                       "disputed": True})])
    assert exc.value.code == "BILL_HAS_PAYMENTS"
    store.reverse_receipt("R1")
    snapshot = store.upsert_bills([Bill.parse({"bill_id": "B1", "customer_id": "Harbor",
                                              "amount_cents": 10000, "due_date": "2026-09-01",
                                              "disputed": True})])
    assert next(item for item in snapshot["bills"] if item["bill_id"] == "B1")["disputed"] is True


def test_claim_accepts_yuan_amount(client):
    post(client, "/api/settlement/bills", {"bill_id": "B1", "customer_id": "Harbor",
                                           "amount_cents": 10000, "due_date": "2026-09-01"})
    receipt = post(client, "/api/settlement/receipts", {"receipt_id": "R1", "customer_id": "Harbor",
                                                        "amount_cents": 15000}).get_json()["receipt"]
    assert receipt["pending_cents"] == 5000
    # A later bill appears; the earlier excess is loose money and may claim it.
    post(client, "/api/settlement/bills", {"bill_id": "B2", "customer_id": "Harbor",
                                           "amount_cents": 8000, "due_date": "2026-10-01"})
    response = post(client, "/api/settlement/receipts/R1/claim", {"bill_id": "B2", "amount": "20.00"})
    assert response.status_code == 200
    assert response.get_json()["receipt"]["pending_cents"] == 3000
    bills = {item["bill_id"]: item for item in client.get("/api/settlement").get_json()["bills"]}
    assert bills["B2"]["paid_cents"] == 2000


def test_claim_drains_earmarked_bucket_before_loose_money(tmp_path):
    store = seeded(tmp_path)
    result = store.post_receipt({
        "receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 30000,
        "designated_bill_id": "B3", "designated_cents": 8000,
    })
    assert result["receipt"]["pending_cents"] == 8000
    # Close B3's dispute, then claim; the earmarked 8000 is used first.
    store.upsert_bills([Bill.parse({"bill_id": "B3", "customer_id": "Harbor",
                                    "amount_cents": 8000, "due_date": "2026-08-01",
                                    "disputed": False})])
    claimed = store.claim("R1", "B3", 5000)
    assert claimed["pending"] == [{"bill_id": "B3", "amount_cents": 3000,
                                   "reason": "DESIGNATED_BILL_DISPUTED"}]
    assert claimed["pending_cents"] == 3000
    snapshot = store.snapshot()
    assert next(item for item in snapshot["bills"] if item["bill_id"] == "B3")["paid_cents"] == 5000
    assert snapshot["totals"]["allocated_cents"] + snapshot["totals"]["pending_cents"] == \
        snapshot["totals"]["received_active_cents"]
    assert snapshot["balanced"] is True


def test_snapshot_identity_after_claim(client):
    post(client, "/api/settlement/bills", {"bill_id": "B1", "customer_id": "Harbor",
                                           "amount_cents": 10000, "due_date": "2026-09-01"})
    post(client, "/api/settlement/receipts", {"receipt_id": "R1", "customer_id": "Harbor",
                                              "amount_cents": 15000})
    post(client, "/api/settlement/bills", {"bill_id": "B2", "customer_id": "Harbor",
                                           "amount_cents": 8000, "due_date": "2026-10-01"})
    post(client, "/api/settlement/receipts/R1/claim", {"bill_id": "B2", "amount_cents": 3000})
    snapshot = client.get("/api/settlement").get_json()
    assert snapshot["totals"]["allocated_cents"] + snapshot["totals"]["pending_cents"] == \
        snapshot["totals"]["received_active_cents"]
    assert snapshot["balanced"] is True


def test_same_due_date_orders_by_bill_id(tmp_path):
    store = SettlementStore(tmp_path / "settlement.sqlite3")
    store.upsert_bills([
        Bill.parse({"bill_id": "B-9", "customer_id": "Harbor", "amount_cents": 1000,
                    "due_date": "2026-10-01"}),
        Bill.parse({"bill_id": "B-1", "customer_id": "Harbor", "amount_cents": 1000,
                    "due_date": "2026-10-01"}),
    ])
    result = store.post_receipt({"receipt_id": "R1", "customer_id": "Harbor", "amount_cents": 1000})
    assert result["receipt"]["allocations"] == [
        {"bill_id": "B-1", "amount_cents": 1000, "source": "PRIORITY"}]


def test_retry_without_timestamp_matches_timestamped_receipt(tmp_path):
    store = seeded(tmp_path)
    first = store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                                "received_at": "2026-11-01T09:00:00"})
    retry = store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000})
    assert retry["replayed"] is True
    assert retry["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]
    assert store.snapshot()["totals"]["received_active_cents"] == 5000


def test_same_amount_different_days_stay_distinct(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                        "received_at": "2026-11-01T09:00:00"})
    store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                        "received_at": "2026-11-02T09:00:00"})
    assert store.snapshot()["totals"]["received_active_cents"] == 10000


def test_ambiguous_content_only_retry_is_rejected(tmp_path):
    store = seeded(tmp_path)
    store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                        "received_at": "2026-11-01T09:00:00"})
    store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                        "received_at": "2026-11-02T09:00:00"})
    with pytest.raises(DomainIssue) as exc:
        store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000})
    assert exc.value.code == "RECEIPT_AMBIGUOUS"


def test_designations_accept_yuan_amounts(client):
    post(client, "/api/settlement/bills", {"bill_id": "B1", "customer_id": "Harbor",
                                           "amount_cents": 10000, "due_date": "2026-09-01"})
    result = post(client, "/api/settlement/receipts", {
        "customer_id": "Harbor", "amount": "150.00",
        "designations": [{"bill_id": "B1", "amount": "100.00"}],
        "bank_ref": "Y-9",
    }).get_json()
    receipt = result["receipt"]
    assert receipt["amount_cents"] == 15000
    assert receipt["allocations"][0] == {"bill_id": "B1", "amount_cents": 10000, "source": "DESIGNATED"}
    assert receipt["pending_cents"] == 5000


def test_non_numeric_designation_is_rejected(client):
    response = post(client, "/api/settlement/receipts", {
        "customer_id": "Harbor", "amount": "10.00",
        "designated_bill_id": "B9", "designated_cents": "xyz",
    })
    assert response.status_code == 400
    assert response.get_json()["code"] == "BAD_DESIGNATION_AMOUNT"


def test_designation_exceeding_receipt_is_rejected(client):
    response = post(client, "/api/settlement/receipts", {
        "customer_id": "Harbor", "amount": "10.00",
        "designated_bill_id": "B9", "designated_cents": 2000,
    })
    assert response.status_code == 400
    assert response.get_json()["code"] == "DESIGNATION_EXCEEDS_RECEIPT"


def test_bad_as_of_is_rejected(client):
    response = client.get("/api/settlement?as_of=not-a-date")
    assert response.status_code == 400
    assert response.get_json()["code"] == "BAD_DATE"


def test_reversing_unknown_receipt_is_404(client):
    response = post(client, "/api/settlement/receipts/nope/reverse", {})
    assert response.status_code == 404
    assert response.get_json()["code"] == "RECEIPT_NOT_FOUND"


def test_timestamped_retry_matches_timestampless_original(tmp_path):
    store = seeded(tmp_path)
    first = store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000})
    retry = store.post_receipt({"customer_id": "Harbor", "amount_cents": 5000,
                                "received_at": "2026-11-01T09:00:00"})
    assert retry["replayed"] is True
    assert retry["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]
    assert store.snapshot()["totals"]["received_active_cents"] == 5000
