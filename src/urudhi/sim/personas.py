"""Debtor personas for the synthetic batch.

Each persona is a deterministic behavioral script: given the agent's message
and how many times it has been contacted, it replies the way a real debtor of
that archetype would. The mix is drawn from how receivables actually resolve —
most businesses pay when asked properly, a slice negotiates, a slice breaks
promises, a few dispute, a few go silent, and one or two tell you to stop.

Determinism matters: the same seed produces the same batch, the same
conversations, and therefore the same published metrics.
"""

from __future__ import annotations

import enum
import random
from dataclasses import dataclass

from urudhi.ledger.money import PAISE_PER_RUPEE


class Archetype(enum.StrEnum):
    PROMPT_PAYER = "prompt_payer"        # pays soon after a polite nudge
    NEGOTIATOR = "negotiator"            # asks for terms, then keeps them
    PROMISE_BREAKER = "promise_breaker"  # promises freely, pays rarely
    SLOW_PARTIAL = "slow_partial"        # pays in parts, eventually most of it
    DISPUTER = "disputer"                # contests the invoice
    GHOST = "ghost"                      # never replies
    STOP_REQUESTER = "stop_requester"    # asks the agent to stop

# Batch mix: weights sum to 100 for readability.
MIX: list[tuple[Archetype, int]] = [
    (Archetype.PROMPT_PAYER, 30),
    (Archetype.NEGOTIATOR, 20),
    (Archetype.PROMISE_BREAKER, 15),
    (Archetype.SLOW_PARTIAL, 15),
    (Archetype.DISPUTER, 8),
    (Archetype.GHOST, 9),
    (Archetype.STOP_REQUESTER, 3),
]


@dataclass
class PersonaReply:
    text: str | None            # None = silence
    pays_paise: int = 0         # money the persona sends after this exchange
    pays_after_days: int = 0    # observed_at offset from the reply


class Persona:
    """One debtor's scripted behavior. ``contacted`` counts prior agent messages."""

    def __init__(self, archetype: Archetype, balance: int, rng: random.Random) -> None:
        self.archetype = archetype
        self.balance = balance
        self._rng = rng

    def reply(self, contacted: int) -> PersonaReply:
        rupees = self.balance // PAISE_PER_RUPEE
        match self.archetype:
            case Archetype.PROMPT_PAYER:
                days = self._rng.randint(1, 3)
                return PersonaReply(
                    text=f"Apologies for the delay, will pay ₹{rupees:,} in {days} days.",
                    pays_paise=self.balance, pays_after_days=days,
                )
            case Archetype.NEGOTIATOR:
                if contacted == 1:
                    return PersonaReply(text="Cash flow is tight — any discount "
                                             "if I clear it this week?")
                days = self._rng.randint(2, 4)
                return PersonaReply(
                    text=f"Ok deal. Will pay ₹{rupees:,} in {days} days.",
                    pays_paise=self.balance, pays_after_days=days,
                )
            case Archetype.PROMISE_BREAKER:
                days = self._rng.randint(2, 5)
                pays = self.balance if contacted >= 3 else 0
                return PersonaReply(
                    text=f"Sure sir, will pay ₹{rupees:,} in {days} days pakka.",
                    pays_paise=pays, pays_after_days=days,
                )
            case Archetype.SLOW_PARTIAL:
                part = self.balance * self._rng.choice([30, 40, 50]) // 100
                days = self._rng.randint(1, 4)
                return PersonaReply(
                    text=f"Will transfer ₹{part // PAISE_PER_RUPEE:,} in {days} days, "
                         "rest next month.",
                    pays_paise=part, pays_after_days=days,
                )
            case Archetype.DISPUTER:
                return PersonaReply(text="This is a wrong invoice — we already "
                                         "paid it in June. Check your records.")
            case Archetype.GHOST:
                return PersonaReply(text=None)
            case Archetype.STOP_REQUESTER:
                return PersonaReply(text="Please stop messaging me about this.")
        raise AssertionError(f"unhandled archetype {self.archetype}")


def assign_archetypes(count: int, rng: random.Random) -> list[Archetype]:
    """Deterministic assignment honoring MIX proportions exactly, then shuffled."""
    assigned: list[Archetype] = []
    for archetype, weight in MIX:
        assigned.extend([archetype] * (count * weight // 100))
    while len(assigned) < count:
        assigned.append(Archetype.PROMPT_PAYER)
    rng.shuffle(assigned)
    return assigned[:count]
