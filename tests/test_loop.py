"""End-to-end scenarios through the recovery loop with the mock brain,
an in-memory store, and a captured outbox — no network, fully deterministic."""

from datetime import UTC, date, datetime

import pytest

from urudhi.agent.brain import MockBrain
from urudhi.agent.loop import Action, RecoveryAgent, chaseable
from urudhi.agent.policy import Offer, OfferType
from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.models import Channel, Debtor, Invoice, InvoiceState, PromiseState
from urudhi.rails.webhooks import ingest_payment_event
from urudhi.store import Store

MORNING = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)


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

    def send(self, debtor, channel, text):
        self.sent.append((debtor.id, channel, text))


@pytest.fixture
def outbox():
    return CapturingOutbox()


@pytest.fixture
def agent(store, outbox):
    return RecoveryAgent(store, MockBrain(), outbox)


def rzp_event(event_id="evt_1", amount=250_000):
    return {
        "id": event_id,
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": f"pay_rzp_{event_id}", "amount": amount, "method": "upi",
            "notes": {"invoice_id": "inv_1"},
        }}},
    }


class TestHappyPath:
    def test_chase_promise_payment_kept(self, agent, store, outbox):
        assert agent.chase("inv_1", MORNING).action is Action.MESSAGE_SENT
        assert len(outbox.sent) == 1

        result = agent.handle_reply(
            "inv_1", "Sorry sir, will pay ₹2,500 in 3 days.", MORNING
        )
        assert result.action is Action.PROMISE_RECORDED
        assert store.get_invoice("inv_1").state is InvoiceState.PROMISED

        ingest_payment_event(store, rzp_event(),
                             now=datetime(2026, 8, 26, 15, 0, tzinfo=UTC))
        assert store.get_invoice("inv_1").state is InvoiceState.PAID
        assert store.promises_for("inv_1")[0].state is PromiseState.KEPT

        kinds = [e.kind for e in store.audit_events()]
        assert EventKind.GATE_ALLOWED in kinds
        assert EventKind.MESSAGE_SENT in kinds
        assert EventKind.PROMISE_RECORDED in kinds
        assert EventKind.PAYMENT_OBSERVED in kinds
        assert verify_chain(store.audit_events()) == len(kinds)


class TestBrokenPromisesEscalate:
    def test_two_broken_promises_hand_over_to_human(self, agent, store):
        day = datetime(2026, 8, 24, 11, 0, tzinfo=UTC)
        for _ in range(2):
            agent.chase("inv_1", day)
            agent.handle_reply("inv_1", "will pay ₹2,500 in 2 days", day)
            day = day.replace(day=day.day + 3)
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


class TestStandDowns:
    def test_stop_contact_honored_and_terminal(self, agent, store, outbox):
        agent.chase("inv_1", MORNING)
        result = agent.handle_reply("inv_1", "Please stop messaging me.", MORNING)
        assert result.action is Action.STOP_CONTACT_HONORED
        assert store.get_invoice("inv_1").state is InvoiceState.STOP_CONTACT
        blocked = agent.chase("inv_1", MORNING.replace(day=25))
        assert blocked.action is Action.BLOCKED
        assert len(outbox.sent) == 1  # nothing sent after the request

    def test_dispute_stands_agent_down(self, agent, store):
        result = agent.handle_reply("inv_1", "We already paid this in July!", MORNING)
        assert result.action is Action.DISPUTE_STOOD_DOWN
        assert store.get_invoice("inv_1").state is InvoiceState.DISPUTED


class TestBoundedConcessions:
    def test_gated_offer_reaches_the_message(self, agent, outbox):
        offer = Offer(type=OfferType.DISCOUNT, invoice_id="inv_1",
                      discount_bps=300, pay_by=date(2026, 8, 28))
        agent.chase("inv_1", MORNING, offer=offer)
        assert "early-payment discount" in outbox.sent[0][2]

    def test_over_cap_offer_is_stripped_not_softened(self, agent, store, outbox):
        offer = Offer(type=OfferType.DISCOUNT, invoice_id="inv_1",
                      discount_bps=900, pay_by=date(2026, 8, 28))
        result = agent.chase("inv_1", MORNING, offer=offer)
        assert result.action is Action.MESSAGE_SENT
        assert "discount" not in outbox.sent[0][2].lower()
        blocked = [e for e in store.audit_events() if e.kind is EventKind.GATE_BLOCKED]
        assert any("exceeds delegated cap" in e.payload["reason"] for e in blocked)

    def test_far_future_promise_is_countered_not_recorded(self, agent, store):
        result = agent.handle_reply("inv_1", "will pay ₹2,500 in 60 days", MORNING)
        assert result.action is Action.COUNTER_OFFERED
        assert store.open_promise_for("inv_1") is None


class TestContactDiscipline:
    def test_outside_hours_nothing_is_sent(self, agent, outbox):
        night = datetime(2026, 8, 24, 21, 30, tzinfo=UTC)
        assert agent.chase("inv_1", night).action is Action.BLOCKED
        assert outbox.sent == []

    def test_one_attempt_per_day(self, agent, outbox):
        assert agent.chase("inv_1", MORNING).action is Action.MESSAGE_SENT
        again = agent.chase("inv_1", MORNING.replace(hour=15))
        assert again.action is Action.BLOCKED
        assert len(outbox.sent) == 1
