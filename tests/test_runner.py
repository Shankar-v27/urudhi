"""The batch run is the submission's headline evidence — test it hardest."""

import pytest

from urudhi.audit.log import verify_chain
from urudhi.ledger.models import InvoiceState
from urudhi.sim.personas import Archetype
from urudhi.sim.report import build_report
from urudhi.sim.runner import RunConfig, run_batch


@pytest.fixture(scope="module")
def result():
    return run_batch(RunConfig(days=21, count=120, seed=2026))


@pytest.fixture(scope="module")
def report(result):
    return build_report(result)


class TestRecovery:
    def test_meaningful_money_recovered(self, report):
        assert report["money"]["recovered_paise"] > 0
        assert 0.3 < report["money"]["recovery_rate"] < 0.95  # honest, not magic

    def test_prompt_payers_fully_recovered(self, result):
        for case in result.cases:
            if case.persona.archetype is Archetype.PROMPT_PAYER:
                invoice = result.store.get_invoice(case.invoice.id)
                assert invoice.state is InvoiceState.PAID, case.invoice.id

    def test_disputers_never_paid_and_stood_down(self, result):
        for case in result.cases:
            if case.persona.archetype is Archetype.DISPUTER:
                invoice = result.store.get_invoice(case.invoice.id)
                assert invoice.state is InvoiceState.DISPUTED
                assert invoice.amount_paid == 0

    def test_stop_requesters_honored(self, result):
        for case in result.cases:
            if case.persona.archetype is Archetype.STOP_REQUESTER:
                invoice = result.store.get_invoice(case.invoice.id)
                assert invoice.state is InvoiceState.STOP_CONTACT

    def test_ghosts_end_escalated_not_hammered(self, result):
        for case in result.cases:
            if case.persona.archetype is Archetype.GHOST:
                invoice = result.store.get_invoice(case.invoice.id)
                assert invoice.state is InvoiceState.ESCALATED
                sent = [
                    e for e in result.store.audit_events()
                    if e.kind.value == "message_sent" and e.invoice_id == invoice.id
                ]
                assert len(sent) <= result.policy.max_attempts_per_invoice


class TestHonestReporting:
    def test_every_unpaid_invoice_is_an_exception(self, result, report):
        unpaid = [
            i for i in result.store.all_invoices() if i.state is not InvoiceState.PAID
        ]
        assert len(report["exceptions"]) == len(unpaid)
        assert report["exceptions"][0]["balance"] >= report["exceptions"][-1]["balance"]

    def test_promise_ledger_shows_breaks(self, report):
        assert report["promises"]["broken"] > 0  # promise-breakers exist; say so
        assert report["promises"]["kept"] > 0

    def test_policy_ships_with_the_numbers(self, report):
        assert report["run"]["policy"]["max_discount_bps"] == 500

    def test_audit_chain_verifies_end_to_end(self, result, report):
        assert report["audit"]["chain_verified"] is True
        assert verify_chain(result.store.audit_events()) == report["audit"]["events"]


class TestReproducibility:
    def test_same_seed_same_money(self):
        a = build_report(run_batch(RunConfig(days=7, count=40, seed=99)))
        b = build_report(run_batch(RunConfig(days=7, count=40, seed=99)))
        assert a["money"] == b["money"]
        assert a["promises"] == b["promises"]
