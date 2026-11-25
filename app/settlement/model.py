"""Data model and parsing helpers for bank-receipt settlement.

Money is always handled as integer cents.  A receipt keeps the allocations it
made for audit even after it is reversed; only *active* receipts count towards
what a bill has actually received.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.errors import DomainIssue
from app.intake.normalize import canonical


ACTIVE = "ACTIVE"
REVERSED = "REVERSED"


def text(value, code="BAD_TEXT"):
    result = canonical(value)
    if result == "":
        raise DomainIssue(code)
    return result


def _decimal(value, code):
    raw = canonical(value)
    if raw == "":
        raise DomainIssue(code)
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise DomainIssue(code)


def cents(value, code="BAD_AMOUNT"):
    """Parse an integer amount expressed in cents."""
    number = _decimal(value, code)
    if number != number.to_integral_value() or number < 0:
        raise DomainIssue(code)
    return int(number)


def yuan_to_cents(value, code="BAD_AMOUNT"):
    number = _decimal(value, code)
    if number < 0:
        raise DomainIssue(code)
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_amount(payload, field, code):
    """Read ``<field>_cents`` (integer cents) or ``<field>`` (yuan)."""
    if f"{field}_cents" in payload and canonical(payload.get(f"{field}_cents")) != "":
        return cents(payload.get(f"{field}_cents"), code)
    value = payload.get(field)
    if canonical(value) != "":
        return yuan_to_cents(value, code)
    raise DomainIssue(code)


def parse_date(value, code="BAD_DATE"):
    raw = canonical(value)
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise DomainIssue(code)


def parse_datetime(value, code="BAD_DATETIME"):
    raw = canonical(value)
    if raw == "":
        return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        result = datetime.fromisoformat(candidate)
    except ValueError:
        raise DomainIssue(code)
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result.replace(microsecond=0)


def parse_bool(value, code="BAD_BOOLEAN"):
    if isinstance(value, bool):
        return value
    raw = canonical(value).lower()
    if raw in {"true", "1", "yes", "y", "是"}:
        return True
    if raw in {"false", "0", "no", "n", "否"}:
        return False
    raise DomainIssue(code)


def _digest(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def receipt_fingerprint(customer_id, amount_cents, designations, bank_ref=""):
    """Content digest of a receipt, independent of its timestamp.

    Used to reject a *different* receipt that reuses an existing id/ref and
    to recognise a retry that drops the original timestamp.
    """
    return _digest([
        customer_id,
        int(amount_cents),
        [[item["bill_id"], int(item["amount_cents"])] for item in designations],
        bank_ref or "",
    ])


@dataclass
class Bill:
    bill_id: str
    customer_id: str
    amount_cents: int
    due_date: date
    signed: bool = True
    disputed: bool = False
    label: str = ""

    @classmethod
    def parse(cls, payload):
        if not isinstance(payload, dict):
            raise DomainIssue("BILL_INVALID")
        amount = parse_amount(payload, "amount", "BAD_BILL_AMOUNT")
        if amount <= 0:
            raise DomainIssue("BAD_BILL_AMOUNT")
        return cls(
            bill_id=text(payload.get("bill_id"), "BILL_ID_MISSING"),
            customer_id=text(payload.get("customer_id"), "CUSTOMER_UNKNOWN"),
            amount_cents=amount,
            due_date=parse_date(payload.get("due_date"), "BAD_DUE_DATE"),
            signed=parse_bool(payload.get("signed", True)),
            disputed=parse_bool(payload.get("disputed", False)),
            label=canonical(payload.get("label", "")),
        )

    def to_dict(self):
        return {
            "bill_id": self.bill_id,
            "customer_id": self.customer_id,
            "amount_cents": self.amount_cents,
            "due_date": self.due_date.isoformat(),
            "signed": self.signed,
            "disputed": self.disputed,
            "label": self.label,
        }


@dataclass
class Receipt:
    receipt_id: str
    customer_id: str
    amount_cents: int
    received_at: datetime
    designations: list = field(default_factory=list)
    bank_ref: str = ""
    status: str = ACTIVE
    allocations: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    fingerprint: str = ""
    identity: str = ""
    seq: int = 0
    reversed_at: str | None = None

    @property
    def active(self):
        return self.status == ACTIVE

    @property
    def allocated_cents(self):
        return sum(int(item["amount_cents"]) for item in self.allocations)

    @property
    def pending_cents(self):
        return sum(int(item["amount_cents"]) for item in self.pending)

    def to_dict(self):
        return {
            "receipt_id": self.receipt_id,
            "customer_id": self.customer_id,
            "amount_cents": self.amount_cents,
            "received_at": self.received_at.isoformat(),
            "designations": [dict(item) for item in self.designations],
            "bank_ref": self.bank_ref,
            "status": self.status,
            "allocations": [dict(item) for item in self.allocations],
            "pending": [dict(item) for item in self.pending],
            "allocated_cents": self.allocated_cents,
            "pending_cents": self.pending_cents,
            "fingerprint": self.fingerprint,
            "identity": self.identity,
            "seq": self.seq,
            "reversed_at": self.reversed_at,
        }

def _designated_cents(entry, fields_cents, fields_yuan):
    """Designation amount in cents or yuan, from the given field names."""
    for name in fields_cents:
        if canonical(entry.get(name)) != "":
            return cents(entry[name], "BAD_DESIGNATION_AMOUNT")
    for name in fields_yuan:
        if canonical(entry.get(name)) != "":
            return yuan_to_cents(entry[name], "BAD_DESIGNATION_AMOUNT")
    return None


def parse_designations(payload, amount_cents):
    """Normalise the several accepted ways of designating receipt money."""
    raw = payload.get("designations")
    items = []
    if raw not in (None, "", []):
        if not isinstance(raw, list):
            raise DomainIssue("BAD_DESIGNATION")
        for entry in raw:
            if not isinstance(entry, dict):
                raise DomainIssue("BAD_DESIGNATION")
            bill_id = text(entry.get("bill_id", entry.get("designated_bill_id")), "BILL_ID_MISSING")
            items.append({
                "bill_id": bill_id,
                "amount_cents": _designated_cents(entry, ("amount_cents", "designated_cents"),
                                                  ("amount", "designated")),
            })
    else:
        bill_id = canonical(payload.get("designated_bill_id", payload.get("bill_id", "")))
        if bill_id:
            # At top level ``amount`` is the receipt total, so only the
            # ``designated*`` spellings mean the designated amount.
            items.append({
                "bill_id": bill_id,
                "amount_cents": _designated_cents(payload, ("designated_cents",), ("designated",)),
            })
    if not items:
        return []
    if len(items) == 1 and items[0]["amount_cents"] is None:
        items[0]["amount_cents"] = amount_cents
    total = 0
    for item in items:
        if item["amount_cents"] is None or item["amount_cents"] <= 0:
            raise DomainIssue("BAD_DESIGNATION_AMOUNT")
        total += item["amount_cents"]
    if total > amount_cents:
        raise DomainIssue("DESIGNATION_EXCEEDS_RECEIPT")
    return items


def receipt_from_payload(payload, seq=0):
    if not isinstance(payload, dict):
        raise DomainIssue("RECEIPT_INVALID")
    customer = text(payload.get("customer_id"), "CUSTOMER_UNKNOWN")
    amount = parse_amount(payload, "amount", "BAD_RECEIPT_AMOUNT")
    if amount <= 0:
        raise DomainIssue("BAD_RECEIPT_AMOUNT")
    designations = parse_designations(payload, amount)
    bank_ref = canonical(payload.get("bank_ref", ""))
    fingerprint = receipt_fingerprint(customer, amount, designations, bank_ref)
    received_raw = canonical(payload.get("received_at", ""))
    explicit_id = canonical(payload.get("receipt_id", ""))
    if explicit_id:
        identity = "id:" + explicit_id
        receipt_id = explicit_id
    elif bank_ref:
        identity = "ref:" + customer + ":" + bank_ref
        receipt_id = "RCPT-" + _digest([identity])[:12]
    else:
        # Content plus an explicitly supplied timestamp.  Without a timestamp
        # the identity is content-only, so a retry that dropped the original
        # timestamp is still recognised as the same payment.
        identity = "fp:" + fingerprint + ("@" + received_raw if received_raw else "")
        receipt_id = "RCPT-" + _digest([identity])[:12]
    return Receipt(
        receipt_id=receipt_id,
        customer_id=customer,
        amount_cents=amount,
        received_at=parse_datetime(received_raw, "BAD_DATETIME"),
        designations=designations,
        bank_ref=bank_ref,
        fingerprint=fingerprint,
        identity=identity,
        seq=seq,
    )
