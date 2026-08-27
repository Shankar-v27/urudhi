# Urudhi — உறுதி

**The receivables agent that turns a promise into a commitment — and only
counts the money the rails confirm.**

India's MSMEs carry over ₹10 lakh crore in unpaid receivables. Urudhi is an AI
agent that recovers overdue B2B invoices the way a careful, polite accounts
person would — reading messy replies, choosing a bounded intervention,
negotiating only inside the authority it was given, converting each
acceptable promise into an **executable payment commitment** with its own
Razorpay Payment Link, escalating to a person when commitments are missed,
and counting a rupee as recovered only when the payment rail says so.

> **Promise** = what the debtor said · **Commitment** = what Urudhi accepted ·
> **Payment** = what Razorpay verified.

Built for the Razorpay AI Buildathon — Track 03: AI Revenue Recovery.

```
 messy human language ──▶ LLM: understand / extract / propose ──▶ typed, validated structure
        ──▶ deterministic policy + domain rules: allow / modify / block ──▶ execute approved action
        ──▶ payment rails observe reality (signed webhooks are the only source of "recovered")
```

## Executable Promise-to-Pay

Urudhi does not merely remember what a debtor promised. It converts a
debtor's natural-language promise into a policy-bounded payment commitment.
When approved, the commitment is linked to a Razorpay Payment Link for the
exact committed amount, tagged with the invoice and commitment ids and
expiring at the deadline. Recovery is recognised only when the corresponding
payment is observed through the webhook pipeline and matched to the
commitment. Commitment outcomes feed future prioritisation, the brain's next
proposal, and escalation.

```
      DEBTOR LANGUAGE   "Cash konjam tight ah iruku. Friday 50k kudukuren, balance next month."
            ↓
        CLAUDE AI       interpret → {promise, ₹50,000, 2026-08-28, confidence 0.88}
            ↓
     PROMISE-TO-PAY     recorded verbatim — what was SAID (never discarded)
            ↓
    POLICY VALIDATION   ✓ invoice active ✓ no dispute ✓ not stop-contact ✓ amount > 0
            ↓           ✓ ≤ balance ✓ partial allowed ✓ ≥ floor ✓ deadline ≤ 30-day horizon
  EXECUTABLE COMMITMENT ₹50,000 by 28 Aug 2026 23:59 IST   (refused → promise DECLINED, kept as evidence)
            ↓
 RAZORPAY PAYMENT LINK  amount ₹50,000 · reference_id cmt_… · notes {invoice_id, commitment_id} · expires at deadline
            ↓
    PAYMENT WEBHOOK     signed · replay-safe · matched to the commitment by the id the instrument carried
            ↓
  COMMITMENT OUTCOME    FULFILLED / PARTIALLY FULFILLED (rail money)  ·  MISSED (calendar)
       ↓          ↓
 CREDIT CREDIBILITY   RE-PLAN / HUMAN   (two misses → escalation; a person can approve a new,
       ↓                                 policy-checked arrangement)
   NEXT STRATEGY
```

A created Payment Link is **not** recovery. Only a verified webhook, matched to
the commitment, moves the ledger. Discount settlements and installment plans
run through the same engine: accepting a 3% settlement opens a commitment for
₹97,000 (the ₹3,000 is waived only when that commitment is fulfilled); an
accepted plan opens one commitment per installment, each with its own link
and deadline. Every lifecycle step is a hash-chained audit event, and the
dashboard's **Commitment integrity** view shows, per commitment: what was
said → what the AI understood → what policy allowed (the full checklist) →
what instrument was created → what money arrived → the final outcome.

`python scripts/demo.py --brain claude` walks both flagship scenarios in the
terminal — the kept commitment above, and a promise broken twice → chasing
stops → human approves a new arrangement. See
[docs/architecture.md](docs/architecture.md) for the state machine.

## What is real, what is simulated, what is test-mode

| | |
|---|---|
| **Implemented — executes in this repo** | Promise-to-pay ledger and state machine; the **executable commitment engine** (`check_commitment` gate, `PaymentCommitment` lifecycle, exact-amount payment links tagged with the commitment id, exact/invoice/late matching in the webhook path, deadline ruling, credibility profile, human-approved arrangements); discount settlements and installment plans with correct waiving; policy gates (contact hours in `Asia/Kolkata`, spacing, attempt caps, discount/installment caps, horizon, escalation rules) and `decide_intervention` (allow / modify / block, every gate audited); the recovery loop with crash-safe outbound claims; HMAC-verified, replay-safe webhook ingestion with remembered rulings for unmatched/late/duplicate deliveries; Smart Collect and Payment Link resolution; hash-chained, DB-enforced append-only audit with transactional appends; human-in-the-loop queue and actions; explainable "why this action?"; email transport (sandbox `.eml` / SMTP) and inbound reply endpoints; bearer-token API with masked contact details; `/health` with counters; React dashboard. |
| **Real LLM mode** | `--brain claude` calls an Anthropic-compatible endpoint configured by `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / `ANTHROPIC_API_KEY`. It interprets replies, proposes interventions and drafts messages. It never touches money; misconfiguration is fatal, never a silent fallback to the mock. The numbers in *Reply evaluation* and *Real-LLM batch* below were produced through the configured external endpoint (`https://api.llmsrelay.com`, model `claude-sonnet-5`). |
| **Simulated** | Debtor behaviour (`sim/personas.py`: reactive personas with latent traits — willingness, liquidity, price sensitivity, reliability, patience — that respond to the intervention received) and every recovery figure derived from it. **No real-world recovery rate is claimed.** |
| **Test-mode integrated** | `rails/razorpay_client.py` wraps the official SDK for Payment Links and Smart Collect virtual accounts and refuses non-`rzp_test_` keys; `scripts/live_demo.py` creates a real test-mode link and receives the real `payment_link.paid` webhook. |

## Reply evaluation — why the LLM is at the language boundary

90 labelled debtor replies (English, Hinglish, Tamil-English; promises, partial
promises, term requests, disputes, "already paid", STOP, acceptances,
questions, deflections) scored against expected intent, amount and date.
`python -m urudhi.eval_replies --brain mock|claude`; results in
`data/reply_eval_*.json` with every row and every failure.

| metric | regex mock | Claude (`claude-sonnet-5` via relay) |
|---|---:|---:|
| intent accuracy | 56.7% | 86.7% (91.1% on an earlier run — model outputs vary between runs) |
| promise detection precision / recall | 0.87 / 0.65 | 1.00 / 0.81 |
| amount extraction accuracy (18 labelled) | 55.6% | 88.9% |
| date extraction accuracy (29 labelled) | 62.1% | 82.8% |
| spurious amount / date rate | 3.3% / 0.0% | 5.6% / 0.0% |
| fallback (unusable output → human review) | 0.0% | 0.0% |
| Hinglish / Tamil-English intent accuracy | 48% / 44% | 86% / 83% |

The regex baseline is the deterministic path the tests run on; it is not
presented as the AI. Remaining Claude misses are mostly "question" replies
routed to dispute (by design: a human reviewing is the safe failure) and
inferred amounts ("clear it" → full balance) that the labels keep as
unstated.

## Measured results — three arms, one portfolio (simulation)

`python -m urudhi.sim --brain mock --arms all` runs 120 seeded overdue
invoices (₹1,90,66,300 at risk) for 21 days under three strategies with
byte-identical portfolios and debtor traits. Same seed → same numbers, same
commitment outcomes (verified). Only the Urudhi arm runs the commitment
engine; the zeros for the other arms are real.

| metric | No action | Fixed-cadence baseline | **Urudhi (mock brain)** |
|---|---:|---:|---:|
| recovered — observed via webhooks | ₹43,41,900 | ₹1,22,95,864 | **₹1,30,28,805** |
| recovery rate | 22.8% | 64.5% | **68.3%** |
| discount cost (waived under fulfilled settlements) | ₹0 | ₹0 | ₹630 |
| net recovered | ₹43,41,900 | ₹1,22,95,864 | **₹1,30,28,175** |
| invoices paid (of 120) | 17 | 75 | 70 |
| days to recovery (median) | 8 | 3 | 3 |
| contact attempts (nudges) / messages incl. answers | 0 / 0 | 413 / 413 | **317** / 486 |
| ₹ recovered per nudge | — | ₹29,772 | **₹41,100** |
| commitments created (promise / concession / installment) | 0 | 0 | 153 |
| fulfilled / partially fulfilled / missed | – | – | 99 / 2 / 41 (70.7% fulfilment) |
| ₹ recovered per commitment | – | – | ₹85,156 |
| **exact-matched ₹ (payment through the commitment's own link)** | ₹0 | ₹0 | **₹1,06,17,896** |
| promises kept / broken · offers made / accepted | – | – | 92 / 33 · 23 / 8 |
| escalations / disputes / stop-contacts | – | 0 / 0 / 5 | 24 / 9 / 7 |
| **uplift vs baseline** | | | **+₹7,32,941 (+3.8 pts), with 23% fewer nudges** |
| uplift vs no action | | | +₹86,86,905 (+45.6 pts) |

Attribution has a hierarchy and the report keeps the tiers apart: **exact**
(the payment arrived through a link issued for a specific commitment —
provenance, not inference), **window** (the last message on that invoice
within 7 days — an accounting rule, not a causal claim), **unattributed**.
The report also carries commitment metrics per arm (created, accepted,
fulfilled, partial, missed, fulfilment rate, ₹ committed, ₹ fulfilled,
commitment-to-payment conversion, median days commitment → payment, ₹ per
commitment, ₹ per nudge), days-to-recovery histograms and a policy
sensitivity sweep (escalation threshold, attempt cap, discount cap,
commitment-reminder cadence). `data/experiment.json` and `data/report.json`
are the full outputs; every unresolved invoice appears in the exception list;
the policy the run executed under is embedded.

Honest note on the debtor model: personas react to the intervention
received; a debtor holding an exact-amount link keeps their word a little
more often (+0.08 reliability) and pays a day sooner, and some keep a
commitment only in part. Those parameters are stated in `sim/personas.py`
and can be disputed; no real-world recovery rate is claimed.

**Real-LLM batch** (`python -m urudhi.sim --brain claude --arms all --count 20
--days 14 --workers 4 --no-sensitivity`, measured through the configured relay
endpoint, `claude-sonnet-5`; `data/experiment_claude.json`,
`data/run_claude.sqlite3`). Smaller portfolio because each turn is up to
three network calls (~5 s each; the run took 22 minutes); *not*
byte-reproducible because model outputs vary.

| metric (20 invoices, ₹33,83,400 at risk, 14 days) | No action | Baseline | **Urudhi (Claude brain)** |
|---|---:|---:|---:|
| recovered — observed via webhooks | ₹4,32,700 | ₹20,00,669 | **₹22,09,039** |
| recovery rate | 12.8% | 59.1% | **65.3%** |
| invoices paid (of 20) · days to recovery (median) | 4 · 11 | 11 · 2 | 13 · 2 |
| contact attempts (nudges) / messages incl. answers | 0 / 0 | 58 / 58 | **54** / 82 |
| ₹ recovered per nudge | — | ₹34,494 | **₹40,908** |
| commitments created (promise / concession) | 0 | 0 | 25 (24 / 1) |
| fulfilled / missed · fulfilment rate | – | – | 19 / 6 · **76.0%** (all 19 on time) |
| commitment → payment conversion · median days | – | – | 80% · 1 day |
| **exact-matched ₹ (payment through the commitment's own link)** | ₹0 | ₹0 (15 payments window-attributed) | **₹21,73,139 (20 of 21 payments)** |
| promises kept / broken · offers made / accepted | – | – | 19 / 5 · 3 / 1 |
| escalations / disputes / stop-contacts | – | 0 / 0 / 1 | 4 / 1 / 1 |
| uplift vs baseline | | | **+₹2,08,370 (+6.2 pts)** |

In that run Claude interpreted 34 replies and made the proposals behind 54
nudges; policy modified 6 of its proposals, blocked 47 gate checks, and
deferred 0 turns for brain failures. Every one of the 25 commitments came
from a Claude interpretation that passed the deterministic checklist; the
brain never touched a balance, a link amount, or a commitment state.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                                   # 311 tests, offline, mock brain

# deterministic batch: three arms, uplift, attribution, sensitivity
.venv/bin/python -m urudhi.sim --brain mock --arms all --db data/run.sqlite3

# reply evaluation
.venv/bin/python -m urudhi.eval_replies --brain mock

# real LLM mode (any Anthropic-compatible endpoint)
cp .env.example .env    # fill ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL,
                        # RAZORPAY_WEBHOOK_SECRET, URUDHI_API_TOKEN
.venv/bin/python -m urudhi.eval_replies --brain claude --workers 4
.venv/bin/python -m urudhi.sim --brain claude --arms all --count 20 --days 14 --workers 4 \
    --out data/report_claude.json --experiment-out data/experiment_claude.json --db data/run_claude.sqlite3

# flagship demo: promise → commitment → payment link → webhook → fulfilled, then
# promise broken twice → escalation → human-approved arrangement (--brain claude for the real brain)
.venv/bin/python scripts/demo.py --brain mock

# serve a run and open the dashboard (token = URUDHI_API_TOKEN)
.venv/bin/python -m urudhi.api --db data/run.sqlite3 --brain mock
cd dashboard && npm install && npm run dev            # http://localhost:5173

# live Razorpay test mode: real payment link, real webhook (needs rzp_test_ keys + ngrok)
.venv/bin/python scripts/live_demo.py --brain claude
```

The API refuses to start without `RAZORPAY_WEBHOOK_SECRET` and
`URUDHI_API_TOKEN`; `--brain claude` refuses to start without the
`ANTHROPIC_*` variables. Keys are never logged (log lines pass through a
redactor) and `.env` is gitignored.

## The demo flow

1. An overdue invoice enters the ledger → 2. Urudhi prioritises it
(explainable score) → 3. the debtor replies in Hinglish / Tamil-English
(`/inbound/email` or `/inbound/reply`) → 4. Claude interprets it into a typed
promise → 5. the promise is recorded verbatim → 6. policy evaluates the
commitment checklist → 7. the commitment is approved (exact amount, exact
deadline) → 8. a Razorpay Payment Link is created for that amount and the
debtor is told → 9. the committed amount and expiry are shown (a link is not
money) → 10. the payment happens → 11. the signed webhook arrives, is
verified, de-duplicated and matched to the commitment → 12. the commitment is
FULFILLED / PARTIALLY FULFILLED, the promise KEPT → 13. recovery metrics
update → 14. the hash-chained audit trail and the commitment integrity chain
prove every step. The dashboard's invoice detail renders "Why this action?"
(priority, credibility, proposal → decision, gates), the per-commitment
integrity chain, blocked commitments, promises, concessions, payments and the
timeline; the Commitments tab lists every commitment; the Escalations tab is
the human queue, with "approve arrangement".

## Repository layout

```
src/urudhi/
  agent/        brain (Claude + mock), intervention types, policy gates (incl. check_commitment),
                recovery loop (commitment lifecycle), human actions (incl. arrange), explain
  ledger/       money, models (invoice / promise / concession / commitment / payment), pure
                transitions, commitment profile → credibility
  scoring/      explainable chase prioritisation
  rails/        Razorpay client (links, Smart Collect), webhook verification & ingestion
  audit/        hash-chained event log
  transport/    email outbox (sandbox / SMTP)
  sim/          reactive personas, batch, three-arm runner, reports
  api/          FastAPI: webhooks, inbound, runtime tick, human actions, read API, health
  eval_replies.py, observability.py, store.py
dashboard/      React: overview & experiment, invoices with "why" + commitment integrity, commitments,
                promises, escalations (approve arrangement), reply eval
data/           reply_eval.jsonl (labelled set), report/experiment JSON, reply_eval_*.json
docs/           architecture.md
scripts/        demo.py (terminal walk-through), live_demo.py (Razorpay test mode)
tests/          311 tests: transitions, settlement, installments, commitments (lifecycle, payment
                matching, expiry, cancellation, superseding, policy, memory, human arrangement,
                audit), policy, interventions, brains
                (Claude via a fake client: malformed / unknown / negative / excessive / past-date /
                timeout), loop, webhooks, API, human workflow, reliability, transport, sim, eval
```

See [docs/architecture.md](docs/architecture.md) for the design and invariants.

## Known limitations

- Debtor behaviour is a stated model, not data; the uplift is a property of
  that model. Treat the experiment as evidence that the *workflow* moves the
  needle under reactive debtors, not as a forecast.
- The Claude arm is slower (one network call per interpretation, proposal and
  draft) and not byte-reproducible; the sensitivity sweep therefore uses the
  mock brain.
- One channel (email) is real; WhatsApp/voice are not pretended.
- Human actions are bearer-token authenticated for a single operator; there
  is no user directory.
- SQLite and a single process: right for a demo and small merchants, not for
  a multi-tenant deployment.
