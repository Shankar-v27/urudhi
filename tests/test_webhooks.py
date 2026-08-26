import hashlib
import hmac
import json
from datetime import UTC, date, datetime

import pytest

from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.models import Invoice, InvoiceState, PromiseState, PromiseToPay
from urudhi.rails.webhooks import (
    IngestStatus,
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


def make_event(event_id="evt_1", amount=100_000, invoice_id="inv_1", kind="payment.captured",
               currency="INR"):
    return {
        "id": event_id,
        "event": kind,
        "payload": {"payment": {"entity": {
            "id": f"pay_rzp_{event_id}",
            "amount": amount, "currency": currency,
            "method": "upi",
            "notes": {"invoice_id": invoice_id} if invoice_id else {},
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

    def test_empty_secret_fails_closed(self):
        body = b"{}"
        forged = hmac.new(b"", body, hashlib.sha256).hexdigest()
        with pytest.raises(WebhookError, match="not configured"):
            verify_signature(body, forged, "")


class TestIngestion:
    def test_full_payment_closes_invoice_and_audits(self, store):
        result = ingest_payment_event(store, make_event())
        assert result.status is IngestStatus.RECORDED
        assert result.payment.amount == 100_000
        assert store.get_invoice("inv_1").state is InvoiceState.PAID
        kinds = [e.kind for e in store.audit_events()]
        assert kinds == [EventKind.PAYMENT_OBSERVED]
        assert verify_chain(store.audit_events()) == 1

    def test_replay_is_dropped(self, store):
        assert ingest_payment_event(store, make_event()).status is IngestStatus.RECORDED
        assert ingest_payment_event(store, make_event()).status is IngestStatus.REPLAY
        assert store.get_invoice("inv_1").state is InvoiceState.PAID
        assert len(store.all_payments()) == 1
        assert store.audit_count() == 1  # a replay leaves no trace in the ledger

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

    def test_untagged_payment_is_unmatched_not_an_error(self, store):
        result = ingest_payment_event(store, make_event(invoice_id=None))
        assert result.status is IngestStatus.UNMATCHED
        events = list(store.audit_events())
        assert events[-1].kind is EventKind.PAYMENT_UNMATCHED
        assert events[-1].payload["razorpay_payment_id"] == "pay_rzp_evt_1"
        # The ruling is remembered: a redelivery is a replay, not a second audit row.
        assert ingest_payment_event(store, make_event(invoice_id=None)).status is IngestStatus.REPLAY
        assert store.audit_count() == 1

    def test_unknown_invoice_is_unmatched_with_diagnostics(self, store):
        result = ingest_payment_event(store, make_event(invoice_id="inv_999"))
        assert result.status is IngestStatus.UNMATCHED and "inv_999" in result.reason
        assert store.audit_count() == 1

    def test_late_event_after_paid_is_rejected_and_audited(self, store):
        ingest_payment_event(store, make_event())
        late = ingest_payment_event(store, make_event(event_id="evt_2", amount=100))
        assert late.status is IngestStatus.REJECTED and "already settled" in late.reason
        assert list(store.audit_events())[-1].kind is EventKind.PAYMENT_REJECTED
        assert store.get_invoice("inv_1").amount_paid == 100_000
        # and it too is remembered
        assert ingest_payment_event(store, make_event(event_id="evt_2", amount=100)).status is IngestStatus.REPLAY

    def test_overpayment_is_rejected(self, store):
        result = ingest_payment_event(store, make_event(amount=100_001))
        assert result.status is IngestStatus.REJECTED and "exceeds balance" in result.reason

    def test_bad_currency_and_amount_rejected(self, store):
        assert ingest_payment_event(store, make_event(currency="USD")).status is IngestStatus.REJECTED
        assert ingest_payment_event(store, make_event(event_id="evt_2", amount=0)).status is IngestStatus.REJECTED
        weird = make_event(event_id="evt_3")
        weird["payload"]["payment"]["entity"]["amount"] = "100000"
        assert ingest_payment_event(store, weird).status is IngestStatus.REJECTED
        assert store.get_invoice("inv_1").amount_paid == 0

    def test_unhandled_event_type_refused(self, store):
        event = make_event()
        event["event"] = "refund.processed"
        with pytest.raises(WebhookError, match="unhandled"):
            ingest_payment_event(store, event)

    def test_event_without_id_refused(self, store):
        event = make_event(event_id="")
        with pytest.raises(WebhookError, match="no id"):
            ingest_payment_event(store, event)


class TestResolution:
    def test_payment_link_paid_resolves_via_link_entity(self, store):
        event = make_event(kind="payment_link.paid", invoice_id=None)
        event["payload"]["payment_link"] = {"entity": {"id": "plink_1", "reference_id": "inv_1",
                                                       "notes": {"invoice_id": "inv_1"}}}
        assert ingest_payment_event(store, event).status is IngestStatus.RECORDED

    def test_smart_collect_resolves_via_virtual_account_notes(self, store):
        event = make_event(kind="virtual_account.credited", invoice_id=None)
        event["payload"]["virtual_account"] = {"entity": {"id": "va_1", "notes": {"invoice_id": "inv_1"}}}
        assert ingest_payment_event(store, event).status is IngestStatus.RECORDED
        assert store.get_invoice("inv_1").state is InvoiceState.PAID

    def test_smart_collect_resolves_via_stored_virtual_account_id(self, store):
        store.put_invoice(store.get_invoice("inv_1").model_copy(
            update={"razorpay_virtual_account_id": "va_77"}))
        event = make_event(kind="virtual_account.credited", invoice_id=None)
        event["payload"]["virtual_account"] = {"entity": {"id": "va_77", "notes": {}}}
        assert ingest_payment_event(store, event).status is IngestStatus.RECORDED

    def test_smart_collect_without_any_reference_is_unmatched(self, store):
        event = make_event(kind="virtual_account.credited", invoice_id=None)
        event["payload"]["virtual_account"] = {"entity": {"id": "va_unknown", "notes": {}}}
        assert ingest_payment_event(store, event).status is IngestStatus.UNMATCHED
