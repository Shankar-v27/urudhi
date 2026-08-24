"""Money handling.

All monetary amounts in Urudhi are integer paise. Floats are never used for
money: they cannot represent 0.1 exactly, and a receivables agent that is off
by a paisa forfeits the right to call its recovery numbers "measured".
Razorpay's APIs use the same convention (amount in the smallest currency unit).
"""

from __future__ import annotations

Paise = int

PAISE_PER_RUPEE = 100


def rupees(amount: int | str) -> Paise:
    """Convert a whole-rupee amount (int, or str like '2,50,000') to paise."""
    if isinstance(amount, str):
        amount = int(amount.replace(",", ""))
    return amount * PAISE_PER_RUPEE


def format_inr(amount: Paise) -> str:
    """Format paise as an INR string in the Indian digit-grouping system.

    >>> format_inr(1234567800)
    '₹1,23,45,678.00'
    """
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    whole, fraction = divmod(amount, PAISE_PER_RUPEE)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}₹{digits}.{fraction:02d}"
