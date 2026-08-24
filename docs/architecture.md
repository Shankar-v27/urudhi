# Urudhi — Architecture

> Track 03 · AI Revenue Recovery — Razorpay AI Buildathon
> An agent that recovers overdue B2B invoices with bounded authority,
> a promise-to-pay ledger, and recovery measured on payment rails.

## The one-diagram version

```
                       ┌───────────────────────────────────────────────┐
                       │                RecoveryAgent                  │
                       │            (agent/loop.py — orchestrator)     │
                       │                                               │
  scoring ranks ──────▶│  contact gate ──▶ brain drafts ──▶ outbox     │
  the chase pool       │       ▲                              │        │
 (scoring/priority)    │       │                              ▼        │
                       │  policy gates                   debtor reply  │
                       │  (agent/policy.py)                   │        │
                       │   discount caps                      ▼        │
                       │   installment rules          brain interprets │
                       │   contact hours              (agent/brain.py) │
                       │   attempt limits                     │        │
                       │   stop-contact               typed intent     │
                       │   escalation                         │        │
                       │       ▲                              ▼        │
                       │       └── every action ──── ledger writes     │
                       │            & decision       (ledger/*)        │
                       └───────────│───────────────────────────────────┘
                                   ▼
                     ┌──────────────────────────┐     ┌──────────────────────┐
                     │  append-only audit chain │     │  Razorpay test mode  │
                     │  (audit/log.py, SQLite   │◀────│  webhooks — the ONLY │
                     │  triggers block edits)   │     │  source of payments  │
                     └──────────────────────────┘     │  (rails/webhooks.py) │
                                                      └──────────────────────┘
```

## Two invariants, everywhere

**1. The LLM has no authority.** The brain (`agent/brain.py`) does exactly two
jobs: interpret a debtor's reply into a typed `ReplyInterpretation`, and draft
prose for an action that policy already approved. Every consequential act —
contacting a debtor, offering a discount or installments, accepting a promise,
escalating — passes through deterministic gates in `agent/policy.py` that
return machine-readable allow/block decisions. A blocked concession is
stripped, never softened. The delegated authority is data (`PolicyConfig`) and
ships inside every published report.

**2. Recovery is observed, never claimed.** The only code path that creates a
`Payment` is `rails/webhooks.py`, fed by signature-verified Razorpay webhook
events (HMAC-SHA256, constant-time compare, replay-dropped on event id). The
agent cannot mark money as recovered; the simulator routes even synthetic
payments through the same webhook path. "₹ recovered" is, by construction, the
sum of rail events.

## The promise-to-pay ledger

The differentiating idea. Every commitment a debtor makes is recorded as a
typed, dated, confidence-scored `PromiseToPay` carrying the debtor's verbatim
words as evidence:

```
            record_promise                    payment arrives on rails
 OUTSTANDING ───────────────▶ PROMISED ──────────────────────────▶ PAID
      ▲                          │ promised date passes
      │      BROKEN (no money)   ▼
      └────────────────────── expire_promise ──▶ PARTIALLY_KEPT (some money)
```

Promise states: `OPEN → KEPT | PARTIALLY_KEPT | BROKEN | SUPERSEDED | WITHDRAWN`.
The confidence score reflects how firm the commitment actually was — an
explicit amount and date scores ~0.9; "will see next week" enters at ≤0.6 and
is never dressed up. Kept/broken history feeds back into chase prioritization
(`scoring/priority.py`: value, urgency, credibility, fatigue — an explainable
weighted sum, breakdown attached to every score) and into escalation: two
broken promises hand the invoice to a human. While a promise is OPEN the agent
does not chase — a given word gets its window.

## Compliance posture

- Contact window 10:00–19:00, one attempt per invoice per day, six lifetime
  attempts — then escalation, not persistence.
- `STOP_CONTACT` is honored immediately and is terminal.
- A dispute stands the agent down on the spot; the invoice becomes
  human-owned. Payments are still recorded if they arrive (money on rails is
  fact), but partial payment never pulls a human-owned invoice back into the
  chase pool.

## Tamper-evident audit

Every event — message sent/received, gate allowed/blocked, offer, promise,
payment, escalation — is a link in a SHA-256 hash chain (`audit/log.py`):
each event hashes its canonical content plus the previous event's hash.
Storage (`store.py`, SQLite) enforces append-only *at the database level* with
triggers that abort UPDATE/DELETE on the audit table. `verify_chain` proves
the trail complete and unedited; the dashboard runs it on load and the batch
report includes the result. Edit, drop, reorder, or resign an event and
verification fails — tested for each case.

## Measurement methodology

`python -m urudhi.sim` runs a 120-invoice, 21-day batch: seeded synthetic
debtors across seven behavioral archetypes (prompt payers, negotiators,
promise-breakers, slow partial payers, disputers, ghosts, stop-requesters) in
proportions drawn from how receivables actually resolve. Same seed →
byte-identical batch → identical numbers; reviewers are invited to re-run.

Honesty rules the report (`sim/report.py`):

- every number is computed from the ledger and audit chain, never from the
  runner's own bookkeeping;
- every unresolved invoice appears in the exception list with its state and
  balance — 40 of 120 in the headline run, including every dispute and ghost;
- broken promises are reported (36 in the headline run), not smoothed over;
- the policy the run executed under is embedded in the report.

The headline run recovers **₹90.7 lakh of ₹1.52 crore (59.7%)** — deliberately
not higher: policy escalates after two broken promises even when more chasing
might extract payment. That trade-off (recovery vs. debtor experience and
compliance) is the operator's dial, not the agent's.

## Live mode

`rails/razorpay_client.py` wraps the official SDK for Invoices, Payment Links
and Smart Collect virtual accounts (it refuses non-test-mode keys), tagging
each with `notes.invoice_id` so webhooks resolve to ledger invoices. The
FastAPI app (`api/app.py`) exposes the webhook receiver and a read-only API;
the React dashboard renders the summary tiles, invoice table, promise ledger
with verbatim quotes, per-invoice audit timelines, and the chain-verification
badge.

## Module map

| Path | Responsibility |
|---|---|
| `ledger/money.py` | integer-paise money, Indian-format INR |
| `ledger/models.py` | Debtor, Invoice, PromiseToPay, Payment |
| `ledger/transitions.py` | pure state transitions; all domain rules |
| `agent/policy.py` | PolicyConfig + deterministic authority gates |
| `agent/brain.py` | Claude + deterministic mock; interpret & draft only |
| `agent/loop.py` | RecoveryAgent orchestration; audits everything |
| `scoring/priority.py` | explainable chase prioritization |
| `audit/log.py` | hash-chained events, chain verification |
| `store.py` | SQLite persistence; DB-enforced append-only audit |
| `rails/` | Razorpay client, webhook verification & ingestion |
| `sim/` | personas, batch generator, day-by-day runner, report |
| `api/` | FastAPI webhook receiver + dashboard read API |
| `dashboard/` | React UI |
