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
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("erp.collection")


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
    #: A cheque the shop wrote — to a supplier, a subcontractor — rather than
    #: one it took. ``customer`` is then who it was written to. It is money
    #: going out on its date, never money coming in.
    outgoing: bool = False

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
        if self.outgoing:
            raise ProfileOSError("צ׳ק שנתתם לספק מפקיד הספק — סמנו ״ירד מהחשבון״ כשהוא נפרע")
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
        """The money is in the account. Only a cheque that went to the bank
        can come back as money — one still in the drawer is not."""
        if self.outgoing and self.state is ChequeState.HELD:
            # The supplier banks it; the shop sees it only when it is debited.
            self.state = ChequeState.CLEARED
            self.cleared_on = on or date.today()
            return self
        if self.state is not ChequeState.DEPOSITED:
            raise ProfileOSError(
                f"הצ׳ק {self.state.hebrew} — רק צ׳ק שהופקד יכול להיפרע"
            )
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
            ("לספק: " if self.outgoing else "")
            + f"⁦{self.amount:,.0f} ₪⁩ · {self.customer} · "
            f"⁦{self.due.strftime('%d/%m/%Y')}⁩ · {self.state.hebrew}"
        )

    # -- on disk ------------------------------------------------------------ #
    _DATES = ("due", "received", "deposited_on", "cleared_on", "bounced_on")

    def as_dict(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            key: getattr(self, key) for key in self.__dataclass_fields__
        }
        for key in self._DATES:
            value = raw[key]
            raw[key] = value.isoformat() if value else None
        raw["state"] = self.state.value
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Cheque":
        values = {key: raw[key] for key in cls.__dataclass_fields__ if key in raw}
        for key in cls._DATES:
            if values.get(key):
                values[key] = date.fromisoformat(str(values[key]))
            elif key in values:
                values[key] = None
        for key in ("due", "received"):
            if values.get(key) is None:
                values.pop(key, None)
        values["state"] = ChequeState(values.get("state", "held"))
        values["amount"] = float(values.get("amount", 0.0))
        values["outgoing"] = bool(values.get("outgoing", False))
        return cls(**values)


from ..core.storage import MergingBook


class ChequeBook(MergingBook):
    """The drawer, and what is in it.

    Kept as a plain collection rather than a ledger on purpose: none of this
    is revenue, and the moment it is treated as revenue the shop stops
    noticing that a customer has been paying it in paper.
    """

    def __init__(
        self, cheques: list[Cheque] | None = None, *, path: Path | None = None
    ) -> None:
        self._cheques: dict[str, Cheque] = {
            cheque.cheque_id: cheque for cheque in (cheques or [])
        }
        #: Where the drawer is kept. ``None`` keeps it in memory only — for
        #: tests and calculations, never for the shop's real cheques.
        self.path = Path(path) if path is not None else None

    _list_key = "cheques"
    _id_field = "cheque_id"

    def _records(self) -> dict[str, Any]:
        return self._cheques

    def _to_raw(self, record: Any) -> dict[str, Any]:
        return record.as_dict()

    def _from_raw(self, raw: dict[str, Any]) -> Any:
        return Cheque.from_dict(raw)

    # -- on disk ------------------------------------------------------------ #
    def load(self) -> "ChequeBook":
        """Read the drawer back. A damaged file is reported, never emptied over."""
        from ..core.storage import stored as _stored

        if self.path is None or not _stored(self.path):
            return self
        raw = self._read_or_mark(self.path)
        for item in raw.get("cheques", []):
            try:
                cheque = Cheque.from_dict(item)
            except Exception as exc:  # noqa: BLE001 - kept on disk by the merge
                _log.warning("Skipped an unreadable cheque: %s", exc)
                continue
            self._cheques[cheque.cheque_id] = cheque
        self._remember_loaded()
        return self

    def save(self) -> None:
        """Write the drawer, all at once: a half-written file is never left."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save_merged(self.path)

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
    def incoming(self) -> list[Cheque]:
        """The cheques customers gave — everything below is about these."""
        return [cheque for cheque in self if not cheque.outgoing]

    def outgoing(self) -> list[Cheque]:
        """The cheques the shop wrote, by date."""
        return [cheque for cheque in self if cheque.outgoing]

    def to_pay(self) -> list[Cheque]:
        """Written and not yet debited: money that will leave on its date."""
        return [cheque for cheque in self.outgoing()
                if cheque.state in (ChequeState.HELD, ChequeState.DEPOSITED)]

    def held(self) -> list[Cheque]:
        return [cheque for cheque in self.incoming() if cheque.state is ChequeState.HELD]

    def expected(self) -> list[Cheque]:
        return [cheque for cheque in self.incoming() if cheque.state.is_expected]

    @property
    def in_hand(self) -> float:
        """Face value of everything still expected to become money."""
        return round(sum(cheque.amount for cheque in self.expected()), 2)

    def bankable(self, today: date | None = None) -> list[Cheque]:
        """The cheques that could be banked this morning and have not been."""
        return [cheque for cheque in self.held() if cheque.is_bankable(today)]

    def bounced(self) -> list[Cheque]:
        return [cheque for cheque in self.incoming() if cheque.state is ChequeState.BOUNCED]

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

    def two_sided(self, start: date | None = None, weeks: int = 12,
                  *, opening: float = 0.0, balance_on: date | None = None) -> list[dict[str, Any]]:
        """Week by week: customers' cheques coming in, the shop's own going out,
        and where the account stands after each — the week that goes below
        zero is the week to move a payment or chase a customer, found now."""
        start = start or date.today()
        horizon = start + timedelta(weeks=weeks)

        def week_of(day: date) -> date:
            if day < start:
                day = start
            return day - timedelta(days=(day.weekday() + 1) % 7)

        coming: dict[date, float] = defaultdict(float)
        going: dict[date, float] = defaultdict(float)
        for cheque in self.expected():
            coming[week_of(cheque.due)] += cheque.amount
        for cheque in self.to_pay():
            # Dated before the balance was read: the bank already took it,
            # it is only waiting to be marked.
            if balance_on is not None and cheque.due < balance_on:
                continue
            going[week_of(cheque.due)] += cheque.amount
        rows: list[dict[str, Any]] = []
        balance = opening
        for week in sorted(set(coming) | set(going)):
            if week > horizon:
                continue
            net = coming[week] - going[week]
            balance += net
            rows.append({
                "week": week, "in": round(coming[week], 2), "out": round(going[week], 2),
                "net": round(net, 2), "balance": round(balance, 2),
            })
        return rows

    def deposited(self) -> list[Cheque]:
        return [c for c in self._cheques.values()
                if c.state is ChequeState.DEPOSITED and not c.outgoing]

    def clear_deposited(self, *, up_to: date, on: date | None = None) -> list[Cheque]:
        """Every cheque deposited on or before ``up_to`` is money now — the
        bank statement's one line for a batch, ticked in one go."""
        cleared = [c for c in self.deposited()
                   if c.deposited_on is not None and c.deposited_on <= up_to]
        for cheque in cleared:
            cheque.clear(on)
        return cleared

    def due_between(self, start: date, end: date) -> list[Cheque]:
        return [cheque for cheque in self.expected() if start <= cheque.due <= end]

    # -- who to be careful with ----------------------------------------------- #
    def bounce_rate(self, customer: str) -> float:
        """What share of this customer's cheques have come back."""
        theirs = [
            cheque for cheque in self.incoming()
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
        cheques = self.incoming()
        if not cheques:
            return 0.0
        return round(sum(cheque.days_out for cheque in cheques) / len(cheques), 1)

    def summary(self) -> dict[str, float]:
        return {
            "count": len(self.incoming()),
            "in_hand": self.in_hand,
            "to_pay": round(sum(cheque.amount for cheque in self.to_pay()), 2),
            "bankable_today": round(
                sum(cheque.amount for cheque in self.bankable()), 2
            ),
            "bounced": round(sum(cheque.amount for cheque in self.bounced()), 2),
            "cleared": round(
                sum(
                    cheque.amount for cheque in self.incoming()
                    if cheque.state.is_money
                ), 2,
            ),
            "average_days_out": self.average_days_out(),
        }


def add_months(day: date, months: int) -> date:
    """The same day ``months`` later; the 31st becomes the month's last day."""
    import calendar

    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_number(number: str, step: int) -> str:
    """Cheque numbers in a book run on: '0012345' + 2 → '0012347'. A number
    that is not all digits is left empty for the person to fill in."""
    text = number.strip()
    if not text.isdigit():
        return ""
    return str(int(text) + step).zfill(len(text))


def cheque_series(
    *, count: int, amount: float, first_due: date, first_number: str = "",
    customer: str = "", job_id: str = "", bank: str = "", branch: str = "",
    account: str = "", every_months: int = 1, outgoing: bool = False,
) -> list[Cheque]:
    """The cheques a customer hands over at once — usually one a month, with
    consecutive numbers from the same book."""
    if count < 1:
        raise ProfileOSError("צריך לפחות צ׳ק אחד")
    return [
        Cheque(
            customer=customer, amount=amount,
            due=add_months(first_due, index * every_months),
            number=first_number.strip() if index == 0 else next_number(first_number, index),
            job_id=job_id, bank=bank, branch=branch, account=account, outgoing=outgoing,
        )
        for index in range(count)
    ]


def default_cheques() -> ChequeBook:
    """The shop's own drawer, from its data folder.

    Every cheque typed into the collection screen used to live only as long as
    the window did. Post-dated cheques are months of the shop's money; they
    are kept on disk like every other record.
    """
    from ..core.config import get_settings

    return ChequeBook(path=get_settings().data_dir / "cheques.json").load()


__all__ = [
    "Cheque", "ChequeBook", "ChequeState", "PaymentMethod", "add_months", "cheque_series",
    "default_cheques", "next_number",
]
