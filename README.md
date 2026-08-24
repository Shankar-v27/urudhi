# Urudhi — உறுதி

**The receivables agent that remembers every promise.**

India's MSMEs carry over ₹10 lakh crore in unpaid receivables. Urudhi is an AI
agent that recovers overdue B2B invoices the way a careful, polite accounts
person would — chasing dues, negotiating within strict bounds, recording every
promise-to-pay, and verifying every rupee on real payment rails.

Built for the Razorpay AI Buildathon — Track 03: AI Revenue Recovery.

## What it does

- **Prioritizes** which overdue invoices to chase, on which channel, and when
- **Negotiates within bounded authority** — discount caps, installment rules,
  hard policy gates the LLM cannot cross
- **Records promises-to-pay** as typed, dated, confidence-scored ledger entries,
  and tracks kept vs. broken promises
- **Collects on real rails** — Razorpay test-mode Invoices, Payment Links, and
  Smart Collect virtual accounts; recovery is *observed via webhook*, never claimed
- **Escalates compliantly** — contact-hour rules, attempt limits, stop-contact
  honored, human handoff on dispute
- **Audits everything** — every action and gate decision in an append-only log

## Repository layout

```
src/urudhi/       Python core
  agent/          negotiation loop, LLM harness, policy gates
  ledger/         invoice + promise-to-pay state machines
  scoring/        chase prioritization
  rails/          Razorpay client, webhook handling
  audit/          append-only event log
  sim/            synthetic debtor personas, batch runner
  api/            FastAPI app (REST + webhooks)
dashboard/        React dashboard (recovery metrics, transcripts, audit trail)
data/             synthetic invoice batches
docs/             architecture, metrics methodology
tests/            pytest suite
```

## Status

Under active development.
