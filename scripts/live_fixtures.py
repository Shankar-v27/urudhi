"""Build a small, controlled LIVE TEST fixture set through the real application.

    python scripts/live_fixtures.py --brain claude [--db data/live_demo.sqlite3] [--reset]

Not a data import: each fixture is an overdue invoice plus a realistic debtor
reply that runs through the real recovery loop — the chosen brain interprets
it, the deterministic policy rules on it, and if the commitment is approved
the same code path as production issues a **real Razorpay test-mode Payment
Link** (amount = the approved commitment, ``reference_id`` = commitment id,
notes = {invoice_id, commitment_id}, expiring at the deadline). Amounts stay
inside this test account's per-link cap; one fixture deliberately exceeds it
so the honest "Instrument failed" state exists, and one promise is refused by
policy so a DECLINED promise with no instrument exists. Nothing is faked:
every id and URL below comes back from Razorpay or from the ledger.

The simulation batch is never turned into real links; it stays in its own
ledger. Run ``python -m urudhi.api --db data/live_demo.sqlite3 --sim-db
data/run.sqlite3`` to serve both, labelled.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from urudhi.agent.brain import BRAIN_MODES, BrainConfigError, make_brain
from urudhi.agent.loop import RecoveryAgent
from urudhi.agent.policy import PolicyConfig
from urudhi.config import format_presence_report
from urudhi.ledger.models import Channel, Debtor, Invoice
from urudhi.ledger.money import format_inr, rupees
from urudhi.observability import configure_logging
from urudhi.rails.razorpay_client import RazorpayRails
from urudhi.store import Store
from urudhi.transport.email import EmailOutbox

# (slug, debtor name, contact, language, invoice ₹, the debtor's reply)
FIXTURES = [
    ("kumar", "Kumar Textiles", "Kumar", "ta", 50_000,
     "Cash konjam tight ah iruku. Friday 20000 kudukuren, balance adutha vaaram."),
    ("sharma", "Sharma Auto Components", "Rajesh", "hi", 40_000,
     "Bhai next Monday tak pura 40000 kar dunga pakka."),
    ("meridian", "Meridian Packaging", "Anita", "en", 30_000,
     "Apologies for the delay — will transfer ₹15,000 in 3 days and the rest by month end."),
    ("salem", "Salem Steel Syndicate", "Ravi", "ta", 45_000,
     "Sari sir, 12000 by Wednesday pannidren."),
    # Deliberately above this test account's per-link cap: the commitment is approved
    # by policy but Razorpay refuses the instrument → an honest "Instrument failed".
    ("coimbatore", "Coimbatore Pumps & Motors", "Suresh", "en", 80_000,
     "Will clear the full 80,000 by Friday."),
    # Deliberately beyond the 30-day horizon: policy declines the commitment; the
    # promise stays as evidence with no instrument.
    ("erode", "Erode Dyeing Works", "Divya", "hi", 25_000,
     "Diwali ke baad, 60 din mein pura de denge."),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain", choices=BRAIN_MODES, default="mock")
    parser.add_argument("--db", default="data/live_demo.sqlite3")
    parser.add_argument("--reset", action="store_true", help="start from an empty live ledger")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    configure_logging("WARNING")
    for line in format_presence_report().splitlines():
        print(f"  {line}")
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id.startswith("rzp_test_") or not key_secret:
        sys.exit("error: RAZORPAY_KEY_ID (rzp_test_…) and RAZORPAY_KEY_SECRET are required")
    try:
        brain = make_brain(args.brain)
    except BrainConfigError as error:
        sys.exit(f"error: {error}")

    tz = ZoneInfo(os.environ.get("URUDHI_TZ", "Asia/Kolkata"))
    db = Path(args.db)
    if args.reset:
        db.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
    store = Store(db)
    if store.origin() == "simulation":
        sys.exit(f"error: {db} was written by the simulator; live fixtures go in a live ledger")
    agent = RecoveryAgent(store, brain, EmailOutbox.from_env(), PolicyConfig(timezone=str(tz)),
                          rails=RazorpayRails(key_id, key_secret))
    run_id = datetime.now(tz).strftime("%Y%m%d%H%M%S")
    now = datetime.now(UTC)
    print(f"\nbrain={agent.brain_name} · rail=razorpay_test · ledger={db}\n")
    for slug, name, contact, lang, amount, reply in FIXTURES:
        debtor = Debtor(id=f"deb_live_{slug}_{run_id}", name=name, contact_name=contact,
                        phone="+919800000001", email="void@razorpay.com",
                        preferred_channel=Channel.EMAIL, language=lang)
        invoice = Invoice(id=f"inv_live_{slug}_{run_id}", debtor_id=debtor.id,
                          number=f"URU/2026/L{slug[:3].upper()}{run_id[-4:]}", amount=rupees(amount),
                          issued_on=date.today() - timedelta(days=60),
                          due_on=date.today() - timedelta(days=30))
        store.put_debtor(debtor)
        store.put_invoice(invoice)
        result = agent.handle_reply(invoice.id, reply, now)
        print(f"{invoice.number}  {name} · outstanding {format_inr(invoice.balance)}")
        print(f"   “{reply}”")
        print(f"   → {result.action}: {result.detail}")
        if result.commitment_id:
            c = store.get_commitment(result.commitment_id)
            if c.instrument_id:
                print(f"   commitment {c.id} · {format_inr(c.committed_amount)} by {c.due_on} · "
                      f"mode {c.instrument_mode} · Razorpay {c.instrument_id} · reference {c.id}")
                print(f"   PAY: {c.payment_url}")
            else:
                print(f"   commitment {c.id} · {format_inr(c.committed_amount)} by {c.due_on} · "
                      f"INSTRUMENT FAILED: {c.instrument_failure}")
        print()


if __name__ == "__main__":
    main()
