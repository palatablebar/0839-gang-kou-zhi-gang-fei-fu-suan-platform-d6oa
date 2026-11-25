"""Pure allocation rules for applying bank receipts to demurrage bills.

Rules (see ``docs/BUSINESS-RULES.md``):

* Only **signed** bills belonging to the **same customer** can be settled.
* A designation that cannot be honoured because the bill is disputed,
  unsigned, unknown or belongs to another customer is held *pending*
  (待认领) and is never redirected, even when other debts are overdue.
* Designated money beyond what the named bill still owes falls back to the
  ordinary priority pool: the instruction is satisfied once that bill is
  settled, and overdue debt should be cleared promptly.
* The **undesignated** part of a receipt pays the earliest due undisputed
  bills first, then the next one, and so on.
* Whatever cannot be placed stays pending as a claim for later review.
* Once posted, a receipt's allocation list is frozen.  Reversing a receipt
  only removes *its own* effect; allocations made by other receipts keep
  applying to the bills they originally paid.
"""

from __future__ import annotations


DESIGNATED_BILL_DISPUTED = "DESIGNATED_BILL_DISPUTED"
DESIGNATED_BILL_UNKNOWN = "DESIGNATED_BILL_UNKNOWN"
DESIGNATED_CUSTOMER_MISMATCH = "DESIGNATED_CUSTOMER_MISMATCH"
DESIGNATED_BILL_UNSIGNED = "DESIGNATED_BILL_UNSIGNED"
NO_ELIGIBLE_BILL = "NO_ELIGIBLE_BILL"

SOURCE_DESIGNATED = "DESIGNATED"
SOURCE_PRIORITY = "PRIORITY"
SOURCE_CLAIM = "CLAIM"


def outstanding(bill):
    return int(bill["amount_cents"]) - int(bill.get("paid_cents", 0))


def eligible_bills(bills, customer_id):
    """Signed, undisputed bills of one customer, earliest due first.

    ``bills`` maps bill_id -> bill dict.  Ties are broken by bill id so the
    same data always produces the same allocation.
    """
    return sorted(
        (
            bill
            for bill in bills.values()
            if bill["customer_id"] == customer_id
            and bill["signed"]
            and not bill["disputed"]
            and outstanding(bill) > 0
        ),
        key=lambda bill: (bill["due_date"], bill["bill_id"]),
    )


def apply_payment(bill, amount):
    amount = int(amount)
    bill["paid_cents"] = int(bill.get("paid_cents", 0)) + amount
    return amount


def allocate_receipt(receipt, bills):
    """Allocate one receipt against ``bills`` (mutating their ``paid_cents``).

    Returns ``(allocations, pending)``.  Every cent of the receipt lands in
    exactly one of the two lists, which makes reconciliation an identity:

        sum(allocations) + sum(pending) == receipt.amount_cents
    """
    remaining = int(receipt.amount_cents)
    allocations = []
    pending = []

    def hold(amount, reason, bill_id=None):
        if amount > 0:
            pending.append({"bill_id": bill_id, "amount_cents": amount, "reason": reason})

    for designation in receipt.designations:
        portion = min(int(designation["amount_cents"]), remaining)
        remaining -= portion
        if portion <= 0:
            continue
        bill = bills.get(designation["bill_id"])
        if bill is None:
            hold(portion, DESIGNATED_BILL_UNKNOWN, designation["bill_id"])
        elif bill["customer_id"] != receipt.customer_id:
            hold(portion, DESIGNATED_CUSTOMER_MISMATCH, bill["bill_id"])
        elif not bill["signed"]:
            hold(portion, DESIGNATED_BILL_UNSIGNED, bill["bill_id"])
        elif bill["disputed"]:
            # The payer pointed at a bill under dispute: hold this part for a
            # claim.  It must not spill over to other (even overdue) debts.
            hold(portion, DESIGNATED_BILL_DISPUTED, bill["bill_id"])
        else:
            paid = min(portion, outstanding(bill))
            if paid > 0:
                apply_payment(bill, paid)
                allocations.append({"bill_id": bill["bill_id"], "amount_cents": paid,
                                    "source": SOURCE_DESIGNATED})
            # Excess is not earmarked to anything else; let priority handle it.
            remaining += portion - paid

    if remaining > 0:
        for bill in eligible_bills(bills, receipt.customer_id):
            if remaining <= 0:
                break
            amount = min(remaining, outstanding(bill))
            if amount <= 0:
                continue
            apply_payment(bill, amount)
            allocations.append({"bill_id": bill["bill_id"], "amount_cents": amount,
                                "source": SOURCE_PRIORITY})
            remaining -= amount

    hold(remaining, NO_ELIGIBLE_BILL)
    return allocations, pending


def rebuild_paid(bills, receipts):
    """Recompute ``paid_cents`` from allocations of *active* receipts only.

    Reversed receipts keep their allocation records for audit but contribute
    nothing, which restores exactly the debt they had paid down.  Later
    receipts are not redistributed: their recorded allocations simply remain
    attached to the bills they originally paid.
    """
    for bill in bills.values():
        bill["paid_cents"] = 0
    for receipt in receipts:
        if not receipt.active:
            continue
        for allocation in receipt.allocations:
            bill = bills.get(allocation["bill_id"])
            if bill is not None:
                bill["paid_cents"] = int(bill.get("paid_cents", 0)) + int(allocation["amount_cents"])
    return bills
