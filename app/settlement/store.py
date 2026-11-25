"""SQLite persistence for bills, receipts and their allocations."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from app.errors import DomainIssue
from app.settlement.engine import allocate_receipt, outstanding, rebuild_paid
from app.settlement.model import REVERSED, Bill, Receipt, receipt_from_payload


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SettlementStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settlement_bills(
              bill_id TEXT PRIMARY KEY,
              customer_id TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              due_date TEXT NOT NULL,
              signed INTEGER NOT NULL,
              disputed INTEGER NOT NULL,
              label TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settlement_receipts(
              receipt_id TEXT PRIMARY KEY,
              identity TEXT UNIQUE NOT NULL,
              customer_id TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              received_at TEXT NOT NULL,
              bank_ref TEXT NOT NULL DEFAULT '',
              designations TEXT NOT NULL,
              status TEXT NOT NULL,
              allocations TEXT NOT NULL,
              pending TEXT NOT NULL,
              fingerprint TEXT NOT NULL,
              seq INTEGER NOT NULL,
              reversed_at TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS settlement_receipts_customer
              ON settlement_receipts(customer_id, received_at);
            ''')

    # ---------------------------------------------------------------- reads
    @staticmethod
    def _bill_dict(row):
        return {
            "bill_id": row["bill_id"],
            "customer_id": row["customer_id"],
            "amount_cents": row["amount_cents"],
            "due_date": date.fromisoformat(row["due_date"]),
            "signed": bool(row["signed"]),
            "disputed": bool(row["disputed"]),
            "label": row["label"],
            "paid_cents": 0,
        }

    def _bills(self, db, customer_id=None):
        if customer_id:
            rows = db.execute("SELECT * FROM settlement_bills WHERE customer_id=? ORDER BY due_date, bill_id",
                              (customer_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM settlement_bills ORDER BY customer_id, due_date, bill_id").fetchall()
        return {row["bill_id"]: self._bill_dict(row) for row in rows}

    @staticmethod
    def _receipt(row):
        return Receipt(
            receipt_id=row["receipt_id"],
            customer_id=row["customer_id"],
            amount_cents=row["amount_cents"],
            received_at=datetime.fromisoformat(row["received_at"]),
            designations=json.loads(row["designations"]),
            bank_ref=row["bank_ref"],
            status=row["status"],
            allocations=json.loads(row["allocations"]),
            pending=json.loads(row["pending"]),
            fingerprint=row["fingerprint"],
            identity=row["identity"],
            seq=row["seq"],
            reversed_at=row["reversed_at"],
        )

    def _receipts(self, db, customer_id=None):
        if customer_id:
            rows = db.execute("SELECT * FROM settlement_receipts WHERE customer_id=? ORDER BY seq",
                              (customer_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM settlement_receipts ORDER BY seq").fetchall()
        return [self._receipt(row) for row in rows]

    def snapshot(self, customer_id=None, as_of=None):
        from app.settlement.service import build_snapshot
        with self.connect() as db:
            bills = self._bills(db, customer_id)
            receipts = self._receipts(db, customer_id)
        return build_snapshot(bills, receipts, as_of=as_of, customer_id=customer_id)

    # ---------------------------------------------------------------- bills
    def upsert_bills(self, bills):
        """Insert or update bills.

        A bill may only be shrunk to an amount that still covers what active
        receipts have already allocated to it, otherwise the ledger would
        carry a negative balance.
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {row["bill_id"]: row for row in db.execute("SELECT * FROM settlement_bills").fetchall()}
            paid = rebuild_paid(self._bills(db), self._receipts(db))
            stamp = _now()
            for bill in bills:
                old = existing.get(bill.bill_id)
                current_paid = int(paid.get(bill.bill_id, {}).get("paid_cents", 0))
                if old is not None:
                    if bill.customer_id != old["customer_id"] and current_paid:
                        raise DomainIssue("BILL_CUSTOMER_LOCKED", 409)
                    if bill.amount_cents < current_paid:
                        raise DomainIssue("BILL_AMOUNT_TOO_SMALL", 409)
                    if current_paid and (not bill.signed or bill.disputed):
                        raise DomainIssue(
                            "BILL_HAS_PAYMENTS", 409,
                            detail="账单已有回款抵扣，先冲回相关回款再标记未签或争议")
                    db.execute(
                        "UPDATE settlement_bills SET customer_id=?, amount_cents=?, due_date=?, signed=?, disputed=?, label=? WHERE bill_id=?",
                        (bill.customer_id, bill.amount_cents, bill.due_date.isoformat(), int(bill.signed),
                         int(bill.disputed), bill.label, bill.bill_id),
                    )
                else:
                    db.execute(
                        "INSERT INTO settlement_bills(bill_id,customer_id,amount_cents,due_date,signed,disputed,label,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (bill.bill_id, bill.customer_id, bill.amount_cents, bill.due_date.isoformat(),
                         int(bill.signed), int(bill.disputed), bill.label, stamp),
                    )
        return self.snapshot()

    # ------------------------------------------------------------- receipts
    def post_receipt(self, payload):
        """Post one bank receipt, idempotently.

        Replaying the same logical receipt (same identity) returns the stored
        record with ``replayed=True`` and never allocates twice.
        """
        receipt = receipt_from_payload(payload)
        content_only = receipt.identity.startswith("fp:") and "@" not in receipt.identity
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM settlement_receipts WHERE identity=?",
                                  (receipt.identity,)).fetchone()
            if existing is None:
                existing = self._content_match(db, receipt, content_only)
            if existing is not None:
                return self._replay(existing, receipt)
            if db.execute("SELECT 1 FROM settlement_receipts WHERE receipt_id=?",
                          (receipt.receipt_id,)).fetchone():
                raise DomainIssue("RECEIPT_CONFLICT", 409,
                                  detail="回款编号已存在且内容不同")
            bills = rebuild_paid(self._bills(db), self._receipts(db))
            receipt.seq = int(db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM settlement_receipts").fetchone()[0])
            allocations, pending = allocate_receipt(receipt, bills)
            receipt.allocations = allocations
            receipt.pending = pending
            try:
                db.execute(
                    "INSERT INTO settlement_receipts(receipt_id,identity,customer_id,amount_cents,received_at,bank_ref,designations,status,allocations,pending,fingerprint,seq,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (receipt.receipt_id, receipt.identity, receipt.customer_id, receipt.amount_cents,
                     receipt.received_at.isoformat(), receipt.bank_ref,
                     json.dumps(receipt.designations, ensure_ascii=False),
                     receipt.status, json.dumps(receipt.allocations, ensure_ascii=False),
                     json.dumps(receipt.pending, ensure_ascii=False), receipt.fingerprint, receipt.seq, _now()),
                )
            except sqlite3.IntegrityError:
                existing = db.execute("SELECT * FROM settlement_receipts WHERE identity=?",
                                      (receipt.identity,)).fetchone()
                if existing is not None:
                    return self._replay(existing, receipt)
                raise DomainIssue("RECEIPT_CONFLICT", 409)
        return {"receipt": receipt.to_dict(), "replayed": False}

    @staticmethod
    def _content_match(db, receipt, content_only):
        """Match a retry whose timestamp differs from the stored copy.

        Timestamps are often dropped on retry and a timestamped re-import can
        follow a timestamp-less original, so identity alone is not enough.
        Content-only retries match any stored receipt of the same content and
        are rejected when that is ambiguous; timestamped retries only match a
        stored *timestamp-less* receipt.  Genuine same-amount payments must
        therefore carry a bank_ref (or explicit receipt_id).
        """
        same = db.execute("SELECT * FROM settlement_receipts WHERE fingerprint=?",
                          (receipt.fingerprint,)).fetchall()
        if content_only:
            if len(same) > 1:
                raise DomainIssue("RECEIPT_AMBIGUOUS", 409,
                                  detail="同内容回款不止一笔，请提供回款编号、流水号或到账时间")
            return same[0] if same else None
        loose = [row for row in same if "@" not in row["identity"]]
        if len(loose) > 1:
            raise DomainIssue("RECEIPT_AMBIGUOUS", 409,
                              detail="同内容回款不止一笔，请提供回款编号、流水号或到账时间")
        return loose[0] if loose else None

    @staticmethod
    def _replay(row, incoming):
        if row["fingerprint"] != incoming.fingerprint:
            raise DomainIssue("RECEIPT_CONFLICT", 409,
                              detail="回款编号相同但内容不同，请核对后重新入账")
        return {"receipt": SettlementStore._receipt(row).to_dict(), "replayed": True}

    def reverse_receipt(self, receipt_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM settlement_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
            if row is None:
                raise DomainIssue("RECEIPT_NOT_FOUND", 404)
            if row["status"] == REVERSED:
                raise DomainIssue("RECEIPT_ALREADY_REVERSED", 409)
            db.execute("UPDATE settlement_receipts SET status=?, reversed_at=? WHERE receipt_id=?",
                       (REVERSED, _now(), receipt_id))
            row = db.execute("SELECT * FROM settlement_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
        return self._receipt(row).to_dict()

    def claim(self, receipt_id, bill_id, amount_cents=None):
        """Turn pending money of one receipt into an allocation against a bill."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM settlement_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
            if row is None:
                raise DomainIssue("RECEIPT_NOT_FOUND", 404)
            receipt = self._receipt(row)
            if not receipt.active:
                raise DomainIssue("RECEIPT_REVERSED", 409, detail="已冲回的回款不能认领")
            bill_row = db.execute("SELECT * FROM settlement_bills WHERE bill_id=?", (bill_id,)).fetchone()
            if bill_row is None:
                raise DomainIssue("BILL_NOT_FOUND", 404)
            if bill_row["customer_id"] != receipt.customer_id:
                raise DomainIssue("CUSTOMER_MISMATCH", 409)
            if not bill_row["signed"]:
                raise DomainIssue("BILL_UNSIGNED", 409)
            if bill_row["disputed"]:
                raise DomainIssue("BILL_DISPUTED", 409)
            bills = rebuild_paid(self._bills(db), self._receipts(db))
            bill = bills[bill_id]
            # Money the payer earmarked for a specific bill may only be claimed
            # onto that bill; loose money (no eligible bill at posting time)
            # may go to any eligible bill.
            claimable = [item for item in receipt.pending if item.get("bill_id") in (None, bill_id)]
            available = sum(int(item["amount_cents"]) for item in claimable)
            if available <= 0:
                raise DomainIssue("NOTHING_PENDING", 409,
                                  detail="该回款的待认领款指定了其他账单，不能改抵本账单")
            capacity = outstanding(bill)
            if capacity <= 0:
                raise DomainIssue("BILL_SETTLED", 409)
            amount = available if amount_cents is None else int(amount_cents)
            if amount <= 0:
                raise DomainIssue("BAD_CLAIM_AMOUNT")
            if amount > available or amount > capacity:
                raise DomainIssue("BAD_CLAIM_AMOUNT",
                                  detail="认领金额超过待认领额或账单未结余额")
            remaining = amount
            # Drain buckets earmarked for this bill first, then loose money.
            drain_order = sorted(
                (index for index, item in enumerate(receipt.pending) if item.get("bill_id") in (None, bill_id)),
                key=lambda index: (receipt.pending[index].get("bill_id") is None, index),
            )
            buckets = [dict(item) for item in receipt.pending]
            for index in drain_order:
                take = min(int(buckets[index]["amount_cents"]), remaining)
                buckets[index]["amount_cents"] = int(buckets[index]["amount_cents"]) - take
                remaining -= take
            pending = [item for item in buckets if item["amount_cents"] > 0]
            allocations = [dict(item) for item in receipt.allocations]
            allocations.append({"bill_id": bill_id, "amount_cents": amount, "source": "CLAIM"})
            db.execute("UPDATE settlement_receipts SET allocations=?, pending=? WHERE receipt_id=?",
                       (json.dumps(allocations, ensure_ascii=False),
                        json.dumps(pending, ensure_ascii=False), receipt_id))
            updated = db.execute("SELECT * FROM settlement_receipts WHERE receipt_id=?",
                                 (receipt_id,)).fetchone()
        return self._receipt(updated).to_dict()

    # ----------------------------------------------------------------- seed
    def seed(self, payload):
        bills = [Bill.parse(item) for item in payload.get("bills", [])]
        if bills:
            self.upsert_bills(bills)
        for item in payload.get("receipts", []):
            self.post_receipt(item)
        for item in payload.get("receipts", []):
            if item.get("reversed"):
                receipt_id = item.get("receipt_id") or self._locate(item)
                if receipt_id:
                    try:
                        self.reverse_receipt(receipt_id)
                    except DomainIssue:
                        pass
        return self.snapshot()

    def _locate(self, payload):
        receipt = receipt_from_payload(payload)
        return receipt.receipt_id

    def reset(self):
        with self.connect() as db:
            db.execute("DELETE FROM settlement_receipts")
            db.execute("DELETE FROM settlement_bills")
