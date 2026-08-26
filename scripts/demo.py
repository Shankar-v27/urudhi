"""One invoice, end to end, in the terminal — the demo flow in eleven steps.

    python scripts/demo.py [--brain mock|claude] [--reply "..."]

 1. an overdue invoice enters the ledger
 2. Urudhi prioritises it (explainable score)
 3. the brain proposes an intervention; policy allows / modifies / blocks
 4. a payment link is created (fake rail) and the message is written to the
    sandbox email outbox
 5. the debtor replies in messy language (default: Tamil-English)
 6. the brain interprets it into a typed intent; the ledger records the promise
 7. the debtor asks for terms; the brain proposes, policy gates, an offer goes out
 8. a signed webhook arrives through the real receiver (FastAPI TestClient)
 9. the ledger updates — money is counted only now
10. recovery metrics update
11. the hash-chained audit timeline proves every step

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--reply",
                        default="Cash konjam tight ah iruku. Friday 50k kudukuren, balance next month.")
    parser.add_argument("--terms", default="Any discount if I clear the balance this week?")
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

    step(1, "An overdue invoice enters the ledger")
    debtor = Debtor(id="deb_1", name="Kumar Textiles", contact_name="Kumar", phone="+919800000001",
                    email="accounts@kumartextiles.example.in", preferred_channel=Channel.EMAIL, language="ta")
    invoice = Invoice(id="inv_1", debtor_id="deb_1", number="URU/2026/0001", amount=rupees(1_00_000),
                      issued_on=date(2026, 6, 1), due_on=date(2026, 7, 1))
    store.put_debtor(debtor)
    store.put_invoice(invoice)
    now = datetime(2026, 8, 24, 11, 0, tzinfo=IST)
    print(f"     {invoice.number} · {debtor.name} · {format_inr(invoice.balance)} · "
          f"{invoice.days_overdue(now.date())} days overdue · brain={agent.brain_name}")

    step(2, "Urudhi prioritises it")
    ex = explain_invoice(store, "inv_1", policy, now)
    print(f"     priority {ex['priority']['score']}/100")
    for reason in ex["priority"]["reasons"]:
        print(f"       {reason}")

    step(3, "The brain proposes; policy decides")
    result = agent.chase("inv_1", now)
    d = result.decision
    print(f"     proposed {d.proposed.action} (confidence {d.proposed.confidence:.2f}) → final {d.final}"
          f"{'  [modified by policy]' if d.modified else ''}")
    for r in d.proposed.rationale:
        print(f"       • {r}")
    for g in d.gates:
        print(f"       {'✓' if g.allowed else '✗'} {g.gate}: {g.reason}")

    step(4, "Payment link created, message written to the sandbox outbox")
    sent = store.events_for("inv_1")[-1]
    print(f"     link: {sent.payload.get('payment_url')}")
    print(f"     eml : {next(outbox_dir.glob('*.eml')).name}")
    print("     ---")
    for line in sent.payload["text"].splitlines():
        print(f"     | {line}")

    step(5, "The debtor replies (messy, code-switched)")
    print(f"     “{args.reply}”")

    step(6, "The brain interprets; the ledger records what was said")
    reply = agent.handle_reply("inv_1", args.reply, now + timedelta(hours=2))
    received = store.events_for("inv_1")[-2 if reply.action.value == "promise_recorded" else -1]
    print(f"     intent={received.payload['intent']} confidence={received.payload['confidence']:.2f} "
          f"amount={received.payload.get('promised_amount')} on={received.payload.get('promised_on')}")
    print(f"     → {reply.action}: {reply.detail}")

    step(7, "Later, the debtor asks for terms; an offer is proposed, gated and sent")
    day = now + timedelta(days=7)
    agent.daily_tick(day.date(), day)  # the Friday promise has lapsed by now
    print(f"     promise state after tick: {store.promises_for('inv_1')[0].state}")
    print(f"     “{args.terms}”")
    counter = agent.handle_reply("inv_1", args.terms, day)
    print(f"     → {counter.action}: {counter.detail}")
    if counter.decision is not None:
        for g in counter.decision.gates:
            print(f"       {'✓' if g.allowed else '✗'} {g.gate}: {g.reason}")
    concession = store.live_concession_for("inv_1")
    if concession:
        print(f"     offer: {concession.type} {concession.discount_bps}bps → settle "
              f"{format_inr(concession.settlement_amount)} by {concession.pay_by} "
              f"(link {concession.payment_link_url})")
        accept = agent.handle_reply("inv_1", "Ok deal. Will pay by Wednesday.", day + timedelta(hours=1))
        print(f"     debtor: “Ok deal. Will pay by Wednesday.” → {accept.action}")

    step(8, "A signed webhook arrives at the real receiver")
    clock["now"] = day + timedelta(days=2)  # the demo clock: two days after the offer
    amount = concession.settlement_amount if concession else invoice.balance
    body = json.dumps({"id": "evt_demo_1", "event": "payment_link.paid", "payload": {"payment": {"entity": {
        "id": "pay_demo_1", "amount": amount, "currency": "INR", "method": "upi",
        "notes": {"invoice_id": "inv_1"}}}}}).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = client.post("/webhooks/razorpay", content=body, headers={"x-razorpay-signature": sig})
    print(f"     POST /webhooks/razorpay → {response.status_code} {response.json()}")
    replay = client.post("/webhooks/razorpay", content=body, headers={"x-razorpay-signature": sig})
    print(f"     same event again      → {replay.json()['status']}")

    step(9, "The ledger updates — money is counted only now")
    inv = store.get_invoice("inv_1")
    print(f"     state={inv.state} paid={format_inr(inv.amount_paid)} waived={format_inr(inv.amount_waived)} "
          f"balance={format_inr(inv.balance)}")
    for c in store.concessions_for("inv_1"):
        print(f"     concession {c.id}: {c.state}")
    for p in store.promises_for("inv_1"):
        print(f"     promise {p.id}: {p.state} — “{p.verbatim}”")

    step(10, "Recovery metrics update")
    summary = client.get("/api/summary", headers=auth).json()
    print(f"     recovered {format_inr(summary['recovered_paise'])} of "
          f"{format_inr(summary['outstanding_paise'])} · waived {format_inr(summary['waived_paise'])}"
          f" · messages {summary['messages_sent']}")

    step(11, "The audit chain proves every step")
    events = list(store.audit_events())
    print(f"     {len(events)} events, chain verified: {verify_chain(events) == len(events)}")
    for e in events:
        detail = e.payload.get("reason") or e.payload.get("intent") or e.payload.get("final") \
            or e.payload.get("outcome") or e.payload.get("intervention") or ""
        print(f"     #{e.seq:02d} {e.at:%d %b %H:%M} {e.actor:<6} {e.kind:<22} {str(detail)[:60]}")
    print(f"\nhealth: {json.dumps(client.get('/health').json()['counters'])[:200]}…")
    print(f"outbox: {outbox_dir}")
    os.environ.pop("ANTHROPIC_API_KEY", None)


if __name__ == "__main__":
    main()
