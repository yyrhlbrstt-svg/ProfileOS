"""Double-entry bookkeeping.

Every other part of the ERP eventually lands here. A goods receipt debits
stock and credits goods-received-not-invoiced; an invoice debits the customer
and credits sales and VAT; a payment clears the customer against the bank. If
those postings are right the accounts tell the truth, and if they are wrong no
amount of care elsewhere will save them.

So the invariant is enforced rather than assumed:

* a journal entry that does not sum to zero is refused at construction,
* the trial balance of a ledger that only ever accepted balanced entries is
  therefore zero, and :meth:`Ledger.check` proves it rather than trusting it,
* posted entries are immutable; a mistake is corrected by a reversing entry,
  which is what an auditor expects to see and what makes the history replayable.

Money is stored in minor units — agorot, cents — as integers. Floating-point
arithmetic on money produces a trial balance that misses by a fraction of an
agora and an accountant who does not trust the system, and it is entirely
avoidable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Iterable, Iterator

from ..core.errors import ProfileOSError


class LedgerError(ProfileOSError):
    """A posting would leave the accounts in an inconsistent state."""


class AccountType(StrEnum):
    """The five classes, and the side each one increases on.

    Assets and expenses increase with a debit; liabilities, equity and income
    increase with a credit. Getting this backwards is the classic bookkeeping
    error, so the direction lives on the type rather than in each caller.
    """

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def debit_positive(self) -> bool:
        return self in (AccountType.ASSET, AccountType.EXPENSE)


@dataclass(frozen=True)
class Account:
    """One account in the chart."""

    code: str
    name: str
    type: AccountType
    #: Free text: VAT rate, bank details, the supplier or customer it tracks.
    note: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.\-]{0,15}", self.code):
            raise LedgerError(f"Invalid account code {self.code!r}", code=self.code)


#: A minimal chart that covers what this suite actually posts.
#:
#: The numbering follows the common Israeli convention — 1xxx assets, 2xxx
#: liabilities, 3xxx equity, 4xxx income, 5xxx-8xxx costs — so a bookkeeper
#: recognises it and can map it onto whatever their software expects.
STANDARD_CHART: tuple[Account, ...] = (
    Account("1100", "Bank", AccountType.ASSET),
    Account("1200", "Trade debtors", AccountType.ASSET),
    Account("1300", "Stock — profiles", AccountType.ASSET),
    Account("1310", "Stock — glass", AccountType.ASSET),
    Account("1320", "Stock — hardware and sundries", AccountType.ASSET),
    Account("1400", "Input VAT", AccountType.ASSET, "recoverable"),
    Account("1500", "Work in progress", AccountType.ASSET),
    Account("2100", "Trade creditors", AccountType.LIABILITY),
    Account("2150", "Goods received not invoiced", AccountType.LIABILITY),
    Account("2400", "Output VAT", AccountType.LIABILITY, "payable"),
    Account("3100", "Capital", AccountType.EQUITY),
    Account("3900", "Retained earnings", AccountType.EQUITY),
    Account("4100", "Sales — fabrication", AccountType.INCOME),
    Account("4900", "Sales returns and allowances", AccountType.INCOME),
    Account("5100", "Materials consumed", AccountType.EXPENSE),
    Account("5200", "Direct labour", AccountType.EXPENSE),
    Account("5300", "Subcontract", AccountType.EXPENSE),
    Account("5900", "Stock variance", AccountType.EXPENSE),
    Account("6100", "Wages", AccountType.EXPENSE),
    Account("6200", "Rent and rates", AccountType.EXPENSE),
    Account("6300", "Plant and consumables", AccountType.EXPENSE),
    Account("6900", "Other overhead", AccountType.EXPENSE),
)


@dataclass(frozen=True)
class Posting:
    """One side of a journal entry, in minor currency units.

    ``amount`` is signed in debit-positive terms: a debit is positive, a credit
    negative. One signed field rather than two columns removes the whole class
    of bug where a value lands in the wrong column.
    """

    account: str
    amount: int
    memo: str | None = None

    @property
    def debit(self) -> int:
        return self.amount if self.amount > 0 else 0

    @property
    def credit(self) -> int:
        return -self.amount if self.amount < 0 else 0


@dataclass(frozen=True)
class JournalEntry:
    """A balanced set of postings, posted as one indivisible fact."""

    entry_id: str
    date: date
    narrative: str
    postings: tuple[Posting, ...]
    #: What produced this: "invoice", "goods_receipt", "stock_issue", ...
    source: str = "manual"
    reference: str | None = None
    posted_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc), compare=False
    )

    def __post_init__(self) -> None:
        if not self.postings:
            raise LedgerError("A journal entry needs at least one posting",
                              entry=self.entry_id)
        total = sum(posting.amount for posting in self.postings)
        if total != 0:
            raise LedgerError(
                "Journal entry does not balance",
                entry=self.entry_id,
                narrative=self.narrative,
                out_by_minor_units=total,
                debits=sum(p.debit for p in self.postings),
                credits=sum(p.credit for p in self.postings),
            )
        if any(posting.amount == 0 for posting in self.postings):
            raise LedgerError(
                "A posting of zero says nothing and hides a calculation that "
                "produced nothing",
                entry=self.entry_id,
            )

    @property
    def total(self) -> int:
        """The entry's size: the debit side, which equals the credit side."""
        return sum(posting.debit for posting in self.postings)

    def accounts(self) -> set[str]:
        return {posting.account for posting in self.postings}

    def reverse(self, entry_id: str, on: date | None = None) -> "JournalEntry":
        """The correcting entry. A posted mistake is reversed, never edited."""
        return JournalEntry(
            entry_id=entry_id,
            date=on or self.date,
            narrative=f"Reversal of {self.entry_id}: {self.narrative}",
            postings=tuple(
                Posting(p.account, -p.amount, p.memo) for p in self.postings
            ),
            source=self.source,
            reference=self.entry_id,
        )


@dataclass
class AccountBalance:
    """An account's movement and closing balance over a period."""

    account: Account
    debits: int = 0
    credits: int = 0

    @property
    def signed(self) -> int:
        """Balance in debit-positive terms."""
        return self.debits - self.credits

    @property
    def natural(self) -> int:
        """Balance in the direction the account naturally carries.

        An asset with 500 more debits than credits has 500; a liability with
        500 more credits than debits also has 500. Reporting both as a signed
        debit balance is correct and unreadable.
        """
        return self.signed if self.account.type.debit_positive else -self.signed


class Ledger:
    """A chart of accounts and the entries posted against it."""

    def __init__(self, chart: Iterable[Account] = STANDARD_CHART, currency: str = "ILS") -> None:
        self.currency = currency
        self.accounts: dict[str, Account] = {}
        for account in chart:
            self.add_account(account)
        self.entries: list[JournalEntry] = []
        self._ids: set[str] = set()

    # -- chart ---------------------------------------------------------------- #
    def add_account(self, account: Account) -> Account:
        if account.code in self.accounts:
            raise LedgerError(f"Account {account.code} already exists", code=account.code)
        self.accounts[account.code] = account
        return account

    def account(self, code: str) -> Account:
        try:
            return self.accounts[code]
        except KeyError:
            raise LedgerError(
                f"No account {code!r} in the chart",
                code=code,
                known=", ".join(sorted(self.accounts)[:12]),
            ) from None

    # -- posting -------------------------------------------------------------- #
    def post(self, entry: JournalEntry) -> JournalEntry:
        """Accept an entry, or refuse it and change nothing.

        Every account is resolved before anything is appended, so a typo in the
        third posting cannot leave the first two recorded.
        """
        if entry.entry_id in self._ids:
            raise LedgerError(
                "That entry has already been posted; correct it with a reversal",
                entry=entry.entry_id,
            )
        for posting in entry.postings:
            self.account(posting.account)
        self.entries.append(entry)
        self._ids.add(entry.entry_id)
        return entry

    def post_simple(
        self,
        entry_id: str,
        on: date,
        narrative: str,
        debit: str,
        credit: str,
        amount: int,
        *,
        source: str = "manual",
        reference: str | None = None,
    ) -> JournalEntry:
        """The two-sided case, which is most of them."""
        if amount <= 0:
            raise LedgerError(
                "Post a positive amount and choose the accounts; a negative "
                "amount silently swaps the sides",
                entry=entry_id,
                amount=amount,
            )
        return self.post(
            JournalEntry(
                entry_id=entry_id,
                date=on,
                narrative=narrative,
                postings=(Posting(debit, amount), Posting(credit, -amount)),
                source=source,
                reference=reference,
            )
        )

    def reverse(self, entry_id: str, new_id: str, on: date | None = None) -> JournalEntry:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return self.post(entry.reverse(new_id, on))
        raise LedgerError(f"No entry {entry_id!r} to reverse", entry=entry_id)

    # -- reporting ------------------------------------------------------------ #
    def in_period(
        self, start: date | None = None, end: date | None = None
    ) -> Iterator[JournalEntry]:
        for entry in self.entries:
            if start is not None and entry.date < start:
                continue
            if end is not None and entry.date > end:
                continue
            yield entry

    def balances(
        self, start: date | None = None, end: date | None = None
    ) -> dict[str, AccountBalance]:
        result = {
            code: AccountBalance(account) for code, account in self.accounts.items()
        }
        for entry in self.in_period(start, end):
            for posting in entry.postings:
                balance = result[posting.account]
                balance.debits += posting.debit
                balance.credits += posting.credit
        return result

    def balance(self, code: str, end: date | None = None) -> int:
        """Natural-direction balance of one account."""
        return self.balances(end=end)[self.account(code).code].natural

    def trial_balance(
        self, start: date | None = None, end: date | None = None
    ) -> list[AccountBalance]:
        """Accounts that moved, in code order."""
        return [
            balance
            for _, balance in sorted(self.balances(start, end).items())
            if balance.debits or balance.credits
        ]

    def check(self, start: date | None = None, end: date | None = None) -> None:
        """Prove the accounts balance. Raises with the discrepancy if not.

        Every entry was balanced when accepted, so this cannot fail — which is
        exactly why it is worth running: if it ever does, the invariant has been
        bypassed and every figure downstream is suspect.
        """
        rows = self.trial_balance(start, end)
        debits = sum(row.debits for row in rows)
        credits = sum(row.credits for row in rows)
        if debits != credits:
            raise LedgerError(
                "Trial balance does not balance",
                debits=debits,
                credits=credits,
                out_by=debits - credits,
            )

    def profit_and_loss(
        self, start: date | None = None, end: date | None = None
    ) -> dict[str, object]:
        rows = self.balances(start, end)
        income = sum(
            row.natural for row in rows.values() if row.account.type is AccountType.INCOME
        )
        expense = sum(
            row.natural for row in rows.values() if row.account.type is AccountType.EXPENSE
        )
        return {
            "income": income,
            "expense": expense,
            "result": income - expense,
            "lines": [
                {
                    "code": row.account.code,
                    "name": row.account.name,
                    "type": str(row.account.type),
                    "amount": row.natural,
                }
                for _, row in sorted(rows.items())
                if row.account.type in (AccountType.INCOME, AccountType.EXPENSE)
                and row.natural
            ],
        }

    def balance_sheet(self, end: date | None = None) -> dict[str, object]:
        rows = self.balances(end=end)
        by_type: dict[str, int] = defaultdict(int)
        for row in rows.values():
            by_type[str(row.account.type)] += row.natural

        assets = by_type[str(AccountType.ASSET)]
        liabilities = by_type[str(AccountType.LIABILITY)]
        equity = by_type[str(AccountType.EQUITY)]
        result = by_type[str(AccountType.INCOME)] - by_type[str(AccountType.EXPENSE)]
        return {
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "result_for_period": result,
            # Assets = liabilities + equity + retained result. Reporting the
            # difference rather than asserting the identity means a broken
            # balance sheet is visible instead of hidden behind an exception.
            "difference": assets - (liabilities + equity + result),
            "lines": [
                {
                    "code": row.account.code,
                    "name": row.account.name,
                    "type": str(row.account.type),
                    "amount": row.natural,
                }
                for _, row in sorted(rows.items())
                if row.account.type
                in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)
                and row.natural
            ],
        }

    def summary(self) -> dict[str, object]:
        rows = self.trial_balance()
        return {
            "currency": self.currency,
            "accounts": len(self.accounts),
            "accounts_used": len(rows),
            "entries": len(self.entries),
            "debits": sum(row.debits for row in rows),
            "credits": sum(row.credits for row in rows),
            "balanced": sum(row.debits for row in rows)
            == sum(row.credits for row in rows),
        }


def money(major: float, *, minor_units: int = 100) -> int:
    """Convert a display amount to minor units, rounding half away from zero.

    Python's ``round`` uses banker's rounding, which turns 0.125 into 0.12 and
    surprises everyone who has ever issued an invoice. Commercial rounding is
    what a customer checks against.
    """
    scaled = major * minor_units
    return int(scaled + (0.5 if scaled >= 0 else -0.5))


def format_money(minor: int, currency: str = "ILS", *, minor_units: int = 100) -> str:
    symbol = {"ILS": "₪", "EUR": "€", "USD": "$", "GBP": "£"}.get(currency, "")
    return f"{symbol}{minor / minor_units:,.2f}"


__all__ = [
    "LedgerError",
    "AccountType",
    "Account",
    "STANDARD_CHART",
    "Posting",
    "JournalEntry",
    "AccountBalance",
    "Ledger",
    "money",
    "format_money",
]
