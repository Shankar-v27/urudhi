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
from urudhi.config import format_presence_report
from urudhi.ledger.money import format_inr
from urudhi.observability import configure_logging
from urudhi.provision import FIXTURES, seed_live_fixtures
from urudhi.rails.razorpay_client import RazorpayRails
from urudhi.store import Store


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

    tz_str = os.environ.get("URUDHI_TZ", "Asia/Kolkata")
    db = Path(args.db)
    if args.reset:
        db.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
    store = Store(db)
    if store.origin() == "simulation":
        sys.exit(f"error: {db} was written by the simulator; live fixtures go in a live ledger")
    rails = RazorpayRails(key_id, key_secret)
    print(f"\nbrain={args.brain} · rail=razorpay_test · ledger={db}\n")
    results = seed_live_fixtures(store, brain, rails, tz_str)
    for r in results:
        inv = store.get_invoice(r["invoice_id"])
        print(f"{r['invoice_number']}  {r['debtor_name']} · outstanding {format_inr(inv.balance)}")
        print(f"   → {r['action']}")
        if r.get("commitment_id"):
            c = store.get_commitment(r["commitment_id"])
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
