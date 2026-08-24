import sqlite3
from datetime import date, datetime

import pytest

from urudhi.audit.log import Actor, EventKind, verify_chain
from urudhi.ledger.models import Channel, Debtor, Invoice, InvoiceState, Payment, PromiseToPay
from urudhi.store import Store


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def make_invoice(store, **overrides):
    defaults = dict(
        id="inv_1", debtor_id="deb_1", number="URU/2026/001",
        amount=100_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
    )
    invoice = Invoice(**{**defaults, **overrides})
    store.put_invoice(invoice)
    return invoice


class TestLedgerRoundTrips:
    def test_debtor(self, store):
        debtor = Debtor(
            id="deb_1", name="Kumar Textiles", contact_name="Kumar",
            phone="+919800000001", email="kumar@example.in",
            preferred_channel=Channel.WHATSAPP, language="ta",
        )
        store.put_debtor(debtor)
        assert store.get_debtor("deb_1") == debtor

    def test_invoice_state_query(self, store):
        make_invoice(store)
        make_invoice(store, id="inv_2", state=InvoiceState.PAID, amount_paid=100_000)
        outstanding = store.invoices_in_state(InvoiceState.OUTSTANDING)
        assert [i.id for i in outstanding] == ["inv_1"]

    def test_open_promise_lookup(self, store):
        make_invoice(store)
        promise = PromiseToPay(
            id="ptp_1", invoice_id="inv_1", debtor_id="deb_1", amount=100_000,
            promised_on=date(2026, 8, 28), made_at=datetime(2026, 8, 24, 11, 0),
            channel=Channel.WHATSAPP, verbatim="Friday varaikkum kudukiren.",
            confidence=0.8,
        )
        store.put_promise(promise)
        assert store.open_promise_for("inv_1") == promise
        assert store.open_promise_for("inv_9") is None

    def test_payment_webhook_idempotency(self, store):
        make_invoice(store)
        payment = Payment(
            id="pay_1", invoice_id="inv_1", amount=40_000, method="upi",
            razorpay_payment_id="pay_rzp_1", razorpay_event_id="evt_1",
            observed_at=datetime(2026, 8, 26, 15, 30),
        )
        assert store.record_payment_row(payment) is True
        replay = payment.model_copy(update={"id": "pay_2"})
        assert store.record_payment_row(replay) is False  # same webhook event
        assert len(store.payments_for("inv_1")) == 1


class TestAuditPersistence:
    def append(self, store, n=3):
        for i in range(1, n + 1):
            store.append_event(
                at=datetime(2026, 8, 24, 10, 0, i), actor=Actor.AGENT,
                kind=EventKind.MESSAGE_SENT, invoice_id="inv_1", payload={"n": i},
            )

    def test_chain_persists_and_verifies(self, store):
        self.append(store)
        assert verify_chain(store.audit_events()) == 3

    def test_update_is_blocked_by_trigger(self, store):
        self.append(store, 1)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute("UPDATE audit_events SET data = '{}' WHERE seq = 1")

    def test_delete_is_blocked_by_trigger(self, store):
        self.append(store, 1)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._conn.execute("DELETE FROM audit_events WHERE seq = 1")
