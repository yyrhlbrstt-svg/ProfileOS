"""The shop's own price book: every kind of work it charges for, at its price.

A quotation for windows is computed from sections and glass. Everything around
it is not: taking out the old windows, the crane, sealing, a boiler swapped,
a blockage cleared, a metre of drain laid. Those were typed by hand each time,
or forgotten — and a forgotten line is the shop's money given away.

The book starts with the *kinds* of work a small aluminium and plumbing shop
does, and no prices: the prices are the shop's own, and a figure this
software made up would be quoted to a customer one day. An item with no price
says so wherever it is used, until somebody enters it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.storage import MergingBook

_log = get_logger("quoting.pricebook")


@dataclass
class PriceItem:
    """One kind of work, at the shop's price for it."""

    code: str
    name: str
    unit: str = "יח׳"
    #: Net of VAT, in shekels. Zero means "not entered yet", never "free".
    price: float = 0.0
    #: "aluminium", "plumbing" or "both".
    trade: str = "both"
    category: str = ""
    #: Where the price came from: "מחירון 2026", "לפי עבודה ב-J-2026-0012".
    source: str = ""
    updated: str = ""
    #: Fixture id on the plumbing page this item installs, if any.
    fixture: str = ""

    @property
    def priced(self) -> bool:
        return self.price > 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PriceItem":
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in known}
        if not values.get("code") or not values.get("name"):
            raise ProfileOSError("פריט במחירון חייב קוד ושם")
        values["price"] = float(values.get("price", 0.0) or 0.0)
        return cls(**values)


#: The kinds of work, without prices. Names are the trade's own words.
STARTER_ITEMS: tuple[tuple[str, str, str, str, str, str], ...] = (
    # code, name, unit, trade, category, fixture
    ("AL-REMOVE", "פירוק חלון / דלת קיימים", "יח׳", "aluminium", "התקנה", ""),
    ("AL-DISPOSE", "פינוי פסולת אלומיניום וזכוכית", "קומפלט", "aluminium", "התקנה", ""),
    ("AL-CRANE", "מנוף להרמת חלונות", "שעה", "aluminium", "הובלה", ""),
    ("AL-DELIVERY", "הובלה לאתר", "קומפלט", "both", "הובלה", ""),
    ("AL-SEAL", "איטום סיליקון היקפי", "מ״א", "aluminium", "התקנה", ""),
    ("AL-SUBFRAME", "משקוף עיוור", "יח׳", "aluminium", "התקנה", ""),
    ("AL-SCREEN", "רשת יתושים", "יח׳", "aluminium", "תוספות", ""),
    ("AL-SHUTTER", "תריס גלילה חשמלי", "יח׳", "aluminium", "תוספות", ""),
    ("AL-MOTOR", "מנוע לתריס", "יח׳", "aluminium", "תוספות", ""),
    ("AL-GLASS-REPLACE", "החלפת זכוכית", "מ״ר", "aluminium", "שירות", ""),
    ("AL-SERVICE", "ביקור שירות / כיוון כנפיים", "ביקור", "aluminium", "שירות", ""),
    ("PL-VISIT", "ביקור טכנאי אינסטלציה", "ביקור", "plumbing", "שירות", ""),
    ("PL-BLOCKAGE", "פתיחת סתימה", "יח׳", "plumbing", "שירות", ""),
    ("PL-LEAK", "איתור נזילה", "יח׳", "plumbing", "שירות", ""),
    ("PL-FAUCET", "החלפת ברז", "יח׳", "plumbing", "שירות", ""),
    ("PL-SIPHON", "החלפת סיפון", "יח׳", "plumbing", "שירות", ""),
    ("PL-BOILER", "החלפת דוד מים חמים", "יח׳", "plumbing", "מים חמים", ""),
    ("PL-ELEMENT", "החלפת גוף חימום בדוד", "יח׳", "plumbing", "מים חמים", ""),
    ("PL-WC", "התקנת אסלה", "יח׳", "plumbing", "כלים סניטריים", "wc-tank"),
    ("PL-WC-HUNG", "התקנת אסלה תלויה כולל מיכל סמוי", "יח׳", "plumbing",
     "כלים סניטריים", ""),
    ("PL-BASIN", "התקנת כיור רחצה", "יח׳", "plumbing", "כלים סניטריים", "basin"),
    ("PL-SINK", "התקנת כיור מטבח", "יח׳", "plumbing", "כלים סניטריים", "sink"),
    ("PL-SHOWER", "התקנת מקלחת", "יח׳", "plumbing", "כלים סניטריים", "shower"),
    ("PL-BATH", "התקנת אמבטיה", "יח׳", "plumbing", "כלים סניטריים", "bath"),
    ("PL-BIDET", "התקנת בידה", "יח׳", "plumbing", "כלים סניטריים", "bidet"),
    ("PL-WASHER", "חיבור מכונת כביסה", "יח׳", "plumbing", "כלים סניטריים",
     "washing-machine"),
    ("PL-DISHWASHER", "חיבור מדיח כלים", "יח׳", "plumbing", "כלים סניטריים", "dishwasher"),
    ("PL-FLOOR-DRAIN", "התקנת מחסום רצפה", "יח׳", "plumbing", "ניקוז", "floor-drain"),
    ("PL-HOSE", "התקנת ברז גינה", "יח׳", "plumbing", "צנרת", "hose-bib"),
    ("PL-SUPPLY-M", "צנרת מים — הנחה", "מ״א", "plumbing", "צנרת", ""),
    ("PL-DRAIN-M", "צנרת ניקוז — הנחה", "מ״א", "plumbing", "צנרת", ""),
    ("PL-MANIFOLD", "התקנת מחלק (מניפולד)", "יח׳", "plumbing", "צנרת", ""),
    ("PL-PRESSURE", "בדיקת לחץ למערכת", "קומפלט", "plumbing", "צנרת", ""),
    ("LAB-HOUR", "שעת עבודה", "שעה", "both", "עבודה", ""),
)


def starter_items() -> list[PriceItem]:
    return [
        PriceItem(code=code, name=name, unit=unit, trade=trade, category=category,
                  fixture=fixture)
        for code, name, unit, trade, category, fixture in STARTER_ITEMS
    ]


class PriceBook(MergingBook):
    """The shop's price book, kept on disk and merged on save."""

    _list_key = "items"
    _id_field = "code"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._items: dict[str, PriceItem] = {}

    def _records(self) -> dict[str, Any]:
        return self._items

    def _to_raw(self, record: Any) -> dict[str, Any]:
        return record.as_dict()

    def _from_raw(self, raw: dict[str, Any]) -> Any:
        return PriceItem.from_dict(raw)

    # -- on disk ---------------------------------------------------------------- #
    def load(self) -> "PriceBook":
        from ..core.storage import stored

        if self.path is None:
            return self
        if not stored(self.path):
            # A new installation starts with the kinds of work, unpriced.
            for item in starter_items():
                self._items[item.code] = item
            self._remember_loaded()
            self._loaded = {}  # all of them are new, so the first save writes them
            return self
        raw = self._read_or_mark(self.path)
        for entry in raw.get("items", []):
            try:
                item = PriceItem.from_dict(entry)
            except Exception as exc:  # noqa: BLE001 - kept on disk by the merge
                _log.warning("Skipped an unreadable price item: %s", exc)
                continue
            self._items[item.code] = item
        self._remember_loaded()
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save_merged(self.path)

    # -- contents --------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(sorted(self._items.values(), key=lambda item: (item.category, item.name)))

    def get(self, code: str) -> PriceItem | None:
        return self._items.get(code)

    def for_trade(self, trade: str) -> list[PriceItem]:
        return [item for item in self if item.trade in (trade, "both") or trade == "both"]

    def search(self, text: str, trade: str = "") -> list[PriceItem]:
        needle = text.strip().casefold()
        items = self.for_trade(trade) if trade else list(self)
        if not needle:
            return items
        return [
            item for item in items
            if needle in f"{item.code} {item.name} {item.category}".casefold()
        ]

    def for_fixture(self, fixture_id: str) -> PriceItem | None:
        """The installation item for a fixture on the plumbing page."""
        matches = [item for item in self if item.fixture == fixture_id]
        return matches[0] if matches else None

    def unpriced(self) -> list[PriceItem]:
        return [item for item in self if not item.priced]

    # -- changes -------------------------------------------------------------- #
    def set_price(self, code: str, price: float, *, source: str = "") -> PriceItem:
        item = self._items.get(code)
        if item is None:
            raise ProfileOSError(f"אין במחירון פריט {code}")
        if price < 0:
            raise ProfileOSError("מחיר לא יכול להיות שלילי")
        item.price = round(float(price), 2)
        item.source = source.strip() or item.source or f"עודכן {date.today():%d/%m/%Y}"
        item.updated = date.today().isoformat()
        self.save()
        return item

    def add(self, item: PriceItem) -> PriceItem:
        if not item.code.strip() or not item.name.strip():
            raise ProfileOSError("פריט במחירון חייב קוד ושם")
        if item.code in self._items:
            raise ProfileOSError(f"כבר יש במחירון פריט בקוד {item.code}")
        item.updated = item.updated or date.today().isoformat()
        self._items[item.code] = item
        self.save()
        return item

    def remove(self, code: str) -> None:
        if code in self._items:
            del self._items[code]
            self.save()

    def next_code(self, trade: str) -> str:
        prefix = {"aluminium": "AL", "plumbing": "PL"}.get(trade, "GEN")
        serial = 1
        while f"{prefix}-{serial:03d}" in self._items:
            serial += 1
        return f"{prefix}-{serial:03d}"


def default_pricebook_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "pricebook.json"


def default_pricebook() -> PriceBook:
    return PriceBook(default_pricebook_path()).load()


def lines_from(items: Iterable[tuple[PriceItem, float]]) -> list[dict[str, Any]]:
    """Price-book items and quantities as quotation lines."""
    return [
        {
            "description": item.name, "quantity": quantity, "unit": item.unit,
            "unit_price": item.price, "item_code": item.code,
        }
        for item, quantity in items
    ]


__all__ = [
    "PriceBook",
    "PriceItem",
    "STARTER_ITEMS",
    "default_pricebook",
    "default_pricebook_path",
    "lines_from",
    "starter_items",
]
