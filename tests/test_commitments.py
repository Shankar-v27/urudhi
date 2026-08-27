"""The executable promise-to-pay commitment engine, end to end.

Promise = what the debtor said · Commitment = what policy accepted (exact
amount, exact deadline, a payment link tagged with the commitment id) ·
Payment = what the rails verified. Every test runs the real loop with the
mock brain, an in-memory store, a capturing outbox and the fake rail.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from urudhi.agent.brain import MockBrain
from urudhi.agent.human import HumanAction, HumanRequest, apply_human_action, escalation_queue
from urudhi.agent.intervention import InterventionKind, InterventionRecommendation
from urudhi.agent.loop import Action, RecoveryAgent
from urudhi.agent.policy import PolicyConfig, check_commitment
from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.commitments import profile_for
from urudhi.ledger.models import (
    CommitmentSource,
    CommitmentState,
    Debtor,
    Invoice,
    InvoiceState,
    PaymentCommitment,
    PromiseState,
)
from urudhi.ledger.transitions import (
    InvalidTransition,
    apply_payment_to_commitments,
    cancel_commitment,
    expire_commitment,
    open_commitment,
)
from urudhi.rails.razorpay_client import FakeRails
from urudhi.rails.webhooks import IngestStatus, ingest_payment_event
from urudhi.scoring.priority import score_invoice
from urudhi.store import Store

IST = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 8, 24, 11, 0, tzinfo=IST)  # a Monday
LAKH = 10_000_000  # ₹1,00,000 in paise


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, debtor, channel, text, *, subject, reference):
        self.sent.append(text)
        return f"m{len(self.sent)}"


class DiscountBrain(MockBrain):
    def recommend_intervention(self, context):
        return InterventionRecommendation(
            action=InterventionKind.DISCOUNT_OFFER, proposed_discount_bps=300,
            proposed_pay_by=date(2026, 8, 29), rationale=["debtor asked for terms"], confidence=0.8,
        )


class GreedyBrain(MockBrain):
    """Proposes a concession far outside delegated authority."""

    def recommend_intervention(self, context):
        return InterventionRecommendation(
            action=InterventionKind.DISCOUNT_OFFER, proposed_discount_bps=2_000,
            proposed_pay_by=date(2026, 8, 29), rationale=["be generous"], confidence=0.9,
        )


class PlanBrain(MockBrain):
    def recommend_intervention(self, context):
        return InterventionRecommendation(
            action=InterventionKind.INSTALLMENT_OFFER, proposed_installments=3,
            proposed_pay_by=date(2026, 9, 20), rationale=["debtor asked to split"], confidence=0.8,
        )


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.put_debtor(Debtor(id="deb_1", name="ACME Industries", contact_name="Meena",
                            phone="+919800000003", email="meena@acme.example.in", language="ta"))
        s.put_invoice(Invoice(id="inv_1", debtor_id="deb_1", number="URU/2026/0007",
                              amount=LAKH, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1)))
        yield s


@pytest.fixture
def rails():
    return FakeRails()


@pytest.fixture
def outbox():
    return Outbox()


@pytest.fixture
def agent(store, outbox, rails):
    return RecoveryAgent(store, MockBrain(), outbox, PolicyConfig(), rails=rails)


def pay(store, event_id, amount, when, *, commitment_id=None, kind="payment.captured"):
    notes = {"invoice_id": "inv_1"}
    if commitment_id:
        notes["commitment_id"] = commitment_id
    return ingest_payment_event(store, {
        "id": event_id, "event": kind,
        "payload": {"payment": {"entity": {"id": f"p_{event_id}", "amount": amount,
                                           "currency": "INR", "method": "upi", "notes": notes}}},
    }, now=when)


def promise(agent, text="Cash konjam tight ah iruku. Friday 50k kudukuren, balance next month.",
            when=MORNING):
    return agent.handle_reply("inv_1", text, when)


# -- promise → commitment -------------------------------------------------------

class TestPromiseBecomesCommitment:
    def test_tamil_english_partial_promise_becomes_an_exact_commitment(self, agent, store, rails, outbox):
        result = promise(agent)
        assert result.action is Action.COMMITMENT_CREATED
        assert result.commitment_verdict is not None and result.commitment_verdict.allowed

        [p] = store.promises_for("inv_1")
        assert p.amount == 5_000_000 and p.promised_on == date(2026, 8, 28) and p.state is PromiseState.OPEN
        assert "kudukuren" in p.verbatim  # what was said, unedited

        [c] = store.commitments_for("inv_1")
        assert c.state is CommitmentState.ACTIVE and c.source is CommitmentSource.PROMISE
        assert c.committed_amount == 5_000_000 and c.due_on == date(2026, 8, 28)
        assert c.promise_id == p.id and c.evidence == p.verbatim
        assert c.due_at.tzinfo is not None and c.due_at.astimezone(IST).hour == 23

        # The instrument is exact and correlated: amount, expiry, invoice + commitment ids.
        [link] = rails.links
        assert link["amount"] == 5_000_000
        assert link["notes"] == {"invoice_id": "inv_1", "commitment_id": c.id}
        assert link["reference_id"] == c.id
        assert link["expire_by"] == int(c.due_at.timestamp())
        assert c.instrument_type.value == "payment_link" and c.payment_url == link["short_url"]

        # The debtor was told, with the link, in responding mode.
        assert c.instrument_sent and outbox.sent and c.payment_url in outbox.sent[-1]
        assert "50,000" in outbox.sent[-1]

    def test_every_lifecycle_step_is_audited_and_chained(self, agent, store):
        promise(agent)
        kinds = [e.kind for e in store.audit_events()]
        for kind in (EventKind.MESSAGE_RECEIVED, EventKind.PROMISE_RECORDED,
                     EventKind.COMMITMENT_PROPOSED, EventKind.COMMITMENT_APPROVED,
                     EventKind.PAYMENT_INSTRUMENT_CREATED, EventKind.COMMITMENT_CREATED,
                     EventKind.MESSAGE_SENT):
            assert kind in kinds, kind
        assert kinds.index(EventKind.COMMITMENT_APPROVED) < kinds.index(EventKind.COMMITMENT_CREATED)
        approved = next(e for e in store.audit_events() if e.kind is EventKind.COMMITMENT_APPROVED)
        assert {c["gate"] for c in approved.payload["checks"]} >= {
            "partial_allowed", "amount_positive", "amount_within_balance",
            "deadline_within_horizon", "no_dispute", "not_stop_contact"}
        assert all(c["allowed"] for c in approved.payload["checks"])
        assert verify_chain(store.audit_events()) == len(kinds)

    def test_a_promise_beyond_the_horizon_is_recorded_but_not_committed(self, store, outbox, rails):
        agent = RecoveryAgent(store, MockBrain(), outbox, PolicyConfig(max_promise_horizon_days=3),
                              rails=rails)
        result = agent.handle_reply("inv_1", "Will pay ₹50,000 in 5 days.", MORNING)
        assert result.action is Action.PROMISE_RECORDED
        assert result.commitment_verdict is not None and not result.commitment_verdict.allowed
        assert "horizon" in result.commitment_verdict.reason
        [p] = store.promises_for("inv_1")
        assert p.verbatim == "Will pay ₹50,000 in 5 days." and p.state is PromiseState.DECLINED
        assert store.get_invoice("inv_1").state is InvoiceState.OUTSTANDING  # not waiting on it
        assert store.commitments_for("inv_1") == []
        assert rails.links == []  # no instrument without a commitment
        blocked = [e for e in store.audit_events() if e.kind is EventKind.COMMITMENT_BLOCKED]
        assert len(blocked) == 1
        failing = [c for c in blocked[0].payload["checks"] if not c["allowed"]]
        assert [c["gate"] for c in failing] == ["deadline_within_horizon"]

    def test_partial_commitments_can_be_switched_off(self, store, outbox, rails):
        agent = RecoveryAgent(store, MockBrain(), outbox,
                              PolicyConfig(allow_partial_commitments=False), rails=rails)
        result = promise(agent)
        assert result.action is Action.PROMISE_RECORDED
        assert "partial" in result.commitment_verdict.reason
        assert store.commitments_for("inv_1") == []

    def test_a_newer_commitment_supersedes_the_old_one(self, agent, store, rails):
        promise(agent)
        promise(agent, "Sorry, make that ₹60,000 by Saturday.", MORNING + timedelta(hours=3))
        states = {c.id: c.state for c in store.commitments_for("inv_1")}
        assert list(states.values()) == [CommitmentState.SUPERSEDED, CommitmentState.ACTIVE]
        assert len(rails.links) == 2 and rails.links[1]["amount"] == 6_000_000
        assert any(e.kind is EventKind.COMMITMENT_SUPERSEDED for e in store.audit_events())

    def test_amount_is_clamped_and_deadline_defaulted_when_vague(self, agent, store):
        result = agent.handle_reply("inv_1", "Will pay ₹5,00,000 soon, pakka.", MORNING)
        assert result.action is Action.COMMITMENT_CREATED
        [c] = store.commitments_for("inv_1")
        assert c.committed_amount == LAKH  # never above the balance
        assert c.due_on == MORNING.date() + timedelta(days=7)
        assert c.confidence < 0.9


# -- fulfilment through the rails ----------------------------------------------

class TestFulfilment:
    def test_exact_payment_through_the_instrument_fulfils_and_keeps(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        result = pay(store, "evt_1", 5_000_000, datetime(2026, 8, 27, 10, 0, tzinfo=IST),
                     commitment_id=c.id, kind="payment_link.paid")
        assert result.status is IngestStatus.RECORDED
        assert result.payment.commitment_id == c.id and result.payment.matched_by == "instrument"

        c = store.get_commitment(c.id)
        assert c.state is CommitmentState.FULFILLED and c.amount_received == 5_000_000
        assert c.days_late == 0 and c.fulfilled_at is not None
        assert store.promises_for("inv_1")[0].state is PromiseState.KEPT
        invoice = store.get_invoice("inv_1")
        assert invoice.state is InvoiceState.PARTIALLY_PAID and invoice.balance == 5_000_000
        assert any(e.kind is EventKind.COMMITMENT_FULFILLED and e.payload["matched_by"] == "instrument"
                   for e in store.audit_events())

    def test_partial_payment_is_partially_fulfilled_with_remaining(self, agent, store):
        promise(agent, "Will pay ₹60,000 by Friday.")
        [c] = store.commitments_for("inv_1")
        pay(store, "evt_1", 3_000_000, datetime(2026, 8, 26, 10, 0, tzinfo=IST), commitment_id=c.id)
        c = store.get_commitment(c.id)
        assert c.state is CommitmentState.PARTIALLY_FULFILLED
        assert c.amount_received == 3_000_000 and c.amount_remaining == 3_000_000
        assert store.promises_for("inv_1")[0].state is PromiseState.OPEN  # not kept yet
        assert any(e.kind is EventKind.COMMITMENT_PARTIALLY_FULFILLED for e in store.audit_events())

    def test_untagged_payment_still_matches_the_live_commitment_by_invoice(self, agent, store):
        promise(agent)
        pay(store, "evt_1", 5_000_000, datetime(2026, 8, 27, 10, 0, tzinfo=IST))
        [c] = store.commitments_for("inv_1")
        assert c.state is CommitmentState.FULFILLED
        assert store.payments_for("inv_1")[0].matched_by == "invoice"

    def test_duplicate_webhook_does_not_double_count(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        when = datetime(2026, 8, 27, 10, 0, tzinfo=IST)
        pay(store, "evt_1", 5_000_000, when, commitment_id=c.id)
        assert pay(store, "evt_1", 5_000_000, when, commitment_id=c.id).status is IngestStatus.REPLAY
        assert store.get_commitment(c.id).amount_received == 5_000_000
        assert store.get_invoice("inv_1").amount_paid == 5_000_000

    def test_payment_after_expiry_is_noted_late_and_does_not_unmiss(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        agent.daily_tick(date(2026, 8, 29), datetime(2026, 8, 29, 10, 0, tzinfo=IST))
        assert store.get_commitment(c.id).state is CommitmentState.MISSED
        pay(store, "evt_late", 5_000_000, datetime(2026, 8, 30, 10, 0, tzinfo=IST), commitment_id=c.id)
        c = store.get_commitment(c.id)
        assert c.state is CommitmentState.MISSED and c.amount_received == 5_000_000
        assert store.payments_for("inv_1")[0].matched_by == "instrument-late"
        assert store.get_invoice("inv_1").amount_paid == 5_000_000  # money is money

    def test_payment_after_cancellation_is_recorded_on_the_invoice_only(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        agent.handle_reply("inv_1", "Please stop messaging me.", MORNING + timedelta(hours=1))
        assert store.get_commitment(c.id).state is CommitmentState.CANCELLED
        result = pay(store, "evt_1", 5_000_000, datetime(2026, 8, 27, 10, 0, tzinfo=IST),
                     commitment_id=c.id)
        assert result.status is IngestStatus.RECORDED
        assert result.payment.matched_by == "instrument-stale"
        assert store.get_commitment(c.id).amount_received == 0
        assert store.get_invoice("inv_1").amount_paid == 5_000_000

    def test_payment_against_an_already_fulfilled_commitment_is_not_double_applied(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        when = datetime(2026, 8, 27, 10, 0, tzinfo=IST)
        pay(store, "evt_1", 5_000_000, when, commitment_id=c.id)
        pay(store, "evt_2", 5_000_000, when + timedelta(hours=1), commitment_id=c.id)
        assert store.get_commitment(c.id).amount_received == 5_000_000
        assert store.get_invoice("inv_1").state is InvoiceState.PAID

    def test_late_full_payment_before_the_tick_is_fulfilled_with_days_late(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        pay(store, "evt_1", 5_000_000, datetime(2026, 8, 30, 8, 0, tzinfo=IST), commitment_id=c.id)
        c = store.get_commitment(c.id)
        assert c.state is CommitmentState.FULFILLED and c.days_late == 2


# -- deadlines ---------------------------------------------------------------------

class TestDeadlines:
    def test_active_commitment_suppresses_chasing(self, agent, store, outbox):
        promise(agent)
        sent_before = len(outbox.sent)
        later = agent.chase("inv_1", MORNING + timedelta(days=2))
        assert later.action is Action.WAITED
        assert len(outbox.sent) == sent_before

    def test_one_bounded_reminder_the_day_before(self, agent, store, outbox):
        promise(agent)  # due Friday 28th
        sent_before = len(outbox.sent)
        results = agent.daily_tick(date(2026, 8, 27), datetime(2026, 8, 27, 11, 0, tzinfo=IST))
        assert any(r.intervention is InterventionKind.COMMITMENT_REMINDER
                   and r.action is Action.MESSAGE_SENT for r in results)
        assert len(outbox.sent) == sent_before + 1
        again = agent.daily_tick(date(2026, 8, 27), datetime(2026, 8, 27, 15, 0, tzinfo=IST))
        assert not any(r.intervention is InterventionKind.COMMITMENT_REMINDER
                       and r.action is Action.MESSAGE_SENT for r in again)

    def test_missed_commitment_returns_invoice_to_the_pool_and_counts(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        results = agent.daily_tick(date(2026, 8, 29), datetime(2026, 8, 29, 10, 0, tzinfo=IST))
        c = store.get_commitment(c.id)
        assert c.state is CommitmentState.MISSED and c.missed_at is not None
        assert store.promises_for("inv_1")[0].state is PromiseState.BROKEN
        assert store.get_invoice("inv_1").state is InvoiceState.OUTSTANDING
        assert any(r.action is Action.NOTED for r in results)
        assert any(e.kind is EventKind.COMMITMENT_MISSED for e in store.audit_events())

    def test_two_missed_commitments_escalate_and_cancel(self, agent, store):
        day = MORNING
        for _ in range(2):
            promise(agent, "Will pay ₹50,000 in 2 days.", day)
            day = day + timedelta(days=3)
            agent.daily_tick(day.date(), day)
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED
        states = [c.state for c in store.commitments_for("inv_1")]
        assert states.count(CommitmentState.MISSED) == 2
        queue = escalation_queue(store)
        assert queue[0]["commitments_missed"] == 2
        assert queue[0]["last_commitment"]["amount_received"] == 0
        assert queue[0]["recommended_action"] == "human review"

    def test_confirmation_held_outside_hours_goes_out_on_the_next_tick(self, agent, store, outbox):
        night = datetime(2026, 8, 24, 22, 0, tzinfo=IST)
        result = promise(agent, when=night)
        assert result.action is Action.COMMITMENT_CREATED
        [c] = store.commitments_for("inv_1")
        assert not c.instrument_sent and outbox.sent == []
        agent.daily_tick(date(2026, 8, 25), datetime(2026, 8, 25, 11, 0, tzinfo=IST))
        assert store.get_commitment(c.id).instrument_sent and len(outbox.sent) == 1


# -- concessions as commitments ---------------------------------------------------

class TestSettlementCommitment:
    def test_three_percent_settlement_commitment_waives_only_on_payment(self, store, outbox, rails):
        agent = RecoveryAgent(store, DiscountBrain(), outbox, PolicyConfig(), rails=rails)
        assert agent.chase("inv_1", MORNING).intervention is InterventionKind.DISCOUNT_OFFER
        accept = agent.handle_reply("inv_1", "Ok deal, will clear it today.", MORNING + timedelta(hours=2))
        assert accept.action is Action.OFFER_ACCEPTED and accept.commitment_id
        c = store.get_commitment(accept.commitment_id)
        assert c.source is CommitmentSource.CONCESSION and c.committed_amount == 9_700_000
        assert c.concession_id == store.concessions_for("inv_1")[0].id
        assert store.get_invoice("inv_1").amount_waived == 0  # nothing waived before money

        pay(store, "evt_1", 9_700_000, datetime(2026, 8, 25, 10, 0, tzinfo=IST), commitment_id=c.id)
        invoice = store.get_invoice("inv_1")
        assert invoice.state is InvoiceState.PAID and invoice.amount_waived == 300_000
        assert invoice.balance == 0
        assert store.get_commitment(c.id).state is CommitmentState.FULFILLED
        assert store.concessions_for("inv_1")[0].state.value == "settled"
        # The waived ₹3,000 never becomes receivable again.
        late = pay(store, "evt_2", 100, datetime(2026, 8, 26, 10, 0, tzinfo=IST))
        assert late.status is IngestStatus.REJECTED and store.get_invoice("inv_1").balance == 0

    def test_unauthorized_discount_is_blocked_and_no_commitment_exists(self, store, outbox, rails):
        agent = RecoveryAgent(store, GreedyBrain(), outbox, PolicyConfig(), rails=rails)
        result = agent.chase("inv_1", MORNING)
        assert result.decision.modified and result.intervention is InterventionKind.REMINDER
        assert any(not g.allowed and "exceeds delegated cap" in g.reason for g in result.decision.gates)
        assert store.concessions_for("inv_1") == [] and store.commitments_for("inv_1") == []
        assert "discount" not in outbox.sent[0].lower()


class TestInstallmentCommitments:
    def test_each_installment_is_its_own_commitment_and_payments_hit_the_right_one(self, store, outbox, rails):
        store.put_invoice(store.get_invoice("inv_1").model_copy(update={"amount": 12_000_000}))
        agent = RecoveryAgent(store, PlanBrain(), outbox, PolicyConfig(), rails=rails)
        agent.chase("inv_1", MORNING)
        accepted = agent.handle_reply("inv_1", "Ok deal, installments fine.", MORNING + timedelta(hours=2))
        assert accepted.action is Action.OFFER_ACCEPTED
        plan = store.commitments_for("inv_1")
        assert [c.installment_index for c in plan] == [1, 2, 3]
        assert all(c.source is CommitmentSource.INSTALLMENT and c.state is CommitmentState.ACTIVE
                   for c in plan)
        assert sum(c.committed_amount for c in plan) == 12_000_000
        assert len([link for link in rails.links if link["notes"].get("commitment_id")]) == 3

        first, second, third = plan
        pay(store, "evt_1", first.committed_amount, datetime.combine(first.due_on, datetime.min.time(), tzinfo=IST)
            + timedelta(hours=10), commitment_id=first.id)
        pay(store, "evt_2", second.committed_amount, datetime.combine(second.due_on, datetime.min.time(), tzinfo=IST)
            + timedelta(hours=10), commitment_id=second.id)
        states = {c.installment_index: c.state for c in store.commitments_for("inv_1")}
        assert states == {1: CommitmentState.FULFILLED, 2: CommitmentState.FULFILLED, 3: CommitmentState.ACTIVE}

        after_third = third.due_on + timedelta(days=1)
        agent.daily_tick(after_third, datetime.combine(after_third, datetime.min.time(), tzinfo=IST) + timedelta(hours=10))
        third_now = store.get_commitment(third.id)
        assert third_now.state is CommitmentState.MISSED
        assert store.concessions_for("inv_1")[0].state.value == "broken"
        assert store.get_invoice("inv_1").state in (InvoiceState.PARTIALLY_PAID, InvoiceState.ESCALATED)

    def test_installment_count_above_cap_is_not_offered(self, store, outbox, rails):
        class SixBrain(MockBrain):
            def recommend_intervention(self, context):
                return InterventionRecommendation(
                    action=InterventionKind.INSTALLMENT_OFFER, proposed_installments=6,
                    proposed_pay_by=date(2026, 9, 30), confidence=0.9,
                )
        agent = RecoveryAgent(store, SixBrain(), outbox, PolicyConfig(), rails=rails)
        result = agent.chase("inv_1", MORNING)
        assert result.decision.modified and store.commitments_for("inv_1") == []


# -- policy ------------------------------------------------------------------------

class TestPolicy:
    def test_checklist_lines(self, store):
        invoice = store.get_invoice("inv_1")
        verdict = check_commitment(invoice, 5_000_000, date(2026, 8, 28), date(2026, 8, 24), PolicyConfig())
        assert verdict.allowed
        assert {c.gate for c in verdict.checks} >= {"partial_allowed", "amount_positive",
                                                   "amount_within_balance", "deadline_not_past",
                                                   "deadline_within_horizon", "no_dispute",
                                                   "not_stop_contact", "consistent_with_offer"}

    @pytest.mark.parametrize("amount,due,needle", [
        (LAKH + 1, date(2026, 8, 28), "exceeds balance"),
        (0, date(2026, 8, 28), "not positive"),
        (5_000_000, date(2026, 10, 30), "exceeds the 30-day horizon"),
        (5_000_000, date(2026, 8, 20), "in the past"),
        (5_000, date(2026, 8, 28), "below the"),
    ])
    def test_refusals(self, store, amount, due, needle):
        verdict = check_commitment(store.get_invoice("inv_1"), amount, due, date(2026, 8, 24), PolicyConfig())
        assert not verdict.allowed and needle in verdict.reason

    @pytest.mark.parametrize("state", [InvoiceState.STOP_CONTACT, InvoiceState.DISPUTED, InvoiceState.ESCALATED])
    def test_hands_off_states_block(self, store, state):
        invoice = store.get_invoice("inv_1").model_copy(update={"state": state})
        verdict = check_commitment(invoice, 5_000_000, date(2026, 8, 28), date(2026, 8, 24), PolicyConfig())
        assert not verdict.allowed

    def test_stop_contact_cancels_and_blocks_further_communication(self, agent, store, outbox):
        promise(agent)
        agent.handle_reply("inv_1", "STOP", MORNING + timedelta(hours=1))
        sent = len(outbox.sent)
        assert store.commitments_for("inv_1")[0].state is CommitmentState.CANCELLED
        agent.daily_tick(date(2026, 8, 27), datetime(2026, 8, 27, 11, 0, tzinfo=IST))
        assert len(outbox.sent) == sent
        assert agent.chase("inv_1", MORNING + timedelta(days=3)).action is Action.BLOCKED

    def test_dispute_cancels_and_a_promise_after_it_is_only_logged(self, agent, store):
        promise(agent)
        agent.handle_reply("inv_1", "Invoice amount itself is wrong.", MORNING + timedelta(hours=1))
        assert store.commitments_for("inv_1")[0].state is CommitmentState.CANCELLED
        result = promise(agent, "Ok will pay ₹50,000 by Friday.", MORNING + timedelta(hours=2))
        assert result.action is Action.NOTED and len(store.commitments_for("inv_1")) == 1

    def test_transition_refuses_bad_commitments(self, store):
        invoice = store.get_invoice("inv_1")
        bad = PaymentCommitment(id="cmt_x", invoice_id="inv_1", debtor_id="deb_1",
                                source=CommitmentSource.PROMISE, committed_amount=LAKH + 1,
                                due_on=date(2026, 8, 28), due_at=datetime(2026, 8, 28, 23, 59, tzinfo=IST),
                                created_at=MORNING)
        with pytest.raises(InvalidTransition):
            open_commitment(invoice, bad, [])


# -- memory ------------------------------------------------------------------------

def _commitment(cid, state, amount=5_000_000, days_late=0, created=MORNING):
    kwargs = dict(id=cid, invoice_id="inv_1", debtor_id="deb_1", source=CommitmentSource.PROMISE,
                  committed_amount=amount, due_on=date(2026, 8, 28),
                  due_at=datetime(2026, 8, 28, 23, 59, tzinfo=IST), created_at=created, state=state,
                  days_late=days_late)
    if state is CommitmentState.FULFILLED:
        kwargs |= {"amount_received": amount, "fulfilled_at": created + timedelta(days=2),
                   "resolved_at": created + timedelta(days=2)}
    if state is CommitmentState.MISSED:
        kwargs |= {"missed_at": created + timedelta(days=5), "resolved_at": created + timedelta(days=5)}
    return PaymentCommitment(**kwargs)


class TestMemory:
    def test_profile_reads_like_the_product_promises(self):
        rows = [_commitment(f"c{i}", CommitmentState.FULFILLED, days_late=d) for i, d in enumerate((0, 2, 0, 4))]
        rows.append(_commitment("c9", CommitmentState.MISSED))
        profile = profile_for(rows)
        assert profile.commitments == 5 and profile.fulfilled == 4 and profile.missed == 1
        assert profile.fulfillment_rate == 0.8 and profile.average_delay_days == 1.5
        assert profile.average_committed == 5_000_000
        assert profile.credibility == round(5 / 7, 4)
        assert "4 of 5" in profile.describe()

    def test_kept_commitments_raise_credibility_and_lower_the_chase_score(self, store):
        invoice = store.get_invoice("inv_1")
        clean = score_invoice(invoice, [], 1, 6, date(2026, 8, 24), commitments=[])
        reliable = score_invoice(invoice, [], 1, 6, date(2026, 8, 24),
                                 commitments=[_commitment("a", CommitmentState.FULFILLED),
                                              _commitment("b", CommitmentState.FULFILLED)])
        flaky = score_invoice(invoice, [], 1, 6, date(2026, 8, 24),
                              commitments=[_commitment("a", CommitmentState.MISSED)])
        assert reliable.components["credibility"] < clean.components["credibility"] < flaky.components["credibility"]
        assert reliable.score < clean.score < flaky.score
        assert profile_for([_commitment("a", CommitmentState.FULFILLED)]).credibility > 0.5

    def test_history_feeds_the_brain_context(self, agent, store):
        promise(agent)
        [c] = store.commitments_for("inv_1")
        agent.daily_tick(date(2026, 8, 29), datetime(2026, 8, 29, 10, 0, tzinfo=IST))
        context = agent._decision_context(store.get_invoice("inv_1"), datetime(2026, 8, 30, 11, 0, tzinfo=IST), None)
        assert context.commitments_missed == 1 and context.commitments_total == 1
        assert context.commitment_fulfillment_rate == 0.0 and context.active_commitment is None

    def test_active_commitment_shows_in_context(self, agent, store):
        promise(agent)
        context = agent._decision_context(store.get_invoice("inv_1"), MORNING + timedelta(hours=3), None)
        assert context.active_commitment and "50,000" in context.active_commitment


# -- humans --------------------------------------------------------------------------

class TestHumanArrangement:
    def test_human_approves_an_arrangement_after_escalation(self, agent, store, rails):
        day = MORNING
        for _ in range(2):
            promise(agent, "Will pay ₹50,000 in 2 days.", day)
            day = day + timedelta(days=3)
            agent.daily_tick(day.date(), day)
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED
        payload = apply_human_action(store, "inv_1", HumanRequest(
            action=HumanAction.ARRANGE, operator="priya", notes="spoke to Meena; ₹40k by the 10th",
            amount=4_000_000, due_on=date(2026, 9, 10),
        ), day, agent=agent)
        assert payload["commitment_id"]
        c = store.get_commitment(payload["commitment_id"])
        assert c.source is CommitmentSource.HUMAN and c.state is CommitmentState.ACTIVE
        assert c.evidence.startswith("arrangement approved by priya")
        assert store.get_invoice("inv_1").state is InvoiceState.OUTSTANDING
        human = [e for e in store.audit_events() if e.kind is EventKind.HUMAN_ACTION]
        assert human and human[-1].payload["action"] == "arrange"
        assert any(e.kind is EventKind.COMMITMENT_APPROVED and e.actor.value == "human"
                   for e in store.audit_events())
        assert verify_chain(store.audit_events()) > 0

    def test_policy_still_refuses_a_bad_human_arrangement(self, agent, store):
        store.put_invoice(store.get_invoice("inv_1").model_copy(update={"state": InvoiceState.ESCALATED}))
        with pytest.raises(InvalidTransition, match="refused by policy"):
            apply_human_action(store, "inv_1", HumanRequest(
                action=HumanAction.ARRANGE, operator="priya", notes="x",
                amount=LAKH * 2, due_on=date(2026, 9, 10)), MORNING, agent=agent)
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED
        assert store.commitments_for("inv_1") == []


# -- pure transitions ------------------------------------------------------------------

class TestTransitions:
    def test_allocation_is_earliest_deadline_first_and_never_negative(self):
        a = _commitment("a", CommitmentState.ACTIVE, amount=3_000_000)
        b = _commitment("b", CommitmentState.ACTIVE, amount=3_000_000).model_copy(
            update={"due_on": date(2026, 9, 5)})
        touched, leftover = apply_payment_to_commitments([b, a], 4_000_000,
                                                         datetime(2026, 8, 26, 10, 0, tzinfo=IST))
        assert [(c.id, c.state, c.amount_received) for c in touched] == [
            ("a", CommitmentState.FULFILLED, 3_000_000), ("b", CommitmentState.PARTIALLY_FULFILLED, 1_000_000)]
        assert leftover == 0
        assert all(c.amount_remaining >= 0 for c in touched)

    def test_expire_and_cancel_are_idempotent(self):
        c = _commitment("a", CommitmentState.ACTIVE)
        missed = expire_commitment(c, date(2026, 8, 29), datetime(2026, 8, 29, tzinfo=IST))
        assert missed.state is CommitmentState.MISSED
        assert expire_commitment(missed, date(2026, 8, 30), datetime(2026, 8, 30, tzinfo=IST)) is None
        cancelled = cancel_commitment(c, datetime(2026, 8, 25, tzinfo=IST), "test")
        assert cancel_commitment(cancelled, datetime(2026, 8, 26, tzinfo=IST), "again") is cancelled

    def test_not_expired_on_the_due_day_itself(self):
        c = _commitment("a", CommitmentState.ACTIVE)
        assert expire_commitment(c, date(2026, 8, 28), datetime(2026, 8, 28, tzinfo=UTC)) is None


class RefusingRails(FakeRails):
    """A rail that refuses everything — e.g. a test account's amount cap."""

    def create_payment_link(self, **kwargs):
        raise RuntimeError("amount exceeds maximum amount allowed.")


class TestRailFailures:
    def test_chase_with_a_refusing_rail_is_deferred_and_audited(self, store, outbox):
        agent = RecoveryAgent(store, MockBrain(), outbox, PolicyConfig(), rails=RefusingRails())
        result = agent.chase("inv_1", MORNING)
        assert result.action is Action.DEFERRED and outbox.sent == []
        failed = [e for e in store.audit_events() if e.kind is EventKind.RAIL_FAILED]
        assert failed and failed[0].payload["job"] == "payment_link"
        assert store.concessions_for("inv_1") == []
        assert store.get_invoice("inv_1").amount_paid == 0

    def test_commitment_stands_without_an_instrument_when_the_rail_fails(self, store, outbox):
        agent = RecoveryAgent(store, MockBrain(), outbox, PolicyConfig(), rails=RefusingRails())
        result = promise(agent)
        assert result.action is Action.COMMITMENT_CREATED
        [c] = store.commitments_for("inv_1")
        assert c.state is CommitmentState.ACTIVE and c.instrument_id is None and c.payment_url is None
        assert any(e.kind is EventKind.RAIL_FAILED and e.payload["commitment_id"] == c.id
                   for e in store.audit_events())
        # Money arriving on the invoice still fulfils it (matched by invoice).
        pay(store, "evt_1", 5_000_000, datetime(2026, 8, 27, 10, 0, tzinfo=IST))
        assert store.get_commitment(c.id).state is CommitmentState.FULFILLED
        assert store.payments_for("inv_1")[0].matched_by == "invoice"
