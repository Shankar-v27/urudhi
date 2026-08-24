import hashlib
import hmac
import json
from datetime import UTC, date, datetime

import pytest

from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.models import Invoice, InvoiceState, PromiseState, PromiseToPay
from urudhi.rails.webhooks import (
    WebhookError,
    ingest_payment_event,
    verify_signature,
)
from urudhi.store import Store

SECRET = "whsec_test"


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.put_invoice(Invoice(
            id="inv_1", debtor_id="deb_1", number="URU/2026/001",
            amount=100_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
        ))
        yield s


def make_event(event_id="evt_1", amount=100_000, invoice_id="inv_1"):
    return {
        "id": event_id,
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": f"pay_rzp_{event_id}",
            "amount": amount,
            "method": "upi",
            "notes": {"invoice_id": invoice_id},
        }}},
    }


class TestSignature:
    def test_valid_signature_passes(self):
        body = json.dumps(make_event()).encode()
        signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        verify_signature(body, signature, SECRET)  # no raise

    def test_bad_signature_rejected(self):
        with pytest.raises(WebhookError, match="signature"):
            verify_signature(b"{}", "deadbeef", SECRET)


class TestIngestion:
    def test_full_payment_closes_invoice_and_audits(self, store):
        payment = ingest_payment_event(store, make_event())
        assert payment.amount == 100_000
        assert store.get_invoice("inv_1").state is InvoiceState.PAID
        kinds = [e.kind for e in store.audit_events()]
        assert kinds == [EventKind.PAYMENT_OBSERVED]
        assert verify_chain(store.audit_events()) == 1

    def test_replay_is_dropped(self, store):
        assert ingest_payment_event(store, make_event()) is not None
        assert ingest_payment_event(store, make_event()) is None
        assert store.get_invoice("inv_1").state is InvoiceState.PAID
        assert len(store.all_payments()) == 1

    def test_on_time_payment_marks_promise_kept(self, store):
        invoice = store.get_invoice("inv_1").model_copy(
            update={"state": InvoiceState.PROMISED}
        )
        store.put_invoice(invoice)
        store.put_promise(PromiseToPay(
            id="ptp_1", invoice_id="inv_1", debtor_id="deb_1", amount=100_000,
            promised_on=date(2026, 8, 28), made_at=datetime(2026, 8, 24, 11, 0, tzinfo=UTC),
            channel="whatsapp", verbatim="Friday kudukiren.", confidence=0.9,
        ))
        ingest_payment_event(
            store, make_event(), now=datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
        )
        promises = store.promises_for("inv_1")
        assert promises[0].state is PromiseState.KEPT
        kinds = [e.kind for e in store.audit_events()]
        assert EventKind.PROMISE_RESOLVED in kinds

    def test_untagged_payment_refused(self, store):
        event = make_event()
        event["payload"]["payment"]["entity"]["notes"] = {}
        with pytest.raises(WebhookError, match="invoice_id"):
            ingest_payment_event(store, event)

    def test_unhandled_event_type_refused(self, store):
        event = make_event()
        event["event"] = "refund.processed"
        with pytest.raises(WebhookError, match="unhandled"):
            ingest_payment_event(store, event)
