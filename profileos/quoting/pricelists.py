"""Supplier price lists: kept edition by edition, compared, and put to work.

The most frequent change a shop receives is not a new profile: it is a new
price list. Aluminium follows the metal exchange, glass and hardware follow
the importer, and the list arrives as an Excel file attached to an email.
Until now that file was either typed into nowhere or imported into a file
the quoting engine never read, and nobody could say what changed.

Here every list the shop takes in is an *edition* of that supplier's prices,
with the file it came from and the date it takes effect. A new edition is
compared with the one in force — what went up, what went down, by how much —
and only becomes the prices quotations are worked out from once somebody
accepts it. Then the comparison is turned on the jobs themselves:

* a quotation already sent was priced on the old list: if the material in it
  now costs more, that is margin gone before the customer has even signed;
* a job that is won but not yet ordered can still be bought at the old price
  if the new list only takes effect next month.

Prices are never guessed. A code that is not in the list stays unpriced; a
line whose unit cannot be matched to how the job counts it is left out of the
estimate and said to be left out.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.storage import MergingBook, stored

_log = get_logger("quoting.pricelists")

#: Job states whose material is not bought yet.
QUOTED_STATES = ("quoted",)
NOT_ORDERED_STATES = ("won",)


class ListState:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    HEBREW = {"pending": "ממתין לבדיקה", "accepted": "בתוקף", "rejected": "נדחה"}


#: The ways a unit is written on an Israeli price list, to one spelling.
_UNITS = {
    "m": ("m", "מ", "מ'", "מ׳", "מטר", "מ\"א", "מ״א", "מטר אורך", "lm", "rm"),
    "pc": ("pc", "pcs", "יח", "יח'", "יח׳", "יחידה", "יחידות", "ea", "each", "unit"),
    "m2": ("m2", "m²", "מ\"ר", "מ״ר", "מטר מרובע", "sqm"),
    "kg": ("kg", "ק\"ג", "ק״ג", "קג", "קילו"),
    "bar": ("bar", "מוט", "מוטות", "length"),
    "set": ("set", "סט", "ערכה", "קומפלט"),
    "l": ("l", "ליטר"),
    "h": ("h", "שעה", "hr"),
}
_UNIT_OF = {spelling.casefold(): unit for unit, spellings in _UNITS.items() for spelling in spellings}

UNIT_HEBREW = {
    "m": "מ׳", "pc": "יח׳", "m2": "מ״ר", "kg": "ק״ג", "bar": "מוט", "set": "סט",
    "l": "ליטר", "h": "שעה", "": "",
}


def unit_key(text: str) -> str:
    """One spelling of a unit; empty when it cannot be told."""
    cleaned = (text or "").strip().casefold().rstrip(".")
    return _UNIT_OF.get(cleaned, cleaned)


def code_key(code: str) -> str:
    """An article code with punctuation and case set aside, nothing more."""
    return re.sub(r"[^0-9A-Za-zא-ת]+", "", code or "").upper()


def supplier_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #

@dataclass
class PriceRow:
    code: str
    price: float
    unit: str = ""
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PriceRow":
        if not raw.get("code"):
            raise ProfileOSError("שורת מחירון בלי מק״ט")
        return cls(
            code=str(raw["code"]), price=float(raw.get("price", 0.0) or 0.0),
            unit=str(raw.get("unit", "") or ""), description=str(raw.get("description", "") or ""),
        )


@dataclass
class PriceListEdition:
    """One supplier's price list, as one file gave it."""

    edition_id: str
    supplier: str
    #: The file and anything else that says which list this is.
    source: str
    received_on: str = field(default_factory=lambda: date.today().isoformat())
    #: When the supplier says the prices take effect; empty means on receipt.
    valid_from: str = ""
    currency: str = "ILS"
    recorded_by: str = ""
    state: str = ListState.PENDING
    decided_at: str = ""
    decided_by: str = ""
    decision_no: int = 0
    note: str = ""
    warnings: list[str] = field(default_factory=list)
    prices: dict[str, PriceRow] = field(default_factory=dict)

    @property
    def supplier_id(self) -> str:
        return supplier_key(self.supplier)

    @property
    def effective(self) -> str:
        return self.valid_from or self.received_on

    def in_force(self, on: date | None = None) -> bool:
        on = on or date.today()
        try:
            return date.fromisoformat(self.effective[:10]) <= on
        except ValueError:
            return True

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["prices"] = [row.as_dict() for row in self.prices.values()]
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PriceListEdition":
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in known}
        if not values.get("edition_id") or not values.get("supplier") or not values.get("source"):
            raise ProfileOSError("מחירון חייב מזהה, ספק ומקור")
        prices: dict[str, PriceRow] = {}
        for entry in values.pop("prices", []) or []:
            row = PriceRow.from_dict(entry)
            prices[code_key(row.code)] = row
        if values.get("state") not in ListState.HEBREW:
            values["state"] = ListState.PENDING
        values["decision_no"] = int(values.get("decision_no", 0) or 0)
        values["warnings"] = [str(w) for w in values.get("warnings") or []]
        return cls(prices=prices, **values)


# --------------------------------------------------------------------------- #
# Comparing
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PriceChange:
    code: str
    description: str
    old: float
    new: float
    unit: str = ""

    @property
    def relative(self) -> float:
        return (self.new - self.old) / self.old if self.old else 0.0

    def describe(self) -> str:
        unit = UNIT_HEBREW.get(unit_key(self.unit), self.unit)
        per = f" ל{unit}" if unit else ""
        return (
            f"⁦{self.code}⁩ {self.description}".rstrip()
            + f": היה ⁦{self.old:,.2f} ₪⁩, עכשיו ⁦{self.new:,.2f} ₪⁩{per}"
            + f" (⁦{self.relative * 100:+.1f}%⁩)"
        )


@dataclass
class PriceDiff:
    first: bool
    up: list[PriceChange] = field(default_factory=list)
    down: list[PriceChange] = field(default_factory=list)
    added: list[PriceRow] = field(default_factory=list)
    missing: list[PriceRow] = field(default_factory=list)
    unchanged: int = 0
    #: Codes whose unit changed: their prices cannot be compared.
    unit_changed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> list[PriceChange]:
        return self.up + self.down

    @property
    def average(self) -> float:
        """The mean change over every code in both lists, unchanged included."""
        count = len(self.up) + len(self.down) + self.unchanged
        if not count:
            return 0.0
        return sum(change.relative for change in self.changed) / count

    def summary(self) -> str:
        if self.first:
            return f"מחירון ראשון מהספק: ⁦{len(self.added)}⁩ פריטים"
        parts = []
        if self.up:
            parts.append(f"⁦{len(self.up)}⁩ התייקרו")
        if self.down:
            parts.append(f"⁦{len(self.down)}⁩ הוזלו")
        if self.added:
            parts.append(f"⁦{len(self.added)}⁩ חדשים")
        if self.missing:
            parts.append(f"⁦{len(self.missing)}⁩ לא במחירון החדש")
        if not parts:
            return f"אין שינוי מחירים (⁦{self.unchanged}⁩ פריטים)"
        if self.changed:
            parts.append(f"ממוצע ⁦{self.average * 100:+.1f}%⁩")
        return " · ".join(parts)

    def lines(self, limit: int = 15) -> list[str]:
        out = [self.summary(), ""]
        if self.up:
            out.append("ההתייקרויות הגדולות")
            for change in sorted(self.up, key=lambda c: -c.relative)[:limit]:
                out.append(f"  • {change.describe()}")
            out.append("")
        if self.down:
            out.append("ההוזלות הגדולות")
            for change in sorted(self.down, key=lambda c: c.relative)[:limit]:
                out.append(f"  • {change.describe()}")
            out.append("")
        if self.unit_changed:
            out.append("יחידת המידה השתנתה — המחיר לא הושווה: "
                       + ", ".join(f"⁦{code}⁩" for code in self.unit_changed[:10]))
            out.append("")
        if self.missing:
            out.append("לא במחירון החדש — לברר מול הספק")
            out += [f"  • ⁦{row.code}⁩ {row.description}".rstrip() for row in self.missing[:limit]]
            out.append("")
        return out


def compare(old: PriceListEdition | None, new: PriceListEdition) -> PriceDiff:
    if old is None:
        return PriceDiff(first=True, added=sorted(new.prices.values(), key=lambda r: r.code))
    diff = PriceDiff(first=False)
    for key, row in sorted(new.prices.items()):
        before = old.prices.get(key)
        if before is None:
            diff.added.append(row)
            continue
        if unit_key(before.unit) != unit_key(row.unit) and before.unit and row.unit:
            diff.unit_changed.append(row.code)
            continue
        if abs(row.price - before.price) <= 1e-9 * max(1.0, abs(before.price)):
            diff.unchanged += 1
            continue
        change = PriceChange(row.code, row.description or before.description,
                             before.price, row.price, row.unit or before.unit)
        (diff.up if row.price > before.price else diff.down).append(change)
    diff.missing = sorted(
        (row for key, row in old.prices.items() if key not in new.prices),
        key=lambda r: r.code,
    )
    return diff


# --------------------------------------------------------------------------- #
# The book
# --------------------------------------------------------------------------- #

class PriceListBook(MergingBook):
    """Every supplier price list the shop has taken in."""

    _list_key = "editions"
    _id_field = "edition_id"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._editions: dict[str, PriceListEdition] = {}

    def _records(self) -> dict[str, Any]:
        return self._editions

    def _to_raw(self, record: Any) -> dict[str, Any]:
        return record.as_dict()

    def _from_raw(self, raw: dict[str, Any]) -> Any:
        return PriceListEdition.from_dict(raw)

    def load(self) -> "PriceListBook":
        if self.path is None or not stored(self.path):
            self._remember_loaded()
            return self
        raw = self._read_or_mark(self.path)
        for entry in raw.get("editions", []):
            try:
                edition = PriceListEdition.from_dict(entry)
            except Exception as exc:  # noqa: BLE001 - kept on disk by the merge
                _log.warning("Skipped an unreadable price list: %s", exc)
                continue
            self._editions[edition.edition_id] = edition
        self._remember_loaded()
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save_merged(self.path)

    def __len__(self) -> int:
        return len(self._editions)

    def __iter__(self):
        """Waiting lists first, then the newest."""
        newest = sorted(
            self._editions.values(),
            key=lambda e: (e.received_on, e.decision_no, e.edition_id), reverse=True,
        )
        return iter(sorted(newest, key=lambda e: e.state != ListState.PENDING))

    def get(self, edition_id: str) -> PriceListEdition | None:
        return self._editions.get(edition_id)

    def pending(self) -> list[PriceListEdition]:
        return [e for e in self if e.state == ListState.PENDING]

    def accepted(self, supplier_id: str) -> list[PriceListEdition]:
        """A supplier's accepted lists, in the order they were accepted."""
        return sorted(
            (e for e in self._editions.values()
             if e.state == ListState.ACCEPTED and e.supplier_id == supplier_id),
            key=lambda e: (e.decision_no, e.decided_at, e.edition_id),
        )

    def suppliers(self) -> list[str]:
        return sorted({e.supplier_id for e in self._editions.values()
                       if e.state == ListState.ACCEPTED})

    def current(self, supplier_id: str, on: date | None = None) -> PriceListEdition | None:
        """The list in force today: the latest accepted one already in effect."""
        in_force = [e for e in self.accepted(supplier_id) if e.in_force(on)]
        return in_force[-1] if in_force else None

    def upcoming(self, supplier_id: str, on: date | None = None) -> PriceListEdition | None:
        """An accepted list that takes effect later — the one to buy ahead of."""
        later = [e for e in self.accepted(supplier_id) if not e.in_force(on)]
        return later[-1] if later else None

    def state_text(self, edition: PriceListEdition, on: date | None = None) -> str:
        """In words: waiting, in force, coming in on a date, or replaced."""
        if edition.state != ListState.ACCEPTED:
            return ListState.HEBREW.get(edition.state, edition.state)
        if not edition.in_force(on):
            return "ייכנס לתוקף"
        current = self.current(edition.supplier_id, on)
        if current is not None and current.edition_id == edition.edition_id:
            return "בתוקף"
        return "הוחלף"

    def previous(self, edition: PriceListEdition) -> PriceListEdition | None:
        """What this edition is compared with: the list accepted before it."""
        accepted = self.accepted(edition.supplier_id)
        if edition.state == ListState.ACCEPTED:
            ids = [e.edition_id for e in accepted]
            index = ids.index(edition.edition_id) if edition.edition_id in ids else len(ids)
            return accepted[index - 1] if index > 0 else None
        return accepted[-1] if accepted else None

    def diff(self, edition: PriceListEdition) -> PriceDiff:
        return compare(self.previous(edition), edition)

    # -- changes -------------------------------------------------------------- #
    def record(self, rows: Iterable[PriceRow], *, supplier: str, source: str,
               valid_from: str = "", currency: str = "ILS", recorded_by: str = "",
               warnings: Iterable[str] = ()) -> PriceListEdition:
        if not supplier.strip():
            raise ProfileOSError("כתבו מאיזה ספק המחירון")
        if not source.strip():
            raise ProfileOSError(
                "כתבו מאיפה המחירון — שם הקובץ, התאריך או המייל. בלי מקור אי אפשר "
                "לדעת מאוחר יותר על מה מבוססים המחירים בהצעה."
            )
        if valid_from:
            try:
                date.fromisoformat(valid_from[:10])
            except ValueError as exc:
                raise ProfileOSError(f"תאריך התחולה ⁦{valid_from}⁩ לא תקין") from exc
        prices: dict[str, PriceRow] = {}
        notes = list(warnings)
        for row in rows:
            key = code_key(row.code)
            if not key:
                continue
            if key in prices:
                notes.append(f"⁦{row.code}⁩ מופיע יותר מפעם אחת — נשמרה השורה הראשונה")
                continue
            prices[key] = row
        if not prices:
            raise ProfileOSError("לא נמצאה אף שורת מחיר בקובץ")
        edition = PriceListEdition(
            edition_id=f"PL-{date.today():%Y%m%d}-{uuid4().hex[:6].upper()}",
            supplier=supplier.strip(), source=source.strip(),
            valid_from=valid_from[:10] if valid_from else "",
            currency=currency or "ILS", recorded_by=recorded_by.strip(),
            warnings=notes, prices=prices,
        )
        self._editions[edition.edition_id] = edition
        self.save()
        return edition

    def _decide(self, edition_id: str, state: str, by: str, note: str) -> PriceListEdition:
        edition = self._editions.get(edition_id)
        if edition is None:
            raise ProfileOSError(f"אין מחירון {edition_id}")
        if self.path is not None and stored(self.path):
            on_disk = PriceListBook(self.path).load().get(edition_id)
            if on_disk is not None and on_disk.state != ListState.PENDING:
                self._editions[edition_id] = on_disk
                self._loaded[edition_id] = on_disk.as_dict()
                edition = on_disk
        if edition.state != ListState.PENDING:
            raise ProfileOSError(
                f"המחירון כבר {ListState.HEBREW[edition.state]} — אפשר להחליט רק על מחירון שממתין"
            )
        edition.state = state
        edition.decided_by = by.strip()
        edition.decided_at = datetime.now().isoformat(timespec="seconds")
        edition.decision_no = 1 + max((e.decision_no for e in self._editions.values()), default=0)
        edition.note = note.strip()
        self.save()
        return edition

    def accept(self, edition_id: str, *, by: str = "", note: str = "") -> PriceListEdition:
        edition = self._decide(edition_id, ListState.ACCEPTED, by, note)
        apply_price_lists(self)
        return edition

    def reject(self, edition_id: str, *, by: str = "", note: str = "") -> PriceListEdition:
        return self._decide(edition_id, ListState.REJECTED, by, note)


def default_price_lists_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "supplier_price_lists.json"


def default_price_lists() -> PriceListBook:
    return PriceListBook(default_price_lists_path()).load()


# --------------------------------------------------------------------------- #
# Reading a file
# --------------------------------------------------------------------------- #

def rows_from_file(path: Path) -> tuple[list[PriceRow], list[str], dict[str, str]]:
    """The price rows of a supplier's Excel or CSV file.

    Returns the rows, what could not be read (row by row, in words), and
    which column was matched to which field — for the person to check before
    a single price is taken in.
    """
    from ..migration.importers import PRICE_ALIASES, plan_prices

    plan = plan_prices(Path(path))
    if plan.problems:
        raise ProfileOSError(" · ".join(plan.problems))
    rows: list[PriceRow] = []
    skipped: list[str] = []
    for row in plan.rows:
        if row.action != "create":
            skipped.append(f"{row.label}: {row.reason}")
            continue
        values = row.values
        rows.append(PriceRow(
            code=str(values["code"]), price=float(values["price"]),
            unit=str(values.get("unit") or ""), description=str(values.get("description") or ""),
        ))
    matched = {name: plan.columns[name] for name in PRICE_ALIASES if name in plan.columns}
    return rows, skipped, matched


# --------------------------------------------------------------------------- #
# Putting the prices to work
# --------------------------------------------------------------------------- #

def _as_supplier(edition: PriceListEdition,
                 mass_per_metre: Callable[[str], float | None] = lambda _code: None) -> Any:
    """The list as the quoting engine reads prices.

    Each price keeps its unit, and the engine will not use it for a quantity
    counted in another. Aluminium priced by the kilogram is turned into a
    price per metre with the weight from the supplier's own catalogue; with
    no weight on record it stays per kilogram, and the job's metres of that
    profile stay unpriced — said, not guessed.
    """
    from .suppliers import PriceEntry, Supplier

    entries = []
    for row in edition.prices.values():
        unit = unit_key(row.unit)
        price = row.price
        metadata: dict[str, Any] = {"unit_known": bool(unit), "list": edition.edition_id}
        if unit == "kg":
            weight = mass_per_metre(row.code)
            if weight:
                metadata.update({"price_per_kg": price, "kg_per_m": weight})
                price, unit = round(price * weight, 4), "m"
        entries.append(PriceEntry(code=row.code, price=price, unit=unit or "pc",
                                  description=row.description or None, metadata=metadata))
    return Supplier(
        id=f"shop:{edition.supplier_id}", name=edition.supplier, version=edition.edition_id,
        currency=edition.currency, entries=entries,
        notes=f"{edition.source} · בתוקף מ־{edition.effective}",
    )


def apply_price_lists(book: PriceListBook | None = None) -> int:
    """Put each supplier's list in force where the quoting engine finds prices."""
    from ..core.registry import SUPPLIERS

    book = book or default_price_lists()
    weights = catalogue_weights()
    count = 0
    for supplier_id in book.suppliers():
        edition = book.current(supplier_id)
        if edition is None:
            continue
        try:
            supplier = _as_supplier(edition, weights)
            SUPPLIERS.add(supplier.id, supplier, version=edition.edition_id, source="shop")
            count += 1
        except Exception as exc:  # noqa: BLE001 - one list, not the rest
            _log.warning("Price list %s not applied: %s", edition.edition_id, exc)
    return count


def _legacy_file() -> Any:
    """Prices imported before price lists were kept by edition.

    They were written to ``price_list.json`` and never read; they are put to
    work as they are, under the name they were imported with, until a list
    from the same supplier is taken in the new way.
    """
    from ..core.config import get_settings
    from ..core.storage import read_json
    from .suppliers import PriceEntry, Supplier

    path = get_settings().data_dir / "price_list.json"
    if not stored(path):
        return []
    raw = read_json(path, default={}) or {}
    by_supplier: dict[str, list[Any]] = {}
    for item in raw.get("prices", []):
        try:
            unit = unit_key(str(item.get("unit") or ""))
            entry = PriceEntry(code=str(item["code"]), price=float(item["price"]),
                               unit=unit or "pc", description=item.get("description") or None,
                               metadata={"unit_known": bool(unit)})
        except Exception:  # noqa: BLE001 - one row, not the list
            continue
        by_supplier.setdefault(str(item.get("supplier") or "מחירון מיובא"), []).append(entry)
    return [
        Supplier(id=f"shop:{supplier_key(name)}", name=name, currency="ILS", entries=entries,
                 notes="יובא בעבר ל־price_list.json")
        for name, entries in by_supplier.items()
    ]


def load_price_lists() -> int:
    """At start-up: every supplier's list in force, where quoting finds prices."""
    from ..core.registry import SUPPLIERS

    try:
        book = default_price_lists()
        kept = set(book.suppliers())
        for supplier in _legacy_file():
            if supplier.id.removeprefix("shop:") not in kept:
                SUPPLIERS.add(supplier.id, supplier, version="legacy", source="shop")
        return apply_price_lists(book)
    except Exception as exc:  # noqa: BLE001 - quoting still runs on its own figures
        _log.warning("Price lists not loaded: %s", exc)
        return 0


# --------------------------------------------------------------------------- #
# What it means for the jobs
# --------------------------------------------------------------------------- #

def job_materials(job: Any) -> list[Any]:
    """The job's bill of materials, from its openings."""
    from ..elements.builder import ElementBuilder
    from .bom import build_bom

    schedule = getattr(job, "schedule", None)
    if schedule is None or not getattr(schedule, "openings", None):
        return []
    builder = ElementBuilder()
    builds = []
    for opening in schedule.openings:
        try:
            builds.append(builder.build(opening, sill_height=schedule.sill_height))
        except Exception as exc:  # noqa: BLE001 - one opening, not the whole job
            _log.info("Opening %s not built for pricing: %s", getattr(opening, "name", "?"), exc)
    if not builds:
        return []
    return build_bom(builds, project_id=job.job_id).sorted_lines()


@dataclass
class JobCostChange:
    """What a price list does to one job's material cost."""

    job: Any
    delta: float
    #: Lines priced on both lists.
    lines: int
    #: Lines in the list whose unit could not be matched to the job's.
    unmatched_units: int = 0

    @property
    def share_of_quote(self) -> float:
        total = float(getattr(self.job, "quote_total", 0.0) or 0.0)
        return self.delta / total if total else 0.0

    def describe(self) -> str:
        sign = "יותר" if self.delta > 0 else "פחות"
        text = (
            f"⁦{self.job.job_id}⁩ {self.job.name}: החומר עולה "
            f"⁦{abs(self.delta):,.0f} ₪⁩ {sign}"
        )
        if getattr(self.job, "quote_total", 0):
            text += f" (⁦{self.share_of_quote * 100:+.1f}%⁩ מההצעה)"
        return text


def _quantity_in(unit: str, line: Any, mass_per_metre: Callable[[str], float | None]) -> float | None:
    """The line's quantity in the price list's unit, or None if it cannot be told."""
    bom_unit = getattr(getattr(line, "unit", ""), "value", str(getattr(line, "unit", "")))
    wanted = unit_key(unit)
    if not wanted or wanted == unit_key(bom_unit):
        return float(line.quantity)
    # Aluminium is sold by the kilogram and cut by the metre: the weight per
    # metre comes from the supplier's own catalogue, or the line is left out.
    if wanted == "kg" and unit_key(bom_unit) == "m":
        weight = mass_per_metre(line.code)
        if weight:
            return float(line.quantity) * weight
    return None


def cost_change(job: Any, old: PriceListEdition, new: PriceListEdition, *,
                materials: list[Any] | None = None,
                mass_per_metre: Callable[[str], float | None] = lambda _code: None,
                ) -> JobCostChange | None:
    """How much more (or less) the job's material costs on the new list."""
    materials = job_materials(job) if materials is None else materials
    delta = 0.0
    lines = unmatched = 0
    for line in materials:
        key = code_key(line.code)
        before, after = old.prices.get(key), new.prices.get(key)
        if before is None or after is None or abs(after.price - before.price) < 1e-9:
            continue
        if unit_key(before.unit) != unit_key(after.unit) and before.unit and after.unit:
            unmatched += 1
            continue
        quantity = _quantity_in(after.unit, line, mass_per_metre)
        if quantity is None:
            unmatched += 1
            continue
        delta += quantity * (after.price - before.price)
        lines += 1
    if not lines and not unmatched:
        return None
    return JobCostChange(job, round(delta, 2), lines, unmatched)


def catalogue_weights() -> Callable[[str], float | None]:
    """Weight per metre by article, from the catalogue editions the shop accepted."""
    try:
        from ..catalogue.editions import code_key as catalogue_key
        from ..catalogue.editions import default_editions

        book = default_editions()
        weights: dict[str, float] = {}
        for key in book.keys():
            for article, snapshot in book.library(key).items():
                value = snapshot.published.get("mass_per_metre") or snapshot.measured.get("mass_per_metre")
                if value:
                    weights[article] = float(value)
    except Exception as exc:  # noqa: BLE001 - without weights, kg lines are left out
        _log.info("Catalogue weights unavailable: %s", exc)
        return lambda _code: None
    return lambda code: weights.get(catalogue_key(code))


@dataclass
class ListImpact:
    """One price list against the shop's open work."""

    edition: PriceListEdition
    #: Quotations sent on the old prices, by how much their material moved.
    quoted: list[JobCostChange] = field(default_factory=list)
    #: Won jobs whose material is not bought yet.
    to_order: list[JobCostChange] = field(default_factory=list)

    @property
    def buy_before(self) -> str:
        """The day the new prices start, when that is still ahead."""
        return "" if self.edition.in_force() else self.edition.effective


def impact(book: PriceListBook, edition: PriceListEdition, jobs: Iterable[Any], *,
           mass_per_metre: Callable[[str], float | None] | None = None) -> ListImpact:
    """What this list does to quotations out and jobs not yet ordered."""
    result = ListImpact(edition)
    old = book.previous(edition)
    if old is None:
        return result
    weights = mass_per_metre or catalogue_weights()
    for job in jobs:
        status = getattr(getattr(job, "status", ""), "value", str(getattr(job, "status", "")))
        if status not in QUOTED_STATES + NOT_ORDERED_STATES:
            continue
        # A quotation made after this list was accepted was priced on it.
        quoted_on = str(getattr(job, "quoted_on", "") or "")
        if status in QUOTED_STATES and quoted_on and edition.decided_at \
                and quoted_on > edition.decided_at[:10]:
            continue
        change = cost_change(job, old, edition, mass_per_metre=weights)
        if change is None or abs(change.delta) < 1:
            continue
        (result.quoted if status in QUOTED_STATES else result.to_order).append(change)
    result.quoted.sort(key=lambda c: -c.delta)
    result.to_order.sort(key=lambda c: -c.delta)
    return result


__all__ = [
    "JobCostChange",
    "ListImpact",
    "ListState",
    "PriceChange",
    "PriceDiff",
    "PriceListBook",
    "PriceListEdition",
    "PriceRow",
    "apply_price_lists",
    "catalogue_weights",
    "code_key",
    "compare",
    "cost_change",
    "default_price_lists",
    "default_price_lists_path",
    "impact",
    "job_materials",
    "load_price_lists",
    "rows_from_file",
    "unit_key",
]
