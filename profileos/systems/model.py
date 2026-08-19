"""What a profile system is, and how much of it we actually know.

A fabricator picks a system by name — "קליל 7000", "Extal E50" — and everything
downstream follows from that choice: which bars get bought, what the cut
deductions are, which hardware fits, how the glass is sized. So the name has to
be a first-class thing in the software, not a string typed into a field.

The uncomfortable part is that the numbers behind those names belong to the
system suppliers. They are in the supplier's own catalogue, and they change.
Software that ships a table of deductions copied from somewhere is software
that will one day cut a whole job 4 mm short and be very confident about it.

This module therefore separates two things that other packages blur:

* **the name** — the manufacturer and series, which is public and stable;
* **the numbers** — which are only trustworthy if they came from a document
  the shop loaded, and which are labelled with where they came from.

:class:`Provenance` is that label, and it travels with the rules everywhere.
Nothing invented is ever used silently: a cut list produced from unconfirmed
figures says so on the sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Provenance(StrEnum):
    """Where a number came from, which decides whether it may be cut to."""

    #: Read from a catalogue or drawing the shop loaded, and traceable to it.
    CONFIRMED = "confirmed"
    #: A representative value for this kind of system. Sound engineering
    #: practice, not this supplier's figure. Fine for a quotation, not for a saw.
    TYPICAL = "typical"
    #: Nothing is known. The system can be named and priced by weight, but no
    #: part may be cut from it.
    UNKNOWN = "unknown"

    @property
    def may_be_cut_to(self) -> bool:
        return self is Provenance.CONFIRMED

    @property
    def hebrew(self) -> str:
        return {
            Provenance.CONFIRMED: "מאושר מקטלוג היצרן",
            Provenance.TYPICAL: "ערך אופייני — לא מהיצרן",
            Provenance.UNKNOWN: "חסר — יש לטעון קטלוג",
        }[self]


class SystemFamily(StrEnum):
    """What kind of system it is, which sets the physics and the hardware."""

    CASEMENT = "casement"
    TILT_TURN = "tilt_turn"
    SLIDING = "sliding"
    LIFT_SLIDE = "lift_slide"
    FOLDING = "folding"
    DOOR = "door"
    PARTITION = "partition"
    CURTAIN_WALL = "curtain_wall"
    SKYLIGHT = "skylight"
    SHADING = "shading"
    MESH = "mesh"

    @property
    def hebrew(self) -> str:
        return {
            SystemFamily.CASEMENT: "פתיחה",
            SystemFamily.TILT_TURN: "נטוי-פתוח (דריי-קיפ)",
            SystemFamily.SLIDING: "הזזה",
            SystemFamily.LIFT_SLIDE: "הזזה מורמת",
            SystemFamily.FOLDING: "אקורדיון",
            SystemFamily.DOOR: "דלת",
            SystemFamily.PARTITION: "מחיצות משרד",
            SystemFamily.CURTAIN_WALL: "קיר מסך",
            SystemFamily.SKYLIGHT: "סקיילייט",
            SystemFamily.SHADING: "הצללה ורפפות",
            SystemFamily.MESH: "רשתות",
        }[self]

    @property
    def has_opening_sash(self) -> bool:
        """Whether a sash moves, which is what needs hardware and a weight check."""
        return self in {
            SystemFamily.CASEMENT,
            SystemFamily.TILT_TURN,
            SystemFamily.SLIDING,
            SystemFamily.LIFT_SLIDE,
            SystemFamily.FOLDING,
            SystemFamily.DOOR,
        }


@dataclass(frozen=True)
class Manufacturer:
    """A system house whose profiles are sold in Israel."""

    id: str
    name: str
    hebrew: str
    #: Where the company is based. Local suppliers hold stock in the country,
    #: which is the difference between a two-day and a six-week lead time.
    country: str
    local_stock: bool
    website: str | None = None

    def __str__(self) -> str:
        return self.hebrew or self.name


@dataclass(frozen=True)
class SystemEntry:
    """One series in one manufacturer's range.

    The identity — manufacturer, series, family — is what the shop chooses by.
    ``rules_id`` points at the rule set that turns an opening size into cut
    lengths, and ``provenance`` says whether that rule set is the supplier's own
    figures or a stand-in.
    """

    manufacturer: str
    series: str
    hebrew: str = ""
    #: ``None`` where the series is known by name but nobody has yet said what
    #: kind of system it is. That is a real state: a directory of series is
    #: useful before every one of them has been worked through.
    family: SystemFamily | None = None
    thermally_broken: bool = False
    #: Frame depth as catalogued [mm], when it is known.
    depth: float | None = None
    #: The rule set used for cut deductions; falls back to the family default.
    rules_id: str | None = None
    provenance: Provenance = Provenance.UNKNOWN
    #: What the provenance is based on: a file name, a catalogue edition, or
    #: the reason a figure is only typical.
    source: str | None = None
    notes: str = ""
    #: Alternative spellings people search by, Hebrew and Latin.
    aliases: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.manufacturer}-{self.series}".lower().replace(" ", "-")

    @property
    def display(self) -> str:
        return f"{self.hebrew} {self.series}".strip()

    def search_terms(self) -> tuple[str, ...]:
        terms = [
            self.series.casefold(),
            self.hebrew,
            self.manufacturer.casefold(),
            *(alias.casefold() for alias in self.aliases),
        ]
        if self.family is not None:
            terms.extend([self.family.value, self.family.hebrew])
        return tuple(term for term in terms if term)


@dataclass
class SystemReadiness:
    """Whether a system may be used, and for what.

    Three answers rather than two, because "can I quote it" and "can I cut it"
    are different questions with different consequences for getting it wrong.
    """

    entry: SystemEntry
    may_quote: bool
    may_cut: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def banner(self) -> str | None:
        """The line printed across a cut list produced from unconfirmed data."""
        if self.may_cut:
            return None
        return (
            "לא לייצור — נתוני היצרן לסדרה זו לא נטענו. "
            "NOT FOR PRODUCTION — this series' figures have not been loaded "
            "from the supplier's catalogue."
        )


__all__ = [
    "Manufacturer",
    "Provenance",
    "SystemEntry",
    "SystemFamily",
    "SystemReadiness",
]
