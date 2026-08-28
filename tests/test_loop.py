"""End-to-end scenarios through the recovery loop with the mock brain,
an in-memory store, and a captured outbox — no network, fully deterministic."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from urudhi.agent.brain import BrainUnavailable, MockBrain
from urudhi.agent.intervention import InterventionKind, InterventionRecommendation
from urudhi.agent.loop import Action, RecoveryAgent, chaseable
from urudhi.audit.log import Actor, EventKind, verify_chain
from urudhi.ledger.models import (
    Channel,
    ConcessionState,
    Debtor,
    Invoice,
    InvoiceState,
    PromiseState,
)
from urudhi.rails.razorpay_client import FakeRails
from urudhi.rails.webhooks import ingest_payment_event
from urudhi.store import Store

IST = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 8, 24, 11, 0, tzinfo=IST)


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.put_debtor(Debtor(
            id="deb_1", name="Kumar Textiles", contact_name="Kumar",
            phone="+919800000001", email="kumar@example.in",
            preferred_channel=Channel.WHATSAPP, language="ta",
        ))
        s.put_invoice(Invoice(
            id="inv_1", debtor_id="deb_1", number="URU/2026/001",
            amount=250_000, issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1),
        ))
        yield s


class CapturingOutbox:
    def __init__(self):
        self.sent = []

    def send(self, debtor, channel, text, *, subject, reference):
        self.sent.append((debtor.id, channel, text))
        return f"msg-{len(self.sent)}"


class CrashingOutbox(CapturingOutbox):
    """Delivers, then dies before returning — the send-then-crash case."""

    def send(self, debtor, channel, text, *, subject, reference):
        super().send(debtor, channel, text, subject=subject, reference=reference)
        raise ConnectionResetError("gateway hung up after delivery")


class ProposingBrain(MockBrain):
    """A mock whose proposal is scripted, to exercise policy against the brain."""

    def __init__(self, recommendation: InterventionRecommendation):
        self.recommendation = recommendation

    def recommend_intervention(self, context):
        return self.recommendation


class FailingBrain(MockBrain):
    def recommend_intervention(self, context):
        raise BrainUnavailable("endpoint down")

    def interpret_reply(self, context, reply):
        raise BrainUnavailable("endpoint down")


@pytest.fixture
def outbox():
    return CapturingOutbox()


@pytest.fixture
def agent(store, outbox):
    return RecoveryAgent(store, MockBrain(), outbox, rails=FakeRails())


def rzp_event(event_id="evt_1", amount=250_000):
    return {
        "id": event_id,
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": f"pay_rzp_{event_id}", "amount": amount, "currency": "INR", "method": "upi",
            "notes": {"invoice_id": "inv_1"},
        }}},
    }


def kinds(store):
    return [e.kind for e in store.audit_events()]


class TestHappyPath:
    def test_chase_promise_payment_kept(self, agent, store, outbox):
        result = agent.chase("inv_1", MORNING)
        assert result.action is Action.MESSAGE_SENT
        assert result.intervention is InterventionKind.PAYMENT_LINK
        assert len(outbox.sent) == 1 and "sandbox.urudhi.invalid" in outbox.sent[0][2]
        assert "rzp.io" not in outbox.sent[0][2]  # the fake rail never impersonates Razorpay

        reply = agent.handle_reply("inv_1", "Sorry sir, will pay ₹2,500 in 3 days.", MORNING)
        assert reply.action is Action.COMMITMENT_CREATED
        assert store.get_invoice("inv_1").state is InvoiceState.PROMISED
        assert len(outbox.sent) == 2  # the confirmation with the commitment's own link

        ingest_payment_event(store, rzp_event(), now=datetime(2026, 8, 26, 15, 0, tzinfo=UTC))
        assert store.get_invoice("inv_1").state is InvoiceState.PAID
        assert store.promises_for("inv_1")[0].state is PromiseState.KEPT
        assert store.commitments_for("inv_1")[0].state.value == "fulfilled"

        seen = kinds(store)
        for kind in (EventKind.GATE_ALLOWED, EventKind.INTERVENTION_PROPOSED,
                     EventKind.INTERVENTION_DECIDED, EventKind.MESSAGE_SENT,
                     EventKind.MESSAGE_RECEIVED, EventKind.PROMISE_RECORDED,
                     EventKind.PAYMENT_OBSERVED, EventKind.PROMISE_RESOLVED):
            assert kind in seen, kind
        assert verify_chain(store.audit_events()) == len(seen)

    def test_open_promise_is_waited_on_not_chased_over(self, agent, store, outbox):
        agent.chase("inv_1", MORNING)
        agent.handle_reply("inv_1", "will pay ₹2,500 in 5 days", MORNING)
        later = agent.chase("inv_1", MORNING + timedelta(days=3))
        assert later.action is Action.WAITED
        assert len(outbox.sent) == 2  # chase + commitment confirmation; nothing more


class TestBrokenPromisesEscalate:
    def test_two_broken_promises_hand_over_to_human(self, agent, store):
        day = MORNING
        for _ in range(2):
            assert agent.chase("inv_1", day).action is Action.MESSAGE_SENT
            assert agent.handle_reply("inv_1", "will pay ₹2,500 in 2 days", day).action is Action.COMMITMENT_CREATED
            day = day + timedelta(days=3)
            results = agent.daily_tick(day.date(), day)
            assert len(results) == 1

        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED
        broken = [p for p in store.all_promises() if p.state is PromiseState.BROKEN]
        assert len(broken) == 2
        assert [e for e in store.audit_events() if e.kind is EventKind.ESCALATED]

    def test_escalated_invoice_is_never_chased_again(self, agent, store):
        store.put_invoice(store.get_invoice("inv_1").model_copy(
            update={"state": InvoiceState.ESCALATED}))
        result = agent.chase("inv_1", MORNING)
        assert result.action is Action.BLOCKED
        assert "inv_1" not in [i.id for i in chaseable(store)]

    def test_reply_on_escalated_invoice_is_logged_not_acted_on(self, agent, store):
        store.put_invoice(store.get_invoice("inv_1").model_copy(
            update={"state": InvoiceState.ESCALATED}))
        result = agent.handle_reply("inv_1", "will pay ₹2,500 in 2 days", MORNING)
        assert result.action is Action.NOTED
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED
        assert store.promises_for("inv_1") == []
        assert kinds(store)[-1] is EventKind.MESSAGE_RECEIVED


class TestStandDowns:
    def test_stop_contact_honored_and_terminal(self, agent, store, outbox):
        agent.chase("inv_1", MORNING)
        result = agent.handle_reply("inv_1", "Please stop messaging me.", MORNING)
        assert result.action is Action.STOP_CONTACT_HONORED
        assert store.get_invoice("inv_1").state is InvoiceState.STOP_CONTACT
        blocked = agent.chase("inv_1", MORNING + timedelta(days=5))
        assert blocked.action is Action.BLOCKED
        assert len(outbox.sent) == 1  # nothing sent after the request

    def test_stop_is_honoured_even_when_a_human_owns_it(self, agent, store):
        store.put_invoice(store.get_invoice("inv_1").model_copy(update={"state": InvoiceState.ESCALATED}))
        assert agent.handle_reply("inv_1", "STOP", MORNING).action is Action.STOP_CONTACT_HONORED
        assert store.get_invoice("inv_1").state is InvoiceState.STOP_CONTACT

    def test_dispute_stands_agent_down(self, agent, store):
        result = agent.handle_reply("inv_1", "Invoice amount itself is wrong.", MORNING)
        assert result.action is Action.DISPUTE_STOOD_DOWN
        assert store.get_invoice("inv_1").state is InvoiceState.DISPUTED

    def test_claims_paid_goes_to_a_human_to_reconcile(self, agent, store):
        result = agent.handle_reply("inv_1", "Payment done, please check.", MORNING)
        assert result.action is Action.DISPUTE_STOOD_DOWN
        event = list(store.audit_events())[-1]
        assert event.kind is EventKind.DISPUTE_RECORDED and event.payload["kind"] == "claims_paid"


class TestNegotiation:
    def test_terms_request_gets_an_immediate_gated_offer(self, agent, store, outbox):
        agent.chase("inv_1", MORNING)
        result = agent.handle_reply(
            "inv_1", "Cash flow is tight — any discount if I clear it this week?", MORNING
        )
        assert result.action is Action.COUNTER_OFFERED
        assert result.intervention is InterventionKind.DISCOUNT_OFFER
        assert len(outbox.sent) == 2 and "discount" in outbox.sent[1][2].lower()
        concession = store.live_concession_for("inv_1")
        assert concession is not None and concession.state is ConcessionState.OFFERED
        assert concession.settlement_amount == 242_500  # 3% off ₹2,500
        assert concession.payment_link_url is not None

        accepted = agent.handle_reply("inv_1", "Ok deal. Will pay by Friday.", MORNING + timedelta(hours=1))
        assert accepted.action is Action.OFFER_ACCEPTED
        assert store.live_concession_for("inv_1").state is ConcessionState.ACCEPTED
        promise = store.open_promise_for("inv_1")
        assert promise is not None and promise.amount == 242_500

    def test_discounted_payment_settles_and_nothing_is_chased(self, agent, store, outbox):
        agent.chase("inv_1", MORNING)
        agent.handle_reply("inv_1", "any discount?", MORNING)
        ingest_payment_event(store, rzp_event(amount=242_500),
                             now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC))
        invoice = store.get_invoice("inv_1")
        assert invoice.state is InvoiceState.PAID
        assert invoice.amount_paid == 242_500 and invoice.amount_waived == 7_500
        assert store.concessions_for("inv_1")[0].state is ConcessionState.SETTLED
        assert agent.chase("inv_1", MORNING + timedelta(days=4)).action is Action.BLOCKED
        assert len(outbox.sent) == 2

    def test_over_cap_proposal_is_stripped_not_softened(self, store, outbox):
        brain = ProposingBrain(InterventionRecommendation(
            action=InterventionKind.DISCOUNT_OFFER, proposed_discount_bps=900,
            proposed_pay_by=date(2026, 8, 30), rationale=["model wants 9%"], confidence=0.9,
        ))
        agent = RecoveryAgent(store, brain, outbox, rails=FakeRails())
        result = agent.chase("inv_1", MORNING)
        assert result.action is Action.MESSAGE_SENT
        assert result.intervention is InterventionKind.REMINDER
        assert result.decision.modified
        assert "discount" not in outbox.sent[0][2].lower()
        assert store.live_concession_for("inv_1") is None
        blocked = [e for e in store.audit_events() if e.kind is EventKind.GATE_BLOCKED]
        assert any("exceeds delegated cap" in e.payload["reason"] for e in blocked)

    def test_model_cannot_escalate_on_a_whim(self, store, outbox):
        brain = ProposingBrain(InterventionRecommendation(
            action=InterventionKind.ESCALATE_HUMAN, rationale=["bored"], confidence=0.99))
        agent = RecoveryAgent(store, brain, outbox, rails=FakeRails())
        result = agent.chase("inv_1", MORNING)
        assert result.intervention is InterventionKind.REMINDER
        assert store.get_invoice("inv_1").state is not InvoiceState.ESCALATED

    def test_far_future_promise_is_recorded_as_evidence_but_declined(self, agent, store):
        result = agent.handle_reply("inv_1", "will pay ₹2,500 in 60 days", MORNING)
        assert result.action is Action.PROMISE_RECORDED
        assert not result.commitment_verdict.allowed and "horizon" in result.commitment_verdict.reason
        assert store.open_promise_for("inv_1") is None
        assert store.promises_for("inv_1")[0].state is PromiseState.DECLINED
        assert store.commitments_for("inv_1") == []
        assert store.get_invoice("inv_1").state is InvoiceState.OUTSTANDING


class TestContactDiscipline:
    def test_outside_hours_nothing_is_sent(self, agent, outbox):
        night = datetime(2026, 8, 24, 21, 30, tzinfo=IST)
        assert agent.chase("inv_1", night).action is Action.BLOCKED
        assert outbox.sent == []

    def test_utc_timestamps_are_judged_in_ist(self, agent, outbox):
        # 15:00 UTC is 20:30 IST — outside the window even though 15:00 "looks" fine.
        assert agent.chase("inv_1", datetime(2026, 8, 24, 15, 0, tzinfo=UTC)).action is Action.BLOCKED
        assert outbox.sent == []

    def test_one_attempt_per_day_and_spacing(self, agent, outbox):
        assert agent.chase("inv_1", MORNING).action is Action.MESSAGE_SENT
        assert agent.chase("inv_1", MORNING.replace(hour=15)).action is Action.BLOCKED
        assert agent.chase("inv_1", MORNING + timedelta(days=1)).action is Action.BLOCKED
        assert agent.chase("inv_1", MORNING + timedelta(days=2)).action is Action.MESSAGE_SENT
        assert len(outbox.sent) == 2

    def test_attempt_cap_escalates(self, agent, store):
        day = MORNING
        for _ in range(6):
            assert agent.chase("inv_1", day).action is Action.MESSAGE_SENT
            day += timedelta(days=2)
        assert agent.chase("inv_1", day).action is Action.ESCALATED
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED


class TestReliability:
    def test_brain_outage_defers_and_sends_nothing(self, store, outbox):
        agent = RecoveryAgent(store, FailingBrain(), outbox, rails=FakeRails())
        assert agent.chase("inv_1", MORNING).action is Action.DEFERRED
        assert outbox.sent == []
        assert EventKind.BRAIN_FAILED in kinds(store)
        reply = agent.handle_reply("inv_1", "will pay tomorrow", MORNING)
        assert reply.action is Action.DEFERRED
        assert store.promises_for("inv_1") == []
        received = [e for e in store.audit_events() if e.kind is EventKind.MESSAGE_RECEIVED]
        assert received[0].payload["verbatim"] == "will pay tomorrow"  # never lost

    def test_send_then_crash_cannot_double_message(self, store):
        outbox = CrashingOutbox()
        agent = RecoveryAgent(store, MockBrain(), outbox, rails=FakeRails())
        first = agent.chase("inv_1", MORNING)
        assert first.action is Action.MESSAGE_FAILED
        assert len(outbox.sent) == 1
        assert EventKind.MESSAGE_FAILED in kinds(store)
        # The attempt slot was claimed before sending: a retry today is refused.
        again = agent.chase("inv_1", MORNING.replace(hour=14))
        assert again.action is Action.BLOCKED
        assert len(outbox.sent) == 1
        total, today, _ = store.attempt_facts("inv_1", "2026-08-24")
        assert (total, today) == (1, 1)


class TestHumanRelease:
    def test_release_resets_the_slate(self, agent, store, outbox):
        from urudhi.agent.human import HumanAction, HumanRequest, apply_human_action

        day = MORNING
        for _ in range(2):
            agent.chase("inv_1", day)
            agent.handle_reply("inv_1", "will pay ₹2,500 in 2 days", day)
            day += timedelta(days=3)
            agent.daily_tick(day.date(), day)
        assert store.get_invoice("inv_1").state is InvoiceState.ESCALATED

        payload = apply_human_action(store, "inv_1", HumanRequest(
            action=HumanAction.RELEASE, operator="asha", notes="spoke to owner; plan agreed"),
            day)
        assert payload["to_state"] == "outstanding"
        human = [e for e in store.audit_events() if e.actor is Actor.HUMAN]
        assert human and human[-1].payload["operator"] == "asha"
        # Without the release timestamp the two broken promises would re-escalate at once.
        result = agent.chase("inv_1", day + timedelta(days=1))
        assert result.action is Action.MESSAGE_SENT
