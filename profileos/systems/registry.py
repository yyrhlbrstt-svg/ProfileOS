"""Looking a system up, and knowing what may be done with it.

Two things live here. The first is a search over the directory, because a
fabricator types "בלגי" or "7300" or "klil", not an internal id. The second,
and the more important one, is the readiness rule: a series may be *quoted*
long before it may be *cut*, and the software has to hold those apart.

Why family-typical rules exist at all
-------------------------------------
An estimator needs a price this afternoon. Refusing to model an opening until
the supplier's catalogue has been loaded would make the software useless for
the job it is most used for. So each family carries a set of dimensionally
sound, clearly labelled stand-in values: enough to size glass, weigh aluminium
and produce a quotation, and never enough to release to a saw. The moment a
catalogue is loaded the stand-ins are replaced and the restriction lifts.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Iterator

from ..core.logging_setup import get_logger
from ..elements.rules import (
    FrameRules,
    GasketRules,
    GlassRules,
    MullionRules,
    SashRules,
    SystemRules,
    register_system_rules,
)
from .israel import HARDWARE_MAKERS, MANUFACTURERS, SERIES
from .model import Manufacturer, Provenance, SystemEntry, SystemFamily, SystemReadiness

_log = get_logger("systems")


# --------------------------------------------------------------------------- #
# Family stand-ins
# --------------------------------------------------------------------------- #
def _rules(
    family: SystemFamily,
    *,
    frame_face: float,
    sash_overlap: float = 8.0,
    rebate_clearance: float = 2.0,
    bottom_clearance: float = 3.0,
    sash_face: float = 32.0,
    edge_cover: float = 15.0,
    edge_clearance: float = 3.0,
    max_glass: float = 44.0,
    mullion_face: float = 50.0,
    install_clearance: float = 10.0,
) -> SystemRules:
    return SystemRules(
        id=f"typical-{family.value}",
        name=f"Typical {family.value.replace('_', ' ')} system (stand-in figures)",
        supplier=None,
        frame=FrameRules(
            face_width=frame_face,
            mitred_corners=family
            not in {SystemFamily.SLIDING, SystemFamily.LIFT_SLIDE, SystemFamily.CURTAIN_WALL},
            installation_clearance=install_clearance,
        ),
        sash=SashRules(
            frame_overlap=sash_overlap,
            rebate_clearance=rebate_clearance,
            bottom_clearance=bottom_clearance,
            sash_face_width=sash_face,
        ),
        glass=GlassRules(
            edge_cover=edge_cover,
            edge_clearance=edge_clearance,
            max_glass_thickness=max_glass,
        ),
        gasket=GasketRules(),
        mullion=MullionRules(face_width=mullion_face),
        notes=(
            "Stand-in figures for this family. Dimensionally sound and fine for "
            "estimating; not any supplier's numbers, and not to be cut to."
        ),
    )


#: One stand-in rule set per family. A sliding system is not a casement system
#: with different numbers — the sash sits in a track rather than overlapping a
#: rebate — so the families differ in structure, not only in dimension.
FAMILY_RULES: dict[SystemFamily, SystemRules] = {
    SystemFamily.CASEMENT: _rules(SystemFamily.CASEMENT, frame_face=52.0),
    SystemFamily.TILT_TURN: _rules(
        SystemFamily.TILT_TURN, frame_face=64.0, sash_overlap=9.0, max_glass=48.0
    ),
    SystemFamily.SLIDING: _rules(
        SystemFamily.SLIDING,
        frame_face=40.0,
        sash_overlap=0.0,
        rebate_clearance=3.0,
        bottom_clearance=5.0,
        sash_face=40.0,
        edge_cover=12.0,
        max_glass=28.0,
    ),
    SystemFamily.LIFT_SLIDE: _rules(
        SystemFamily.LIFT_SLIDE,
        frame_face=60.0,
        sash_overlap=0.0,
        rebate_clearance=3.0,
        bottom_clearance=6.0,
        sash_face=54.0,
        edge_cover=17.0,
        max_glass=52.0,
    ),
    SystemFamily.FOLDING: _rules(SystemFamily.FOLDING, frame_face=56.0, max_glass=40.0),
    SystemFamily.DOOR: _rules(
        SystemFamily.DOOR, frame_face=58.0, bottom_clearance=8.0, sash_face=100.0
    ),
    SystemFamily.PARTITION: _rules(
        SystemFamily.PARTITION,
        frame_face=45.0,
        sash_overlap=6.0,
        edge_cover=12.0,
        max_glass=12.0,
    ),
    SystemFamily.CURTAIN_WALL: _rules(
        SystemFamily.CURTAIN_WALL,
        frame_face=50.0,
        sash_overlap=0.0,
        edge_cover=17.0,
        max_glass=52.0,
        mullion_face=50.0,
        install_clearance=20.0,
    ),
    SystemFamily.SKYLIGHT: _rules(
        SystemFamily.SKYLIGHT, frame_face=50.0, edge_cover=17.0, max_glass=52.0
    ),
    SystemFamily.SHADING: _rules(
        SystemFamily.SHADING, frame_face=40.0, edge_cover=0.0, max_glass=1.0
    ),
    SystemFamily.MESH: _rules(SystemFamily.MESH, frame_face=25.0, edge_cover=0.0, max_glass=1.0),
}


# --------------------------------------------------------------------------- #
# The directory
# --------------------------------------------------------------------------- #
class SystemDirectory:
    """Every series the shop can name, with what is known about each."""

    def __init__(
        self,
        entries: Iterable[SystemEntry] = SERIES,
        manufacturers: Iterable[Manufacturer] = MANUFACTURERS,
    ) -> None:
        self._entries: dict[str, SystemEntry] = {entry.id: entry for entry in entries}
        self._makers: dict[str, Manufacturer] = {m.id: m for m in manufacturers}

    # -- reading ------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[SystemEntry]:
        return iter(self._entries.values())

    def get(self, entry_id: str) -> SystemEntry | None:
        return self._entries.get(entry_id.lower())

    def manufacturer(self, maker_id: str) -> Manufacturer | None:
        return self._makers.get(maker_id.lower())

    def manufacturers(self) -> list[Manufacturer]:
        """Local suppliers first: stock in the country is a real difference."""
        return sorted(self._makers.values(), key=lambda m: (not m.local_stock, m.name))

    def by_manufacturer(self, maker_id: str) -> list[SystemEntry]:
        return [e for e in self._entries.values() if e.manufacturer == maker_id.lower()]

    def by_family(self, family: SystemFamily) -> list[SystemEntry]:
        return [e for e in self._entries.values() if e.family is family]

    def unclassified(self) -> list[SystemEntry]:
        """Series known by name but not yet placed in a family."""
        return [e for e in self._entries.values() if e.family is None]

    def search(self, text: str) -> list[SystemEntry]:
        """Find a series the way somebody types it: '7300', 'בלגי', 'klil'."""
        needle = text.strip().casefold()
        if not needle:
            return list(self._entries.values())
        hits: list[tuple[int, SystemEntry]] = []
        for entry in self._entries.values():
            terms = entry.search_terms()
            if any(term == needle for term in terms):
                hits.append((0, entry))
            elif any(needle in term for term in terms):
                hits.append((1, entry))
            elif needle in entry.display.casefold():
                hits.append((2, entry))
        hits.sort(key=lambda pair: (pair[0], pair[1].manufacturer, pair[1].series))
        return [entry for _, entry in hits]

    # -- rules -------------------------------------------------------------- #
    def provenance_for(self, entry_id: str) -> Provenance:
        """How far the figures actually in use for this series can be trusted.

        Distinct from ``entry.provenance``, which records what has been claimed
        about the series itself. A series with a family but no catalogue is
        working from stand-ins — typical, not unknown — and saying so plainly is
        the difference between a warning an estimator can act on and one they
        learn to ignore.
        """
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"No system named {entry_id!r}")
        if entry.provenance is Provenance.CONFIRMED and entry.rules_id:
            return Provenance.CONFIRMED
        if entry.family is None:
            return Provenance.UNKNOWN
        return Provenance.TYPICAL

    def rules_for(self, entry_id: str) -> tuple[SystemRules, Provenance]:
        """The rule set to use for a series, and how much it can be trusted."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"No system named {entry_id!r}")
        if entry.rules_id:
            from ..elements.rules import get_system_rules

            rules = get_system_rules(entry.rules_id)
            if rules.id == entry.rules_id:
                return rules, self.provenance_for(entry_id)
            _log.warning(
                "System %s points at rule set %s, which is not loaded; "
                "falling back to the family stand-in",
                entry.id,
                entry.rules_id,
            )
        if entry.family is None:
            raise UnclassifiedSystem(
                f"{entry.display} has not been placed in a family yet, so there "
                "is no rule set to use. Set its family, or load its catalogue."
            )
        return FAMILY_RULES[entry.family], Provenance.TYPICAL

    def readiness(self, entry_id: str) -> SystemReadiness:
        """What this series may currently be used for, and why not more."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"No system named {entry_id!r}")

        reasons: list[str] = []
        if entry.family is None:
            reasons.append(
                "The series is in the directory but has not been classified, so "
                "neither its hardware nor its deductions can be chosen."
            )
            return SystemReadiness(entry=entry, may_quote=False, may_cut=False, reasons=reasons)

        provenance = self.provenance_for(entry_id)
        if provenance.may_be_cut_to:
            return SystemReadiness(entry=entry, may_quote=True, may_cut=True)

        reasons.append(
            f"Working from {FAMILY_RULES[entry.family].name.lower()} — "
            f"{provenance.hebrew}. Load {self.manufacturer(entry.manufacturer) or entry.manufacturer}'s "
            f"catalogue for {entry.series} to cut from this series."
        )
        return SystemReadiness(entry=entry, may_quote=True, may_cut=False, reasons=reasons)

    # -- writing ------------------------------------------------------------ #
    def add(self, entry: SystemEntry) -> SystemEntry:
        self._entries[entry.id] = entry
        return entry

    def add_manufacturer(self, maker: Manufacturer) -> Manufacturer:
        self._makers[maker.id] = maker
        return maker

    def load_document(self, catalogue: Any) -> int:
        """Merge a ``kind: "system_catalogue"`` document into the directory."""
        return catalogue.merge_into(self)

    def classify(self, entry_id: str, family: SystemFamily, *, source: str) -> SystemEntry:
        """Place a named series in a family. Does not make it cuttable."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"No system named {entry_id!r}")
        # Classifying gives the series the family stand-ins, which is enough to
        # quote from and deliberately not enough to cut from.
        updated = replace(entry, family=family, source=source)
        self._entries[updated.id] = updated
        return updated

    def confirm(self, entry_id: str, rules: SystemRules, *, source: str) -> SystemEntry:
        """Attach the supplier's own figures, which is what unlocks cutting.

        ``source`` is required and stored: a confirmed number with no record of
        where it came from is not confirmed, it is just an assertion.
        """
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"No system named {entry_id!r}")
        if not source.strip():
            raise ValueError("Confirming a system needs a source to point at")
        register_system_rules(rules)
        updated = replace(
            entry,
            rules_id=rules.id,
            provenance=Provenance.CONFIRMED,
            source=source.strip(),
        )
        self._entries[updated.id] = updated
        _log.info("Confirmed %s from %s", updated.display, source)
        return updated

    # -- reporting ---------------------------------------------------------- #
    def coverage(self) -> dict[str, int]:
        """How much of the directory is actually usable, in one line."""
        counts = {level.value: 0 for level in Provenance}
        for entry in self._entries.values():
            counts[self.provenance_for(entry.id).value] += 1
        counts["unclassified"] = len(self.unclassified())
        counts["total"] = len(self._entries)
        return counts


class UnclassifiedSystem(KeyError):
    """The series is named but nothing is known about what kind it is."""


#: The directory the application uses. Replaceable in tests.
DIRECTORY = SystemDirectory()


def hardware_makers() -> tuple[Manufacturer, ...]:
    return HARDWARE_MAKERS


__all__ = [
    "DIRECTORY",
    "FAMILY_RULES",
    "SystemDirectory",
    "UnclassifiedSystem",
    "hardware_makers",
]
