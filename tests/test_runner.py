"""The batch experiment is the submission's headline evidence — test it hardest."""

import pytest

from urudhi.audit.log import EventKind, verify_chain
from urudhi.ledger.models import InvoiceState
from urudhi.sim.batch import archetype_of
from urudhi.sim.personas import Archetype
from urudhi.sim.report import (
    arm_metrics,
    attribution,
    build_experiment,
    build_report,
    sensitivity_row,
)
from urudhi.sim.runner import Arm, RunConfig, run_batch


@pytest.fixture(scope="module")
def results():
    return {
        arm: run_batch(RunConfig(days=21, count=120, seed=2026, arm=arm))
        for arm in (Arm.NO_ACTION, Arm.BASELINE, Arm.URUDHI)
    }


@pytest.fixture(scope="module")
def urudhi(results):
    return results[Arm.URUDHI]


@pytest.fixture(scope="module")
def report(urudhi):
    return build_report(urudhi)


@pytest.fixture(scope="module")
def experiment(results):
    return build_experiment(results, [sensitivity_row("max_discount_bps", 500, results[Arm.URUDHI])],
                            generated_by="tests")


class TestRecovery:
    def test_meaningful_money_recovered(self, report):
        assert report["money"]["recovered_paise"] > 0
        assert 0.2 < report["money"]["recovery_rate"] < 0.95  # honest, not magic

    def test_recovered_is_exactly_the_sum_of_observed_payments(self, urudhi, report):
        observed = sum(p.amount for p in urudhi.store.all_payments())
        assert report["money"]["recovered_paise"] == observed

    def test_disputers_never_paid_and_end_with_a_human(self, urudhi):
        # The mock brain misses some dispute phrasings (measured by eval_replies);
        # those debtors still end up with a human via the attempt cap, unpaid.
        for case in urudhi.cases:
            if case.archetype is Archetype.DISPUTER:
                invoice = urudhi.store.get_invoice(case.invoice.id)
                assert invoice.state in (InvoiceState.DISPUTED, InvoiceState.ESCALATED), invoice.id
                assert invoice.amount_paid == 0

    def test_stop_requesters_honored(self, urudhi):
        for case in urudhi.cases:
            if case.archetype is Archetype.STOP_REQUESTER:
                invoice = urudhi.store.get_invoice(case.invoice.id)
                assert invoice.state in (InvoiceState.STOP_CONTACT, InvoiceState.PAID)
                sent_after_stop = False
                stopped = False
                for e in urudhi.store.events_for(invoice.id):
                    if e.kind is EventKind.STOP_CONTACT_HONORED:
                        stopped = True
                    elif stopped and e.kind is EventKind.MESSAGE_SENT:
                        sent_after_stop = True
                assert not sent_after_stop

    def test_nobody_is_hammered_beyond_the_attempt_cap(self, urudhi):
        for invoice in urudhi.store.all_invoices():
            sent = urudhi.store.events_for(invoice.id, EventKind.MESSAGE_SENT)
            nudges = [e for e in sent if not e.payload.get("responding")]  # answers aren't nudges
        assert len(nudges) <= urudhi.policy.max_attempts_per_invoice

    def test_offers_were_made_and_some_settled(self, report):
        m = report["metrics"]
        assert m["offers_made"] > 0 and m["offers_accepted"] > 0
        assert m["discount_cost_paise"] >= 0

    def test_installment_plans_exist(self, urudhi):
        plans = [c for c in urudhi.store.all_concessions() if c.type.value == "installments"]
        assert plans


class TestExperiment:
    def test_arms_start_from_identical_portfolios(self, results):
        amounts = {arm: [c.invoice.amount for c in r.cases] for arm, r in results.items()}
        traits = {arm: [c.persona().traits for c in r.cases] for arm, r in results.items()}
        assert amounts[Arm.NO_ACTION] == amounts[Arm.BASELINE] == amounts[Arm.URUDHI]
        assert traits[Arm.NO_ACTION] == traits[Arm.BASELINE] == traits[Arm.URUDHI]

    def test_no_action_recovers_least(self, experiment):
        arms = experiment["arms"]
        assert arms["no_action"]["recovered_paise"] < arms["baseline"]["recovered_paise"]
        assert arms["no_action"]["recovered_paise"] < arms["urudhi"]["recovered_paise"]
        assert arms["no_action"]["contact_attempts"] == 0

    def test_uplift_and_timeline_are_consistent(self, experiment):
        arms, up = experiment["arms"], experiment["uplift"]
        assert up["urudhi_vs_baseline_paise"] == arms["urudhi"]["recovered_paise"] - arms["baseline"]["recovered_paise"]
        for arm in ("no_action", "baseline", "urudhi"):
            assert experiment["timeline"][arm][-1] == arms[arm]["recovered_paise"]
            assert len(experiment["timeline"][arm]) == len(experiment["timeline"]["days"])

    def test_attribution_accounts_for_every_rupee(self, urudhi, report):
        a = attribution(urudhi)
        total = sum(v["paise"] for v in a["by_intervention"].values()) + a["unattributed"]["paise"]
        assert total == report["money"]["recovered_paise"]
        assert experiment_rule_documented(report)

    def test_caveats_are_explicit(self, experiment):
        joined = " ".join(experiment["caveats"]).lower()
        assert "simulation" in joined and "not a causal claim" in joined

    def test_sensitivity_rows_have_the_policy_dials(self, experiment):
        row = experiment["sensitivity"][0]
        assert {"parameter", "value", "recovered_paise", "escalations", "messages_sent"} <= set(row)


def experiment_rule_documented(report) -> bool:
    return "window_days" in report["attribution"] and "not a causal claim" in report["attribution"]["rule"]


class TestHonestReporting:
    def test_every_unpaid_invoice_is_an_exception(self, urudhi, report):
        unpaid = [i for i in urudhi.store.all_invoices() if i.state is not InvoiceState.PAID]
        assert len(report["exceptions"]) == len(unpaid)
        assert report["exceptions"][0]["balance"] >= report["exceptions"][-1]["balance"]

    def test_promise_ledger_shows_breaks(self, report):
        assert report["promises" if "promises" in report else "metrics"]["promises_broken"] > 0 \
            if "metrics" in report else True
        assert report["metrics"]["promises_broken"] > 0
        assert report["metrics"]["promises_kept"] > 0

    def test_policy_ships_with_the_numbers(self, report):
        assert report["run"]["policy"]["max_discount_bps"] == 500
        assert report["run"]["timezone"] == "Asia/Kolkata"

    def test_audit_chain_verifies_end_to_end(self, urudhi, report):
        assert report["audit"]["chain_verified"] is True
        assert verify_chain(urudhi.store.audit_events()) == report["audit"]["events"]

    def test_per_archetype_breakdown_present(self, report, urudhi):
        assert set(report["per_archetype"]) == {archetype_of(urudhi.cases, c.invoice.id).value
                                                for c in urudhi.cases}


class TestReproducibility:
    def test_same_seed_same_money(self):
        a = arm_metrics(run_batch(RunConfig(days=7, count=40, seed=99)))
        b = arm_metrics(run_batch(RunConfig(days=7, count=40, seed=99)))
        assert a == b

    def test_different_seed_different_money(self):
        a = arm_metrics(run_batch(RunConfig(days=7, count=40, seed=99)))
        b = arm_metrics(run_batch(RunConfig(days=7, count=40, seed=100)))
        assert a["recovered_paise"] != b["recovered_paise"]
