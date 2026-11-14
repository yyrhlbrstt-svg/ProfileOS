"""The bank statement, read against the drawer and the jobs.

Every few days somebody opens the bank's site, looks down the list, and ticks
by hand: this cheque cleared, that transfer is the Levi family's balance, this
debit is the supplier's cheque. What is not ticked is a cheque that bounced
unnoticed, or a customer sent a balance reminder for money already paid.

Here the bank's own export (Excel or CSV) is read, each line is set against
what the shop is expecting — deposited cheques, the shop's own cheques to
suppliers, balances owed on jobs — and a match is *proposed*. Nothing is
marked without the person's click, and a line once applied is remembered, so
importing the same statement twice records nothing twice.

The columns are the person's to name, once: banks differ, and a layout
guessed wrong would read a debit as a credit. A first guess from the headers
is offered, never applied silently.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ProfileOSError

#: The fields a statement line needs, and the words banks put over them.
FIELDS: dict[str, tuple[str, ...]] = {
    "date": ("תאריך ערך", "תאריך", "יום ערך", "date"),
    "description": ("תיאור", "פרטים", "תאור", "פעולה", "description"),
    "reference": ("אסמכתא", "אסמכתה", "מספר שיק", "מס' שיק", "reference"),
    "credit": ("זכות", "בזכות", "הפקדה", "credit"),
    "debit": ("חובה", "בחובה", "משיכה", "debit"),
    "amount": ("סכום", "amount"),
}
FIELD_HEBREW = {"date": "תאריך", "description": "תיאור", "reference": "אסמכתא",
                "credit": "זכות", "debit": "חובה", "amount": "סכום (± בעמודה אחת)"}

#: A credit is matched to a cheque deposited this many days before it at most.
CLEARING_DAYS = 10


@dataclass
class BankLine:
    on: date
    text: str
    reference: str
    #: Positive into the account, negative out of it.
    amount: float
    #: Which of the statement's identical lines this is (two equal transfers
    #: on one day are two payments, not one seen twice).
    occurrence: int = 0

    @property
    def key(self) -> str:
        """What makes this line itself — so it is applied once, ever."""
        raw = f"{self.on.isoformat()}|{self.amount:.2f}|{self.reference}|{self.text}"
        if self.occurrence:
            raw += f"|{self.occurrence}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def describe(self) -> str:
        sign = "+" if self.amount > 0 else "−"
        return (f"⁦{self.on:%d/%m/%Y}⁩ · ⁦{sign}{abs(self.amount):,.2f} ₪⁩ · {self.text}"
                + (f" · ⁦{self.reference}⁩" if self.reference else ""))


@dataclass
class Match:
    line: BankLine
    #: "clear" (a deposited cheque), "bounce", "paid_out" (the shop's own
    #: cheque debited), "payment" (a transfer for a job).
    kind: str
    target: Any
    why: str
    #: Proposed ticked: the match is exact (number and amount).
    sure: bool = False

    def describe(self) -> str:
        what = {
            "clear": "צ׳ק שהופקד נפרע", "bounce": "צ׳ק חזר",
            "paid_out": "צ׳ק לספק ירד מהחשבון", "payment": "תשלום לפרויקט",
        }[self.kind]
        return f"{what}: {self.why}"


@dataclass
class Reading:
    lines: list[BankLine] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    #: Credits nothing explains — money in that no record expected.
    unexplained: list[BankLine] = field(default_factory=list)
    already: int = 0
    skipped_rows: int = 0


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #
def guess_columns(headers: list[str]) -> dict[str, str]:
    """A first guess from the header words, for the person to confirm."""
    chosen: dict[str, str] = {}
    for name, words in FIELDS.items():
        for header in headers:
            low = header.strip().lower()
            if header in chosen.values():
                continue
            if any(word in low for word in words):
                chosen[name] = header
                break
    if "credit" in chosen or "debit" in chosen:
        chosen.pop("amount", None)
    return chosen


def _number(text: str) -> float | None:
    cleaned = str(text).replace("₪", "").replace(",", "").replace("‏", "").strip()
    if not cleaned:
        return None
    negative = cleaned.startswith("-") or cleaned.endswith("-") or (
        cleaned.startswith("(") and cleaned.endswith(")"))
    cleaned = cleaned.strip("-()").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _date(text: str) -> date | None:
    text = str(text).strip()
    if not text:
        return None
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.split(" ")[0], pattern).date()
        except ValueError:
            continue
    serial = _number(text)
    if serial and 30_000 < serial < 80_000:          # an Excel day number
        return date(1899, 12, 30) + timedelta(days=int(serial))
    return None


def read_statement(path: Path, columns: dict[str, str]) -> tuple[list[BankLine], int]:
    """The statement's lines, by the columns named. Rows that are not
    transactions (totals, balances, blanks) are counted, not read."""
    from ..migration.reader import read_table

    if "date" not in columns or not ({"credit", "debit", "amount"} & set(columns)):
        raise ProfileOSError("צריך לבחור לפחות עמודת תאריך ועמודת סכום (או זכות/חובה)")
    table = read_table(Path(path), expected=[w for words in FIELDS.values() for w in words])
    missing = [name for name in columns.values() if name and name not in table.headers]
    if missing:
        raise ProfileOSError("בקובץ אין עמודות בשם: " + ", ".join(missing))
    lines: list[BankLine] = []
    skipped = 0
    seen: dict[tuple, int] = {}
    for row in table:
        on = _date(row.get(columns["date"], ""))
        if "amount" in columns:
            amount = _number(row.get(columns["amount"], ""))
        else:
            credit = _number(row.get(columns.get("credit", ""), "")) or 0.0
            debit = _number(row.get(columns.get("debit", ""), "")) or 0.0
            amount = abs(credit) - abs(debit) if (credit or debit) else None
        if on is None or not amount:
            skipped += 1
            continue
        line = BankLine(
            on=on, amount=round(amount, 2),
            text=str(row.get(columns.get("description", ""), "")).strip(),
            reference=str(row.get(columns.get("reference", ""), "")).strip(),
        )
        same = (line.on, line.amount, line.reference, line.text)
        seen[same] = seen.get(same, -1) + 1
        line.occurrence = seen[same]
        lines.append(line)
    return lines, skipped


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _digits(text: str) -> set[str]:
    return {run.lstrip("0") for run in re.findall(r"\d{3,}", text) if run.lstrip("0")}


def _says_number(line: BankLine, number: str) -> bool:
    wanted = number.strip().lstrip("0")
    return bool(wanted) and wanted in _digits(f"{line.reference} {line.text}")


def match(lines: Iterable[BankLine], *, cheques: Iterable[Any] = (), jobs: Iterable[Any] = (),
          seen: Iterable[str] = ()) -> Reading:
    """Each line set against what the shop is expecting."""
    from ..projects.account import job_account

    reading = Reading()
    seen = set(seen)
    cheques = list(cheques)
    jobs = list(jobs)
    taken: set[str] = set()
    owed = {job.job_id: job_account(job, cheques).uncovered for job in jobs}
    for line in lines:
        if line.key in seen:
            reading.already += 1
            continue
        reading.lines.append(line)
        found: Match | None = None
        if line.amount > 0:
            for cheque in cheques:
                if (cheque.outgoing or cheque.cheque_id in taken
                        or cheque.state.value != "deposited"
                        or abs(cheque.amount - line.amount) > 0.5):
                    continue
                deposited = cheque.deposited_on or cheque.due
                if not (deposited <= line.on <= deposited + timedelta(days=CLEARING_DAYS)):
                    continue
                numbered = bool(cheque.number) and _says_number(line, cheque.number)
                found = Match(line, "clear", cheque,
                              f"{cheque.customer} ⁦{cheque.amount:,.0f} ₪⁩"
                              + (f" · צ׳ק ⁦{cheque.number}⁩" if cheque.number else ""),
                              sure=numbered)
                if numbered:
                    break
            if found is None:
                text = f"{line.text} {line.reference}"
                for job in jobs:
                    name = (job.customer_name or "").strip()
                    words = [w for w in re.split(r"\s+", name) if len(w) >= 3]
                    due = owed.get(job.job_id, 0.0)
                    if (words and any(w in text for w in words)
                            and 0 < line.amount <= due + 0.5):
                        found = Match(line, "payment", job,
                                      f"⁦{job.job_id}⁩ {name} — נותר ⁦{due:,.0f} ₪⁩",
                                      sure=abs(line.amount - due) < 0.5)
                        break
        else:
            out = -line.amount
            for cheque in cheques:
                if cheque.cheque_id in taken or abs(cheque.amount - out) > 0.5:
                    continue
                numbered = bool(cheque.number) and _says_number(line, cheque.number)
                if cheque.outgoing and cheque.state.value == "held" and numbered:
                    found = Match(line, "paid_out", cheque,
                                  f"{cheque.customer} · צ׳ק ⁦{cheque.number}⁩", sure=True)
                    break
                if (not cheque.outgoing and cheque.state.value in ("deposited", "cleared")
                        and ("חזר" in line.text or "החזר" in line.text) and numbered):
                    found = Match(line, "bounce", cheque,
                                  f"{cheque.customer} · צ׳ק ⁦{cheque.number}⁩", sure=True)
                    break
        if found is not None:
            if hasattr(found.target, "cheque_id"):
                taken.add(found.target.cheque_id)
            if found.kind == "payment":
                # What this line pays is no longer owed: a second transfer
                # is matched against what is left, not the whole balance.
                owed[found.target.job_id] -= line.amount
            reading.matches.append(found)
        elif line.amount > 0:
            reading.unexplained.append(line)
    return reading


# --------------------------------------------------------------------------- #
# What was applied
# --------------------------------------------------------------------------- #
def seen_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "bank_lines_applied.json"


def applied_keys() -> set[str]:
    from ..core.storage import read_json

    raw = read_json(seen_path(), default={}) or {}
    return set(raw.get("keys", []))


def remember(keys: Iterable[str]) -> None:
    from ..core.storage import path_lock, read_json, write_json

    path = seen_path()
    with path_lock(path):
        raw = read_json(path, default={}) or {}
        known = list(dict.fromkeys(list(raw.get("keys", [])) + list(keys)))
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {"keys": known})


__all__ = [
    "BankLine",
    "CLEARING_DAYS",
    "FIELDS",
    "FIELD_HEBREW",
    "Match",
    "Reading",
    "applied_keys",
    "guess_columns",
    "match",
    "read_statement",
    "remember",
]
