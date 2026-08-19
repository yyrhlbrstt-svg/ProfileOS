"""Telling somebody an opening cannot be made, while they are still drawing it.

Every one of these checks exists because the alternative is finding out later,
and later is always more expensive: a sash too heavy for its hinges is found by
the fitter, a pane bigger than the float line is found by the glass supplier
three days after the order, a handle 40 mm off the bottom rail is found by the
assembler with the gear already in his hand.

Severity, and why a guess never blocks
--------------------------------------
A check compares a measurement against a limit, and the limit is only as good
as where it came from — the same problem as cut deductions, handled the same
way. So severity is decided by two things:

* **what kind of limit it is.** A pane wider than any float line makes cannot
  exist; that is physics and it blocks whatever the paperwork says. A pane
  heavier than the crew can lift depends on the crew, the suction cups and the
  site; that is equipment.
* **where the limit came from.** An equipment limit blocks only when it is the
  shop's own confirmed figure. A family-typical stand-in warns instead, because
  stopping production on a number nobody has confirmed teaches people to
  override the warnings, and then the real ones get overridden too.

A regulation is its own case: safety glass where the law requires it is not a
matter of anybody's catalogue, and it blocks.

What is deliberately not checked
--------------------------------
Nothing here invents a hinge's load rating. The shipped hardware limits are
class ranges — what friction hinges, tilt-turn gear and lift-slide gear are
commonly rated to — and they warn. Enter the rating off the hardware supplier's
own sheet and the same check blocks instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Callable, Iterable

from ..core.logging_setup import get_logger
from ..systems.model import Provenance
from .builder import ElementBuild, GlassPanel, MemberCut
from .model import Cell, OpeningType

_log = get_logger("elements.feasibility")


class Severity(IntEnum):
    """Ordered so the worst finding sorts first."""

    BLOCKER = 3
    WARNING = 2
    NOTE = 1

    @property
    def hebrew(self) -> str:
        return {
            Severity.BLOCKER: "לא ניתן לייצור",
            Severity.WARNING: "אזהרה",
            Severity.NOTE: "לתשומת לב",
        }[self]


class LimitKind(StrEnum):
    """What sort of thing the limit is, which decides whether it can block."""

    #: The thing cannot exist. Blocks regardless of provenance.
    PHYSICAL = "physical"
    #: A machine, a product or a crew. Blocks only on a confirmed figure.
    EQUIPMENT = "equipment"
    #: The law. Blocks.
    REGULATION = "regulation"


@dataclass(frozen=True)
class Limit:
    """One number a design is measured against, and where it came from."""

    code: str
    value: float
    unit: str
    kind: LimitKind
    provenance: Provenance
    hebrew: str
    english: str
    source: str = ""

    def severity(self) -> Severity:
        """How hard this limit can push back when it is exceeded."""
        if self.kind is LimitKind.PHYSICAL or self.kind is LimitKind.REGULATION:
            return Severity.BLOCKER
        return Severity.BLOCKER if self.provenance.may_be_cut_to else Severity.WARNING

    def confirm(self, value: float, source: str) -> "Limit":
        """The shop's own figure, which is what lets this limit block."""
        if not source.strip():
            raise ValueError("A confirmed limit needs a source to point at")
        return Limit(
            code=self.code,
            value=value,
            unit=self.unit,
            kind=self.kind,
            provenance=Provenance.CONFIRMED,
            hebrew=self.hebrew,
            english=self.english,
            source=source.strip(),
        )


@dataclass(frozen=True)
class Finding:
    """One thing wrong, said in a way somebody can act on."""

    severity: Severity
    code: str
    hebrew: str
    english: str
    #: What it is about: an element, a cell, a pane.
    subject: str
    measured: float | None = None
    limit: Limit | None = None

    @property
    def blocks(self) -> bool:
        return self.severity is Severity.BLOCKER

    def describe(self) -> str:
        text = f"[{self.severity.name}] {self.subject}: {self.english}"
        if self.measured is not None and self.limit is not None:
            text += (
                f" ({self.measured:.1f} against {self.limit.value:.1f} "
                f"{self.limit.unit}, {self.limit.provenance.value})"
            )
        return text


@dataclass
class FeasibilityReport:
    """Everything wrong with one element, worst first."""

    element_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def can_be_made(self) -> bool:
        return not any(finding.blocks for finding in self.findings)

    @property
    def blockers(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.blocks]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    def sorted(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (-f.severity, f.subject, f.code))

    def summary(self) -> str:
        if self.can_be_made and not self.findings:
            return "ניתן לייצור. Nothing found."
        counts = {
            severity: sum(1 for f in self.findings if f.severity is severity)
            for severity in Severity
        }
        return (
            f"{counts[Severity.BLOCKER]} blocking, "
            f"{counts[Severity.WARNING]} warning, "
            f"{counts[Severity.NOTE]} note"
        )


# --------------------------------------------------------------------------- #
# The limits
# --------------------------------------------------------------------------- #
def _limit(
    code: str,
    value: float,
    unit: str,
    kind: LimitKind,
    hebrew: str,
    english: str,
    *,
    provenance: Provenance = Provenance.TYPICAL,
    source: str = "",
) -> Limit:
    return Limit(
        code=code,
        value=value,
        unit=unit,
        kind=kind,
        provenance=provenance,
        hebrew=hebrew,
        english=english,
        source=source,
    )


@dataclass
class FabricationLimits:
    """What this shop, its suppliers and its hardware can actually do.

    Everything defaults to a stand-in that warns. Replace any of them with
    :meth:`Limit.confirm` and that check starts blocking instead.
    """

    #: The largest float sheet produced, before any cutting.
    glass_max_width: Limit = field(
        default_factory=lambda: _limit(
            "glass.sheet_width", 6000.0, "mm", LimitKind.PHYSICAL,
            "רוחב מרבי של לוח זכוכית גלם", "widest float sheet produced",
            source="jumbo float sheet, 6000 x 3210 mm",
        )
    )
    glass_max_height: Limit = field(
        default_factory=lambda: _limit(
            "glass.sheet_height", 3210.0, "mm", LimitKind.PHYSICAL,
            "גובה מרבי של לוח זכוכית גלם", "tallest float sheet produced",
            source="jumbo float sheet, 6000 x 3210 mm",
        )
    )
    #: Below this a pane cannot be toughened without the tongs marking it.
    glass_min_dimension: Limit = field(
        default_factory=lambda: _limit(
            "glass.min_dimension", 250.0, "mm", LimitKind.EQUIPMENT,
            "מידה מזערית לחיסום", "smallest side a toughening oven will take",
        )
    )
    #: A very long thin pane bows in the oven.
    glass_max_aspect: Limit = field(
        default_factory=lambda: _limit(
            "glass.aspect", 10.0, ":1", LimitKind.EQUIPMENT,
            "יחס צלעות מרבי לחיסום", "longest side to shortest, for toughening",
        )
    )
    #: What the glazing crew can lift between them, with cups.
    glass_max_mass: Limit = field(
        default_factory=lambda: _limit(
            "glass.mass", 60.0, "kg", LimitKind.EQUIPMENT,
            "משקל שמשה מרבי להרמה ידנית", "pane mass two people can set by hand",
        )
    )
    #: Sash mass by hardware class. These are what the classes are commonly
    #: rated to, not any product's figure — which is why they warn.
    sash_max_mass: dict[str, Limit] = field(
        default_factory=lambda: {
            OpeningType.CASEMENT.value: _limit(
                "sash.mass.casement", 80.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי לצירי חיכוך", "friction hinge class rating",
            ),
            OpeningType.TOP_HUNG.value: _limit(
                "sash.mass.top_hung", 60.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי לפתיחה עליונה", "top-hung hinge class rating",
            ),
            OpeningType.BOTTOM_HUNG.value: _limit(
                "sash.mass.bottom_hung", 60.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי לפתיחה תחתונה", "bottom-hung hinge class rating",
            ),
            OpeningType.TILT_TURN.value: _limit(
                "sash.mass.tilt_turn", 130.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי לדריי-קיפ", "tilt-and-turn gear class rating",
            ),
            OpeningType.SLIDING.value: _limit(
                "sash.mass.sliding", 100.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי להזזה", "sliding roller class rating",
            ),
            OpeningType.LIFT_SLIDE.value: _limit(
                "sash.mass.lift_slide", 300.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי להזזה מורמת", "lift-slide gear class rating",
            ),
            OpeningType.DOOR.value: _limit(
                "sash.mass.door", 120.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף דלת מרבי", "door hinge class rating",
            ),
            OpeningType.PIVOT.value: _limit(
                "sash.mass.pivot", 150.0, "kg", LimitKind.EQUIPMENT,
                "משקל כנף מרבי לציר מרכזי", "pivot hardware class rating",
            ),
        }
    )
    #: A tall narrow casement racks on its hinges; a wide short one drops.
    sash_max_width: Limit = field(
        default_factory=lambda: _limit(
            "sash.width", 1200.0, "mm", LimitKind.EQUIPMENT,
            "רוחב כנף מרבי", "widest sash the gear is made for",
        )
    )
    sash_max_height: Limit = field(
        default_factory=lambda: _limit(
            "sash.height", 2400.0, "mm", LimitKind.EQUIPMENT,
            "גובה כנף מרבי", "tallest sash the gear is made for",
        )
    )
    #: Clear space needed above and below the handle for the corner drive.
    handle_end_clearance: Limit = field(
        default_factory=lambda: _limit(
            "handle.end_clearance", 120.0, "mm", LimitKind.EQUIPMENT,
            "מרווח נדרש מקצה הכנף לידית", "clear length the corner drive needs",
        )
    )
    #: Reach, measured from the finished floor.
    handle_max_reach: Limit = field(
        default_factory=lambda: _limit(
            "handle.reach", 1900.0, "mm", LimitKind.EQUIPMENT,
            "גובה ידית מרבי מהרצפה", "highest a handle can be reached",
        )
    )
    handle_min_reach: Limit = field(
        default_factory=lambda: _limit(
            "handle.low", 800.0, "mm", LimitKind.EQUIPMENT,
            "גובה ידית מזערי מהרצפה", "lowest a handle is comfortable",
        )
    )

    def all_limits(self) -> list[Limit]:
        limits = [
            self.glass_max_width, self.glass_max_height, self.glass_min_dimension,
            self.glass_max_aspect, self.glass_max_mass, self.sash_max_width,
            self.sash_max_height, self.handle_end_clearance, self.handle_max_reach,
            self.handle_min_reach,
        ]
        limits.extend(self.sash_max_mass.values())
        return limits

    def confirmed_count(self) -> int:
        return sum(1 for limit in self.all_limits() if limit.provenance.may_be_cut_to)


#: The stand-in limits, used when the shop has entered none of its own.
DEFAULT_LIMITS = FabricationLimits()


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #
MassLookup = Callable[[str], float | None]


def _sash_outer_size(build: ElementBuild, cell_key: tuple[int, int]) -> tuple[float, float] | None:
    """Recover a sash's outer width and height from its own cut list.

    The members are mitred, so each one's length *is* the outer dimension. This
    reads the answer off the parts that will actually be cut rather than
    recomputing it from the rules, which means the check and the saw cannot
    disagree.
    """
    width = height = None
    for cut in build.cuts:
        if cut.cell_key != cell_key:
            continue
        if cut.role == "sash_horizontal":
            width = cut.length
        elif cut.role == "sash_vertical":
            height = cut.length
    if width is None or height is None:
        return None
    return width, height


def _member_mass(cuts: Iterable[MemberCut], lookup: MassLookup | None) -> tuple[float, bool]:
    """Mass of a set of members [kg], and whether every one of them was known.

    When a profile's linear mass is not in the library the total is a *lower*
    bound, and the caller is told so rather than being given a confident number
    that is too small — which on a weight check is the dangerous direction.
    """
    total = 0.0
    complete = True
    for cut in cuts:
        per_metre = lookup(cut.profile_id) if lookup else None
        if per_metre is None:
            complete = False
            continue
        total += per_metre * (cut.length / 1000.0) * cut.quantity
    return total, complete


def _default_mass_lookup(profile_id: str) -> float | None:
    """Linear mass from the profile library, when the profile is in it."""
    from ..core.registry import PROFILE_SYSTEMS

    profile = PROFILE_SYSTEMS.get_or_none(profile_id)
    for attribute in ("mass_per_metre_declared", "mass_per_metre"):
        value = getattr(profile, attribute, None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def check_glass(
    panel: GlassPanel, limits: FabricationLimits, subject: str
) -> list[Finding]:
    """Everything that can be wrong with one pane."""
    findings: list[Finding] = []
    long_side = max(panel.width, panel.height)
    short_side = min(panel.width, panel.height)

    # A pane is cut from a sheet, so either orientation is allowed — it only
    # fails if it does not fit the sheet even turned.
    sheet_long = max(limits.glass_max_width.value, limits.glass_max_height.value)
    sheet_short = min(limits.glass_max_width.value, limits.glass_max_height.value)
    if long_side > sheet_long or short_side > sheet_short:
        limit = limits.glass_max_width
        findings.append(
            Finding(
                severity=limit.severity(),
                code="glass.oversize",
                hebrew=(
                    f"השמשה {panel.width:.0f}×{panel.height:.0f} מ\"מ גדולה מלוח "
                    f"הגלם הגדול ביותר ({sheet_long:.0f}×{sheet_short:.0f})."
                ),
                english=(
                    f"pane {panel.width:.0f}x{panel.height:.0f} mm does not fit the "
                    f"largest sheet made ({sheet_long:.0f}x{sheet_short:.0f} mm)"
                ),
                subject=subject,
                measured=long_side,
                limit=limit,
            )
        )

    if short_side < limits.glass_min_dimension.value and panel.build_up.is_safety_glass:
        findings.append(
            Finding(
                severity=limits.glass_min_dimension.severity(),
                code="glass.too_small_to_toughen",
                hebrew=f"צלע {short_side:.0f} מ\"מ קטנה מהמידה המזערית לחיסום.",
                english=f"a {short_side:.0f} mm side is below the toughening minimum",
                subject=subject,
                measured=short_side,
                limit=limits.glass_min_dimension,
            )
        )

    if short_side > 0:
        aspect = long_side / short_side
        if aspect > limits.glass_max_aspect.value and panel.build_up.is_safety_glass:
            findings.append(
                Finding(
                    severity=limits.glass_max_aspect.severity(),
                    code="glass.aspect",
                    hebrew=f"יחס צלעות {aspect:.1f}:1 — השמשה עלולה להתעוות בחיסום.",
                    english=f"aspect ratio {aspect:.1f}:1 will bow in the oven",
                    subject=subject,
                    measured=aspect,
                    limit=limits.glass_max_aspect,
                )
            )

    if panel.mass > limits.glass_max_mass.value:
        findings.append(
            Finding(
                severity=limits.glass_max_mass.severity(),
                code="glass.mass",
                hebrew=(
                    f"משקל השמשה {panel.mass:.0f} ק\"ג — מעל מה שניתן להרים ידנית; "
                    "נדרש ציוד הרמה."
                ),
                english=(
                    f"pane weighs {panel.mass:.0f} kg, past a manual set; "
                    "lifting equipment needed"
                ),
                subject=subject,
                measured=panel.mass,
                limit=limits.glass_max_mass,
            )
        )

    if not panel.compliant:
        findings.append(
            Finding(
                severity=Severity.BLOCKER,
                code="glass.safety_required",
                hebrew=f"נדרשת זכוכית בטיחותית ({panel.safety_reason}) והמפרט אינו כזה.",
                english=(
                    f"safety glass is required here ({panel.safety_reason}) and the "
                    "specified build-up is not safety glass"
                ),
                subject=subject,
            )
        )
    return findings


def check_sash(
    cell: Cell,
    size: tuple[float, float],
    mass: float,
    mass_is_complete: bool,
    limits: FabricationLimits,
    subject: str,
    *,
    handle_absolute_height: float | None = None,
) -> list[Finding]:
    """Everything that can be wrong with one opening leaf."""
    findings: list[Finding] = []
    if cell.sash is None:
        return findings
    width, height = size
    opening_type = cell.sash.opening_type

    limit = limits.sash_max_mass.get(opening_type.value)
    if limit is not None and mass > limit.value:
        qualifier = "" if mass_is_complete else " (aluminium not fully costed, so this is a floor)"
        findings.append(
            Finding(
                severity=limit.severity(),
                code="sash.mass",
                hebrew=(
                    f"משקל הכנף {mass:.0f} ק\"ג מעל {limit.value:.0f} ק\"ג "
                    f"שהפרזול מסוג זה נושא."
                ),
                english=(
                    f"sash weighs {mass:.0f} kg against {limit.value:.0f} kg for this "
                    f"class of hardware{qualifier}"
                ),
                subject=subject,
                measured=mass,
                limit=limit,
            )
        )
    elif limit is not None and not mass_is_complete and mass > 0.6 * limit.value:
        findings.append(
            Finding(
                severity=Severity.NOTE,
                code="sash.mass_incomplete",
                hebrew=(
                    f"משקל הכנף לפחות {mass:.0f} ק\"ג — משקל הפרופילים חסר בספרייה, "
                    "והמספר האמיתי גבוה יותר."
                ),
                english=(
                    f"sash is at least {mass:.0f} kg; some profile masses are missing "
                    "from the library so the real figure is higher"
                ),
                subject=subject,
                measured=mass,
                limit=limit,
            )
        )

    if width > limits.sash_max_width.value:
        findings.append(
            Finding(
                severity=limits.sash_max_width.severity(),
                code="sash.width",
                hebrew=f"רוחב כנף {width:.0f} מ\"מ מעל המרבי לפרזול.",
                english=f"sash is {width:.0f} mm wide, past what the gear is made for",
                subject=subject,
                measured=width,
                limit=limits.sash_max_width,
            )
        )
    if height > limits.sash_max_height.value:
        findings.append(
            Finding(
                severity=limits.sash_max_height.severity(),
                code="sash.height",
                hebrew=f"גובה כנף {height:.0f} מ\"מ מעל המרבי לפרזול.",
                english=f"sash is {height:.0f} mm tall, past what the gear is made for",
                subject=subject,
                measured=height,
                limit=limits.sash_max_height,
            )
        )

    # Handle position along the lock stile.
    if opening_type.is_operable and opening_type is not OpeningType.SLIDING:
        handle_height = cell.sash.handle_height
        if handle_height is None:
            handle_height = height / 2.0
        clearance = limits.handle_end_clearance.value
        if handle_height < clearance or handle_height > height - clearance:
            findings.append(
                Finding(
                    severity=limits.handle_end_clearance.severity(),
                    code="handle.fouls_rail",
                    hebrew=(
                        f"הידית בגובה {handle_height:.0f} מ\"מ מקצה הכנף — "
                        f"נדרשים {clearance:.0f} מ\"מ פנויים למוט ההינע."
                    ),
                    english=(
                        f"handle sits {handle_height:.0f} mm from the sash end; the "
                        f"corner drive needs {clearance:.0f} mm clear"
                    ),
                    subject=subject,
                    measured=min(handle_height, height - handle_height),
                    limit=limits.handle_end_clearance,
                )
            )

        if handle_absolute_height is not None:
            if handle_absolute_height > limits.handle_max_reach.value:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        code="handle.out_of_reach",
                        hebrew=(
                            f"הידית בגובה {handle_absolute_height:.0f} מ\"מ מהרצפה — "
                            "מחוץ להישג יד."
                        ),
                        english=(
                            f"handle is {handle_absolute_height:.0f} mm above the floor, "
                            "out of reach"
                        ),
                        subject=subject,
                        measured=handle_absolute_height,
                        limit=limits.handle_max_reach,
                    )
                )
            elif handle_absolute_height < limits.handle_min_reach.value:
                findings.append(
                    Finding(
                        severity=Severity.NOTE,
                        code="handle.low",
                        hebrew=f"הידית בגובה {handle_absolute_height:.0f} מ\"מ מהרצפה — נמוכה.",
                        english=f"handle is only {handle_absolute_height:.0f} mm above the floor",
                        subject=subject,
                        measured=handle_absolute_height,
                        limit=limits.handle_min_reach,
                    )
                )
    return findings


def check_element(
    build: ElementBuild,
    *,
    limits: FabricationLimits | None = None,
    mass_lookup: MassLookup | None = _default_mass_lookup,
    sill_height: float = 0.0,
) -> FeasibilityReport:
    """Every reason this element cannot, or should not, be made as drawn."""
    limits = limits or DEFAULT_LIMITS
    opening = build.opening
    report = FeasibilityReport(element_id=opening.element_id)

    for panel in build.glass:
        subject = panel.mark or f"{opening.element_id} pane"
        report.findings.extend(check_glass(panel, limits, subject))

    for cell in opening.all_cells():
        if cell.sash is None:
            continue
        size = _sash_outer_size(build, cell.key)
        if size is None:
            continue
        members = [
            cut for cut in build.cuts
            if cut.cell_key == cell.key and cut.role.startswith("sash")
        ]
        frame_mass, complete = _member_mass(members, mass_lookup)
        glass_mass = sum(
            panel.mass * panel.quantity
            for panel in build.glass
            if panel.cell_key == cell.key
        )
        subject = cell.label or f"{opening.element_id} {cell.column + 1}-{cell.row + 1}"
        handle_height = cell.sash.handle_height
        absolute = None
        if sill_height:
            absolute = sill_height + (handle_height if handle_height is not None else size[1] / 2.0)
        report.findings.extend(
            check_sash(
                cell,
                size,
                frame_mass + glass_mass,
                complete,
                limits,
                subject,
                handle_absolute_height=absolute,
            )
        )

    # The builder's own warnings are findings too; losing them here would mean
    # two places to look for the same kind of problem. The ones this module has
    # already raised properly are dropped rather than repeated as a weaker
    # duplicate — a blocker and a warning about the same pane read as two
    # problems, and the second one teaches people to skim.
    already = {
        finding.subject for finding in report.findings
        if finding.code == "glass.safety_required"
    }
    for warning in build.warnings:
        if "safety glass" in warning and any(mark in warning for mark in already):
            continue
        report.findings.append(
            Finding(
                severity=Severity.WARNING,
                code="build.warning",
                hebrew=warning,
                english=warning,
                subject=opening.element_id,
            )
        )

    if not build.may_be_cut:
        report.findings.append(
            Finding(
                severity=Severity.NOTE,
                code="system.unconfirmed",
                hebrew=build.production_banner or "",
                english=(
                    "these dimensions come from stand-in deductions, so every "
                    "measurement checked here is provisional"
                ),
                subject=opening.element_id,
            )
        )

    _log.info(
        "Feasibility %s: %s", opening.element_id, report.summary()
    )
    return report


def check_elements(
    builds: Iterable[ElementBuild], **kwargs
) -> list[FeasibilityReport]:
    return [check_element(build, **kwargs) for build in builds]


__all__ = [
    "DEFAULT_LIMITS",
    "FabricationLimits",
    "FeasibilityReport",
    "Finding",
    "Limit",
    "LimitKind",
    "Severity",
    "check_element",
    "check_elements",
    "check_glass",
    "check_sash",
]
