"""The batch runner: N synthetic days of recovery over the whole invoice set.

Each simulated day:

1. ``daily_tick`` rules on lapsed promises and escalates where policy says so;
2. scoring ranks the chaseable pool; the agent works the top of the list,
   one gated attempt per invoice per day;
3. personas reply in-character; replies flow back through the normal
   ``handle_reply`` path;
4. personas that decided to pay do so via fabricated Razorpay webhook events —
   the same verified-ingestion path a live deployment uses, so even simulated
   money enters the ledger only through :mod:`urudhi.rails.webhooks`.

The runner owns the clock. Nothing in the core knows it is being simulated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from urudhi.agent.brain import Brain, MockBrain
from urudhi.agent.loop import Action, Outbox, RecoveryAgent, chaseable
from urudhi.agent.policy import Offer, OfferType, PolicyConfig
from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import Channel, Debtor
from urudhi.rails.webhooks import ingest_payment_event
from urudhi.sim.batch import SimCase, generate_batch
from urudhi.store import Store


class SilentOutbox:
    """Messages are audited by the loop; the sim needs no real transport."""

    def send(self, debtor: Debtor, channel: Channel, text: str) -> None:
        return None


@dataclass
class PendingPayment:
    invoice_id: str
    amount: int
    on: date


@dataclass
class RunConfig:
    days: int = 21
    start: date = date(2026, 8, 24)
    chase_hour: time = time(11, 0)
    seed: int = 2026
    count: int = 120
    daily_chase_budget: int = 60   # attempts per day; scoring decides who


@dataclass
class RunResult:
    store: Store
    cases: list[SimCase]
    config: RunConfig
    policy: PolicyConfig
    finished_on: date = field(default=date(2026, 8, 24))


def _rank_ids(store: Store, policy: PolicyConfig, today: date) -> list[str]:
    from urudhi.scoring.priority import rank, score_invoice

    scores = []
    for invoice in chaseable(store):
        promises = store.promises_for(invoice.id)
        attempts = sum(
            1 for e in store.audit_events()
            if e.kind is EventKind.MESSAGE_SENT and e.invoice_id == invoice.id
        )
        scores.append(score_invoice(
            invoice, promises, attempts, policy.max_attempts_per_invoice, today
        ))
    return [s.invoice_id for s in rank(scores)]


def _offer_for(store: Store, invoice_id: str, today: date, policy: PolicyConfig) -> Offer | None:
    """Carry a gated discount offer only once a debtor has asked for terms."""
    asked = any(
        e.kind is EventKind.MESSAGE_RECEIVED
        and e.invoice_id == invoice_id
        and e.payload.get("intent") == "request_terms"
        for e in store.audit_events()
    )
    if not asked:
        return None
    return Offer(
        type=OfferType.DISCOUNT, invoice_id=invoice_id,
        discount_bps=min(300, policy.max_discount_bps),
        pay_by=today + timedelta(days=5),
    )


def run_batch(
    config: RunConfig | None = None,
    policy: PolicyConfig | None = None,
    brain: Brain | None = None,
    db_path: str = ":memory:",
) -> RunResult:
    config = config or RunConfig()
    policy = policy or PolicyConfig()
    store = Store(db_path)
    cases = generate_batch(count=config.count, today=config.start, seed=config.seed)
    by_invoice = {c.invoice.id: c for c in cases}

    for case in cases:
        store.put_debtor(case.debtor)
        store.put_invoice(case.invoice)

    agent = RecoveryAgent(store, brain or MockBrain(), SilentOutbox(), policy)
    contacted: dict[str, int] = {c.invoice.id: 0 for c in cases}
    pending: list[PendingPayment] = []
    event_seq = 0

    start_at = datetime.combine(config.start, config.chase_hour, tzinfo=UTC)
    store.append_event(
        at=start_at, actor=Actor.SYSTEM, kind=EventKind.RUN_STARTED,
        payload={
            "days": config.days, "count": config.count, "seed": config.seed,
            "policy": policy.model_dump(mode="json"),
        },
    )

    today = config.start
    for _ in range(config.days):
        now = datetime.combine(today, config.chase_hour, tzinfo=UTC)

        # 1. settle payments that fall due today, via the webhook path
        for payment in [p for p in pending if p.on <= today]:
            event_seq += 1
            ingest_payment_event(store, {
                "id": f"evt_sim_{event_seq:05d}",
                "event": "payment.captured",
                "payload": {"payment": {"entity": {
                    "id": f"pay_sim_{event_seq:05d}",
                    "amount": payment.amount, "method": "upi",
                    "notes": {"invoice_id": payment.invoice_id},
                }}},
            }, now=now.replace(hour=9))
        pending = [p for p in pending if p.on > today]

        # 2. rule on lapsed promises, escalate where earned
        agent.daily_tick(today, now.replace(hour=10))

        # 3. chase by priority, within the day's budget
        for invoice_id in _rank_ids(store, policy, today)[: config.daily_chase_budget]:
            result = agent.chase(
                invoice_id, now, offer=_offer_for(store, invoice_id, today, policy)
            )
            if result.action is not Action.MESSAGE_SENT:
                continue
            contacted[invoice_id] += 1
            case = by_invoice[invoice_id]
            reply = case.persona.reply(contacted[invoice_id])
            if reply.text is not None:
                agent.handle_reply(invoice_id, reply.text, now + timedelta(hours=2))
            if reply.pays_paise > 0:
                already_paid = store.get_invoice(invoice_id).amount_paid
                already_queued = sum(
                    p.amount for p in pending if p.invoice_id == invoice_id
                )
                remaining = case.invoice.amount - already_paid - already_queued
                amount = min(reply.pays_paise, remaining)
                if amount > 0:
                    pending.append(PendingPayment(
                        invoice_id=invoice_id, amount=amount,
                        on=today + timedelta(days=reply.pays_after_days),
                    ))
        today += timedelta(days=1)

    store.append_event(
        at=datetime.combine(today, config.chase_hour, tzinfo=UTC),
        actor=Actor.SYSTEM, kind=EventKind.RUN_FINISHED,
        payload={"finished_on": today.isoformat()},
    )
    return RunResult(
        store=store, cases=cases, config=config, policy=policy, finished_on=today
    )
