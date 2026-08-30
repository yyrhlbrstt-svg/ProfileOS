"""Bringing a shop's existing records across, without trusting the file.

An import is the most dangerous thing a business application does: it writes
hundreds of records at once, from a file nobody has read, into the place the
shop keeps its customers. So nothing here writes anything until somebody has
seen what it is about to do.

Every import produces a plan first — what will be created, what will be
updated, what will be skipped and why, and which spreadsheet column was
matched to which field. Only then, and only on a second call, does anything
land in the shop's data.

Rows are never half-imported. A row missing the one thing that makes it
useful is skipped by name rather than written with a blank where the name
should be, because a customer called "" is worse than a customer missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .reader import Table, match_columns, read_table, to_number

_log = get_logger("migration.importers")


#: Every spelling of a column these exports use. Longer, more specific
#: spellings are listed first so an exact match wins over a contains match.
CUSTOMER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("שם לקוח", "שם הלקוח", "לקוח", "שם", "customer", "name", "client"),
    "contact": ("איש קשר", "נציג", "contact"),
    "phone": ("טלפון", "נייד", "פלאפון", "phone", "mobile", "tel"),
    "email": ("דואל", 'דוא"ל', "מייל", "email", "e mail"),
    "address": ("כתובת", "רחוב", "address", "street"),
    "city": ("עיר", "יישוב", "ישוב", "city", "town"),
    "tax_id": ("ח.פ.", 'ח"פ', "חפ", "עוסק מורשה", "עוסק", "מספר עוסק", "vat", "tax id"),
    "notes": ("הערות", "הערה", "notes", "remark"),
}

JOB_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("שם הפרויקט", "שם פרויקט", "פרויקט", "עבודה", "project", "job"),
    "customer_name": ("לקוח", "שם לקוח", "customer", "client"),
    "reference": ("אסמכתא", "הזמנה", "מספר הזמנה", "reference", "order"),
    "site_address": ("כתובת האתר", "אתר", "כתובת", "site", "address"),
    "quoted": ("סכום", "מחיר", "הצעה", "סה\"כ", "total", "amount", "price"),
    "notes": ("הערות", "notes"),
}

PRICE_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("מק\"ט", "מקט", "קוד", "פריט", "code", "item", "sku", "article"),
    "description": ("תיאור", "שם פריט", "תאור", "description", "name"),
    "price": ("מחיר", "מחירון", "price", "unit price", "rate"),
    "unit": ("יחידה", "יח", "unit", "uom"),
    "supplier": ("ספק", "יצרן", "supplier", "vendor"),
    "currency": ("מטבע", "currency"),
}


@dataclass
class Row:
    """One line of the spreadsheet, and what will become of it."""

    number: int
    action: str          # "create" | "update" | "skip"
    label: str
    reason: str = ""
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportPlan:
    """What an import would do, shown before it does any of it."""

    kind: str
    table: Table
    columns: dict[str, str] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)
    #: Fields the file has no column for, so nobody assumes they came across.
    unmatched_fields: list[str] = field(default_factory=list)
    #: Columns in the file that nothing was read from.
    ignored_columns: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def of_action(self, action: str) -> list[Row]:
        return [row for row in self.rows if row.action == action]

    @property
    def creates(self) -> int:
        return len(self.of_action("create"))

    @property
    def updates(self) -> int:
        return len(self.of_action("update"))

    @property
    def skips(self) -> int:
        return len(self.of_action("skip"))

    @property
    def is_safe(self) -> bool:
        """Whether there is anything to do and nothing that would go wrong."""
        return not self.problems and (self.creates or self.updates)

    def summary(self) -> str:
        return (
            f"{self.table.source}: ⁦{self.creates}⁩ חדשים, ⁦{self.updates}⁩ עדכונים, "
            f"⁦{self.skips}⁩ דילוגים (קידוד {self.table.encoding})"
        )

    def describe_columns(self) -> list[tuple[str, str]]:
        return sorted(self.columns.items())


def _plan(
    path: Path,
    kind: str,
    aliases: dict[str, tuple[str, ...]],
    required: tuple[str, ...],
) -> ImportPlan:
    """Read the file and work out the column mapping, with nothing written."""
    expected = [spelling for spellings in aliases.values() for spelling in spellings]
    table = read_table(path, expected=expected)
    columns = match_columns(table.headers, aliases)
    plan = ImportPlan(kind=kind, table=table, columns=columns)

    plan.unmatched_fields = [name for name in aliases if name not in columns]
    plan.ignored_columns = [
        header for header in table.headers if header not in columns.values()
    ]
    for name in required:
        if name not in columns:
            plan.problems.append(
                f"אין עמודה עבור ״{name}״ — הקובץ מכיל: "
                + ", ".join(table.headers[:8])
            )
    return plan


def _value(row: dict[str, str], columns: dict[str, str], name: str) -> str:
    header = columns.get(name)
    return row.get(header, "").strip() if header else ""


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #

def plan_customers(path: Path, book: Any = None) -> ImportPlan:
    """What importing this customer list would do."""
    from ..projects import default_customers

    book = book if book is not None else default_customers()
    plan = _plan(path, "customers", CUSTOMER_ALIASES, required=("name",))
    if plan.problems:
        return plan

    existing = {customer.name.strip().casefold(): customer for customer in book.all()}
    seen: set[str] = set()
    for number, row in enumerate(plan.table.rows, start=1):
        name = _value(row, plan.columns, "name")
        if not name:
            plan.rows.append(Row(number, "skip", f"שורה {number}", "אין שם לקוח"))
            continue
        key = name.casefold()
        if key in seen:
            plan.rows.append(Row(number, "skip", name, "כפול בתוך הקובץ"))
            continue
        seen.add(key)

        values = {
            name_: _value(row, plan.columns, name_)
            for name_ in CUSTOMER_ALIASES
            if _value(row, plan.columns, name_)
        }
        if key in existing:
            plan.rows.append(Row(number, "update", name, "קיים — יעודכן", values))
        else:
            plan.rows.append(Row(number, "create", name, "", values))
    return plan


def import_customers(plan: ImportPlan, book: Any = None) -> dict[str, int]:
    """Write a plan that somebody has looked at."""
    from ..projects import default_customers

    if plan.kind != "customers":
        raise ProfileOSError("התוכנית הזאת אינה של לקוחות")
    if plan.problems:
        raise ProfileOSError("אי אפשר לייבא: " + " · ".join(plan.problems))

    book = book if book is not None else default_customers()
    existing = {customer.name.strip().casefold(): customer for customer in book.all()}
    created = updated = failed = 0

    for row in plan.rows:
        if row.action == "skip":
            continue
        values = dict(row.values)
        values.pop("name", None)
        try:
            if row.action == "update":
                customer = existing[row.label.casefold()]
                for name_, value in values.items():
                    setattr(customer, name_, value)
                book.save()
                updated += 1
            else:
                book.add(name=row.label, **values)
                created += 1
        except Exception as exc:  # noqa: BLE001 - one bad row, not the import
            _log.warning("Could not import %s: %s", row.label, exc)
            row.reason = str(exc)
            failed += 1
    _log.info("Imported customers: %d created, %d updated", created, updated)
    return {"created": created, "updated": updated, "failed": failed}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #

def plan_jobs(path: Path, store: Any = None) -> ImportPlan:
    """What importing this job list would do."""
    from ..projects import default_store

    store = store if store is not None else default_store()
    plan = _plan(path, "jobs", JOB_ALIASES, required=("name",))
    if plan.problems:
        return plan

    existing = {job.name.strip().casefold() for job in store.all()}
    for number, row in enumerate(plan.table.rows, start=1):
        name = _value(row, plan.columns, "name")
        if not name:
            plan.rows.append(Row(number, "skip", f"שורה {number}", "אין שם פרויקט"))
            continue
        if name.casefold() in existing:
            plan.rows.append(Row(number, "skip", name, "פרויקט בשם הזה כבר קיים"))
            continue
        values: dict[str, Any] = {
            name_: _value(row, plan.columns, name_)
            for name_ in ("customer_name", "reference", "site_address", "notes")
            if _value(row, plan.columns, name_)
        }
        quoted = to_number(_value(row, plan.columns, "quoted"))
        if quoted:
            values["quoted"] = quoted
        plan.rows.append(Row(number, "create", name, "", values))
    return plan


def import_jobs(plan: ImportPlan, store: Any = None) -> dict[str, int]:
    from ..projects import default_store

    if plan.kind != "jobs":
        raise ProfileOSError("התוכנית הזאת אינה של פרויקטים")
    if plan.problems:
        raise ProfileOSError("אי אפשר לייבא: " + " · ".join(plan.problems))

    from ..projects import default_customers

    store = store if store is not None else default_store()
    book = default_customers()
    # An imported job should point at the imported customer, not merely carry
    # their name as text — otherwise the two lists never join up and every
    # later filter by customer misses these jobs.
    by_name = {customer.name.strip().casefold(): customer for customer in book.all()}

    created = failed = 0
    for row in plan.rows:
        if row.action != "create":
            continue
        values = dict(row.values)
        quoted = values.pop("quoted", None)
        customer_name = values.pop("customer_name", "")
        customer = by_name.get(customer_name.strip().casefold()) if customer_name else None
        try:
            job = store.create(name=row.label, customer=customer, **values)
            if customer is None and customer_name:
                # Named but not in the book: keep the name so nothing is lost,
                # and let somebody link it later.
                job.customer_name = customer_name
            if quoted:
                job.record_quote(float(quoted))
            store.save(job)
            created += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not import job %s: %s", row.label, exc)
            row.reason = str(exc)
            failed += 1
    return {"created": created, "updated": 0, "failed": failed}


# --------------------------------------------------------------------------- #
# Price lists
# --------------------------------------------------------------------------- #

def plan_prices(path: Path) -> ImportPlan:
    """What importing this supplier price list would do."""
    plan = _plan(path, "prices", PRICE_ALIASES, required=("code", "price"))
    if plan.problems:
        return plan

    seen: set[str] = set()
    for number, row in enumerate(plan.table.rows, start=1):
        code = _value(row, plan.columns, "code")
        price = to_number(_value(row, plan.columns, "price"))
        if not code:
            plan.rows.append(Row(number, "skip", f"שורה {number}", "אין מק״ט"))
            continue
        if price is None:
            plan.rows.append(Row(number, "skip", code, "אין מחיר קריא"))
            continue
        if price < 0:
            plan.rows.append(Row(number, "skip", code, "מחיר שלילי"))
            continue
        if code in seen:
            plan.rows.append(Row(number, "skip", code, "כפול בתוך הקובץ"))
            continue
        seen.add(code)
        plan.rows.append(Row(number, "create", code, "", {
            "code": code,
            "price": price,
            "description": _value(row, plan.columns, "description"),
            "unit": _value(row, plan.columns, "unit") or "pc",
            "supplier": _value(row, plan.columns, "supplier"),
            "currency": _value(row, plan.columns, "currency") or "ILS",
        }))
    return plan


def import_prices(plan: ImportPlan, path: Path | None = None) -> dict[str, int]:
    """Write the price list where the quoting engine will find it."""
    import json

    from ..core.config import get_settings

    if plan.kind != "prices":
        raise ProfileOSError("התוכנית הזאת אינה של מחירון")
    if plan.problems:
        raise ProfileOSError("אי אפשר לייבא: " + " · ".join(plan.problems))

    destination = Path(path) if path else get_settings().data_dir / "price_list.json"
    destination.parent.mkdir(parents=True, exist_ok=True)

    entries: dict[str, dict[str, Any]] = {}
    if destination.is_file():
        try:
            entries = {
                item["code"]: item
                for item in json.loads(destination.read_text(encoding="utf-8")).get(
                    "prices", []
                )
            }
        except Exception:  # noqa: BLE001 - a corrupt list is replaced, not merged
            _log.warning("Existing price list at %s unreadable; replacing", destination)
            entries = {}

    created = updated = 0
    for row in plan.rows:
        if row.action != "create":
            continue
        if row.label in entries:
            updated += 1
        else:
            created += 1
        entries[row.label] = dict(row.values)

    destination.write_text(
        json.dumps(
            {"prices": sorted(entries.values(), key=lambda item: item["code"])},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    _log.info("Price list: %d new, %d updated at %s", created, updated, destination)
    return {"created": created, "updated": updated, "failed": 0}


PLANNERS = {
    "customers": plan_customers,
    "jobs": plan_jobs,
    "prices": plan_prices,
}

IMPORTERS = {
    "customers": import_customers,
    "jobs": import_jobs,
    "prices": import_prices,
}


__all__ = [
    "CUSTOMER_ALIASES",
    "IMPORTERS",
    "ImportPlan",
    "JOB_ALIASES",
    "PLANNERS",
    "PRICE_ALIASES",
    "Row",
    "import_customers",
    "import_jobs",
    "import_prices",
    "plan_customers",
    "plan_jobs",
    "plan_prices",
]
