"""HTTP endpoints for the bank-receipt settlement workbench."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, current_app, jsonify, request

from app.errors import DomainIssue
from app.settlement.model import Bill, cents, yuan_to_cents
from app.settlement.seed import payload as sample_payload


settlement = Blueprint("settlement", __name__)


def store():
    return current_app.extensions["settlement"]


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DomainIssue("BAD_REQUEST")
    return data


@settlement.get("/api/settlement")
def snapshot():
    customer = request.args.get("customer_id") or None
    as_of = request.args.get("as_of")
    try:
        as_of_date = date.fromisoformat(as_of) if as_of else None
    except ValueError:
        raise DomainIssue("BAD_DATE")
    data = store().snapshot(customer_id=customer, as_of=as_of_date)
    return jsonify(data)


@settlement.post("/api/settlement/bills")
def upsert_bills():
    data = _body()
    raw = data.get("bills")
    if raw is None and "bill_id" in data:
        raw = [data]
    if not isinstance(raw, list) or not raw:
        raise DomainIssue("BAD_REQUEST")
    bills = [Bill.parse(item) for item in raw]
    return jsonify(store().upsert_bills(bills))


@settlement.post("/api/settlement/receipts")
def post_receipt():
    return jsonify(store().post_receipt(_body()))


@settlement.post("/api/settlement/receipts/<receipt_id>/reverse")
def reverse_receipt(receipt_id):
    receipt = store().reverse_receipt(receipt_id)
    return jsonify({"receipt": receipt, "snapshot": store().snapshot()})


@settlement.post("/api/settlement/receipts/<receipt_id>/claim")
def claim(receipt_id):
    data = request.get_json(silent=True) or {}
    bill_id = data.get("bill_id")
    if not bill_id:
        raise DomainIssue("BAD_REQUEST")
    raw_cents = data.get("amount_cents")
    raw_yuan = data.get("amount")
    if raw_cents not in (None, ""):
        amount = cents(raw_cents, "BAD_CLAIM_AMOUNT")
    elif raw_yuan not in (None, ""):
        amount = yuan_to_cents(raw_yuan, "BAD_CLAIM_AMOUNT")
    else:
        amount = None
    receipt = store().claim(receipt_id, bill_id, amount)
    return jsonify({"receipt": receipt, "snapshot": store().snapshot()})


@settlement.post("/api/settlement/seed")
def seed():
    return jsonify(store().seed(sample_payload()))


@settlement.post("/api/settlement/reset")
def reset():
    store().reset()
    return jsonify(store().snapshot())
