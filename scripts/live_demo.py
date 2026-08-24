"""Live demo against Razorpay test mode.

Creates one overdue invoice in a fresh ledger, creates a real test-mode
Payment Link tagged with the ledger invoice id, and starts the webhook
receiver. Paying the link with a test card/UPI flow fires a real
``payment_link.paid`` webhook — and the money shows up in Urudhi the only way
money ever does: observed on the rails.

Prereqs (.env):
    RAZORPAY_KEY_ID=rzp_test_...
    RAZORPAY_KEY_SECRET=...
    RAZORPAY_WEBHOOK_SECRET=...   # set the same value in the dashboard webhook
Expose the receiver with e.g. `ngrok http 8000` and register
``<public-url>/webhooks/razorpay`` for payment_link.paid / payment.captured.

Run:
    python scripts/live_demo.py
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import uvicorn
from dotenv import load_dotenv

from urudhi.api.app import create_app
from urudhi.ledger.models import Debtor, Invoice
from urudhi.ledger.money import format_inr, rupees
from urudhi.rails.razorpay_client import RazorpayRails
from urudhi.store import Store


def main() -> None:
    load_dotenv()
    key_id = os.environ["RAZORPAY_KEY_ID"]
    key_secret = os.environ["RAZORPAY_KEY_SECRET"]

    store = Store("data/live_demo.sqlite3")
    debtor = Debtor(
        id="deb_live_1", name="Kumar Textiles", contact_name="Kumar",
        phone="+919800000001", email="void@razorpay.com",
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
    link = rails.create_payment_link(
        amount=invoice.balance,
        description=f"Invoice {invoice.number} — {debtor.name}",
        reference_id=invoice.id,
        customer_name=debtor.name,
        customer_email=debtor.email,
        customer_contact=debtor.phone,
    )
    # Tag the link's payments back to the ledger invoice for webhook resolution.
    # (payment_link.create carries notes through to its payments.)
    print(f"invoice   : {invoice.number} — {format_inr(invoice.balance)} overdue")
    print(f"pay here  : {link['short_url']}")
    print("webhook   : POST /webhooks/razorpay  (watch this terminal)")
    print("dashboard : python -m urudhi.api --db data/live_demo.sqlite3")

    uvicorn.run(create_app(store), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
