"""Installment plans end to end: proposal → gate → offer → acceptance →
payments observed → kept / broken → follow-up or escalation."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from urudhi.agent.brain import MockBrain
from urudhi.agent.intervention import InterventionKind, InterventionRecommendation
from urudhi.agent.loop import Action, RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import EventKind
from urudhi.ledger.models import ConcessionState, Debtor, Invoice, InvoiceState
from urudhi.ledger.transitions import installment_statuses
from urudhi.rails.razorpay_client import FakeRails
from urudhi.rails.webhooks import ingest_payment_event
from urudhi.store import Store

IST = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 8, 24, 11, 0, tzinfo=IST)


class PlanBrain(MockBrain):
    def recommend_intervention(self, context):
        return InterventionRecommendation(
            action=InterventionKind.INSTALLMENT_OFFER, proposed_installments=3,
            proposed_pay_by=date(2026, 9, 20), rationale=["debtor asked to split"], confidence=0.8,
        )


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, debtor, channel, text, *, subject, reference):
        self.sent.append(text)
        return "m"


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.put_debtor(Debtor(id="deb_1", name="Salem Steel", contact_name="Ravi",
                            phone="+919800000002", email="ravi@example.in"))
        s.put_invoice(Invoice(id="inv_1", debtor_id="deb_1", number="URU/2026/002",
                              amount=9_000_000, issued_on=date(2026, 5, 1), due_on=date(2026, 6, 1)))
        yield s


def pay(store, event_id, amount, on):
    return ingest_payment_event(store, {
        "id": event_id, "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": f"p_{event_id}", "amount": amount, "currency": "INR",
                                           "method": "neft", "notes": {"invoice_id": "inv_1"}}}},
    }, now=datetime(on.year, on.month, on.day, 12, 0, tzinfo=UTC))


def offer_and_accept(store, outbox, policy=None):
    agent = RecoveryAgent(store, PlanBrain(), outbox, policy, rails=FakeRails())
    result = agent.chase("inv_1", MORNING)
    assert result.action is Action.MESSAGE_SENT and result.intervention is InterventionKind.INSTALLMENT_OFFER
    accepted = agent.handle_reply("inv_1", "Ok deal, installments fine.", MORNING + timedelta(hours=2))
    assert accepted.action is Action.OFFER_ACCEPTED
    return agent


class TestLifecycle:
    def test_offer_is_gated_recorded_and_communicated(self, store):
        outbox = Outbox()
        offer_and_accept(store, outbox)
        plan = store.live_concession_for("inv_1")
        assert plan.state is ConcessionState.ACCEPTED
        assert len(plan.installments) == 3
        assert sum(i.amount for i in plan.installments) == 9_000_000
        assert plan.installments[-1].due_on == date(2026, 9, 20)
        assert "instalments" in outbox.sent[0]
        assert EventKind.OFFER_MADE in [e.kind for e in store.audit_events()]
        assert EventKind.CONCESSION_ACCEPTED in [e.kind for e in store.audit_events()]

    def test_plan_is_waited_on_not_chased_over(self, store):
        agent = offer_and_accept(store, Outbox())
        assert agent.chase("inv_1", MORNING + timedelta(days=3)).action is Action.WAITED

    def test_payments_mark_installments_kept_and_settle_the_plan(self, store):
        agent = offer_and_accept(store, Outbox())
        plan = store.live_concession_for("inv_1")
        first, second, third = plan.installments
        pay(store, "e1", first.amount, first.due_on - timedelta(days=1))
        statuses = installment_statuses(plan, store.paid_since("inv_1", plan.offered_at), first.due_on)
        assert [s for _, s in statuses] == ["kept", "pending", "pending"]
        agent.daily_tick(first.due_on + timedelta(days=1), MORNING + timedelta(days=10))
        assert store.live_concession_for("inv_1").state is ConcessionState.ACCEPTED  # still alive
        pay(store, "e2", second.amount, second.due_on)
        result = pay(store, "e3", third.amount, third.due_on)
        assert result.status.value == "recorded"
        invoice = store.get_invoice("inv_1")
        assert invoice.state is InvoiceState.PAID and invoice.amount_waived == 0
        assert store.concessions_for("inv_1")[0].state is ConcessionState.SETTLED

    def test_missed_installment_breaks_plan_and_counts_as_broken(self, store):
        policy = PolicyConfig(escalate_after_broken_promises=1)
        agent = offer_and_accept(store, Outbox(), policy)
        plan = store.live_concession_for("inv_1")
        first = plan.installments[0]
        results = agent.daily_tick(first.due_on + timedelta(days=1),
                                   datetime.combine(first.due_on + timedelta(days=1), MORNING.timetz()))
        assert store.concessions_for("inv_1")[0].state is ConcessionState.BROKEN
        assert results and results[0].action is Action.ESCALATED
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED

    def test_partial_installment_is_partial_then_broken(self, store):
        agent = offer_and_accept(store, Outbox())
        plan = store.live_concession_for("inv_1")
        first = plan.installments[0]
        pay(store, "e1", first.amount // 2, first.due_on)
        after = first.due_on + timedelta(days=1)
        statuses = installment_statuses(plan, store.paid_since("inv_1", plan.offered_at), after)
        assert statuses[0][1] == "partial"
        agent.daily_tick(after, datetime.combine(after, MORNING.timetz()))
        assert store.concessions_for("inv_1")[0].state is ConcessionState.BROKEN
        assert store.get_invoice("inv_1").state is InvoiceState.PARTIALLY_PAID  # back in the pool

    def test_plan_below_authority_is_not_offered(self, store):
        store.put_invoice(store.get_invoice("inv_1").model_copy(update={"amount": 240_000}))
        agent = RecoveryAgent(store, PlanBrain(), Outbox(), rails=FakeRails())
        result = agent.chase("inv_1", MORNING)
        assert result.intervention is InterventionKind.REMINDER and result.decision.modified
        assert store.concessions_for("inv_1") == []
