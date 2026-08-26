"""Live demo against Razorpay test mode.

Creates one overdue invoice in a fresh ledger, runs one real agent turn (the
chosen brain proposes, policy decides, a *real* test-mode Payment Link is
created and the message is written to the sandbox email outbox), and starts
the webhook receiver. Paying the link with a test card/UPI flow fires a real
``payment_link.paid`` webhook — and the money shows up in Urudhi the only way
money ever does: observed on the rails.

Prereqs (.env):
    RAZORPAY_KEY_ID=rzp_test_...
    RAZORPAY_KEY_SECRET=...
    RAZORPAY_WEBHOOK_SECRET=...   # set the same value in the dashboard webhook
    URUDHI_API_TOKEN=...
    ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL   (for --brain claude)
Expose the receiver with e.g. `ngrok http 8000` and register
``<public-url>/webhooks/razorpay`` for payment_link.paid / payment.captured.

Run:
    python scripts/live_demo.py [--brain mock|claude]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from urudhi.agent.brain import BRAIN_MODES, BrainConfigError, make_brain
from urudhi.agent.loop import RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.api.app import create_app
from urudhi.ledger.models import Channel, Debtor, Invoice
from urudhi.ledger.money import format_inr, rupees
from urudhi.observability import configure_logging
from urudhi.rails.razorpay_client import RazorpayRails
from urudhi.store import Store
from urudhi.transport.email import EmailOutbox


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    configure_logging("INFO")

    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id.startswith("rzp_test_") or not key_secret:
        sys.exit("error: RAZORPAY_KEY_ID (rzp_test_…) and RAZORPAY_KEY_SECRET are required")
    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        sys.exit(f"error: {error}")

    db = Path("data/live_demo.sqlite3")
    db.unlink(missing_ok=True)
    store = Store(db)
    debtor = Debtor(
        id="deb_live_1", name="Kumar Textiles", contact_name="Kumar",
        phone="+919800000001", email="void@razorpay.com", preferred_channel=Channel.EMAIL,
    )
    invoice = Invoice(
        id="inv_live_1", debtor_id=debtor.id, number="URU/2026/LIVE1",
        amount=rupees(2_500),
        issued_on=date.today() - timedelta(days=60),
        due_on=date.today() - timedelta(days=30),
    )
    store.put_debtor(debtor)
    store.put_invoice(invoice)

    rails = RazorpayRails(key_id, key_secret)
    outbox = EmailOutbox.from_env()
    policy = PolicyConfig(timezone=os.environ.get("URUDHI_TZ", "Asia/Kolkata"))
    agent = RecoveryAgent(store, brain, outbox, policy, rails=rails)

    now = datetime.now(UTC)
    result = agent.chase(invoice.id, now)
    print(f"invoice   : {invoice.number} — {format_inr(invoice.balance)} overdue")
    print(f"brain     : {agent.brain_name}")
    print(f"turn      : {result.action} ({result.intervention}) — {result.detail}")
    if result.decision is not None:
        for gate in result.decision.gates:
            print(f"  gate    : {'✓' if gate.allowed else '✗'} {gate.gate}: {gate.reason}")
    sent = store.events_for(invoice.id)
    link = next((e.payload.get("payment_url") for e in sent if e.payload.get("payment_url")), None)
    print(f"pay here  : {link or '(no link — turn was blocked; check contact hours in IST)'}")
    print(f"email     : written to {os.environ.get('URUDHI_OUTBOX_DIR', 'data/outbox')} (sandbox mode)")
    print("webhook   : POST /webhooks/razorpay  (watch this terminal)")
    print(f"dashboard : python -m urudhi.api --db {db}")

    app = create_app(
        store, webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
        api_token=os.environ.get("URUDHI_API_TOKEN", ""), agent=agent, policy=policy,
        brain_name=agent.brain_name, transport_mode=f"email:{outbox.mode}", rails_mode="razorpay-test",
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
