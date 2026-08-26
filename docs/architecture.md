# Urudhi — Architecture

> Track 03 · AI Revenue Recovery — Razorpay AI Buildathon
> An agent that recovers overdue B2B invoices with bounded authority,
> a promise-to-pay ledger, and recovery measured on payment rails.

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

Kept/broken history feeds chase prioritisation (`scoring/priority.py`: value,
urgency, credibility, fatigue — an explainable weighted sum whose breakdown
is shown in the dashboard's "Why this action?") and escalation: two broken
promises (or a broken installment plan plus a broken promise) hand the
invoice to a human. While a promise or plan is running the agent does not
chase — a given word gets its window.

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

## Runtime surfaces

| surface | notes |
|---|---|
| `POST /webhooks/razorpay` | signed rail events; the only write path for money |
| `POST /inbound/email`, `/inbound/reply` | debtor replies into the brain (bearer token) |
| `POST /api/run/tick` | one scheduler tick: expire commitments, chase by priority |
| `GET /api/*` | ledger, promises, concessions, escalations, explain, audit, timeline, experiment, reply-eval — bearer token, contact details masked |
| `GET /health` | brain / transport / rails mode, chain status, counters |

Email is the one real channel: `sandbox` writes RFC-822 `.eml` files locally
(what the demo uses, and says so); `smtp` delivers through a configured
server. No other channel is pretended.

## Module map

| Path | Responsibility |
|---|---|
| `ledger/money.py` | integer-paise money, Indian-format INR |
| `ledger/models.py` | Debtor, Invoice, PromiseToPay, Concession, Payment |
| `ledger/transitions.py` | pure state transitions; all domain rules incl. settlement |
| `agent/intervention.py` | intervention kinds, DecisionContext, recommendations |
| `agent/policy.py` | PolicyConfig, gates, `decide_intervention` |
| `agent/brain.py` | Claude (any base URL) + deterministic mock; sanitisation |
| `agent/loop.py` | RecoveryAgent orchestration; claims, sends, audits |
| `agent/human.py` | human-in-the-loop actions and the escalation queue |
| `agent/explain.py` | "Why this action?" evidence for the dashboard |
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
