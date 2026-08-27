"""The flagship demo: a debtor's words become a bounded, executable commitment.

    python scripts/demo.py [--brain mock|claude] [--reply "..."]

Scenario A — promise → commitment → payment link → webhook → fulfilled:
 1. an overdue invoice enters the ledger
 2. Urudhi prioritises it (explainable score)
 3. the debtor replies in messy Tamil-English
 4. the brain interprets it into a typed promise (amount, date, confidence)
 5. the ledger records the promise — what was said, verbatim
 6. policy evaluates the commitment checklist
 7. the commitment is approved: exact amount, exact deadline
 8. a Razorpay Payment Link (fake rail here; test-mode in live_demo.py) is
    created for that amount, tagged invoice_id + commitment_id, expiring at
    the deadline — and the debtor is told
 9. the exact committed amount and expiry are shown
10. the payment happens: a signed ``payment_link.paid`` webhook arrives at the
    real receiver carrying the commitment id
11. the webhook is verified, matched to the commitment exactly
12. the commitment is FULFILLED, the promise KEPT, the invoice partially paid
13. the dashboard's numbers update — recovered money is counted only now
14. the hash-chained audit trail proves every step, and the commitment
    integrity chain is printed

Scenario B — promise broken twice → automated chasing stops → human:
    two commitments missed → escalation → the escalation queue shows the
    commitment record → a person approves a new arrangement → a fresh
    commitment with its own link, back under automation.

Nothing here is a mock of the flow: every call is the same code path the
batch runner and the API use. Only the debtor and the rail are stand-ins.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from urudhi.agent.brain import BRAIN_MODES, BrainConfigError, make_brain
from urudhi.agent.explain import explain_invoice
from urudhi.agent.loop import RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.api.app import create_app
from urudhi.audit.log import verify_chain
from urudhi.ledger.models import Channel, Debtor, Invoice
from urudhi.ledger.money import format_inr, rupees
from urudhi.observability import configure_logging
from urudhi.rails.razorpay_client import FakeRails
from urudhi.store import Store
from urudhi.transport.email import EmailOutbox

IST = ZoneInfo("Asia/Kolkata")
SECRET = "whsec_demo"
TOKEN = "demo-token-12345"


def step(n: int, title: str) -> None:
    print(f"\n[{n:02d}] {title}")


def say(text: str) -> None:
    print(f"     {text}")


def signed(client: TestClient, body: dict) -> dict:
    raw = json.dumps(body).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post("/webhooks/razorpay", content=raw, headers={"x-razorpay-signature": sig})
    return {"status_code": response.status_code, **response.json()}


def print_chain(chain: dict) -> None:
    said, understood, policy = chain["said"], chain["understood"], chain["policy"]
    instrument, outcome = chain["instrument"], chain["outcome"]
    say(f"WHAT WAS SAID          “{said['verbatim']}”  (#{said['event']['seq'] if said['event'] else '—'})")
    say(f"WHAT AI UNDERSTOOD     {understood['intent']} · {format_inr(understood['amount'] or 0)} · "
        f"{understood['on']} · confidence {understood['confidence']:.2f}"
        f"{' · partial' if understood['partial'] else ''}")
    ok = sum(1 for c in policy["checks"] if c["allowed"])
    say(f"WHAT POLICY ALLOWED    {ok}/{len(policy['checks'])} checks passed — {policy['reason']}"
        f"  (#{policy['event']['seq'] if policy['event'] else '—'})")
    say(f"PAYMENT INSTRUMENT     {instrument['type']} {instrument['id']} · "
        f"{format_inr(instrument['amount'])} · expires {instrument['expires'][:16]} · "
        f"{instrument['url']} · sent={instrument['sent']}")
    for r in chain["rail"]:
        if "payment_id" in r:
            say(f"MONEY ON THE RAILS     {format_inr(r['amount'])} · {r['method']} · "
                f"{r['razorpay_payment_id']} · matched by {r['matched_by']}")
    if not chain["rail"]:
        say("MONEY ON THE RAILS     nothing observed yet")
    say(f"FINAL OUTCOME          commitment {outcome['state'].upper()} · promise "
        f"{(outcome['promise_state'] or '—').upper()} · received "
        f"{format_inr(chain['amount_received'])} of {format_inr(chain['committed_amount'])}"
        f"{f' · {chain['days_late']} day(s) late' if chain['days_late'] else ''}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--reply",
                        default="Cash konjam tight ah iruku. Friday 50k kudukuren, balance next month.")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    configure_logging("WARNING")
    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        sys.exit(f"error: {error}")

    outbox_dir = Path(tempfile.mkdtemp(prefix="urudhi-demo-outbox-"))
    store = Store(":memory:")
    rails = FakeRails()
    outbox = EmailOutbox("sandbox", directory=outbox_dir)
    policy = PolicyConfig()
    agent = RecoveryAgent(store, brain, outbox, policy, rails=rails)
    clock = {"now": datetime(2026, 8, 24, 11, 0, tzinfo=IST)}
    app = create_app(store, webhook_secret=SECRET, api_token=TOKEN, agent=agent,
                     brain_name=agent.brain_name, transport_mode="email:sandbox", rails_mode="fake",
                     clock=lambda: clock["now"])
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}

    print("=" * 78)
    print("SCENARIO A — a promise becomes an executable commitment, and is kept")
    print("=" * 78)

    step(1, "An overdue invoice enters the ledger")
    debtor = Debtor(id="deb_1", name="Kumar Textiles", contact_name="Kumar", phone="+919800000001",
                    email="accounts@kumartextiles.example.in", preferred_channel=Channel.EMAIL, language="ta")
    invoice = Invoice(id="inv_1", debtor_id="deb_1", number="URU/2026/0001", amount=rupees(1_80_000),
                      issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1))
    store.put_debtor(debtor)
    store.put_invoice(invoice)
    now = clock["now"]
    say(f"{invoice.number} · {debtor.name} · outstanding {format_inr(invoice.balance)} · "
        f"{invoice.days_overdue(now.date())} days overdue · brain={agent.brain_name}")

    step(2, "Urudhi prioritises it, then sends the first gated reminder")
    ex = explain_invoice(store, "inv_1", policy, now)
    say(f"priority {ex['priority']['score']}/100")
    for reason in ex["priority"]["reasons"]:
        say(f"  {reason}")
    first = agent.chase("inv_1", now)
    say(f"proposed {first.decision.proposed.action} → final {first.intervention}"
        f"{'  [modified by policy]' if first.decision.modified else ''}")

    step(3, "The debtor replies (messy, code-switched)")
    say(f"“{args.reply}”")

    step(4, "The brain interprets it into a typed promise")
    reply_at = now + timedelta(hours=2)
    result = agent.handle_reply("inv_1", args.reply, reply_at)
    received = next(e for e in reversed(store.events_for("inv_1")) if e.kind.value == "message_received")
    say(f"intent={received.payload['intent']} · amount={received.payload.get('promised_amount')} paise · "
        f"on={received.payload.get('promised_on')} · confidence={received.payload['confidence']:.2f} · "
        f"flags={received.payload.get('flags')}")

    step(5, "The ledger records what was said — the promise, verbatim")
    for p in store.promises_for("inv_1"):
        say(f"{p.id}: {format_inr(p.amount)} by {p.promised_on} · state {p.state} · “{p.verbatim}”")
    promised = store.promises_for("inv_1")[0].amount
    say(f"remaining after the promise would be {format_inr(invoice.balance - promised)}")

    step(6, "Policy evaluates the commitment checklist")
    verdict = result.commitment_verdict
    for check in verdict.checks:
        say(f"{'✓' if check.allowed else '✗'} {check.gate}: {check.reason}")
    say(f"decision: {'APPROVED' if verdict.allowed else 'REFUSED'} — {verdict.reason}")

    step(7, "The commitment is created — exact amount, exact deadline")
    assert result.commitment_id, result.detail
    commitment = store.get_commitment(result.commitment_id)
    say(f"{commitment.id} · {format_inr(commitment.committed_amount)} · due {commitment.due_on} "
        f"({commitment.due_at.astimezone(IST):%d %b %Y %H:%M} IST) · source {commitment.source} · "
        f"state {commitment.state}")

    step(8, "A Razorpay Payment Link is created for that amount and the debtor is told")
    link = rails.links[-1]
    say(f"link {link['id']} · amount {format_inr(link['amount'])} · reference {link['reference_id']} · "
        f"notes {link['notes']} · expires {datetime.fromtimestamp(link['expire_by'], IST):%d %b %H:%M}")
    say(f"url  {link['short_url']}")
    sent = store.events_for("inv_1")[-1]
    say(f"eml  {sorted(outbox_dir.glob('*.eml'))[-1].name}")
    for line in sent.payload["text"].splitlines():
        print(f"     | {line}")

    step(9, "Committed amount + expiry (a link is not money)")
    say(f"committed {format_inr(commitment.committed_amount)} by {commitment.due_on} · "
        f"received so far {format_inr(commitment.amount_received)} · "
        f"invoice recovered {format_inr(store.get_invoice('inv_1').amount_paid)}")

    step(10, "The debtor pays: a signed payment_link.paid webhook reaches the real receiver")
    clock["now"] = reply_at + timedelta(days=2)
    body = {"id": "evt_demo_1", "event": "payment_link.paid", "payload": {
        "payment": {"entity": {"id": "pay_demo_1", "amount": commitment.committed_amount, "currency": "INR",
                               "method": "upi", "notes": link["notes"]}},
        "payment_link": {"entity": {"id": link["id"], "reference_id": link["reference_id"],
                                    "notes": link["notes"]}}}}
    say(f"POST /webhooks/razorpay → {signed(client, body)}")

    step(11, "Verified, de-duplicated, matched to the commitment exactly")
    say(f"same event again → {signed(client, body)['status']}")
    payment = store.payments_for("inv_1")[0]
    say(f"payment {payment.id} · {format_inr(payment.amount)} · commitment {payment.commitment_id} · "
        f"matched by {payment.matched_by}")

    step(12, "Commitment FULFILLED · promise KEPT · invoice partially paid")
    commitment = store.get_commitment(commitment.id)
    inv = store.get_invoice("inv_1")
    say(f"commitment {commitment.state} · received {format_inr(commitment.amount_received)} · "
        f"days late {commitment.days_late}")
    say(f"promise {store.promises_for('inv_1')[0].state}")
    say(f"invoice {inv.state} · paid {format_inr(inv.amount_paid)} · balance {format_inr(inv.balance)}")

    step(13, "Recovery metrics update — money is counted only now")
    summary = client.get("/api/summary", headers=auth).json()
    c = summary["commitments"]
    say(f"recovered {format_inr(summary['recovered_paise'])} of {format_inr(summary['outstanding_paise'])} · "
        f"commitments {c['created']} created / {c['fulfilled']} fulfilled · "
        f"exact-matched {format_inr(c['exact_instrument_matched_paise'])} · "
        f"₹/attempt {format_inr(c['recovered_per_attempt_paise'] or 0)}")
    ex = explain_invoice(store, "inv_1", policy, clock["now"])
    say(f"credibility now {ex['credibility']['credibility']:.2f} — {ex['credibility']['summary']}")

    step(14, "The audit chain proves every step; the commitment integrity chain")
    events = list(store.audit_events())
    say(f"{len(events)} events, chain verified: {verify_chain(events) == len(events)}")
    for e in events:
        detail = (e.payload.get("reason") or e.payload.get("intent") or e.payload.get("final")
                  or e.payload.get("outcome") or e.payload.get("intervention") or "")
        say(f"#{e.seq:02d} {e.at.astimezone(IST):%d %b %H:%M} {e.actor:<6} {e.kind:<30} {str(detail)[:48]}")
    print()
    print_chain(ex["commitments"][0])

    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("SCENARIO B — a promise broken twice: chasing stops, a human takes over")
    print("=" * 78)
    debtor2 = Debtor(id="deb_2", name="Salem Steel Syndicate", contact_name="Ravi", phone="+919800000002",
                     email="accounts@salemsteel.example.in", preferred_channel=Channel.EMAIL, language="hi")
    invoice2 = Invoice(id="inv_2", debtor_id="deb_2", number="URU/2026/0002", amount=rupees(50_000),
                       issued_on=date(2026, 5, 1), due_on=date(2026, 6, 15))
    store.put_debtor(debtor2)
    store.put_invoice(invoice2)
    day = datetime(2026, 8, 24, 11, 0, tzinfo=IST)
    for round_no, words in enumerate(("Bhai 2 din mein full amount kar dunga pakka.",
                                      "Sorry sir, is baar pakka — 2 din mein kar denge."), start=1):
        step(round_no, f"Round {round_no}: the debtor promises again")
        clock["now"] = day
        agent.chase("inv_2", day)
        r = agent.handle_reply("inv_2", words, day + timedelta(hours=1))
        say(f"“{words}” → {r.action} · {r.detail}")
        day = day + timedelta(days=3)
        ticked = agent.daily_tick(day.date(), day)
        cm = store.commitments_for("inv_2")[-1]
        say(f"{day.date()}: commitment {cm.id} is {cm.state.upper()} (received "
            f"{format_inr(cm.amount_received)}); tick → {[t.action.value for t in ticked]}")

    step(3, "Policy: two missed commitments → escalation, live commitments cancelled")
    inv2 = store.get_invoice("inv_2")
    say(f"invoice {inv2.state} · missed commitments "
        f"{sum(1 for c in store.commitments_for('inv_2') if c.state.value == 'missed')}")
    queue = client.get("/api/escalations", headers=auth).json()
    row = next(q for q in queue if q["invoice_id"] == "inv_2")
    lc = row["last_commitment"]
    say(f"escalation queue: {row['number']} · reason “{row['reason']}” · last commitment "
        f"{format_inr(lc['committed_amount'])} by {lc['due_on']} received "
        f"{format_inr(lc['amount_received'])} · credibility {row['credibility']:.2f} · "
        f"recommended: {row['recommended_action']}")
    blocked = agent.chase("inv_2", day + timedelta(days=1))
    say(f"automated chase now → {blocked.action}: {blocked.detail}")

    step(4, "A human approves a new arrangement (policy still checks it)")
    clock["now"] = day + timedelta(hours=2)
    response = client.post("/api/invoices/inv_2/human", headers=auth, json={
        "action": "arrange", "operator": "priya", "notes": "Spoke to Ravi; ₹25,000 by the 5th, rest after",
        "amount": rupees(25_000), "due_on": "2026-09-05"})
    say(f"POST /api/invoices/inv_2/human → {response.status_code} {response.json()}")
    cm = store.get_commitment(response.json()["commitment_id"])
    say(f"{cm.id} · source {cm.source} · {format_inr(cm.committed_amount)} by {cm.due_on} · "
        f"link {cm.payment_url} · invoice {store.get_invoice('inv_2').state}")
    events = list(store.audit_events())
    say(f"audit chain: {len(events)} events, verified {verify_chain(events) == len(events)}")

    print(f"\nhealth: {json.dumps(client.get('/health').json()['counters'])[:240]}…")
    print(f"outbox: {outbox_dir}")
    os.environ.pop("ANTHROPIC_API_KEY", None)


if __name__ == "__main__":
    main()
