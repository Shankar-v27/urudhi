"""SQLite persistence for the ledger and the audit chain.

Design choices, deliberately boring:

* stdlib ``sqlite3``, no ORM — the schema is small and judges can read it;
* one file, zero external services — ``git clone`` and run;
* the audit table is append-only *at the database level*: triggers abort any
  UPDATE or DELETE, so the hash chain's integrity doesn't depend on the
  application remembering to behave.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from urudhi.audit.log import GENESIS_HASH, Actor, AuditEvent, EventKind, make_event
from urudhi.ledger.models import Debtor, Invoice, Payment, PromiseToPay

_SCHEMA = """
CREATE TABLE IF NOT EXISTS debtors (
    id    TEXT PRIMARY KEY,
    data  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id    TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    data  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promises (
    id         TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id),
    state      TEXT NOT NULL,
    data       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id                 TEXT PRIMARY KEY,
    invoice_id         TEXT NOT NULL REFERENCES invoices(id),
    razorpay_event_id  TEXT NOT NULL UNIQUE,  -- webhook idempotency
    data               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY,
    hash       TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    data       TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE INDEX IF NOT EXISTS idx_invoices_state   ON invoices(state);
CREATE INDEX IF NOT EXISTS idx_promises_invoice ON promises(invoice_id, state);
CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        # check_same_thread=False: FastAPI serves sync endpoints from a worker
        # threadpool. Access is one-request-at-a-time in this app, and sqlite3
        # serializes at the C level; this only lifts the same-thread assertion.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- debtors -----------------------------------------------------------

    def put_debtor(self, debtor: Debtor) -> None:
        self._upsert("debtors", debtor.id, debtor.model_dump_json())

    def get_debtor(self, debtor_id: str) -> Debtor:
        return Debtor.model_validate_json(self._get("debtors", debtor_id))

    # -- invoices ----------------------------------------------------------

    def put_invoice(self, invoice: Invoice) -> None:
        self._conn.execute(
            "INSERT INTO invoices (id, state, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (invoice.id, invoice.state.value, invoice.model_dump_json()),
        )
        self._conn.commit()

    def get_invoice(self, invoice_id: str) -> Invoice:
        return Invoice.model_validate_json(self._get("invoices", invoice_id))

    def invoices_in_state(self, *states: str) -> list[Invoice]:
        marks = ",".join("?" * len(states))
        rows = self._conn.execute(
            f"SELECT data FROM invoices WHERE state IN ({marks}) ORDER BY id",
            [str(s) for s in states],
        ).fetchall()
        return [Invoice.model_validate_json(r[0]) for r in rows]

    def all_invoices(self) -> list[Invoice]:
        rows = self._conn.execute("SELECT data FROM invoices ORDER BY id").fetchall()
        return [Invoice.model_validate_json(r[0]) for r in rows]

    # -- promises ----------------------------------------------------------

    def put_promise(self, promise: PromiseToPay) -> None:
        self._conn.execute(
            "INSERT INTO promises (id, invoice_id, state, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (promise.id, promise.invoice_id, promise.state.value, promise.model_dump_json()),
        )
        self._conn.commit()

    def open_promise_for(self, invoice_id: str) -> PromiseToPay | None:
        row = self._conn.execute(
            "SELECT data FROM promises WHERE invoice_id = ? AND state = 'open'",
            (invoice_id,),
        ).fetchone()
        return PromiseToPay.model_validate_json(row[0]) if row else None

    def promises_for(self, invoice_id: str) -> list[PromiseToPay]:
        rows = self._conn.execute(
            "SELECT data FROM promises WHERE invoice_id = ? ORDER BY id", (invoice_id,)
        ).fetchall()
        return [PromiseToPay.model_validate_json(r[0]) for r in rows]

    def all_promises(self) -> list[PromiseToPay]:
        rows = self._conn.execute("SELECT data FROM promises ORDER BY id").fetchall()
        return [PromiseToPay.model_validate_json(r[0]) for r in rows]

    # -- payments ----------------------------------------------------------

    def record_payment_row(self, payment: Payment) -> bool:
        """Insert a payment; returns False if the webhook event was already seen."""
        try:
            self._conn.execute(
                "INSERT INTO payments (id, invoice_id, razorpay_event_id, data) "
                "VALUES (?, ?, ?, ?)",
                (payment.id, payment.invoice_id, payment.razorpay_event_id,
                 payment.model_dump_json()),
            )
        except sqlite3.IntegrityError:
            return False
        self._conn.commit()
        return True

    def payments_for(self, invoice_id: str) -> list[Payment]:
        rows = self._conn.execute(
            "SELECT data FROM payments WHERE invoice_id = ? ORDER BY id", (invoice_id,)
        ).fetchall()
        return [Payment.model_validate_json(r[0]) for r in rows]

    def paid_between(self, invoice_id: str, start: datetime, end_date: str) -> int:
        """Total paise observed on an invoice in [start, end of end_date]."""
        return sum(
            p.amount
            for p in self.payments_for(invoice_id)
            if p.observed_at >= start and p.observed_at.date().isoformat() <= end_date
        )

    def all_payments(self) -> list[Payment]:
        rows = self._conn.execute("SELECT data FROM payments ORDER BY id").fetchall()
        return [Payment.model_validate_json(r[0]) for r in rows]

    # -- audit chain -------------------------------------------------------

    def append_event(
        self,
        *,
        at: datetime,
        actor: Actor,
        kind: EventKind,
        invoice_id: str | None = None,
        debtor_id: str | None = None,
        payload: dict | None = None,
    ) -> AuditEvent:
        """Seal and persist the next event in the chain."""
        row = self._conn.execute(
            "SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, prev_hash = (row[0] + 1, row[1]) if row else (1, GENESIS_HASH)
        event = make_event(
            seq=seq, at=at, actor=actor, kind=kind, prev_hash=prev_hash,
            invoice_id=invoice_id, debtor_id=debtor_id, payload=payload,
        )
        self._conn.execute(
            "INSERT INTO audit_events (seq, hash, prev_hash, data) VALUES (?, ?, ?, ?)",
            (event.seq, event.hash, event.prev_hash, event.model_dump_json()),
        )
        self._conn.commit()
        return event

    def audit_events(self) -> Iterator[AuditEvent]:
        rows = self._conn.execute("SELECT data FROM audit_events ORDER BY seq").fetchall()
        for (data,) in rows:
            yield AuditEvent.model_validate(json.loads(data))

    # -- internals ---------------------------------------------------------

    def _upsert(self, table: str, key: str, data: str) -> None:
        self._conn.execute(
            f"INSERT INTO {table} (id, data) VALUES (?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (key, data),
        )
        self._conn.commit()

    def _get(self, table: str, key: str) -> str:
        row = self._conn.execute(f"SELECT data FROM {table} WHERE id = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(f"{table}: no row with id {key!r}")
        return row[0]
