# Urudhi — Architecture

> Track 03 · AI Revenue Recovery — Razorpay AI Buildathon
> An agent that recovers overdue B2B invoices with bounded authority,
> an **executable promise-to-pay commitment engine**, and recovery measured
> on payment rails.

> **Promise** = what the debtor said · **Commitment** = what Urudhi accepted ·
> **Payment** = what Razorpay verified. The three never collapse into one.

## The one-diagram version

```
  messy human language                      ┌──────────────────────────────┐
  ("Friday 50k kudukuren,     ─────────────▶│  brain  (agent/brain.py)     │
    balance next month")                    │  interpret · propose · draft │
                                            │  Claude via ANTHROPIC_BASE_URL│
                                            │  or deterministic mock       │
                                            └──────────────┬───────────────┘
                                                           │ typed, validated structures
                                                           │ ReplyInterpretation
                                                           │ InterventionRecommendation
                                                           ▼
  scoring ranks the pool  ────▶  ┌──────────────────────────────────────────┐
  (scoring/priority.py)          │  policy  (agent/policy.py)               │
                                 │  contact gate · offer gate · escalation  │
                                 │  decide_intervention: ALLOW/MODIFY/BLOCK │
                                 └──────────────┬───────────────────────────┘
                                                │ Decision (gates, reasons, offer)
                                                ▼
                                 ┌──────────────────────────────────────────┐
                                 │  RecoveryAgent  (agent/loop.py)          │
                                 │  claim outbound slot → send → audit      │
                                 │  promises · concessions · escalation     │
                                 └───────┬──────────────────────┬───────────┘
                                         │                      │
                        ┌────────────────▼────────┐   ┌─────────▼────────────────┐
                        │ email transport         │   │ Razorpay rails            │
                        │ sandbox (.eml) or SMTP  │   │ payment links, Smart      │
                        │ inbound: /inbound/email │   │ Collect; signed webhooks  │
                        └─────────────────────────┘   │ are the ONLY source of    │
                                                      │ Payment rows              │
                                                      └─────────┬────────────────┘
                                                                ▼
                        ┌──────────────────────────────────────────────────────┐
                        │ ledger + audit  (store.py, ledger/*, audit/log.py)    │
                        │ SQLite · append-only triggers · SHA-256 hash chain   │
                        │ BEGIN IMMEDIATE + process lock on every append        │
                        └──────────────────────────────────────────────────────┘
```

## Two invariants, everywhere

**1. The LLM has no authority.** The brain does three jobs: interpret a
debtor's reply into a typed `ReplyInterpretation`; propose an
`InterventionRecommendation` from a `DecisionContext` of structured facts;
draft prose for an action policy already approved. Every field it returns is
sanitised (`sanitize_interpretation`: non-numeric or non-positive amounts
dropped, amounts above balance clamped, past or unparseable dates dropped
with confidence capped) and every proposal passes through
`decide_intervention`, which returns a `Decision` with the final action, the
gate verdicts and human-readable reasons. A blocked concession degrades to a
plain reminder — never to a smaller concession. A proposal to escalate is
honoured only after a broken promise. The model cannot mark money, change a
balance, override STOP, or lift a contact limit, because no code path exists
for it to do so. If the model endpoint fails, the loop records
`BRAIN_FAILED` and does nothing; there is no fallback from `claude` to
`mock` — selection is explicit at startup and misconfiguration is fatal.

**2. Recovery is observed, never claimed.** The only code path that creates a
`Payment` is `rails/webhooks.py`, fed by signature-verified Razorpay events
(HMAC-SHA256, constant-time compare; the receiver refuses to start with an
empty secret). Every delivery gets exactly one remembered ruling —
`recorded`, `replay_ignored`, `unmatched`, `rejected` — and the last three
are acknowledged with 200 so Razorpay does not retry what retrying cannot
fix. Recovered ₹ is, by construction, the sum of rail events. Even the
simulator's synthetic payments enter through this path.

## Executable Promise-to-Pay — the commitment engine

Urudhi does not merely remember what a debtor promised. It converts a
debtor's natural-language promise into a **policy-bounded payment
commitment**: an exact amount, an exact deadline, and a Razorpay Payment
Link issued for that amount, tagged with the commitment id and expiring at
the deadline. Recovery is recognised only when the corresponding payment is
observed through the webhook pipeline and matched to the commitment.
Commitment outcomes — fulfilled, partially fulfilled, missed — feed
credibility, prioritisation, the brain's next proposal, and escalation.

```
                  DEBTOR LANGUAGE
                        │  "Cash konjam tight ah iruku. Friday 50k kudukuren, balance next month."
                        ▼
                    CLAUDE (or mock)          interpret → ReplyInterpretation
                        │                     {promise, ₹50,000, 2026-08-28, 0.94}
                        ▼
                 PROMISE-TO-PAY               PromiseToPay — what was SAID, verbatim, scored
                        │                     (always recorded; evidence never discarded)
                        ▼
               POLICY VALIDATION              check_commitment: invoice active · no dispute ·
                        │                     not stop-contact · amount > 0 · ≤ balance ·
                        │                     partial allowed · ≥ floor · deadline not past ·
                        │                     ≤ horizon · consistent with an accepted offer
             ┌──────────┴──────────┐
        APPROVED                REFUSED       → promise DECLINED (kept as evidence),
             │                                  COMMITMENT_BLOCKED audited with every check,
             ▼                                  invoice stays chaseable
       EXECUTABLE COMMITMENT                  PaymentCommitment ACTIVE — ₹50,000 by 28 Aug 23:59 IST
             │                                (supersedes any live commitment on the invoice)
             ▼
       RAZORPAY PAYMENT LINK                  amount = ₹50,000 · reference_id = cmt_… ·
             │                                notes = {invoice_id, commitment_id} · expire_by = deadline
             │                                → confirmation sent to the debtor (an answer, not a nudge)
             ▼
         PAYMENT WEBHOOK                      signature verified · replay-safe · currency/amount validated
             │                                → invoice resolved → commitment resolved (exact: the
             │                                instrument carried the id; else earliest live commitment)
             ▼
       COMMITMENT OUTCOME
    ┌─────────┴─────────┐
FULFILLED / PARTIAL    MISSED (deadline passed; ruled by the daily tick, never by a claim)
    │                    │
    ▼                    ▼
CREDIT CREDIBILITY    RE-PLAN / ESCALATE  (2 missed → human; a person may approve a new
    │                                      arrangement, which is itself policy-checked)
    ▼
NEXT STRATEGY         priority score · DecisionContext for the brain · reminder cadence
```

Commitment states: `ACTIVE → PARTIALLY_FULFILLED → FULFILLED` (rail money,
with `days_late` when it arrives after the deadline but before the tick
rules), `→ MISSED` (calendar), `→ CANCELLED` (stop-contact, dispute,
escalation, human close), `→ SUPERSEDED` (a newer commitment replaced it).
Money against a MISSED commitment is recorded on the invoice and noted on the
commitment; the miss stands. Money against a cancelled or superseded
commitment's link is recorded on the invoice only (`matched_by =
instrument-stale`).

Sources: `promise` (interpreted words), `concession` (acceptance of a
policy-approved discount → a commitment for the settlement amount; the
discount is waived only when that commitment is fulfilled), `installment`
(one commitment per installment of an accepted plan, each with its own link
and deadline; a missed installment breaks the plan), `human` (an arrangement
a person approved after escalation — still run through `check_commitment`).

Around a commitment the loop sends at most two messages: a **confirmation**
when it is created (responding mode — it answers the debtor's own words, so
it burns no attempt) and **one bounded reminder** the day before the
deadline (a normal nudge, fully gated by contact hours, spacing and the
attempt cap; `commitment_reminder_days_before`). While a commitment is live
the agent does not chase.

Attribution has a hierarchy: a payment whose instrument carried the
commitment id is **exactly** attributed (provenance, not inference); otherwise
the 7-day window rule applies (an accounting rule); otherwise it is
unattributed. The experiment report keeps the three apart.

Every lifecycle step is a link in the audit chain: `COMMITMENT_PROPOSED`,
`COMMITMENT_APPROVED` / `COMMITMENT_BLOCKED` (with the full checklist),
`COMMITMENT_CREATED`, `PAYMENT_INSTRUMENT_CREATED`,
`COMMITMENT_PARTIALLY_FULFILLED`, `COMMITMENT_FULFILLED`,
`COMMITMENT_MISSED`, `COMMITMENT_CANCELLED`, `COMMITMENT_SUPERSEDED`. The
dashboard's **Commitment integrity** view reconstructs the chain for each
commitment — what was said, what the AI understood, what policy allowed,
what instrument was created, what money arrived, the final outcome — each
step pointing at its audit event.

## The promise-to-pay ledger, and concessions

Every commitment a debtor makes is a typed, dated, confidence-scored
`PromiseToPay` carrying their verbatim words:

```
 OUTSTANDING ──record_promise──▶ PROMISED ──payment on rails──▶ PAID
      ▲                              │ promised date passes
      └── BROKEN / PARTIALLY_KEPT ◀──┘  (expire_promise; invoice back in the pool)
```

A `Concession` is what the operator's policy agreed to give up:

```
 DISCOUNT      OFFERED ─accept─▶ ACCEPTED ─settlement lands by pay_by─▶ SETTLED (remainder WAIVED now, not before)
                       └────────────── pay_by passes unsettled ───────▶ EXPIRED (nothing waived)
 INSTALLMENTS  OFFERED ─accept─▶ ACCEPTED ─every installment met──────▶ SETTLED
                       └────────────── an installment missed ─────────▶ BROKEN (counts as a broken promise)
```

`Invoice.balance = amount − amount_paid − amount_waived`. A debtor who takes
a 3% discount and pays 97% is settled; the 3% is recorded as discount cost,
not chased. Paying the discounted amount late clears nothing extra; paying
the full balance despite an offer waives nothing.

Commitment history feeds chase prioritisation (`scoring/priority.py`: value,
urgency, credibility, fatigue — an explainable weighted sum whose breakdown
is shown in the dashboard's "Why this action?"). Credibility is derived from
the commitment record (`ledger/commitments.py`: fulfilled / missed /
partial, fulfilment rate, average delay, a Laplace-smoothed belief that the
next commitment is kept): missed commitments push the chase score up,
fulfilled ones pull it down, a live commitment suppresses chasing entirely.
Escalation: two broken promises / missed commitments (or a broken installment
plan plus a broken promise) hand the invoice to a human.

## Compliance posture

- Contact window 10:00–19:00 **in the policy timezone** (`Asia/Kolkata` by
  default). Timestamps must be timezone-aware; a naive datetime is an error,
  not a guess. Minimum two days between nudges, one attempt per invoice per
  day, six lifetime attempts — then escalation, not persistence. Answering
  a debtor's own question (terms, a question) is not a nudge.
- `STOP_CONTACT` is honoured immediately and is terminal — even on invoices a
  human owns.
- A dispute, or a claim that the invoice is already paid with nothing on the
  rails, stands the agent down; the invoice becomes human-owned.
- Outbound messages are *claimed* in the database before they are sent
  (`outbound_messages`). A crash between delivery and audit leaves a claimed
  slot, which counts as the day's attempt: the debtor is never messaged twice.
- Humans act through `POST /api/invoices/{id}/human`: acknowledge, note,
  release (back to automation with a clean slate, timestamped), close.
  Every action is a domain transition plus an `Actor.HUMAN` audit event.

## Tamper-evident audit

Every event — message sent/received, gate allowed/blocked, intervention
proposed/decided, offer, acceptance, promise, payment, escalation, human
action, brain failure — is a link in a SHA-256 hash chain. Storage enforces
append-only at the database level (triggers abort UPDATE/DELETE) and every
append runs under `BEGIN IMMEDIATE` behind a process lock, so concurrent
webhooks cannot fork the chain (tested with eight threads). `verify_chain`
proves the trail complete and unedited; the dashboard and `/health` run it.

## Measurement methodology

`python -m urudhi.sim --arms all` runs three arms over the **same** seeded
portfolio and the **same** debtor traits:

| arm | what happens |
|---|---|
| `no_action` | nobody is contacted; only spontaneous payments arrive |
| `baseline` | a fixed-cadence reminder with a static payment link every 3 days, up to the attempt cap; replies are not interpreted (STOP is honoured); no promises, no offers |
| `urudhi` | the full loop: prioritisation, brain interpretation and proposals, policy gates, promise memory, concessions, waiting on commitments, escalation |

Debtors are reactive (`sim/personas.py`): each has latent traits
(willingness, liquidity, price sensitivity, reliability, patience, reply
rate) sampled per archetype, and reacts to the stimulus it actually received
— a payment link, a discount, an installment plan, a request for a firm
promise, contact fatigue. The trait ranges are stated in code and can be
disputed; the model is a construction, not data.

The experiment report (`data/experiment.json`) keeps four things apart:
**observed payments** (rail events), **attributed interventions** (the last
message on that invoice within 7 days — an accounting rule, not a causal
claim), **simulation results** (every figure), and **real-world claims**
(none). It includes uplift vs baseline and vs no action, discount cost and
net recovery, days-to-recovery, attribution by intervention, and a policy
sensitivity sweep (escalation threshold, attempt cap, discount cap) run with
the deterministic mock so the policy effect is isolated from LLM variance.

## Reply evaluation

`python -m urudhi.eval_replies --brain mock|claude` scores each brain on a
labelled set of 90 realistic replies (English, Hinglish, Tamil-English) for
intent accuracy, promise precision/recall, amount and date extraction,
spurious extractions and fallback rate. This is the evidence for the only
place the LLM is used: the language boundary.

## Two ledgers, one product view

The API (`api/app.py`) serves a **primary ledger** (live test-mode; the
webhook receiver writes here) and, optionally, the **simulation ledger** the
batch runner produced. They stay separate files; the API labels every row
with `source` from the ledger it lives in and accepts
`?source=all|live_test|simulation`. A commitment additionally persists the
rail that issued its instrument (`instrument_mode`: `razorpay_test` or
`sandbox`) and whether issuing failed — the dashboard never infers this from
a URL. `FakeRails` issues instruments under a reserved non-resolving host so
a sandbox link can never be mistaken for a Razorpay checkout; `RazorpayRails`
stores Razorpay's returned `short_url` verbatim. Real instruments are issued
only through `agent/instruments.py` — from the loop at commitment time, or
later by `python -m urudhi.provision` for selected commitments (idempotent;
refuses to convert sandbox records; records refusals as instrument failures).

## Runtime surfaces

| surface | notes |
|---|---|
| `POST /webhooks/razorpay` | signed rail events; the only write path for money |
| `POST /inbound/email`, `/inbound/reply` | debtor replies into the brain (bearer token) |
| `POST /api/run/tick` | one scheduler tick: expire commitments, chase by priority |
| `GET /api/*` | ledger, promises, commitments (+ `/api/commitments/{id}` with its integrity chain, and per-invoice chains), concessions, escalations, explain, audit, timeline, experiment, reply-eval — bearer token, contact details masked, `?source=` on every list |
| `POST /api/invoices/{id}/human` | acknowledge · note · **arrange** (amount + due date → policy-checked commitment) · release · close |
| `GET /health` | brain / transport / rails mode, chain status, counters |

Email is the one real channel: `sandbox` writes RFC-822 `.eml` files locally
(what the demo uses, and says so); `smtp` delivers through a configured
server. No other channel is pretended.

## Module map

| Path | Responsibility |
|---|---|
| `ledger/money.py` | integer-paise money, Indian-format INR |
| `ledger/models.py` | Debtor, Invoice, PromiseToPay, Concession, PaymentCommitment, Payment |
| `ledger/transitions.py` | pure state transitions; all domain rules incl. settlement and commitments |
| `ledger/commitments.py` | commitment profile → credibility, reasons |
| `agent/intervention.py` | intervention kinds, DecisionContext, recommendations |
| `agent/policy.py` | PolicyConfig, gates, `decide_intervention`, `check_commitment` |
| `agent/brain.py` | Claude (any base URL) + deterministic mock; sanitisation |
| `agent/loop.py` | RecoveryAgent orchestration; claims, sends, audits |
| `agent/human.py` | human-in-the-loop actions (incl. approving an arrangement) and the queue |
| `agent/explain.py` | "Why this action?" and commitment-integrity provenance |
| `scoring/priority.py` | explainable chase prioritisation |
| `audit/log.py` | hash-chained events, chain verification |
| `store.py` | SQLite; append-only audit; outbound claims; webhook ledger |
| `rails/` | Razorpay client (links, Smart Collect), webhook verification & ingestion |
| `transport/email.py` | sandbox / SMTP email outbox, inbound reference matching |
| `observability.py` | logger with secret redaction, counters |
| `sim/` | reactive personas, batch generator, three-arm runner, reports |
| `eval_replies.py` | labelled reply evaluation |
| `api/` | FastAPI: webhooks, inbound, runtime tick, human actions, read API |
| `dashboard/` | React UI: overview & experiment, invoices with "why", promises, escalations, reply eval |
