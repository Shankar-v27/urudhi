from datetime import date, datetime

import pytest

from urudhi.ledger.models import (
    Channel,
    Invoice,
    InvoiceState,
    Payment,
    PromiseState,
    PromiseToPay,
)
from urudhi.ledger.transitions import (
    InvalidTransition,
    escalate,
    expire_promise,
    record_dispute,
    record_payment,
    record_promise,
    stop_contact,
)


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        id="inv_1",
        debtor_id="deb_1",
        number="URU/2026/001",
        amount=100_000,  # ₹1,000
        issued_on=date(2026, 6, 1),
        due_on=date(2026, 7, 1),
    )
    return Invoice(**{**defaults, **overrides})


def make_promise(**overrides) -> PromiseToPay:
    defaults = dict(
        id="ptp_1",
        invoice_id="inv_1",
        debtor_id="deb_1",
        amount=100_000,
        promised_on=date(2026, 8, 28),
        made_at=datetime(2026, 8, 24, 11, 0),
        channel=Channel.WHATSAPP,
        verbatim="I will clear the full amount by Friday.",
        confidence=0.9,
    )
    return PromiseToPay(**{**defaults, **overrides})


def make_payment(**overrides) -> Payment:
    defaults = dict(
        id="pay_1",
        invoice_id="inv_1",
        amount=100_000,
        method="upi",
        razorpay_payment_id="pay_rzp_1",
        razorpay_event_id="evt_1",
        observed_at=datetime(2026, 8, 26, 15, 30),
    )
    return Payment(**{**defaults, **overrides})


class TestRecordPromise:
    def test_outstanding_invoice_becomes_promised(self):
        invoice, promise, superseded = record_promise(make_invoice(), make_promise())
        assert invoice.state is InvoiceState.PROMISED
        assert promise.state is PromiseState.OPEN
        assert superseded is None

    def test_new_promise_supersedes_open_one(self):
        older = make_promise(id="ptp_0", made_at=datetime(2026, 8, 20, 10, 0))
        newer = make_promise(id="ptp_1")
        _, _, superseded = record_promise(
            make_invoice(state=InvoiceState.PROMISED), newer, open_promise=older
        )
        assert superseded.state is PromiseState.SUPERSEDED
        assert superseded.resolved_at == newer.made_at

    def test_promise_beyond_balance_rejected(self):
        with pytest.raises(InvalidTransition, match="outside"):
            record_promise(make_invoice(), make_promise(amount=200_000))

    def test_promise_on_paid_invoice_rejected(self):
        paid = make_invoice(state=InvoiceState.PAID, amount_paid=100_000)
        with pytest.raises(InvalidTransition, match="state paid"):
            record_promise(paid, make_promise())

    def test_promise_for_other_invoice_rejected(self):
        with pytest.raises(InvalidTransition, match="not inv_1"):
            record_promise(make_invoice(), make_promise(invoice_id="inv_2"))


class TestRecordPayment:
    def test_full_payment_closes_invoice(self):
        invoice, _ = record_payment(make_invoice(), make_payment())
        assert invoice.state is InvoiceState.PAID
        assert invoice.balance == 0

    def test_partial_payment(self):
        invoice, _ = record_payment(make_invoice(), make_payment(amount=40_000))
        assert invoice.state is InvoiceState.PARTIALLY_PAID
        assert invoice.balance == 60_000

    def test_on_time_full_payment_keeps_promise(self):
        promise = make_promise()
        invoice, resolved = record_payment(
            make_invoice(state=InvoiceState.PROMISED), make_payment(), open_promise=promise
        )
        assert invoice.state is InvoiceState.PAID
        assert resolved.state is PromiseState.KEPT

    def test_partial_on_time_payment_leaves_promise_open(self):
        _, resolved = record_payment(
            make_invoice(state=InvoiceState.PROMISED),
            make_payment(amount=40_000),
            open_promise=make_promise(),
        )
        assert resolved is None

    def test_cumulative_payments_keep_promise(self):
        _, resolved = record_payment(
            make_invoice(state=InvoiceState.PARTIALLY_PAID, amount_paid=60_000),
            make_payment(amount=40_000),
            open_promise=make_promise(amount=100_000),
            paid_against_promise=60_000,
        )
        assert resolved.state is PromiseState.KEPT

    def test_late_clearing_payment_marks_promise_partially_kept(self):
        late = make_payment(observed_at=datetime(2026, 9, 2, 10, 0))
        invoice, resolved = record_payment(
            make_invoice(state=InvoiceState.PROMISED), late, open_promise=make_promise()
        )
        assert invoice.state is InvoiceState.PAID
        assert resolved.state is PromiseState.PARTIALLY_KEPT

    def test_payment_on_escalated_invoice_is_recorded(self):
        escalated = make_invoice(state=InvoiceState.ESCALATED)
        invoice, _ = record_payment(escalated, make_payment())
        assert invoice.state is InvoiceState.PAID

    def test_partial_payment_keeps_hands_off_state(self):
        escalated = make_invoice(state=InvoiceState.ESCALATED)
        invoice, _ = record_payment(escalated, make_payment(amount=40_000))
        assert invoice.state is InvoiceState.ESCALATED
        assert invoice.balance == 60_000

    def test_payment_on_settled_invoice_rejected(self):
        paid = make_invoice(state=InvoiceState.PAID, amount_paid=100_000)
        with pytest.raises(InvalidTransition, match="already settled"):
            record_payment(paid, make_payment())

    def test_overpayment_rejected(self):
        with pytest.raises(InvalidTransition, match="overpay"):
            record_payment(make_invoice(amount_paid=90_000), make_payment(amount=20_000))


class TestExpirePromise:
    def test_not_yet_due_returns_none(self):
        result = expire_promise(
            make_invoice(state=InvoiceState.PROMISED),
            make_promise(),
            today=date(2026, 8, 28),
            paid_against_promise=0,
            now=datetime(2026, 8, 28, 0, 5),
        )
        assert result is None

    def test_no_money_marks_broken_and_invoice_chaseable(self):
        invoice, promise = expire_promise(
            make_invoice(state=InvoiceState.PROMISED),
            make_promise(),
            today=date(2026, 8, 29),
            paid_against_promise=0,
            now=datetime(2026, 8, 29, 0, 5),
        )
        assert promise.state is PromiseState.BROKEN
        assert invoice.state is InvoiceState.OUTSTANDING

    def test_some_money_marks_partially_kept(self):
        invoice, promise = expire_promise(
            make_invoice(state=InvoiceState.PROMISED, amount_paid=40_000),
            make_promise(),
            today=date(2026, 8, 29),
            paid_against_promise=40_000,
            now=datetime(2026, 8, 29, 0, 5),
        )
        assert promise.state is PromiseState.PARTIALLY_KEPT
        assert invoice.state is InvoiceState.PARTIALLY_PAID


class TestGuards:
    def test_dispute_stands_agent_down(self):
        assert record_dispute(make_invoice()).state is InvoiceState.DISPUTED

    def test_stop_contact_is_idempotent(self):
        stopped = stop_contact(make_invoice())
        assert stop_contact(stopped).state is InvoiceState.STOP_CONTACT

    def test_escalate_from_active_only(self):
        assert escalate(make_invoice()).state is InvoiceState.ESCALATED
        with pytest.raises(InvalidTransition):
            escalate(make_invoice(state=InvoiceState.DISPUTED))
