"""Debtor personas for the synthetic batch — reactive, seeded, and honest
about being synthetic.

Each debtor carries latent *traits* (willingness, liquidity, price
sensitivity, reliability, patience) sampled from an archetype. What a debtor
does is a function of those traits and the **stimulus** it receives — which
intervention, whether a payment link was included, whether a discount or an
installment plan was offered, how many times it has been contacted — drawn
from its own seeded RNG. So the same debtor behaves reproducibly, and
differently, under the no-action, fixed-cadence and Urudhi arms.

The model is a construction, not data: its parameters are stated here so a
reviewer can dispute them, and the experiment report says so.
"""

from __future__ import annotations

import enum
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from urudhi.agent.intervention import InterventionKind
from urudhi.ledger.money import PAISE_PER_RUPEE


class Archetype(enum.StrEnum):
    PROMPT_PAYER = "prompt_payer"        # pays soon after a polite nudge
    NEGOTIATOR = "negotiator"            # asks for terms; a discount unlocks payment
    CASH_STRAPPED = "cash_strapped"      # can't pay in one go; installments unlock it
    PROMISE_BREAKER = "promise_breaker"  # promises freely, keeps rarely
    DISPUTER = "disputer"                # contests the invoice
    GHOST = "ghost"                      # rarely replies, rarely pays
    STOP_REQUESTER = "stop_requester"    # asks the agent to stop after a nudge or two


# Batch mix: weights sum to 100 for readability.
MIX: list[tuple[Archetype, int]] = [
    (Archetype.PROMPT_PAYER, 30),
    (Archetype.NEGOTIATOR, 18),
    (Archetype.CASH_STRAPPED, 15),
    (Archetype.PROMISE_BREAKER, 14),
    (Archetype.DISPUTER, 8),
    (Archetype.GHOST, 10),
    (Archetype.STOP_REQUESTER, 5),
]


@dataclass
class Traits:
    willingness: float       # base inclination to pay when nudged
    liquidity: float         # ability to pay the whole balance at once
    price_sensitivity: float  # how much a discount moves them
    reliability: float       # probability a promise is kept
    patience: int            # contacts tolerated before annoyance sets in
    reply_rate: float        # probability of replying at all
    spontaneous_daily: float  # chance per day of paying with no contact
    disputer: bool = False
    stopper: bool = False


# (low, high) ranges per trait, per archetype — the whole behavioural model.
TRAIT_RANGES: dict[Archetype, dict[str, tuple[float, float]]] = {
    Archetype.PROMPT_PAYER:    {"willingness": (0.75, 0.95), "liquidity": (0.8, 1.0),
                                "price_sensitivity": (0.1, 0.3), "reliability": (0.85, 0.98),
                                "patience": (4, 6), "reply_rate": (0.9, 1.0),
                                "spontaneous_daily": (0.02, 0.04)},
    Archetype.NEGOTIATOR:      {"willingness": (0.25, 0.45), "liquidity": (0.6, 0.9),
                                "price_sensitivity": (0.7, 0.95), "reliability": (0.75, 0.9),
                                "patience": (3, 5), "reply_rate": (0.85, 1.0),
                                "spontaneous_daily": (0.0, 0.01)},
    Archetype.CASH_STRAPPED:   {"willingness": (0.55, 0.75), "liquidity": (0.1, 0.35),
                                "price_sensitivity": (0.3, 0.5), "reliability": (0.6, 0.8),
                                "patience": (3, 5), "reply_rate": (0.8, 0.95),
                                "spontaneous_daily": (0.0, 0.01)},
    Archetype.PROMISE_BREAKER: {"willingness": (0.5, 0.7), "liquidity": (0.4, 0.7),
                                "price_sensitivity": (0.3, 0.5), "reliability": (0.15, 0.35),
                                "patience": (4, 6), "reply_rate": (0.85, 1.0),
                                "spontaneous_daily": (0.0, 0.005)},
    Archetype.DISPUTER:        {"willingness": (0.0, 0.0), "liquidity": (0.5, 0.5),
                                "price_sensitivity": (0.0, 0.0), "reliability": (0.5, 0.5),
                                "patience": (9, 9), "reply_rate": (0.9, 1.0),
                                "spontaneous_daily": (0.0, 0.0)},
    Archetype.GHOST:           {"willingness": (0.1, 0.25), "liquidity": (0.5, 0.8),
                                "price_sensitivity": (0.2, 0.4), "reliability": (0.5, 0.7),
                                "patience": (2, 4), "reply_rate": (0.1, 0.3),
                                "spontaneous_daily": (0.0, 0.01)},
    Archetype.STOP_REQUESTER:  {"willingness": (0.15, 0.35), "liquidity": (0.5, 0.8),
                                "price_sensitivity": (0.2, 0.4), "reliability": (0.6, 0.8),
                                "patience": (1, 2), "reply_rate": (0.9, 1.0),
                                "spontaneous_daily": (0.0, 0.01)},
}


def sample_traits(archetype: Archetype, rng: random.Random) -> Traits:
    r = TRAIT_RANGES[archetype]
    return Traits(
        willingness=rng.uniform(*r["willingness"]),
        liquidity=rng.uniform(*r["liquidity"]),
        price_sensitivity=rng.uniform(*r["price_sensitivity"]),
        reliability=rng.uniform(*r["reliability"]),
        patience=rng.randint(int(r["patience"][0]), int(r["patience"][1])),
        reply_rate=rng.uniform(*r["reply_rate"]),
        spontaneous_daily=rng.uniform(*r["spontaneous_daily"]),
        disputer=archetype is Archetype.DISPUTER,
        stopper=archetype is Archetype.STOP_REQUESTER,
    )


@dataclass
class Stimulus:
    """What the debtor just received."""

    kind: InterventionKind
    contact_number: int
    has_link: bool = False
    discount_bps: int = 0
    settlement_amount: int = 0     # paise, when a discount was offered
    installments: int = 0
    first_installment: int = 0     # paise
    installment_due_days: list[int] = field(default_factory=list)  # days from today
    asked_for_promise: bool = False
    # The agent turns promises into commitments with an exact-amount payment
    # link and a deadline confirmation (Urudhi arm). A debtor with a concrete
    # instrument in hand keeps their word a little more often and pays sooner.
    commitment_links: bool = False


@dataclass
class ScheduledPayment:
    amount: int        # paise
    after_days: int


@dataclass
class Reaction:
    text: str | None                    # None = silence
    payments: list[ScheduledPayment] = field(default_factory=list)
    kept: bool = True                   # False = the words are empty


def _fmt_amount(paise: int) -> str:
    rupees = paise // PAISE_PER_RUPEE
    if rupees >= 1000 and rupees % 1000 == 0 and rupees < 100_000:
        return f"{rupees // 1000}k"
    return f"₹{rupees:,}"


_WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class Persona:
    """One debtor's behaviour. ``rng`` is seeded per debtor for reproducibility."""

    def __init__(self, archetype: Archetype, balance: int, rng: random.Random,
                 language: str = "en") -> None:
        self.archetype = archetype
        self.balance = balance
        self.traits = sample_traits(archetype, rng)
        self.language = language
        self._rng = rng
        self.remaining = balance
        self.broken_so_far = 0
        self.annoyed = False

    # -- language ----------------------------------------------------------

    def _say_promise(self, amount: int, days: int, today: date, partial: bool) -> str:
        weekday = _WEEKDAY[(today + timedelta(days=days)).weekday()]
        amt = _fmt_amount(amount)
        rest = " Rest next month." if partial else ""
        r = self._rng.random()
        if self.language == "hi":
            options = [
                f"Bhai {weekday} tak {amt} kar dunga pakka.{rest}",
                f"{days} din mein {amt} bhej denge, tension mat lo.{rest}",
                f"Sorry sir, {weekday} ko {amt} transfer kar denge.{rest}",
            ]
        elif self.language == "ta":
            options = [
                f"{weekday} {amt} kudukiren sir.{rest}",
                f"Konjam late aayiduchu, {days} naal la {amt} transfer pannidren.{rest}",
                f"Cash tight ah iruku, {weekday} {amt} kudukuren.{rest}",
            ]
        else:
            options = [
                f"Apologies for the delay, will pay {amt} in {days} days.{rest}",
                f"Will transfer {amt} by {weekday}.{rest}",
                f"Payment of {amt} will be released on {weekday}.{rest}",
            ]
        return options[int(r * len(options))]

    def _say_terms(self) -> str:
        wants_installments = self.traits.liquidity < 0.4
        r = self._rng.random()
        if self.language == "hi":
            options = (["Ek saath nahi ho payega, do teen installments mein kar sakte hain?",
                        "Thoda time do, EMI jaisa arrange ho sakta hai kya?"]
                       if wants_installments else
                       ["Cash flow tight hai, kuch discount milega agar is hafte clear kar dein?",
                        "Full amount mein thoda kam karo, turant settle kar denge."])
        elif self.language == "ta":
            options = (["Oru thadava la mudiyadhu, installments la kudukalama?",
                        "Konjam split panni kudukka mudiyuma, moonu parts la?"]
                       if wants_installments else
                       ["Konjam discount kuduthinga na intha vaaram clear pannidren.",
                        "Cash konjam tight ah iruku, any discount for early payment?"])
        else:
            options = (["Can't do it in one go — can we split into installments?",
                        "Any option to pay in two or three parts over the next few weeks?"]
                       if wants_installments else
                       ["Cash flow is tight — any discount if I clear it this week?",
                        "If you can knock something off, I'll settle immediately."])
        return options[int(r * len(options))]

    def _say_vague(self) -> str:
        r = self._rng.random()
        if self.language == "hi":
            options = ["Dekhte hain, accounts se baat karta hoon.", "Abhi busy hoon, baad mein."]
        elif self.language == "ta":
            options = ["Paakalam, accounts kitta kekkaren.", "Ippo busy, aprom pesalam."]
        else:
            options = ["Will check with accounts and get back.", "Noted, let me see."]
        return options[int(r * len(options))]

    def _say_dispute(self) -> str:
        r = self._rng.random()
        options = [
            "This is a wrong invoice — we already paid it in June. Check your records.",
            "Invoice amount itself is wrong, the rate agreed was lower. Please recheck.",
            "Goods were short by 20 cartons; we are not paying the full invoice.",
            "Yeh invoice galat hai, hume yeh order mila hi nahi.",
        ]
        return options[int(r * len(options))]

    def _say_stop(self) -> str:
        r = self._rng.random()
        options = ["Please stop messaging me about this.", "STOP",
                   "Don't message on this number, call the office.", "Unsubscribe."]
        return options[int(r * len(options))]

    def _say_accept(self, amount: int, days: int, today: date) -> str:
        weekday = _WEEKDAY[(today + timedelta(days=days)).weekday()]
        amt = _fmt_amount(amount)
        r = self._rng.random()
        if self.language == "hi":
            options = [f"Ok deal, {weekday} tak {amt} kar denge.", f"Theek hai deal, {amt} {weekday} ko."]
        elif self.language == "ta":
            options = [f"Sari deal, {weekday} {amt} kudukiren.", f"Ok deal. {amt} by {weekday}."]
        else:
            options = [f"Ok deal. Will pay {amt} by {weekday}.", f"Agreed — {amt} on {weekday} then."]
        return options[int(r * len(options))]

    # -- behaviour ---------------------------------------------------------

    def spontaneous_payment(self) -> int:
        """Money that arrives with no contact at all (the no-action floor)."""
        if self.remaining <= 0 or self.traits.disputer:
            return 0
        if self._rng.random() < self.traits.spontaneous_daily:
            amount = self.remaining if self._rng.random() < self.traits.liquidity else self.remaining // 2
            return max(amount, 0)
        return 0

    def react(self, stimulus: Stimulus, today: date) -> Reaction:
        """Respond to one message. Deterministic given the seeded RNG state."""
        t = self.traits
        rng = self._rng
        if self.remaining <= 0:
            return Reaction(text=None)
        if t.disputer:
            return Reaction(text=self._say_dispute())
        if t.stopper and stimulus.contact_number > t.patience:
            return Reaction(text=self._say_stop())
        if stimulus.contact_number > t.patience + 2 and rng.random() < 0.35:
            self.annoyed = True
            return Reaction(text=self._say_stop())
        if rng.random() > t.reply_rate:
            return Reaction(text=None)

        offered_discount = stimulus.discount_bps > 0
        offered_plan = stimulus.installments > 1
        pay_prob = t.willingness
        pay_prob += 0.12 if stimulus.has_link else 0.0
        pay_prob += 0.45 * t.price_sensitivity if offered_discount else 0.0
        pay_prob += 0.5 * (1.0 - t.liquidity) if offered_plan else 0.0
        pay_prob += 0.05 if stimulus.asked_for_promise else 0.0
        pay_prob -= 0.06 * max(0, stimulus.contact_number - 1)         # fatigue
        pay_prob -= 0.15 if self.annoyed else 0.0
        pay_prob = max(0.0, min(0.98, pay_prob))

        if rng.random() < pay_prob:
            kept = rng.random() < t.reliability + (0.08 if stimulus.commitment_links else 0.0)
            if offered_plan:
                text = self._say_accept(stimulus.first_installment,
                                        min(stimulus.installment_due_days or [3]), today)
                payments = []
                if kept:
                    payments = [ScheduledPayment(stimulus.first_installment,
                                                 max(1, stimulus.installment_due_days[0] - 1))]
                    # Later installments are honoured with independent reliability draws.
                    remaining = self.remaining - stimulus.first_installment
                    per = (remaining // max(1, stimulus.installments - 1)
                           if stimulus.installments > 1 else remaining)
                    for i, due in enumerate(stimulus.installment_due_days[1:]):
                        if rng.random() < t.reliability + 0.1:
                            last = i == len(stimulus.installment_due_days) - 2
                            amount = remaining - per * (stimulus.installments - 2) if last else per
                            payments.append(ScheduledPayment(amount, max(1, due - 1)))
                        else:
                            break
                if not kept:
                    self.broken_so_far += 1
                return Reaction(text=text, payments=payments, kept=kept)
            if offered_discount and rng.random() < 0.5 + 0.5 * t.price_sensitivity:
                days = rng.randint(1, 4)
                text = self._say_accept(stimulus.settlement_amount, days, today)
                payments = [ScheduledPayment(stimulus.settlement_amount, days)] if kept else []
                if not kept:
                    self.broken_so_far += 1
                return Reaction(text=text, payments=payments, kept=kept)
            full = rng.random() < t.liquidity
            amount = self.remaining if full else max(
                PAISE_PER_RUPEE * 500, int(self.remaining * rng.uniform(0.3, 0.6)) // 100 * 100
            )
            amount = min(amount, self.remaining)
            days = rng.randint(1, 3) if stimulus.has_link else rng.randint(2, 6)
            if stimulus.commitment_links and days > 1:
                days -= 1  # a link for the exact amount removes a day of friction
            text = self._say_promise(amount, days, today, partial=not full)
            payments = [ScheduledPayment(amount, days)] if kept else []
            # Some debtors honour a commitment only in part: money arrives, but
            # short of what was agreed — a partially fulfilled commitment.
            if kept and rng.random() < 0.18 * (1.0 - t.liquidity):
                payments = [ScheduledPayment(max(PAISE_PER_RUPEE * 100, amount // 2), days)]
            if not kept:
                self.broken_so_far += 1
            return Reaction(text=text, payments=payments, kept=kept)

        wants_terms = (t.price_sensitivity > 0.6 or t.liquidity < 0.4) and not (
            offered_discount or offered_plan
        )
        if wants_terms and rng.random() < 0.8:
            return Reaction(text=self._say_terms())
        if rng.random() < 0.6:
            return Reaction(text=self._say_vague())
        return Reaction(text=None)

    def note_payment(self, amount: int) -> None:
        self.remaining = max(0, self.remaining - amount)


def assign_archetypes(count: int, rng: random.Random) -> list[Archetype]:
    """Deterministic assignment honoring MIX proportions exactly, then shuffled."""
    assigned: list[Archetype] = []
    for archetype, weight in MIX:
        assigned.extend([archetype] * (count * weight // 100))
    while len(assigned) < count:
        assigned.append(Archetype.PROMPT_PAYER)
    rng.shuffle(assigned)
    return assigned[:count]
