from datetime import UTC, date, datetime

import pytest

from urudhi.ledger.models import (
    Channel,
    Concession,
    ConcessionState,
    ConcessionType,
    Installment,
    Invoice,
    InvoiceState,
    Payment,
    PromiseState,
    PromiseToPay,
)
from urudhi.ledger.transitions import (
    InvalidTransition,
    accept_concession,
    escalate,
    expire_concession,
    expire_promise,
    human_close,
    human_release,
    installment_statuses,
    offer_concession,
    record_dispute,
    record_payment,
    record_promise,
    stop_contact,
)

NOW = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        id="inv_1", debtor_id="deb_1", number="URU/2026/001",
        amount=10_000_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
    )
    return Invoice(**{**defaults, **overrides})


def make_promise(**overrides) -> PromiseToPay:
    defaults = dict(
        id="ptp_1", invoice_id="inv_1", debtor_id="deb_1", amount=10_000_000,
        promised_on=date(2026, 8, 28), made_at=NOW, channel=Channel.WHATSAPP,
        verbatim="Friday kudukiren.", confidence=0.9,
    )
    return PromiseToPay(**{**defaults, **overrides})


def make_payment(amount=10_000_000, on=date(2026, 8, 26), **overrides) -> Payment:
    defaults = dict(
        id="pay_1", invoice_id="inv_1", amount=amount, method="upi",
        razorpay_payment_id="pay_rzp_1", razorpay_event_id="evt_1",
        observed_at=datetime(on.year, on.month, on.day, 15, 0, tzinfo=UTC),
    )
    return Payment(**{**defaults, **overrides})


def discount(bps=300, pay_by=date(2026, 8, 31), balance=10_000_000, **overrides) -> Concession:
    defaults = dict(
        id="con_1", invoice_id="inv_1", debtor_id="deb_1", type=ConcessionType.DISCOUNT,
        discount_bps=bps, balance_at_offer=balance,
        settlement_amount=balance * (10_000 - bps) // 10_000, pay_by=pay_by, offered_at=NOW,
    )
    return Concession(**{**defaults, **overrides})


def plan(balance=10_000_000, **overrides) -> Concession:
    defaults = dict(
        id="con_2", invoice_id="inv_1", debtor_id="deb_1", type=ConcessionType.INSTALLMENTS,
        balance_at_offer=balance, settlement_amount=balance,
        installments=[Installment(due_on=date(2026, 9, 1), amount=balance // 2),
                      Installment(due_on=date(2026, 9, 15), amount=balance - balance // 2)],
        pay_by=date(2026, 9, 15), offered_at=NOW, state=ConcessionState.ACCEPTED,
    )
    return Concession(**{**defaults, **overrides})


class TestRecordPromise:
    def test_records_and_moves_to_promised(self):
        invoice, promise, superseded = record_promise(make_invoice(), make_promise())
        assert invoice.state is InvoiceState.PROMISED and superseded is None

    def test_supersedes_open_promise(self):
        old = make_promise(id="ptp_0")
        _, _, superseded = record_promise(make_invoice(), make_promise(), open_promise=old)
        assert superseded.state is PromiseState.SUPERSEDED

    def test_rejects_over_balance(self):
        with pytest.raises(InvalidTransition):
            record_promise(make_invoice(), make_promise(amount=20_000_000))

    @pytest.mark.parametrize("state", [InvoiceState.PAID, InvoiceState.ESCALATED,
                                       InvoiceState.STOP_CONTACT, InvoiceState.CLOSED])
    def test_rejects_on_hands_off_states(self, state):
        with pytest.raises(InvalidTransition):
            record_promise(make_invoice(state=state), make_promise())


class TestRecordPayment:
    def test_full_payment_closes_invoice(self):
        invoice, promise, concession = record_payment(make_invoice(), make_payment())
        assert invoice.state is InvoiceState.PAID and invoice.balance == 0
        assert promise is None and concession is None

    def test_partial_payment(self):
        invoice, _, _ = record_payment(make_invoice(), make_payment(amount=4_000_000))
        assert invoice.state is InvoiceState.PARTIALLY_PAID and invoice.balance == 6_000_000

    def test_on_time_full_payment_keeps_promise(self):
        invoice, promise, _ = record_payment(make_invoice(state=InvoiceState.PROMISED),
                                             make_payment(), open_promise=make_promise())
        assert promise.state is PromiseState.KEPT

    def test_partial_on_time_payment_leaves_promise_open(self):
        _, promise, _ = record_payment(make_invoice(state=InvoiceState.PROMISED),
                                       make_payment(amount=4_000_000), open_promise=make_promise())
        assert promise is None

    def test_cumulative_payments_keep_promise(self):
        _, promise, _ = record_payment(
            make_invoice(state=InvoiceState.PROMISED, amount_paid=6_000_000),
            make_payment(amount=4_000_000), open_promise=make_promise(), paid_against_promise=6_000_000,
        )
        assert promise.state is PromiseState.KEPT

    def test_late_clearing_payment_marks_promise_partially_kept(self):
        _, promise, _ = record_payment(make_invoice(state=InvoiceState.PROMISED),
                                       make_payment(on=date(2026, 9, 5)), open_promise=make_promise())
        assert promise.state is PromiseState.PARTIALLY_KEPT

    def test_payment_on_escalated_invoice_is_recorded(self):
        invoice, _, _ = record_payment(make_invoice(state=InvoiceState.ESCALATED), make_payment())
        assert invoice.state is InvoiceState.PAID

    def test_partial_payment_keeps_hands_off_state(self):
        invoice, _, _ = record_payment(make_invoice(state=InvoiceState.DISPUTED),
                                       make_payment(amount=1_000_000))
        assert invoice.state is InvoiceState.DISPUTED and invoice.amount_paid == 1_000_000

    def test_refuses_after_paid(self):
        with pytest.raises(InvalidTransition):
            record_payment(make_invoice(state=InvoiceState.PAID, amount_paid=10_000_000), make_payment())

    def test_refuses_overpayment(self):
        with pytest.raises(InvalidTransition, match="exceeds balance"):
            record_payment(make_invoice(), make_payment(amount=10_000_001))

    def test_refuses_non_positive(self):
        with pytest.raises(InvalidTransition):
            record_payment(make_invoice(), make_payment(amount=0))


class TestDiscountSettlement:
    """₹1,00,000 invoice, 3% approved discount → ₹97,000 settles it, ₹3,000 waived."""

    def test_exact_discounted_payment_settles(self):
        invoice, _, concession = record_payment(make_invoice(), make_payment(amount=9_700_000),
                                                concession=discount())
        assert invoice.state is InvoiceState.PAID
        assert invoice.amount_paid == 9_700_000 and invoice.amount_waived == 300_000
        assert invoice.balance == 0
        assert concession.state is ConcessionState.SETTLED

    def test_discounted_payment_after_expiry_does_not_settle(self):
        invoice, _, concession = record_payment(
            make_invoice(), make_payment(amount=9_700_000, on=date(2026, 9, 3)), concession=discount()
        )
        assert invoice.state is InvoiceState.PARTIALLY_PAID
        assert invoice.amount_waived == 0 and invoice.balance == 300_000
        assert concession is None  # nothing resolved; expiry is ruled on by the daily tick

    def test_less_than_settlement_stays_partial_with_offer_live(self):
        invoice, _, concession = record_payment(make_invoice(), make_payment(amount=5_000_000),
                                                concession=discount())
        assert invoice.state is InvoiceState.PARTIALLY_PAID and invoice.amount_waived == 0
        assert concession is None

    def test_cumulative_payments_reach_settlement(self):
        first, _, _ = record_payment(make_invoice(), make_payment(amount=5_000_000),
                                     concession=discount())
        second, _, concession = record_payment(
            first, make_payment(id="pay_2", amount=4_700_000, razorpay_event_id="evt_2"),
            concession=discount(), paid_since_offer=5_000_000,
        )
        assert second.state is InvoiceState.PAID and second.amount_waived == 300_000
        assert concession.state is ConcessionState.SETTLED

    def test_more_than_settlement_waives_only_the_remainder(self):
        invoice, _, concession = record_payment(make_invoice(), make_payment(amount=9_800_000),
                                                concession=discount())
        assert invoice.state is InvoiceState.PAID
        assert invoice.amount_paid == 9_800_000 and invoice.amount_waived == 200_000
        assert concession.state is ConcessionState.SETTLED

    def test_full_balance_despite_offer_waives_nothing(self):
        invoice, _, concession = record_payment(make_invoice(), make_payment(), concession=discount())
        assert invoice.state is InvoiceState.PAID and invoice.amount_waived == 0
        assert concession.state is ConcessionState.SETTLED

    def test_expired_discount_is_never_written_off(self):
        ruled = expire_concession(make_invoice(), discount(), date(2026, 9, 1), 0, NOW)
        assert ruled.state is ConcessionState.EXPIRED
        assert expire_concession(make_invoice(), discount(), date(2026, 8, 31), 0, NOW) is None

    def test_offer_validation(self):
        with pytest.raises(InvalidTransition, match="below balance"):
            offer_concession(make_invoice(), discount(settlement_amount=10_000_000))
        with pytest.raises(InvalidTransition, match="priced on balance"):
            offer_concession(make_invoice(amount_paid=1_000_000), discount())
        assert offer_concession(make_invoice(), discount()).state is ConcessionState.OFFERED

    def test_accept_then_pay(self):
        accepted = accept_concession(make_invoice(), discount(), NOW)
        assert accepted.state is ConcessionState.ACCEPTED
        with pytest.raises(InvalidTransition):
            accept_concession(make_invoice(), accepted, NOW)
        with pytest.raises(InvalidTransition, match="after its pay-by"):
            accept_concession(make_invoice(), discount(), datetime(2026, 9, 2, tzinfo=UTC))


class TestInstallments:
    def test_schedule_must_sum_to_balance(self):
        bad = plan(installments=[Installment(due_on=date(2026, 9, 1), amount=1)],
                   state=ConcessionState.OFFERED)
        with pytest.raises(InvalidTransition, match="sum to the balance"):
            offer_concession(make_invoice(), bad)

    def test_statuses_allocate_money_in_order(self):
        statuses = installment_statuses(plan(), 5_000_000, date(2026, 9, 2))
        assert [s for _, s in statuses] == ["kept", "pending"]
        statuses = installment_statuses(plan(), 2_000_000, date(2026, 9, 2))
        assert [s for _, s in statuses] == ["partial", "pending"]
        statuses = installment_statuses(plan(), 0, date(2026, 9, 2))
        assert [s for _, s in statuses] == ["missed", "pending"]

    def test_missed_installment_breaks_the_plan(self):
        assert expire_concession(make_invoice(), plan(), date(2026, 9, 1), 0, NOW) is None
        ruled = expire_concession(make_invoice(), plan(), date(2026, 9, 2), 0, NOW)
        assert ruled.state is ConcessionState.BROKEN

    def test_kept_installments_keep_the_plan_alive(self):
        assert expire_concession(make_invoice(), plan(), date(2026, 9, 2), 5_000_000, NOW) is None

    def test_paying_everything_settles_the_plan(self):
        first, _, c1 = record_payment(make_invoice(), make_payment(amount=5_000_000), concession=plan())
        assert c1 is None and first.state is InvoiceState.PARTIALLY_PAID
        second, _, c2 = record_payment(first, make_payment(id="p2", amount=5_000_000,
                                                           razorpay_event_id="e2"),
                                       concession=plan(), paid_since_offer=5_000_000)
        assert second.state is InvoiceState.PAID and c2.state is ConcessionState.SETTLED
        assert second.amount_waived == 0


class TestExpirePromise:
    def test_not_yet(self):
        assert expire_promise(make_invoice(state=InvoiceState.PROMISED), make_promise(),
                              date(2026, 8, 28), 0, NOW) is None

    def test_broken_returns_to_pool(self):
        invoice, promise = expire_promise(make_invoice(state=InvoiceState.PROMISED), make_promise(),
                                          date(2026, 8, 29), 0, NOW)
        assert promise.state is PromiseState.BROKEN and invoice.state is InvoiceState.OUTSTANDING

    def test_partially_kept(self):
        invoice, promise = expire_promise(
            make_invoice(state=InvoiceState.PROMISED, amount_paid=1_000_000), make_promise(),
            date(2026, 8, 29), 1_000_000, NOW,
        )
        assert promise.state is PromiseState.PARTIALLY_KEPT
        assert invoice.state is InvoiceState.PARTIALLY_PAID


class TestStandDownsAndHumans:
    def test_dispute_escalate_stop(self):
        assert record_dispute(make_invoice()).state is InvoiceState.DISPUTED
        assert escalate(make_invoice()).state is InvoiceState.ESCALATED
        assert stop_contact(make_invoice(state=InvoiceState.ESCALATED)).state is InvoiceState.STOP_CONTACT
        assert stop_contact(make_invoice(state=InvoiceState.PAID)).state is InvoiceState.PAID

    def test_human_release_returns_to_automation_with_timestamp(self):
        released = human_release(make_invoice(state=InvoiceState.ESCALATED), NOW)
        assert released.state is InvoiceState.OUTSTANDING and released.human_released_at == NOW
        partial = human_release(make_invoice(state=InvoiceState.DISPUTED, amount_paid=1), NOW)
        assert partial.state is InvoiceState.PARTIALLY_PAID
        with pytest.raises(InvalidTransition):
            human_release(make_invoice(), NOW)

    def test_human_close_is_terminal_and_waives_nothing(self):
        closed = human_close(make_invoice(state=InvoiceState.DISPUTED, amount_paid=1_000_000))
        assert closed.state is InvoiceState.CLOSED and closed.amount_waived == 0
        with pytest.raises(InvalidTransition):
            human_close(make_invoice(state=InvoiceState.PAID, amount_paid=10_000_000))
