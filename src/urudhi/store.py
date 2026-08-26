"""SQLite persistence for the ledger and the audit chain.

Design choices, deliberately boring:

* stdlib ``sqlite3``, no ORM — the schema is small and judges can read it;
* one file, zero external services — ``git clone`` and run;
* the audit table is append-only *at the database level*: triggers abort any
  UPDATE or DELETE, so the hash chain's integrity doesn't depend on the
  application remembering to behave;
* one shared connection, one process-wide re-entrant lock around **every**
  statement — reads included, because a COMMIT on another thread resets this
  connection's open cursors — and audit appends run inside ``BEGIN
  IMMEDIATE``, so concurrent webhooks or parallel agent turns cannot read the
  same sequence number and fork the chain;
* outbound messages are *claimed* in the database before they are sent
  (``outbound_messages``), so a crash between "sent" and "audited" cannot
  produce a second send — the claim is the attempt.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from urudhi.audit.log import GENESIS_HASH, Actor, AuditEvent, EventKind, make_event
from urudhi.ledger.models import Concession, Debtor, Invoice, Payment, PromiseToPay

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

CREATE TABLE IF NOT EXISTS concessions (
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

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id   TEXT PRIMARY KEY,               -- every delivery we ever ruled on
    status     TEXT NOT NULL,                  -- recorded / unmatched / rejected
    at         TEXT NOT NULL,
    data       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_messages (
    key        TEXT PRIMARY KEY,               -- idempotency key: invoice:day:attempt
    invoice_id TEXT NOT NULL,
    day        TEXT NOT NULL,                  -- local calendar day of the attempt
    at         TEXT NOT NULL,
    channel    TEXT NOT NULL,
    state      TEXT NOT NULL,                  -- claimed / sent / failed
    data       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY,
    hash       TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    invoice_id TEXT,
    kind       TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_invoices_state     ON invoices(state);
CREATE INDEX IF NOT EXISTS idx_promises_invoice   ON promises(invoice_id, state);
CREATE INDEX IF NOT EXISTS idx_concessions_invoice ON concessions(invoice_id, state);
CREATE INDEX IF NOT EXISTS idx_payments_invoice   ON payments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_outbound_invoice   ON outbound_messages(invoice_id, day);
CREATE INDEX IF NOT EXISTS idx_audit_invoice      ON audit_events(invoice_id, seq);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        # isolation_level=None: autocommit, so transactions are explicit
        # (BEGIN IMMEDIATE ... COMMIT) where they matter. check_same_thread=False:
        # FastAPI threadpools and parallel agent turns share this connection;
        # the RLock serializes every statement on it.
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize a multi-statement write; nested calls join the outer transaction."""
        with self._lock:
            nested = self._conn.in_transaction
            if not nested:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                if not nested:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                if not nested:
                    self._conn.execute("COMMIT")

    # -- locked primitives ---------------------------------------------------

    def _run(self, sql: str, params: tuple | list = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def _rows(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _row(self, sql: str, params: tuple | list = ()) -> tuple | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # -- debtors -----------------------------------------------------------

    def put_debtor(self, debtor: Debtor) -> None:
        self._upsert("debtors", debtor.id, debtor.model_dump_json())

    def get_debtor(self, debtor_id: str) -> Debtor:
        return Debtor.model_validate_json(self._get("debtors", debtor_id))

    def all_debtors(self) -> list[Debtor]:
        rows = self._rows("SELECT data FROM debtors ORDER BY id")
        return [Debtor.model_validate_json(r[0]) for r in rows]

    def find_debtor_by_email(self, email: str) -> Debtor | None:
        for debtor in self.all_debtors():
            if debtor.email.lower() == email.lower():
                return debtor
        return None

    # -- invoices ----------------------------------------------------------

    def put_invoice(self, invoice: Invoice) -> None:
        self._run(
            "INSERT INTO invoices (id, state, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (invoice.id, invoice.state.value, invoice.model_dump_json()),
        )

    def get_invoice(self, invoice_id: str) -> Invoice:
        return Invoice.model_validate_json(self._get("invoices", invoice_id))

    def find_invoice_by_number(self, number: str) -> Invoice | None:
        for invoice in self.all_invoices():
            if invoice.number == number:
                return invoice
        return None

    def invoices_in_state(self, *states: str) -> list[Invoice]:
        marks = ",".join("?" * len(states))
        rows = self._rows(
            f"SELECT data FROM invoices WHERE state IN ({marks}) ORDER BY id",
            [str(s) for s in states],
        )
        return [Invoice.model_validate_json(r[0]) for r in rows]

    def invoices_for_debtor(self, debtor_id: str) -> list[Invoice]:
        return [i for i in self.all_invoices() if i.debtor_id == debtor_id]

    def all_invoices(self) -> list[Invoice]:
        rows = self._rows("SELECT data FROM invoices ORDER BY id")
        return [Invoice.model_validate_json(r[0]) for r in rows]

    # -- promises ----------------------------------------------------------

    def put_promise(self, promise: PromiseToPay) -> None:
        self._run(
            "INSERT INTO promises (id, invoice_id, state, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (promise.id, promise.invoice_id, promise.state.value, promise.model_dump_json()),
        )

    def open_promise_for(self, invoice_id: str) -> PromiseToPay | None:
        row = self._row(
            "SELECT data FROM promises WHERE invoice_id = ? AND state = 'open'", (invoice_id,)
        )
        return PromiseToPay.model_validate_json(row[0]) if row else None

    def promises_for(self, invoice_id: str) -> list[PromiseToPay]:
        rows = self._rows("SELECT data FROM promises WHERE invoice_id = ? ORDER BY id", (invoice_id,))
        return [PromiseToPay.model_validate_json(r[0]) for r in rows]

    def all_promises(self) -> list[PromiseToPay]:
        rows = self._rows("SELECT data FROM promises ORDER BY id")
        return [PromiseToPay.model_validate_json(r[0]) for r in rows]

    # -- concessions -------------------------------------------------------

    def put_concession(self, concession: Concession) -> None:
        self._run(
            "INSERT INTO concessions (id, invoice_id, state, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, data = excluded.data",
            (concession.id, concession.invoice_id, concession.state.value,
             concession.model_dump_json()),
        )

    def live_concession_for(self, invoice_id: str) -> Concession | None:
        row = self._row(
            "SELECT data FROM concessions WHERE invoice_id = ? "
            "AND state IN ('offered', 'accepted') ORDER BY id DESC LIMIT 1",
            (invoice_id,),
        )
        return Concession.model_validate_json(row[0]) if row else None

    def concessions_for(self, invoice_id: str) -> list[Concession]:
        rows = self._rows(
            "SELECT data FROM concessions WHERE invoice_id = ? ORDER BY id", (invoice_id,)
        )
        return [Concession.model_validate_json(r[0]) for r in rows]

    def all_concessions(self) -> list[Concession]:
        rows = self._rows("SELECT data FROM concessions ORDER BY id")
        return [Concession.model_validate_json(r[0]) for r in rows]

    # -- payments ----------------------------------------------------------

    def record_payment_row(self, payment: Payment) -> bool:
        """Insert a payment; returns False if the webhook event was already seen."""
        try:
            self._run(
                "INSERT INTO payments (id, invoice_id, razorpay_event_id, data) VALUES (?, ?, ?, ?)",
                (payment.id, payment.invoice_id, payment.razorpay_event_id,
                 payment.model_dump_json()),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def payments_for(self, invoice_id: str) -> list[Payment]:
        rows = self._rows("SELECT data FROM payments WHERE invoice_id = ? ORDER BY id", (invoice_id,))
        return [Payment.model_validate_json(r[0]) for r in rows]

    def paid_between(self, invoice_id: str, start: datetime, end_date: str) -> int:
        """Total paise observed on an invoice in [start, end of end_date]."""
        return sum(
            p.amount
            for p in self.payments_for(invoice_id)
            if p.observed_at >= start and p.observed_at.date().isoformat() <= end_date
        )

    def paid_since(self, invoice_id: str, start: datetime) -> int:
        return sum(p.amount for p in self.payments_for(invoice_id) if p.observed_at >= start)

    def all_payments(self) -> list[Payment]:
        rows = self._rows("SELECT data FROM payments ORDER BY id")
        return [Payment.model_validate_json(r[0]) for r in rows]

    # -- webhook deliveries ------------------------------------------------

    def record_webhook_event(self, event_id: str, status: str, at: datetime,
                             data: dict[str, Any]) -> bool:
        """Remember a ruling on a delivery; False if this event id was already ruled on."""
        try:
            self._run(
                "INSERT INTO webhook_events (event_id, status, at, data) VALUES (?, ?, ?, ?)",
                (event_id, status, at.isoformat(), json.dumps(data, ensure_ascii=False)),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def webhook_event_status(self, event_id: str) -> str | None:
        row = self._row("SELECT status FROM webhook_events WHERE event_id = ?", (event_id,))
        return row[0] if row else None

    # -- outbound messages (send idempotency) ------------------------------

    def claim_outbound(self, key: str, invoice_id: str, day: str, at: datetime,
                       channel: str, data: dict[str, Any]) -> bool:
        """Claim an attempt slot before sending. False if the key already exists."""
        try:
            self._run(
                "INSERT INTO outbound_messages (key, invoice_id, day, at, channel, state, data) "
                "VALUES (?, ?, ?, ?, ?, 'claimed', ?)",
                (key, invoice_id, day, at.isoformat(), channel, json.dumps(data, ensure_ascii=False)),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def mark_outbound(self, key: str, state: str) -> None:
        self._run("UPDATE outbound_messages SET state = ? WHERE key = ?", (state, key))

    def outbound_for(self, invoice_id: str) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT key, day, at, channel, state, data FROM outbound_messages "
            "WHERE invoice_id = ? ORDER BY at", (invoice_id,)
        )
        return [
            {"key": k, "day": d, "at": datetime.fromisoformat(a), "channel": c,
             "state": s, **json.loads(data)}
            for (k, d, a, c, s, data) in rows
        ]

    def attempt_facts(self, invoice_id: str, day: str,
                      since: datetime | None = None) -> tuple[int, int, datetime | None]:
        """(attempts_total, attempts_on_day, last_attempt_at) from claimed slots.

        Claimed-but-unsent rows count: a crash after delivery must not earn
        the debtor a second message. ``since`` lets a human release reset
        the count without rewriting history.
        """
        rows = self.outbound_for(invoice_id)
        if since is not None:
            rows = [r for r in rows if r["at"] >= since]
        total = len(rows)
        today = sum(1 for r in rows if r["day"] == day)
        last = max((r["at"] for r in rows), default=None)
        return total, today, last

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
        """Seal and persist the next event in the chain, atomically."""
        with self.transaction():
            row = self._conn.execute(
                "SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq, prev_hash = (row[0] + 1, row[1]) if row else (1, GENESIS_HASH)
            event = make_event(
                seq=seq, at=at, actor=actor, kind=kind, prev_hash=prev_hash,
                invoice_id=invoice_id, debtor_id=debtor_id, payload=payload,
            )
            self._conn.execute(
                "INSERT INTO audit_events (seq, hash, prev_hash, invoice_id, kind, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event.seq, event.hash, event.prev_hash, invoice_id, kind.value,
                 event.model_dump_json()),
            )
        return event

    def audit_events(self) -> Iterator[AuditEvent]:
        rows = self._rows("SELECT data FROM audit_events ORDER BY seq")
        for (data,) in rows:
            yield AuditEvent.model_validate(json.loads(data))

    def events_for(self, invoice_id: str, kind: EventKind | None = None) -> list[AuditEvent]:
        if kind is None:
            rows = self._rows(
                "SELECT data FROM audit_events WHERE invoice_id = ? ORDER BY seq", (invoice_id,)
            )
        else:
            rows = self._rows(
                "SELECT data FROM audit_events WHERE invoice_id = ? AND kind = ? ORDER BY seq",
                (invoice_id, kind.value),
            )
        return [AuditEvent.model_validate(json.loads(r[0])) for r in rows]

    def events_of_kind(self, kind: EventKind) -> list[AuditEvent]:
        rows = self._rows("SELECT data FROM audit_events WHERE kind = ? ORDER BY seq", (kind.value,))
        return [AuditEvent.model_validate(json.loads(r[0])) for r in rows]

    def audit_count(self) -> int:
        return self._row("SELECT COUNT(*) FROM audit_events")[0]

    # -- internals ---------------------------------------------------------

    def _upsert(self, table: str, key: str, data: str) -> None:
        self._run(
            f"INSERT INTO {table} (id, data) VALUES (?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (key, data),
        )

    def _get(self, table: str, key: str) -> str:
        row = self._row(f"SELECT data FROM {table} WHERE id = ?", (key,))
        if row is None:
            raise KeyError(f"{table}: no row with id {key!r}")
        return row[0]
