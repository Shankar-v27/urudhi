"""Live demo against Razorpay **test mode** — the real commitment loop.

    python scripts/live_demo.py --brain claude \\
        --reply "Cash konjam tight ah iruku. Friday 50000 kudukuren." \\
        [--amount 50000] [--port 8000] [--public-url https://…trycloudflare.com] [--wait 3600]

 1. a fresh overdue test invoice enters a fresh ledger (unique id per run, so
    every Razorpay ``reference_id`` this run creates is unique too)
 2. one real agent turn: the brain proposes, policy decides, a *real*
    test-mode Payment Link for the balance goes into the sandbox email
 3. the debtor's reply (``--reply``) is interpreted by the chosen brain
 4. the PromiseToPay is recorded — what was said, verbatim
 5. policy runs the commitment checklist
 6. if approved, a **real test-mode Razorpay Payment Link for the exact
    committed amount** is created: ``reference_id`` = commitment id,
    ``notes`` = {invoice_id, commitment_id}, ``expire_by`` = the deadline —
    the amount comes from the approved commitment, never from the model
 7. the commitment is recorded (ACTIVE) and the link is displayed
 8. the webhook receiver starts on ``--port``; pay the link with a Razorpay
    test card / test UPI; Razorpay posts ``payment_link.paid`` /
    ``payment.captured`` to ``<public-url>/webhooks/razorpay``
 9. the signed webhook is verified, de-duplicated, validated and matched to
    the commitment by the id the instrument carried
10. the commitment is FULFILLED, the promise KEPT, the invoice updated
11. recovered money is shown — counted only now
12. the hash-chained audit timeline and the commitment integrity chain print

Nothing is faked: if no webhook arrives before ``--wait`` seconds the script
says so and keeps serving until interrupted.

Prereqs in ``.env``: RAZORPAY_KEY_ID (rzp_test_…), RAZORPAY_KEY_SECRET,
RAZORPAY_WEBHOOK_SECRET (the same value entered in the Razorpay dashboard),
URUDHI_API_TOKEN, and ANTHROPIC_* for ``--brain claude``. Expose the port
with a public HTTPS tunnel and register ``<public-url>/webhooks/razorpay``.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from dotenv import load_dotenv

from urudhi.agent.brain import BRAIN_MODES, BrainConfigError, make_brain
from urudhi.agent.explain import explain_invoice
from urudhi.agent.loop import RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.api.app import create_app
from urudhi.audit.log import verify_chain
from urudhi.config import format_presence_report
from urudhi.ledger.models import Channel, CommitmentState, Debtor, Invoice
from urudhi.ledger.money import format_inr, rupees
from urudhi.observability import configure_logging
from urudhi.rails.razorpay_client import RazorpayRails
from urudhi.store import Store
from urudhi.transport.email import EmailOutbox


def step(n: int, title: str) -> None:
    print(f"\n[{n:02d}] {title}", flush=True)


def say(text: str) -> None:
    print(f"     {text}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--reply", default="Cash konjam tight ah iruku. Friday 50000 kudukuren.",
                        help="the debtor's reply to turn into a commitment ('' to skip)")
    parser.add_argument("--amount", type=int, default=50_000,
                        help="invoice amount in rupees (Razorpay TEST accounts cap a single Payment "
                             "Link — this account refused links above ₹50,000; a refused link is "
                             "audited as rail_failed and the flow continues)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-url", default=os.environ.get("URUDHI_PUBLIC_BASE_URL", ""),
                        help="public HTTPS base URL of the tunnel (for display only)")
    parser.add_argument("--wait", type=int, default=3600,
                        help="seconds to wait for the real webhook before reporting a timeout")
    parser.add_argument("--db", default="data/live_demo.sqlite3")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    configure_logging("INFO")

    step(0, "Configuration (presence only — no secret is ever printed)")
    for line in format_presence_report().splitlines():
        say(line)
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id.startswith("rzp_test_") or not key_secret:
        sys.exit("error: RAZORPAY_KEY_ID (rzp_test_…) and RAZORPAY_KEY_SECRET are required")
    if not os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip():
        sys.exit("error: RAZORPAY_WEBHOOK_SECRET is required (same value as in the Razorpay dashboard)")
    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        sys.exit(f"error: {error}")

    tz = ZoneInfo(os.environ.get("URUDHI_TZ", "Asia/Kolkata"))
    policy = PolicyConfig(timezone=str(tz))
    run_id = datetime.now(tz).strftime("%Y%m%d%H%M%S")
    db = Path(args.db)
    db.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    store = Store(db)

    step(1, "A fresh overdue test invoice enters a fresh ledger")
    debtor = Debtor(
        id=f"deb_live_{run_id}", name="Kumar Textiles", contact_name="Kumar",
        phone="+919800000001", email="void@razorpay.com", preferred_channel=Channel.EMAIL,
        language="ta",
    )
    invoice = Invoice(
        id=f"inv_live_{run_id}", debtor_id=debtor.id, number=f"URU/2026/L{run_id[-6:]}",
        amount=rupees(args.amount),
        issued_on=date.today() - timedelta(days=60), due_on=date.today() - timedelta(days=30),
    )
    store.put_debtor(debtor)
    store.put_invoice(invoice)
    now = datetime.now(UTC)
    say(f"{invoice.number} ({invoice.id}) · {debtor.name} · outstanding {format_inr(invoice.balance)} · "
        f"{invoice.days_overdue(now.date())} days overdue · brain={getattr(brain, 'name', '?')}")

    rails = RazorpayRails(key_id, key_secret)
    outbox = EmailOutbox.from_env()
    agent = RecoveryAgent(store, brain, outbox, policy, rails=rails)

    step(2, "One real agent turn: proposal → policy → test-mode link for the balance → sandbox email")
    result = agent.chase(invoice.id, now)
    say(f"{result.action} ({result.intervention}) — {result.detail}")
    if result.decision is not None:
        for gate in result.decision.gates:
            say(f"  {'✓' if gate.allowed else '✗'} {gate.gate}: {gate.reason}")
    first_link = next((e.payload.get("payment_url") for e in store.events_for(invoice.id)
                       if e.payload.get("payment_url")), None)
    say(f"balance link: {first_link or '(none — turn blocked; commitment flow continues)'}")

    commitment = None
    if args.reply:
        step(3, "The debtor replies; the brain interprets it")
        say(f"“{args.reply}”")
        reply_at = datetime.now(UTC)
        reply = agent.handle_reply(invoice.id, args.reply, reply_at)
        received = next(e for e in reversed(store.events_for(invoice.id))
                        if e.kind.value == "message_received")
        say(f"intent={received.payload['intent']} · amount={received.payload.get('promised_amount')} paise · "
            f"on={received.payload.get('promised_on')} · confidence={received.payload['confidence']:.2f}")

        step(4, "The PromiseToPay is recorded — what was said, verbatim")
        for p in store.promises_for(invoice.id):
            say(f"{p.id}: {format_inr(p.amount)} by {p.promised_on} · {p.state} · “{p.verbatim}”")

        step(5, "Policy runs the commitment checklist")
        if reply.commitment_verdict is None:
            say(f"no commitment evaluated: {reply.action} — {reply.detail}")
        else:
            for check in reply.commitment_verdict.checks:
                say(f"{'✓' if check.allowed else '✗'} {check.gate}: {check.reason}")
            say(f"decision: {'APPROVED' if reply.commitment_verdict.allowed else 'REFUSED'} — "
                f"{reply.commitment_verdict.reason}")

        if reply.commitment_id:
            commitment = store.get_commitment(reply.commitment_id)
            step(6, "A REAL Razorpay test-mode Payment Link for the exact committed amount")
            say(f"Razorpay link id : {commitment.instrument_id}")
            say(f"amount           : {format_inr(commitment.committed_amount)}  (= committed amount; "
                f"the model never set it)")
            say(f"reference_id     : {commitment.id}")
            say(f"notes            : invoice_id={invoice.id}, commitment_id={commitment.id}")
            say(f"expires          : {commitment.due_at.astimezone(tz):%d %b %Y %H:%M} {tz}")
            say(f"PAY HERE         : {commitment.payment_url}")

            step(7, "The commitment is recorded")
            say(f"{commitment.id} · {commitment.state} · source {commitment.source} · "
                f"due {commitment.due_on} · confirmation sent={commitment.instrument_sent}")
        else:
            say("no commitment was created; nothing to pay against")

    step(8, f"Webhook receiver on 127.0.0.1:{args.port} — waiting for the REAL Razorpay webhook")
    app = create_app(
        store, webhook_secret=os.environ["RAZORPAY_WEBHOOK_SECRET"],
        api_token=os.environ.get("URUDHI_API_TOKEN", ""), agent=agent, policy=policy,
        brain_name=agent.brain_name, transport_mode=f"email:{outbox.mode}", rails_mode="razorpay-test",
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    public = args.public_url.rstrip("/")
    say(f"POST {public or 'https://<public-tunnel>'}/webhooks/razorpay  →  127.0.0.1:{args.port}")
    say(f"dashboard API: http://127.0.0.1:{args.port}/api/summary (bearer URUDHI_API_TOKEN) · db {db}")
    say(f"waiting up to {args.wait}s; pay the link with a Razorpay test card / test UPI…")

    deadline = time.time() + args.wait
    seen_rulings: set[str] = set()
    fulfilled = False
    while time.time() < deadline:
        time.sleep(3)
        for row in store._rows("SELECT event_id, status, data FROM webhook_events ORDER BY at"):
            if row[0] not in seen_rulings:
                seen_rulings.add(row[0])
                say(f"webhook {row[0]} → ruled {row[1]}")
        payments = store.payments_for(invoice.id)
        current = store.get_commitment(commitment.id) if commitment else None
        if payments and (current is None or current.state in (
                CommitmentState.FULFILLED, CommitmentState.PARTIALLY_FULFILLED)):
            fulfilled = True
            break

    if not fulfilled:
        say(f"no qualifying webhook within {args.wait}s — nothing is recorded as recovered. "
            f"Still serving; pay the link and re-check /api/summary, or Ctrl-C.")
        thread.join()
        return

    step(9, "Verified, matched to the commitment by the id the instrument carried")
    for p in store.payments_for(invoice.id):
        say(f"payment {p.id} · {format_inr(p.amount)} · {p.method} · razorpay {p.razorpay_payment_id} · "
            f"event {p.razorpay_event_id} · commitment {p.commitment_id} · matched by {p.matched_by}")

    step(10, "Commitment / promise / invoice after the rails spoke")
    inv = store.get_invoice(invoice.id)
    if commitment:
        c = store.get_commitment(commitment.id)
        say(f"commitment {c.id}: {c.state.upper()} · received {format_inr(c.amount_received)} of "
            f"{format_inr(c.committed_amount)} · days late {c.days_late}")
    for p in store.promises_for(invoice.id):
        say(f"promise {p.id}: {p.state.upper()}")
    say(f"invoice {inv.state} · paid {format_inr(inv.amount_paid)} · "
        f"waived {format_inr(inv.amount_waived)} · balance {format_inr(inv.balance)}")

    step(11, "Recovered money — counted only now, from rail events")
    ex = explain_invoice(store, invoice.id, policy, datetime.now(UTC))
    say(f"recovered {format_inr(inv.amount_paid)} of {format_inr(inv.amount)} · "
        f"credibility {ex['credibility']['credibility']:.2f} — {ex['credibility']['summary']}")

    step(12, "Audit chain and commitment integrity")
    events = list(store.audit_events())
    say(f"{len(events)} events, chain verified: {verify_chain(events) == len(events)}")
    for e in events:
        detail = (e.payload.get("reason") or e.payload.get("intent") or e.payload.get("final")
                  or e.payload.get("outcome") or e.payload.get("intervention") or "")
        when = e.at.astimezone(tz)
        say(f"#{e.seq:02d} {when:%d %b %H:%M:%S} {e.actor:<6} {e.kind:<30} {str(detail)[:48]}")
    for chain in ex["commitments"]:
        say(f"SAID “{chain['said']['verbatim']}” → UNDERSTOOD {chain['understood']['intent']} "
            f"{format_inr(chain['understood']['amount'] or 0)} {chain['understood']['on']} → POLICY "
            f"{sum(1 for k in chain['policy']['checks'] if k['allowed'])}/{len(chain['policy']['checks'])} → "
            f"INSTRUMENT {chain['instrument']['id']} {format_inr(chain['instrument']['amount'])} → RAIL "
            f"{', '.join(format_inr(r['amount']) for r in chain['rail'] if 'amount' in r) or 'none'} → "
            f"OUTCOME {chain['outcome']['state'].upper()} / promise {chain['outcome']['promise_state']}")
    say("still serving so late/duplicate deliveries are ruled on; Ctrl-C to stop")
    thread.join()


if __name__ == "__main__":
    main()
