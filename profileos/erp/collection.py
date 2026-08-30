"""Cheques, and the money that is promised but not yet money.

A foreign package models a receipt: an invoice is paid, the debtor clears, the
cash arrives. Here a customer hands over five post-dated cheques and the shop
has been paid in a sense that no ledger recognises — the invoice is settled
socially, the debtor is still open, and the cash arrives on five different
dates over five months, unless one of them bounces.

Everything a fabricator worries about in that arrangement is in this file: how
much is in the drawer, what clears this month, which cheques came back, and
which customer's cheques come back often enough that the next job should be
cash in advance.

Nothing here posts to the ledger. A cheque is a promise, and a promise that is
already booked as revenue is exactly the error that hides a bad debtor.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from uuid import uuid4

from ..core.errors import ProfileOSError


class ChequeState(StrEnum):
    """Where one cheque is in its life."""

    HELD = "held"
    DEPOSITED = "deposited"
    CLEARED = "cleared"
    BOUNCED = "bounced"
    RETURNED = "returned"
    CANCELLED = "cancelled"

    @property
    def hebrew(self) -> str:
        return {
            "held": "במגירה",
            "deposited": "הופקד",
            "cleared": "נפרע",
            "bounced": "חזר",
            "returned": "הוחזר ללקוח",
            "cancelled": "בוטל",
        }[self.value]

    @property
    def is_money(self) -> bool:
        """Whether the shop actually has the money."""
        return self is ChequeState.CLEARED

    @property
    def is_expected(self) -> bool:
        """Whether it is still expected to become money."""
        return self in (ChequeState.HELD, ChequeState.DEPOSITED)


class PaymentMethod(StrEnum):
    CHEQUE = "cheque"
    TRANSFER = "transfer"
    CARD = "card"
    CASH = "cash"
    BIT = "bit"

    @property
    def hebrew(self) -> str:
        return {
            "cheque": "צ׳ק",
            "transfer": "העברה בנקאית",
            "card": "אשראי",
            "cash": "מזומן",
            "bit": "ביט / העברה מיידית",
        }[self.value]


@dataclass
class Cheque:
    """One post-dated cheque, and everything that decides whether it is money."""

    cheque_id: str = field(default_factory=lambda: f"CHQ-{uuid4().hex[:6].upper()}")
    customer: str = ""
    #: The amount in shekels — this ledger is read by people, not machines.
    amount: float = 0.0
    #: The date written on it, which is the date it may be banked.
    due: date = field(default_factory=date.today)
    received: date = field(default_factory=date.today)
    bank: str = ""
    branch: str = ""
    account: str = ""
    number: str = ""
    job_id: str = ""
    invoice_id: str = ""
    state: ChequeState = ChequeState.HELD
    deposited_on: date | None = None
    cleared_on: date | None = None
    bounced_on: date | None = None
    #: Why it came back, in the bank's words.
    bounce_reason: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ProfileOSError("סכום הצ׳ק חייב להיות חיובי")

    @property
    def days_out(self) -> int:
        """How long the shop waits for this one, from the day it was taken."""
        return (self.due - self.received).days

    def is_bankable(self, today: date | None = None) -> bool:
        """Whether it can be banked yet — a post-dated cheque cannot."""
        return self.state is ChequeState.HELD and self.due <= (today or date.today())

    def deposit(self, on: date | None = None) -> "Cheque":
        on = on or date.today()
        if self.state is not ChequeState.HELD:
            raise ProfileOSError(
                f"צ׳ק ב-{self.state.hebrew} — אי אפשר להפקיד אותו שוב"
            )
        if on < self.due:
            raise ProfileOSError(
                f"הצ׳ק דחוי ל-⁦{self.due.strftime('%d/%m/%Y')}⁩ ואי אפשר להפקיד אותו לפני"
            )
        self.state = ChequeState.DEPOSITED
        self.deposited_on = on
        return self

    def clear(self, on: date | None = None) -> "Cheque":
        self.state = ChequeState.CLEARED
        self.cleared_on = on or date.today()
        return self

    def bounce(self, on: date | None = None, reason: str = "") -> "Cheque":
        self.state = ChequeState.BOUNCED
        self.bounced_on = on or date.today()
        self.bounce_reason = reason
        return self

    def describe(self) -> str:
        return (
            f"⁦{self.amount:,.0f}⁩ ₪ · {self.customer} · "
            f"⁦{self.due.strftime('%d/%m/%Y')}⁩ · {self.state.hebrew}"
        )


class ChequeBook:
    """The drawer, and what is in it.

    Kept as a plain collection rather than a ledger on purpose: none of this
    is revenue, and the moment it is treated as revenue the shop stops
    noticing that a customer has been paying it in paper.
    """

    def __init__(self, cheques: list[Cheque] | None = None) -> None:
        self._cheques: dict[str, Cheque] = {
            cheque.cheque_id: cheque for cheque in (cheques or [])
        }

    def __len__(self) -> int:
        return len(self._cheques)

    def __iter__(self):
        return iter(sorted(self._cheques.values(), key=lambda cheque: cheque.due))

    def add(self, cheque: Cheque) -> Cheque:
        self._cheques[cheque.cheque_id] = cheque
        return cheque

    def get(self, cheque_id: str) -> Cheque | None:
        return self._cheques.get(cheque_id)

    # -- what is in the drawer ----------------------------------------------- #
    def held(self) -> list[Cheque]:
        return [cheque for cheque in self if cheque.state is ChequeState.HELD]

    def expected(self) -> list[Cheque]:
        return [cheque for cheque in self if cheque.state.is_expected]

    @property
    def in_hand(self) -> float:
        """Face value of everything still expected to become money."""
        return round(sum(cheque.amount for cheque in self.expected()), 2)

    def bankable(self, today: date | None = None) -> list[Cheque]:
        """The cheques that could be banked this morning and have not been."""
        return [cheque for cheque in self.held() if cheque.is_bankable(today)]

    def bounced(self) -> list[Cheque]:
        return [cheque for cheque in self if cheque.state is ChequeState.BOUNCED]

    # -- when the money arrives ----------------------------------------------- #
    def cash_flow(
        self, start: date | None = None, weeks: int = 12
    ) -> list[tuple[date, float]]:
        """What clears, week by week — the only forecast a shop really uses."""
        start = start or date.today()
        buckets: dict[date, float] = defaultdict(float)
        for cheque in self.expected():
            if cheque.due < start:
                buckets[start] += cheque.amount
                continue
            # Weeks start on Sunday here, because that is when the shop's
            # week starts and when somebody looks at what is coming in.
            week = cheque.due - timedelta(days=(cheque.due.weekday() + 1) % 7)
            buckets[week] += cheque.amount
        horizon = start + timedelta(weeks=weeks)
        return sorted(
            (day, round(amount, 2))
            for day, amount in buckets.items()
            if day <= horizon
        )

    def due_between(self, start: date, end: date) -> list[Cheque]:
        return [cheque for cheque in self.expected() if start <= cheque.due <= end]

    # -- who to be careful with ----------------------------------------------- #
    def bounce_rate(self, customer: str) -> float:
        """What share of this customer's cheques have come back."""
        theirs = [
            cheque for cheque in self
            if cheque.customer.strip().casefold() == customer.strip().casefold()
        ]
        if not theirs:
            return 0.0
        returned = sum(1 for cheque in theirs if cheque.state is ChequeState.BOUNCED)
        return round(returned / len(theirs) * 100.0, 1)

    def risky_customers(self, minimum: int = 2) -> list[tuple[str, int, float]]:
        """Customers whose cheques come back often enough to matter."""
        counts = Counter(cheque.customer for cheque in self.bounced())
        found = [
            (customer, count, self.bounce_rate(customer))
            for customer, count in counts.items()
            if count >= minimum
        ]
        return sorted(found, key=lambda row: (-row[1], row[0]))

    def average_days_out(self) -> float:
        """How long, on average, the shop is asked to wait for its money."""
        cheques = list(self)
        if not cheques:
            return 0.0
        return round(sum(cheque.days_out for cheque in cheques) / len(cheques), 1)

    def summary(self) -> dict[str, float]:
        return {
            "count": len(self),
            "in_hand": self.in_hand,
            "bankable_today": round(
                sum(cheque.amount for cheque in self.bankable()), 2
            ),
            "bounced": round(sum(cheque.amount for cheque in self.bounced()), 2),
            "cleared": round(
                sum(
                    cheque.amount for cheque in self
                    if cheque.state.is_money
                ), 2,
            ),
            "average_days_out": self.average_days_out(),
        }


__all__ = ["Cheque", "ChequeBook", "ChequeState", "PaymentMethod"]
