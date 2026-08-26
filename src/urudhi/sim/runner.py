"""The batch runner: N synthetic days of recovery over the whole invoice set,
under one of three *arms* that start from the identical portfolio:

* ``no_action`` — nobody contacts anyone; only spontaneous payments arrive;
* ``baseline`` — a fixed-cadence reminder with a static payment link every
  few days, no interpretation of replies (STOP is honoured — a compliance
  floor, not intelligence), no promises, no offers, no escalation logic;
* ``urudhi``   — the full loop: prioritisation, brain interpretation and
  proposals, policy gates, promise memory, concessions, waiting on
  commitments, escalation.

Each simulated day: payments that fell due arrive **through the webhook
path** (fabricated, signature-shaped events — even synthetic money enters the
ledger only via :mod:`urudhi.rails.webhooks`); the arm acts; personas react
to what they actually received; their scheduled payments are queued.

The runner owns the clock. Nothing in the core knows it is being simulated.
"""

from __future__ import annotations

import enum
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from urudhi.agent.brain import Brain, MockBrain
from urudhi.agent.intervention import InterventionKind
from urudhi.agent.loop import Action, RecoveryAgent, TurnResult, chaseable
from urudhi.agent.policy import PolicyConfig
from urudhi.audit.log import Actor, EventKind
from urudhi.ledger.models import Channel, ConcessionType, Debtor, InvoiceState
from urudhi.ledger.transitions import stop_contact
from urudhi.rails.razorpay_client import FakeRails
from urudhi.rails.webhooks import ingest_payment_event
from urudhi.scoring.priority import rank, score_invoice
from urudhi.sim.batch import SimCase, generate_batch
from urudhi.sim.personas import Persona, Reaction, Stimulus
from urudhi.store import Store


class Arm(enum.StrEnum):
    NO_ACTION = "no_action"
    BASELINE = "baseline"
    URUDHI = "urudhi"


class SilentOutbox:
    """Messages are audited by the loop; the sim needs no real transport."""

    def send(self, debtor: Debtor, channel: Channel, text: str, *, subject: str,
             reference: str) -> str:
        return f"sim-{reference}"


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
    timezone: str = "Asia/Kolkata"
    seed: int = 2026
    count: int = 120
    daily_chase_budget: int = 60   # attempts per day; scoring decides who
    arm: Arm = Arm.URUDHI
    baseline_cadence_days: int = 3
    workers: int = 1               # >1 parallelises a day's chases (LLM-bound runs); 1 = deterministic


@dataclass
class RunResult:
    store: Store
    cases: list[SimCase]
    config: RunConfig
    policy: PolicyConfig
    arm: Arm
    brain_name: str
    finished_on: date = field(default=date(2026, 8, 24))
    turns: list[TurnResult] = field(default_factory=list)


_STOP_RE = re.compile(r"^\s*stop\b|\bstop (?:messaging|contacting)|\bunsubscribe\b|\bdon'?t message\b",
                      re.IGNORECASE)


def _rank_ids(store: Store, policy: PolicyConfig, today: date) -> list[str]:
    scores = []
    for invoice in chaseable(store):
        if store.open_promise_for(invoice.id) is not None:
            continue  # a promise is running; chasing over it burns goodwill
        live = store.live_concession_for(invoice.id)
        if live is not None and live.type is ConcessionType.INSTALLMENTS:
            continue
        attempts, _, _ = store.attempt_facts(invoice.id, today.isoformat(),
                                             invoice.human_released_at)
        scores.append(score_invoice(
            invoice, store.promises_for(invoice.id), attempts,
            policy.max_attempts_per_invoice, today,
        ))
    return [s.invoice_id for s in rank(scores)]


def _stimulus_from(store: Store, invoice_id: str, kind: InterventionKind,
                   contact_number: int, today: date) -> Stimulus:
    sent = store.events_for(invoice_id, EventKind.MESSAGE_SENT)
    last = sent[-1].payload if sent else {}
    stimulus = Stimulus(kind=kind, contact_number=contact_number,
                        has_link=bool(last.get("payment_url")),
                        asked_for_promise=kind is InterventionKind.REQUEST_PROMISE)
    concession = store.live_concession_for(invoice_id)
    if concession is not None and concession.id == last.get("concession_id"):
        if concession.type is ConcessionType.DISCOUNT:
            stimulus.discount_bps = concession.discount_bps
            stimulus.settlement_amount = concession.settlement_amount
        else:
            stimulus.installments = len(concession.installments)
            stimulus.first_installment = concession.installments[0].amount
            stimulus.installment_due_days = [
                (i.due_on - today).days for i in concession.installments
            ]
    return stimulus


class _World:
    """Everything the runner needs to keep the three arms honest and identical."""

    def __init__(self, config: RunConfig, store: Store) -> None:
        self.config = config
        self.store = store
        self.cases = generate_batch(count=config.count, today=config.start, seed=config.seed)
        self.by_invoice = {c.invoice.id: c for c in self.cases}
        self.personas: dict[str, Persona] = {c.invoice.id: c.persona() for c in self.cases}
        self.contacted: dict[str, int] = {c.invoice.id: 0 for c in self.cases}
        self.pending: list[PendingPayment] = []
        self.event_seq = 0
        self.tz = ZoneInfo(config.timezone)
        for case in self.cases:
            store.put_debtor(case.debtor)
            store.put_invoice(case.invoice)

    def at(self, today: date, hour: time) -> datetime:
        return datetime.combine(today, hour, tzinfo=self.tz)

    def deliver_due(self, today: date) -> None:
        for payment in [p for p in self.pending if p.on <= today]:
            self.event_seq += 1
            ingest_payment_event(self.store, {
                "id": f"evt_sim_{self.event_seq:05d}",
                "event": "payment.captured",
                "payload": {"payment": {"entity": {
                    "id": f"pay_sim_{self.event_seq:05d}",
                    "amount": payment.amount, "currency": "INR", "method": "upi",
                    "notes": {"invoice_id": payment.invoice_id},
                }}},
            }, now=self.at(today, time(9, 0)))
            self.personas[payment.invoice_id].note_payment(payment.amount)
        self.pending = [p for p in self.pending if p.on > today]

    def spontaneous(self, today: date) -> None:
        for invoice_id, persona in self.personas.items():
            invoice = self.store.get_invoice(invoice_id)
            if invoice.state is InvoiceState.PAID:
                continue
            amount = persona.spontaneous_payment()
            if amount > 0:
                self.queue(invoice_id, amount, today, 0)

    def queue(self, invoice_id: str, amount: int, today: date, after_days: int) -> None:
        invoice = self.store.get_invoice(invoice_id)
        queued = sum(p.amount for p in self.pending if p.invoice_id == invoice_id)
        remaining = invoice.balance - queued
        amount = min(amount, remaining)
        if amount > 0:
            self.pending.append(PendingPayment(invoice_id=invoice_id, amount=amount,
                                               on=today + timedelta(days=after_days)))

    def react(self, invoice_id: str, stimulus: Stimulus, today: date) -> Reaction:
        self.contacted[invoice_id] += 1
        stimulus.contact_number = self.contacted[invoice_id]
        reaction = self.personas[invoice_id].react(stimulus, today)
        for scheduled in reaction.payments:
            self.queue(invoice_id, scheduled.amount, today, scheduled.after_days)
        return reaction


def run_batch(
    config: RunConfig | None = None,
    policy: PolicyConfig | None = None,
    brain: Brain | None = None,
    db_path: str = ":memory:",
) -> RunResult:
    config = config or RunConfig()
    policy = policy or PolicyConfig(timezone=config.timezone)
    brain = brain or MockBrain()
    store = Store(db_path)
    world = _World(config, store)
    agent = RecoveryAgent(store, brain, SilentOutbox(), policy, rails=FakeRails())
    brain_name = getattr(brain, "name", "mock") if config.arm is Arm.URUDHI else "none"

    store.append_event(
        at=world.at(config.start, config.chase_hour), actor=Actor.SYSTEM,
        kind=EventKind.RUN_STARTED,
        payload={"arm": config.arm, "brain": brain_name, "days": config.days,
                 "count": config.count, "seed": config.seed,
                 "policy": policy.model_dump(mode="json")},
    )
    turns: list[TurnResult] = []
    today = config.start
    for day_index in range(config.days):
        now = world.at(today, config.chase_hour)
        world.deliver_due(today)
        world.spontaneous(today)

        if config.arm is Arm.URUDHI:
            turns.extend(agent.daily_tick(today, world.at(today, time(10, 0))))
            ranked = _rank_ids(store, policy, today)[: config.daily_chase_budget]

            def work(invoice_id: str, now: datetime = now, today: date = today) -> list[TurnResult]:
                local: list[TurnResult] = []
                result = agent.chase(invoice_id, now)
                local.append(result)
                if result.action is Action.MESSAGE_SENT:
                    _converse(world, agent, invoice_id, result.intervention, now, today, local)
                return local

            if config.workers > 1:
                with ThreadPoolExecutor(max_workers=config.workers) as pool:
                    for batch in pool.map(work, ranked):
                        turns.extend(batch)
            else:
                for invoice_id in ranked:
                    turns.extend(work(invoice_id))
        elif config.arm is Arm.BASELINE:
            if day_index % config.baseline_cadence_days == 0:
                _baseline_day(world, policy, now, today)
        today += timedelta(days=1)

    store.append_event(
        at=world.at(today, config.chase_hour), actor=Actor.SYSTEM, kind=EventKind.RUN_FINISHED,
        payload={"finished_on": today.isoformat(), "arm": config.arm},
    )
    return RunResult(store=store, cases=world.cases, config=config, policy=policy,
                     arm=config.arm, brain_name=brain_name, finished_on=today, turns=turns)


def _converse(world: _World, agent: RecoveryAgent, invoice_id: str,
              kind: InterventionKind | None, now: datetime, today: date,
              turns: list[TurnResult]) -> None:
    """One exchange: the debtor reacts, the agent handles it; at most one
    follow-up round so an immediate gated offer can itself be answered."""
    stimulus = _stimulus_from(world.store, invoice_id, kind or InterventionKind.REMINDER,
                              world.contacted[invoice_id] + 1, today)
    reaction = world.react(invoice_id, stimulus, today)
    if reaction.text is None:
        return
    result = agent.handle_reply(invoice_id, reaction.text, now + timedelta(hours=2))
    turns.append(result)
    if result.action is Action.COUNTER_OFFERED and result.intervention in (
        InterventionKind.DISCOUNT_OFFER, InterventionKind.INSTALLMENT_OFFER,
        InterventionKind.PAYMENT_LINK, InterventionKind.REMINDER,
    ):
        follow = _stimulus_from(world.store, invoice_id, result.intervention,
                                world.contacted[invoice_id] + 1, today)
        reaction = world.react(invoice_id, follow, today)
        if reaction.text is not None:
            turns.append(agent.handle_reply(invoice_id, reaction.text, now + timedelta(hours=4)))


def _baseline_day(world: _World, policy: PolicyConfig, now: datetime, today: date) -> None:
    """Fixed-cadence reminder with a static link; ignores everything but STOP."""
    store = world.store
    rails = FakeRails()
    for invoice in store.all_invoices():
        if invoice.state in (InvoiceState.PAID, InvoiceState.STOP_CONTACT):
            continue
        attempts, _, _ = store.attempt_facts(invoice.id, today.isoformat())
        if attempts >= policy.max_attempts_per_invoice:
            continue
        debtor = store.get_debtor(invoice.debtor_id)
        key = f"{invoice.id}:{today.isoformat()}:{attempts + 1}"
        if not store.claim_outbound(key, invoice.id, today.isoformat(), now,
                                    debtor.preferred_channel.value, {"intervention": "reminder"}):
            continue
        link = rails.create_payment_link(
            amount=invoice.balance, description=invoice.number, invoice_id=invoice.id,
            customer_name=debtor.name, customer_email=debtor.email, customer_contact=debtor.phone,
        )
        store.mark_outbound(key, "sent")
        store.append_event(
            at=now, actor=Actor.SYSTEM, kind=EventKind.MESSAGE_SENT,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"channel": debtor.preferred_channel, "intervention": "reminder",
                     "text": f"Reminder: invoice {invoice.number} is overdue. Pay: {link['short_url']}",
                     "outbound_key": key, "payment_url": link["short_url"], "brain": "none"},
        )
        stimulus = Stimulus(kind=InterventionKind.REMINDER, contact_number=0, has_link=True)
        reaction = world.react(invoice.id, stimulus, today)
        if reaction.text is None:
            continue
        stop = bool(_STOP_RE.search(reaction.text))
        store.append_event(
            at=now + timedelta(hours=2), actor=Actor.SYSTEM, kind=EventKind.MESSAGE_RECEIVED,
            invoice_id=invoice.id, debtor_id=debtor.id,
            payload={"verbatim": reaction.text, "intent": "stop_contact" if stop else None,
                     "summary": "baseline does not interpret replies", "brain": "none"},
        )
        if stop:
            store.put_invoice(stop_contact(store.get_invoice(invoice.id)))
            store.append_event(
                at=now + timedelta(hours=2), actor=Actor.POLICY, kind=EventKind.STOP_CONTACT_HONORED,
                invoice_id=invoice.id, debtor_id=debtor.id, payload={"verbatim": reaction.text},
            )
