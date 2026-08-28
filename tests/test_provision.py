"""Provisioning real instruments for selected commitments: controlled, idempotent, honest."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from urudhi.agent.brain import MockBrain
from urudhi.agent.loop import RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import EventKind
from urudhi.ledger.models import Debtor, InstrumentMode, Invoice, RecordOrigin
from urudhi.provision import provision
from urudhi.rails.razorpay_client import FakeRails
from urudhi.store import Store

IST = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 8, 24, 11, 0, tzinfo=IST)


class Outbox:
    def send(self, *a, **k):
        return "m"


class RecordingTestRails(FakeRails):
    """Stands in for RazorpayRails in tests: same protocol, explicit test-mode identity."""

    mode = "razorpay_test"

    def create_payment_link(self, **kwargs):
        link = super().create_payment_link(**kwargs)
        n = len(self.links)
        link["id"] = f"plink_TEST{n:04d}"
        link["short_url"] = f"https://rzp.io/rzp/test{n:04d}"   # what Razorpay would return
        return link


class RefusingRails(RecordingTestRails):
    def create_payment_link(self, **kwargs):
        raise RuntimeError("amount exceeds maximum amount allowed.")


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.put_debtor(Debtor(id="deb_1", name="ACME", contact_name="Meena", phone="+919800000003",
                            email="m@acme.example.in"))
        s.put_invoice(Invoice(id="inv_1", debtor_id="deb_1", number="URU/2026/0007", amount=5_000_000,
                              issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1)))
        yield s


def commitment_without_instrument(store):
    """A commitment created with no rail at all (instrument never issued)."""
    agent = RecoveryAgent(store, MockBrain(), Outbox(), PolicyConfig(), rails=None)
    result = agent.handle_reply("inv_1", "Will pay ₹20,000 by Friday.", MORNING)
    assert result.commitment_id
    return store.get_commitment(result.commitment_id)


class TestProvision:
    def test_provisions_exact_amount_reference_and_verbatim_url(self, store):
        c = commitment_without_instrument(store)
        assert c.instrument_id is None and c.origin is RecordOrigin.SIMULATION
        rails = RecordingTestRails()
        [r] = provision(store, rails, [c.id], now=MORNING)
        assert r.outcome == "provisioned"
        [link] = rails.links
        assert link["amount"] == 2_000_000 and link["reference_id"] == c.id
        assert link["notes"] == {"invoice_id": "inv_1", "commitment_id": c.id}
        updated = store.get_commitment(c.id)
        assert updated.instrument_id == "plink_TEST0001"
        assert updated.payment_url == "https://rzp.io/rzp/test0001"     # verbatim, not rebuilt
        assert updated.instrument_mode is InstrumentMode.RAZORPAY_TEST
        created = [e for e in store.audit_events() if e.kind is EventKind.PAYMENT_INSTRUMENT_CREATED]
        assert created and created[-1].payload["instrument_mode"] == "razorpay_test"

    def test_idempotent_second_run_creates_nothing(self, store):
        c = commitment_without_instrument(store)
        rails = RecordingTestRails()
        provision(store, rails, [c.id], now=MORNING)
        [again] = provision(store, rails, [c.id], now=MORNING)
        assert again.outcome == "skipped_has_instrument" and len(rails.links) == 1

    def test_dry_run_touches_nothing(self, store):
        c = commitment_without_instrument(store)
        rails = RecordingTestRails()
        [r] = provision(store, rails, [c.id], dry_run=True, now=MORNING)
        assert r.outcome == "dry_run" and rails.links == []
        assert store.get_commitment(c.id).instrument_id is None

    def test_refuses_to_replace_a_sandbox_instrument(self, store):
        agent = RecoveryAgent(store, MockBrain(), Outbox(), PolicyConfig(), rails=FakeRails())
        cid = agent.handle_reply("inv_1", "Will pay ₹20,000 by Friday.", MORNING).commitment_id
        assert store.get_commitment(cid).instrument_mode is InstrumentMode.SANDBOX
        rails = RecordingTestRails()
        [r] = provision(store, rails, [cid], now=MORNING)
        assert r.outcome == "refused_sandbox" and rails.links == []

    def test_missing_and_limit(self, store):
        c = commitment_without_instrument(store)
        rails = RecordingTestRails()
        assert provision(store, rails, ["cmt_nope"], now=MORNING)[0].outcome == "missing"
        [r] = provision(store, rails, limit=5, now=MORNING)
        assert r.commitment_id == c.id and r.outcome == "provisioned"

    def test_refusal_is_recorded_not_raised(self, store):
        c = commitment_without_instrument(store)
        [r] = provision(store, RefusingRails(), [c.id], now=MORNING)
        assert r.outcome == "failed" and "maximum amount" in r.detail
        updated = store.get_commitment(c.id)
        assert updated.instrument_failed and updated.instrument_id is None
        assert any(e.kind is EventKind.RAIL_FAILED for e in store.audit_events())
