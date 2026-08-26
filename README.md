# Urudhi — உறுதி

**The receivables agent that remembers every promise.**

India's MSMEs carry over ₹10 lakh crore in unpaid receivables. Urudhi is an AI
agent that recovers overdue B2B invoices the way a careful, polite accounts
person would — reading messy replies, choosing a bounded intervention,
negotiating only inside the authority it was given, recording every
promise-to-pay, escalating to a person when words stop working, and counting a
rupee as recovered only when the payment rail says so.

Built for the Razorpay AI Buildathon — Track 03: AI Revenue Recovery.

```
 messy human language ──▶ LLM: understand / extract / propose ──▶ typed, validated structure
        ──▶ deterministic policy + domain rules: allow / modify / block ──▶ execute approved action
        ──▶ payment rails observe reality (signed webhooks are the only source of "recovered")
```

## What is real, what is simulated, what is test-mode

| | |
|---|---|
| **Implemented — executes in this repo** | Promise-to-pay ledger and state machine; discount settlements and installment plans with correct waiving; policy gates (contact hours in `Asia/Kolkata`, spacing, attempt caps, discount/installment caps, horizon, escalation rules) and `decide_intervention` (allow / modify / block, every gate audited); the recovery loop with crash-safe outbound claims; HMAC-verified, replay-safe webhook ingestion with remembered rulings for unmatched/late/duplicate deliveries; Smart Collect and Payment Link resolution; hash-chained, DB-enforced append-only audit with transactional appends; human-in-the-loop queue and actions; explainable "why this action?"; email transport (sandbox `.eml` / SMTP) and inbound reply endpoints; bearer-token API with masked contact details; `/health` with counters; React dashboard. |
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
byte-identical portfolios and debtor traits. Same seed → same numbers.

| metric | No action | Fixed-cadence baseline | **Urudhi (mock brain)** |
|---|---:|---:|---:|
| recovered — observed via webhooks | ₹43,41,900 | ₹1,23,80,083 | **₹1,34,52,895** |
| recovery rate | 22.8% | 64.9% | **70.6%** |
| discount cost (waived under settled offers) | ₹0 | ₹0 | ₹985 |
| net recovered | ₹43,41,900 | ₹1,23,80,083 | **₹1,34,51,910** |
| invoices paid (of 120) | 17 | 70 | 73 |
| days to recovery (median) | 8 | 3 | 3 |
| promises kept / broken | – | – | 84 / 42 |
| offers made / accepted / settled | – | – | 25 / 10 / 6 |
| contact attempts | 0 | 408 | **302** |
| escalations / disputes / stop-contacts | – | 0 / 0 / 7 | 23 / 9 / 7 |
| **uplift vs baseline** | | | **+₹10,72,812 (+5.6 pts), with 26% fewer messages** |
| uplift vs no action | | | +₹91,10,995 (+47.8 pts) |

Attribution (an accounting rule, not a causal claim): each observed payment
is attributed to the last message on that invoice within the previous 7 days,
else "unattributed". The report also carries days-to-recovery histograms and a
policy sensitivity sweep (escalation threshold, attempt cap, discount cap).
`data/experiment.json` and `data/report.json` are the full outputs; every
unresolved invoice appears in the exception list; the policy the run executed
under is embedded.

**Real-LLM batch** (`python -m urudhi.sim --brain claude --arms all --count 20
--days 14 --workers 4`, measured through the configured relay endpoint,
`claude-sonnet-5`; `data/experiment_claude.json`). Smaller portfolio because
each turn is three network calls; *not* byte-reproducible because model
outputs vary.

| metric (20 invoices, ₹33,83,400 at risk, 14 days) | No action | Baseline | **Urudhi (Claude brain)** |
|---|---:|---:|---:|
| recovered — observed via webhooks | ₹4,32,700 | ₹20,00,669 | **₹22,94,055** |
| recovery rate | 12.8% | 59.1% | **67.8%** |
| contact attempts | 0 | 61 | **45** |
| promises kept / broken · offers made / accepted | – | – | 14 / 2 · 4 / 2 |
| escalations / disputes / stop-contacts | – | 0 / 0 / 1 | 2 / 1 / 2 |
| uplift vs baseline | | | **+₹2,93,386 (+8.7 pts)** |

In that run the brain made 93 proposals; policy modified 7 of them (three
proposals to escalate refused because no promise had been broken, one
installment plan refused below the ₹5,000 threshold), blocked 29 gates, and
deferred 0 turns for brain failures. 31 replies were interpreted (18
promises, 5 term requests, 2 acceptances, 2 STOP, 1 claims-paid, 3 vague);
one interpretation carried a sanitiser flag.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                                   # 267 tests, offline, mock brain

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

# one invoice, end to end, in the terminal (11 steps; --brain claude to use the real brain)
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

1. Overdue invoices enter the ledger → 2. Urudhi prioritises (explainable
score) → 3. a debtor reply arrives (`/inbound/email` or `/inbound/reply`) →
4. Claude interprets the messy language into a typed intent → 5. the brain
proposes an intervention → 6. policy allows / modifies / blocks, every gate
audited → 7. the message and payment link go out (outbound slot claimed
first) → 8. a signed webhook arrives → 9. the ledger updates (settlement,
promise kept/broken, plan status) → 10. recovery metrics update → 11. the
hash-chained audit timeline shows every step. The dashboard's invoice detail
renders "Why this action?" (priority breakdown, proposal → decision, gate
verdicts, offer), promise and concession history, payments and the timeline;
the Escalations tab is the human queue.

## Repository layout

```
src/urudhi/
  agent/        brain (Claude + mock), intervention types, policy gates, recovery loop,
                human actions, explain
  ledger/       money, models (invoice / promise / concession / payment), pure transitions
  scoring/      explainable chase prioritisation
  rails/        Razorpay client (links, Smart Collect), webhook verification & ingestion
  audit/        hash-chained event log
  transport/    email outbox (sandbox / SMTP)
  sim/          reactive personas, batch, three-arm runner, reports
  api/          FastAPI: webhooks, inbound, runtime tick, human actions, read API, health
  eval_replies.py, observability.py, store.py
dashboard/      React: overview & experiment, invoices with "why", promises, escalations, reply eval
data/           reply_eval.jsonl (labelled set), report/experiment JSON, reply_eval_*.json
docs/           architecture.md
scripts/        demo.py (terminal walk-through), live_demo.py (Razorpay test mode)
tests/          267 tests: transitions, settlement, installments, policy, interventions, brains
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
