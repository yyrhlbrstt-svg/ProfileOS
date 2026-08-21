"""The shop's hardware library, and choosing from it by load.

The library is a file the shop owns, the same as its systems. It starts with
the part *kinds* an aluminium window needs and no manufacturer's ratings,
because those are published by Roto, Giesse, Savio and Stublina and are theirs
to publish. Entering one supplier's load chart takes a few minutes and turns
every selection from a suggestion into an order.

Selection is deliberately strict. A part with no recorded rating never carries
a load, however obviously it would do in practice, and when nothing in the
library can carry a leaf the answer is that nothing can — not the largest
thing available.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .model import Confidence, Part, PartKind, Selection, sash_mass

_log = get_logger("hardware.library")


#: What each way of opening needs fitted to it. The quantities are the ones
#: the ironmongery is sold in; the parts themselves are chosen by load.
REQUIREMENTS: dict[str, tuple[tuple[PartKind, int], ...]] = {
    "casement": (
        (PartKind.HINGE, 2), (PartKind.HANDLE, 1), (PartKind.ESPAGNOLETTE, 1),
    ),
    "tilt_turn": (
        (PartKind.TILT_TURN_GEAR, 1), (PartKind.HANDLE, 1),
        (PartKind.CORNER_DRIVE, 2), (PartKind.STRIKE_PLATE, 2),
    ),
    "top_hung": (
        (PartKind.FRICTION_STAY, 2), (PartKind.HANDLE, 1),
        (PartKind.RESTRICTOR, 1),
    ),
    "bottom_hung": (
        (PartKind.FRICTION_STAY, 2), (PartKind.HANDLE, 1),
        (PartKind.RESTRICTOR, 1),
    ),
    "pivot": ((PartKind.HINGE, 2), (PartKind.HANDLE, 1)),
    "sliding": ((PartKind.ROLLER, 2), (PartKind.HANDLE, 1)),
    "lift_slide": (
        (PartKind.LIFT_SLIDE_GEAR, 1), (PartKind.HANDLE, 1),
    ),
    "door": (
        (PartKind.HINGE, 3), (PartKind.MULTIPOINT_LOCK, 1),
        (PartKind.CYLINDER, 1), (PartKind.HANDLE, 1),
    ),
    "fixed": (),
}


class HardwareLibrary:
    """Every part the shop can fit, and what each is rated for."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._parts: dict[str, Part] = {}
        if self.path is not None:
            self.load()

    # -- persistence --------------------------------------------------------- #
    def load(self) -> "HardwareLibrary":
        if self.path is None or not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file must not empty the shop
            _log.exception("Hardware library at %s unreadable", self.path)
            return self
        for entry in raw.get("parts", []):
            try:
                self.add(_part_from(entry), save=False)
            except Exception:  # noqa: BLE001 - one bad row, not the library
                _log.warning("Skipping unreadable part: %s", entry.get("code"))
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "parts": [
                {**asdict(part), "kind": part.kind.value,
                 "confidence": part.confidence.value,
                 "opening_types": list(part.opening_types)}
                for part in self._parts.values()
            ]
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    # -- contents ------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._parts)

    def __iter__(self):
        return iter(self._parts.values())

    def add(self, part: Part, *, save: bool = True) -> Part:
        if part.kind.carries_load and part.max_sash_kg and not part.source:
            raise ProfileOSError(
                f"{part.code}: חלק נושא עומס חייב מקור לדירוג — "
                "טבלת עומסים של היצרן"
            )
        self._parts[part.code] = part
        if save:
            self.save()
        return part

    def get(self, code: str) -> Part | None:
        return self._parts.get(code)

    def of_kind(self, kind: PartKind) -> list[Part]:
        return [part for part in self if part.kind is kind]

    def rated(self) -> list[Part]:
        """Parts whose figures came from a manufacturer."""
        return [part for part in self if part.confidence is Confidence.CATALOGUE]

    def search(self, text: str) -> list[Part]:
        needle = text.strip().casefold()
        if not needle:
            return list(self)
        return [
            part for part in self
            if needle in f"{part.code} {part.hebrew} {part.maker} "
                         f"{part.kind.value} {part.notes}".casefold()
        ]

    # -- choosing ------------------------------------------------------------ #
    def choose(
        self,
        kind: PartKind,
        *,
        width: float,
        height: float,
        mass: float,
        opening_type: str = "",
    ) -> Part | None:
        """The lightest part that can carry this leaf, or nothing.

        Lightest-that-fits rather than largest-available: over-specifying
        hardware costs the shop money on every unit, and the cheapest part
        that carries the load is the right one.
        """
        candidates = [
            part for part in self.of_kind(kind)
            if part.fits(width=width, height=height, mass=mass,
                         opening_type=opening_type)
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda part: (part.max_sash_kg or 1e9, part.price or 1e9, part.code),
        )

    def _why_not(
        self,
        kind: PartKind,
        *,
        width: float,
        height: float,
        mass: float,
        opening_type: str,
    ) -> str:
        """Say the real reason nothing fits, not the first one checked.

        "Too heavy" sends somebody to the load chart; if the true reason was
        the leaf width, they will read the chart, find a part that carries the
        weight, and not understand why the software still refuses it.
        """
        available = self.of_kind(kind)
        if not available:
            return f"{kind.hebrew}: אין בספרייה — הזינו את הקטלוג של הספק"

        for_type = [
            part for part in available
            if not part.opening_types or opening_type in part.opening_types
        ]
        if not for_type:
            return f"{kind.hebrew}: אין פריט המתאים לפתיחה מסוג זה"

        by_size = [
            part for part in for_type
            if part.fits(width=width, height=height, mass=0.0,
                         opening_type=opening_type)
            or not part.kind.carries_load
        ]
        fits_size = [
            part for part in for_type
            if (not part.max_width or width <= part.max_width)
            and (not part.min_width or width >= part.min_width)
            and (not part.max_height or height <= part.max_height)
            and (not part.min_height or height >= part.min_height)
        ]
        if not fits_size:
            widest = max((p.max_width for p in for_type if p.max_width), default=0.0)
            tallest = max((p.max_height for p in for_type if p.max_height), default=0.0)
            return (
                f"{kind.hebrew}: כנף ⁦{width:.0f}×{height:.0f}⁩ גדולה מכל פריט "
                f"בספרייה (עד ⁦{widest:.0f}×{tallest:.0f}⁩)"
            )

        if kind.carries_load:
            rated = [part for part in fits_size if part.max_sash_kg]
            if not rated:
                return (
                    f"{kind.hebrew}: יש פריט במידה הנכונה אבל בלי דירוג עומס — "
                    "הזינו את טבלת העומסים כדי שאפשר יהיה לבחור אותו"
                )
            best = max(part.max_sash_kg * part.per_sash for part in rated)
            return (
                f"{kind.hebrew}: כנף של ⁦{mass:.0f}⁩ ק״ג מעל הדירוג הגבוה "
                f"במידה הזאת (⁦{best:.0f}⁩ ק״ג)"
            )
        _ = by_size
        return f"{kind.hebrew}: אין פריט מתאים"

    def select_for(
        self,
        *,
        opening_type: str,
        width: float,
        height: float,
        mass: float = 0.0,
        glass_mass_per_m2: float = 25.0,
    ) -> Selection:
        """Everything one leaf needs, chosen against what it weighs."""
        requirements = REQUIREMENTS.get(opening_type)
        if requirements is None:
            raise ProfileOSError(f"אין דרישות פרזול לסוג פתיחה {opening_type}")

        leaf_mass = mass or sash_mass(width, height, glass_mass_per_m2)
        selection = Selection(sash_mass=round(leaf_mass, 1))

        for kind, quantity in requirements:
            part = self.choose(
                kind, width=width, height=height, mass=leaf_mass,
                opening_type=opening_type,
            )
            if part is None:
                selection.unmet.append(self._why_not(
                    kind, width=width, height=height, mass=leaf_mass,
                    opening_type=opening_type,
                ))
                continue

            selection.parts.append((part, quantity))
            if not part.confidence.may_be_fitted and kind.carries_load:
                selection.warnings.append(
                    f"{part.hebrew}: {part.confidence.hebrew} — "
                    "אמתו מול טבלת העומסים של היצרן לפני הזמנה"
                )

        if leaf_mass > 130 and opening_type in ("casement", "tilt_turn"):
            selection.warnings.append(
                f"כנף של ⁦{leaf_mass:.0f}⁩ ק״ג — מעל מה שרוב המנגנונים מדורגים לו. "
                "שקלו לפצל את הפתח"
            )
        return selection


def _part_from(entry: dict[str, Any]) -> Part:
    return Part(
        code=entry["code"],
        hebrew=entry.get("hebrew", entry["code"]),
        kind=PartKind(entry["kind"]),
        maker=entry.get("maker", ""),
        max_sash_kg=float(entry.get("max_sash_kg", 0.0)),
        min_width=float(entry.get("min_width", 0.0)),
        max_width=float(entry.get("max_width", 0.0)),
        min_height=float(entry.get("min_height", 0.0)),
        max_height=float(entry.get("max_height", 0.0)),
        opening_types=tuple(entry.get("opening_types", ())),
        per_sash=int(entry.get("per_sash", 1)),
        unit=entry.get("unit", "pc"),
        price=float(entry.get("price", 0.0)),
        currency=entry.get("currency", "ILS"),
        confidence=Confidence(entry.get("confidence", "unknown")),
        source=entry.get("source", ""),
        notes=entry.get("notes", ""),
    )


def template(maker: str = "") -> dict[str, Any]:
    """A blank hardware file to fill in from a supplier's load chart."""
    return {
        "_כיצד": (
            "לכל פריט: קוד, שם, סוג, ודירוג העומס מטבלת היצרן. "
            "פריט נושא עומס בלי ״source״ לא ייבחר — וזה בכוונה."
        ),
        "_סוגים": {kind.value: kind.hebrew for kind in PartKind},
        "parts": [
            {
                "code": f"{maker or 'MAKER'}-EXAMPLE-100",
                "hebrew": "ציר לדוגמה",
                "kind": "hinge",
                "maker": maker,
                "max_sash_kg": 100.0,
                "max_width": 1200.0,
                "max_height": 2400.0,
                "opening_types": ["casement"],
                "per_sash": 2,
                "price": 0.0,
                "confidence": "catalogue",
                "source": "טבלת עומסים של היצרן, מהדורה ושנה",
            }
        ],
    }


def default_library_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "hardware.json"


def default_library() -> HardwareLibrary:
    return HardwareLibrary(default_library_path())


__all__ = [
    "REQUIREMENTS",
    "HardwareLibrary",
    "default_library",
    "default_library_path",
    "template",
]
