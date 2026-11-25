"""Read models for the bank-receipt settlement workbench."""

from __future__ import annotations

from datetime import date

from app.settlement.engine import outstanding, rebuild_paid


def _bill_status(bill):
    if bill["disputed"]:
        return "DISPUTED"
    if not bill["signed"]:
        return "UNSIGNED"
    if int(bill.get("paid_cents", 0)) >= int(bill["amount_cents"]):
        return "SETTLED"
    if int(bill.get("paid_cents", 0)) > 0:
        return "PARTIAL"
    return "OPEN"


def build_snapshot(bills, receipts, as_of=None, customer_id=None):
    paid_bills = rebuild_paid({key: dict(value) for key, value in bills.items()}, receipts)
    as_of_date = as_of or date.today()

    bill_rows = []
    for bill in paid_bills.values():
        row = {
            "bill_id": bill["bill_id"],
            "customer_id": bill["customer_id"],
            "amount_cents": int(bill["amount_cents"]),
            "paid_cents": int(bill.get("paid_cents", 0)),
            "outstanding_cents": outstanding(bill),
            "due_date": bill["due_date"].isoformat(),
            "signed": bool(bill["signed"]),
            "disputed": bool(bill["disputed"]),
            "label": bill["label"],
            "status": _bill_status(bill),
            "overdue": bool(bill["signed"] and not bill["disputed"]
                           and bill["due_date"] < as_of_date and outstanding(bill) > 0),
        }
        bill_rows.append(row)
    bill_rows.sort(key=lambda item: (item["customer_id"], item["due_date"], item["bill_id"]))

    receipt_rows = []
    for receipt in receipts:
        row = receipt.to_dict()
        row["received_at"] = receipt.received_at.isoformat()
        row["allocated_cents"] = receipt.allocated_cents
        row["pending_cents"] = receipt.pending_cents
        receipt_rows.append(row)
    receipt_rows.sort(key=lambda item: (item["received_at"], item["seq"]))

    active = [item for item in receipt_rows if item["status"] == "ACTIVE"]
    reversed_rows = [item for item in receipt_rows if item["status"] != "ACTIVE"]

    received_active = sum(int(item["amount_cents"]) for item in active)
    allocated = sum(int(item["allocated_cents"]) for item in active)
    pending = sum(int(item["pending_cents"]) for item in active)
    received_reversed = sum(int(item["amount_cents"]) for item in reversed_rows)

    bills_total = sum(int(item["amount_cents"]) for item in bill_rows)
    outstanding_total = sum(int(item["outstanding_cents"]) for item in bill_rows)
    paid_total = sum(int(item["paid_cents"]) for item in bill_rows)
    overdue_total = sum(int(item["outstanding_cents"]) for item in bill_rows if item["overdue"])

    checks = [
        {
            "id": "receipt_identity",
            "label": "每笔实收等于已抵加待认领",
            "ok": all(int(item["allocated_cents"]) + int(item["pending_cents"]) == int(item["amount_cents"])
                      for item in active),
        },
        {
            "id": "settlement_identity",
            "label": "已抵加待认领等于未冲回实收",
            "ok": allocated + pending == received_active,
            "detail": {"allocated_cents": allocated, "pending_cents": pending,
                       "received_active_cents": received_active},
        },
        {
            "id": "bill_capacity",
            "label": "账单已抵不超过账单金额",
            "ok": all(0 <= item["paid_cents"] <= item["amount_cents"] for item in bill_rows),
        },
        {
            "id": "customer_match",
            "label": "回款只抵同一客户账单",
            "ok": all(
                any(bill["bill_id"] == allocation["bill_id"] and bill["customer_id"] == item["customer_id"]
                    for bill in bill_rows)
                for item in active
                for allocation in item["allocations"]
            ),
        },
    ]

    return {
        "customer_id": customer_id,
        "as_of": as_of_date.isoformat(),
        "bills": bill_rows,
        "receipts": receipt_rows,
        "totals": {
            "bills_cents": bills_total,
            "paid_cents": paid_total,
            "outstanding_cents": outstanding_total,
            "overdue_cents": overdue_total,
            "received_active_cents": received_active,
            "received_reversed_cents": received_reversed,
            "allocated_cents": allocated,
            "pending_cents": pending,
        },
        "checks": checks,
        "balanced": all(item["ok"] for item in checks),
    }
