"""Demo data for the bank-receipt settlement workbench.

The scenario is deliberately awkward:

* ``Harbor`` has an overdue disputed bill ``B-1003`` that the payer points at,
  plus older undisputed bills.  The designated money must stay pending even
  though the undisputed bills are overdue.
* A second receipt arrives later and must keep applying to the bill it paid
  even after the first receipt is reversed.
* A third receipt leaves a small amount pending because the only remaining
  bill of that customer is unsigned.
"""

BILLS = [
    {"bill_id": "B-1001", "customer_id": "Harbor", "amount_cents": 52000,
     "due_date": "2026-09-30", "signed": True, "disputed": False, "label": "滞港费 9 月"},
    {"bill_id": "B-1002", "customer_id": "Harbor", "amount_cents": 24000,
     "due_date": "2026-10-15", "signed": True, "disputed": False, "label": "滞港费 10 月"},
    {"bill_id": "B-1003", "customer_id": "Harbor", "amount_cents": 16000,
     "due_date": "2026-10-05", "signed": True, "disputed": True, "label": "滞港费争议件"},
    {"bill_id": "B-2001", "customer_id": "Northwind", "amount_cents": 8000,
     "due_date": "2026-10-01", "signed": True, "disputed": False, "label": "滞港费 10 月"},
    {"bill_id": "B-2002", "customer_id": "Northwind", "amount_cents": 14000,
     "due_date": "2026-11-05", "signed": False, "disputed": False, "label": "未签账单"},
]

RECEIPTS = [
    {"receipt_id": "R-001", "customer_id": "Harbor", "amount_cents": 50000,
     "received_at": "2026-11-01T09:00:00", "designated_bill_id": "B-1003",
     "designated_cents": 16000, "bank_ref": "BK-7781"},
    {"receipt_id": "R-002", "customer_id": "Harbor", "amount_cents": 30000,
     "received_at": "2026-11-10T14:30:00", "bank_ref": "BK-7802"},
    {"receipt_id": "R-003", "customer_id": "Northwind", "amount_cents": 9000,
     "received_at": "2026-11-12T10:00:00", "bank_ref": "BK-7815"},
]


def payload():
    return {"bills": [dict(item) for item in BILLS], "receipts": [dict(item) for item in RECEIPTS]}
