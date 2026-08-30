"""Turning an opening into cut lists, glass sizes, gaskets and hardware.

This is the calculation at the centre of every window-fabrication package: an
elevation in, a complete production package out. The chain is

    outer frame size
      -> frame member cut lengths (mitred)
      -> inner (daylight) opening
        -> division grid -> per-cell daylight openings
          -> sash outer sizes (via overlap and clearance)
            -> sash daylight openings
              -> glass sizes (via edge cover and clearance)
                -> gasket lengths, hardware, weights

Every step reads its numbers from a :class:`~profileos.elements.rules.SystemRules`
set rather than hard-coding them, because those numbers are exactly what
differs between MB-70, Reynaers CS 77 and a Klil series.

All returned lengths are in millimetres, areas in m^2, masses in kg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..glazing.glass import GlassBuildUp, STANDARD_BUILDUPS
from ..models.orders import CutItem, CutOrientation
from ..systems.model import Provenance
from .model import Cell, ElementKind, Opening, OpeningType, Sash
from .rules import SystemRules, get_system_rules

_log = get_logger("elements.builder")


@dataclass
class Rect:
    """An axis-aligned rectangle in element coordinates."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        """Area [m^2]."""
        return self.width * self.height / 1_000_000.0

    @property
    def perimeter(self) -> float:
        """Perimeter [mm]."""
        return 2.0 * (self.width + self.height)


@dataclass
class MemberCut:
    """One profile piece in the element's cut list."""

    profile_id: str
    length: float
    quantity: int = 1
    angle_left: float = 90.0
    angle_right: float = 90.0
    role: str = "frame"
    mark: str | None = None
    cell_key: tuple[int, int] | None = None

    @property
    def total_length(self) -> float:
        return self.length * self.quantity


@dataclass
class GlassPanel:
    """One glass pane to be ordered."""

    width: float
    height: float
    build_up: GlassBuildUp
    quantity: int = 1
    cell_key: tuple[int, int] | None = None
    mark: str | None = None
    #: True when regulation requires safety glass in this position.
    safety_required: bool = False
    safety_reason: str | None = None

    @property
    def area(self) -> float:
        """Pane area [m^2]."""
        return self.width * self.height / 1_000_000.0

    @property
    def total_area(self) -> float:
        return self.area * self.quantity

    @property
    def mass(self) -> float:
        """Mass of one pane [kg]."""
        return self.build_up.mass(self.width, self.height)

    @property
    def perimeter(self) -> float:
        return 2.0 * (self.width + self.height)

    @property
    def compliant(self) -> bool:
        """False when safety glass is required but the build-up is not safety glass."""
        return not self.safety_required or self.build_up.is_safety_glass

    def describe(self) -> str:
        return f"{self.width:.0f} x {self.height:.0f} mm {self.build_up.describe()}"


@dataclass
class GasketRun:
    """A length of gasket or weatherstrip to be ordered."""

    code: str
    name: str
    length: float
    quantity: int = 1

    @property
    def total_length(self) -> float:
        return self.length * self.quantity


@dataclass
class HardwareItem:
    """One piece of hardware required by the element."""

    code: str
    name: str
    quantity: int = 1
    unit: str = "pc"
    cell_key: tuple[int, int] | None = None
    supplier: str | None = None
    notes: str | None = None


@dataclass
class ElementBuild:
    """The complete production package for one element."""

    opening: Opening
    rules: SystemRules
    cuts: list[MemberCut] = field(default_factory=list)
    glass: list[GlassPanel] = field(default_factory=list)
    gaskets: list[GasketRun] = field(default_factory=list)
    hardware: list[HardwareItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: How far the deductions these parts were computed from can be trusted.
    #: This travels with the build all the way to the cut list, because that is
    #: the last moment anybody can stop a bar being cut to a guessed figure.
    provenance: Provenance = Provenance.TYPICAL

    @property
    def may_be_cut(self) -> bool:
        """Whether these lengths came from the system supplier's own figures."""
        return self.provenance.may_be_cut_to

    @property
    def production_banner(self) -> str | None:
        """The line that has to appear on any sheet made from guessed numbers."""
        if self.may_be_cut:
            return None
        return (
            "לא לייצור — הקיזוזים אינם מקטלוג היצרן. "
            "NOT FOR PRODUCTION — these deductions are not the supplier's."
        )

    # -- aggregates --------------------------------------------------------- #
    @property
    def total_profile_length(self) -> float:
        """Total aluminium length, before nesting [mm]."""
        return sum(cut.total_length for cut in self.cuts) * self.opening.quantity

    @property
    def total_glass_area(self) -> float:
        """Total glass area [m^2]."""
        return sum(panel.total_area for panel in self.glass) * self.opening.quantity

    @property
    def total_glass_mass(self) -> float:
        return (
            sum(panel.mass * panel.quantity for panel in self.glass) * self.opening.quantity
        )

    @property
    def total_gasket_length(self) -> float:
        return sum(run.total_length for run in self.gaskets) * self.opening.quantity

    @property
    def non_compliant_glass(self) -> list[GlassPanel]:
        """Panes where regulation requires safety glass but the spec is not."""
        return [panel for panel in self.glass if not panel.compliant]

    def cut_items(self) -> list[CutItem]:
        """Convert the cut list into nesting demand lines."""
        items: list[CutItem] = []
        for cut in self.cuts:
            items.append(
                CutItem(
                    profile_id=cut.profile_id,
                    length=cut.length,
                    quantity=cut.quantity * self.opening.quantity,
                    angle_left=cut.angle_left,
                    angle_right=cut.angle_right,
                    mark=cut.mark or cut.role,
                    element_ref=self.opening.element_id,
                    orientation=(
                        CutOrientation.SYMMETRIC
                        if cut.angle_left == cut.angle_right
                        else CutOrientation.FIXED
                    ),
                )
            )
        return items

    def summary(self) -> dict[str, Any]:
        return {
            "element_id": self.opening.element_id,
            "name": self.opening.name,
            "size": f"{self.opening.width:.0f} x {self.opening.height:.0f}",
            "quantity": self.opening.quantity,
            "area_m2": round(self.opening.area * self.opening.quantity, 3),
            "pieces": sum(c.quantity for c in self.cuts) * self.opening.quantity,
            "profile_length_m": round(self.total_profile_length / 1000.0, 2),
            "glass_panes": sum(p.quantity for p in self.glass) * self.opening.quantity,
            "glass_area_m2": round(self.total_glass_area, 3),
            "glass_mass_kg": round(self.total_glass_mass, 1),
            "gasket_m": round(self.total_gasket_length / 1000.0, 2),
            "hardware_items": sum(h.quantity for h in self.hardware) * self.opening.quantity,
            "warnings": len(self.warnings),
        }


# --------------------------------------------------------------------------- #
# Safety glass regulation
# --------------------------------------------------------------------------- #

#: Panes at or below this height above finished floor are impact-critical.
CRITICAL_HEIGHT_MM = 800.0
#: Panes larger than this need safety glass even above the critical height.
LARGE_PANE_AREA_M2 = 2.0


def safety_glass_required(
    panel_rect: Rect,
    *,
    sill_height: float = 0.0,
    is_door: bool = False,
    is_overhead: bool = False,
) -> tuple[bool, str | None]:
    """Decide whether a pane must be safety glass.

    Follows the common regulatory pattern (EN 12600 / BS 6262-4 / IBC 2406):
    glass in and beside doors, glass low enough to be walked into, large panes,
    and overhead glazing are all impact-critical.

    ``sill_height`` is the height of the pane's bottom edge above finished
    floor level [mm].
    """
    if is_door:
        return True, "glazing in a door leaf"
    if is_overhead:
        return True, "overhead glazing"

    bottom = sill_height + panel_rect.y
    if bottom < CRITICAL_HEIGHT_MM:
        return True, f"bottom edge {bottom:.0f} mm above floor, below the {CRITICAL_HEIGHT_MM:.0f} mm critical height"
    if panel_rect.area > LARGE_PANE_AREA_M2:
        return True, f"pane area {panel_rect.area:.2f} m^2 exceeds {LARGE_PANE_AREA_M2:.1f} m^2"
    return False, None


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #

class ElementBuilder:
    """Computes the production package for one :class:`Opening`."""

    def __init__(
        self,
        rules: SystemRules | None = None,
        *,
        glass_catalogue: dict[str, GlassBuildUp] | None = None,
        default_glass: GlassBuildUp | None = None,
        provenance: Provenance = Provenance.TYPICAL,
    ) -> None:
        """
        ``provenance`` defaults to *typical* rather than *confirmed*: rules
        handed in directly are good enough to quote from, and treating them as
        the supplier's own figures without being told so is exactly the
        assumption that gets a job cut short. Use :meth:`for_system` to carry a
        real provenance through from the系 directory.
        """
        self.rules = rules
        self.provenance = provenance
        self.glass_catalogue = glass_catalogue or dict(STANDARD_BUILDUPS)
        self.default_glass = default_glass or self.glass_catalogue.get(
            "dgu-6-16-4"
        ) or next(iter(self.glass_catalogue.values()))

    @classmethod
    def for_system(
        cls,
        entry_id: str,
        *,
        directory: Any = None,
        glass_catalogue: dict[str, GlassBuildUp] | None = None,
    ) -> "ElementBuilder":
        """Build for a named series, carrying its provenance with it.

        This is the route that lets a cut sheet say, truthfully, whether it may
        be worked to. Constructing the builder with a rule set directly cannot
        know where that rule set came from, so it stays cautious.
        """
        from ..systems import DIRECTORY

        source = directory if directory is not None else DIRECTORY
        rules, provenance = source.rules_for(entry_id)
        return cls(rules, glass_catalogue=glass_catalogue, provenance=provenance)

    # -- geometry ----------------------------------------------------------- #
    def inner_opening(self, opening: Opening, rules: SystemRules) -> Rect:
        """The daylight rectangle inside the frame."""
        face = rules.frame.face_width
        width = opening.width - 2.0 * face
        height = opening.height - 2.0 * face
        if width <= 0 or height <= 0:
            raise ProfileOSError(
                "Frame profile is wider than the element itself",
                element=opening.element_id,
                face_width=face,
                width=opening.width,
                height=opening.height,
            )
        return Rect(face, face, width, height)

    def cell_rects(self, opening: Opening, rules: SystemRules) -> dict[tuple[int, int], Rect]:
        """The daylight rectangle of every cell in the division grid."""
        inner = self.inner_opening(opening, rules)
        half_mullion = rules.mullion.face_width / 2.0

        # Column boundaries: frame inner edge, then each side of every mullion.
        x_edges: list[tuple[float, float]] = []
        left = inner.x
        for position in opening.mullion_positions:
            x_edges.append((left, position - half_mullion))
            left = position + half_mullion
        x_edges.append((left, inner.right))

        y_edges: list[tuple[float, float]] = []
        bottom = inner.y
        for position in opening.transom_positions:
            y_edges.append((bottom, position - half_mullion))
            bottom = position + half_mullion
        y_edges.append((bottom, inner.top))

        rects: dict[tuple[int, int], Rect] = {}
        for column, (x0, x1) in enumerate(x_edges):
            for row, (y0, y1) in enumerate(y_edges):
                rects[(column, row)] = Rect(x0, y0, x1 - x0, y1 - y0)
        return rects

    # -- the build ---------------------------------------------------------- #
    @timed("elements.build")
    def build(self, opening: Opening, *, sill_height: float = 0.0) -> ElementBuild:
        """Compute the full production package for ``opening``."""
        rules = self.rules or get_system_rules(opening.system_id)
        build = ElementBuild(opening=opening, rules=rules, provenance=self.provenance)

        inner = self.inner_opening(opening, rules)
        rects = self.cell_rects(opening, rules)

        self._add_frame(build, opening, rules)
        self._add_divisions(build, opening, rules, inner, rects)

        for cell in opening.all_cells():
            rect = rects[cell.key]
            if rect.width <= 0 or rect.height <= 0:
                build.warnings.append(
                    f"Cell {cell.key} has a non-positive daylight opening "
                    f"({rect.width:.1f} x {rect.height:.1f} mm); divisions are too close."
                )
                continue
            self._add_cell(build, opening, rules, cell, rect, sill_height)

        self._check_compliance(build)
        _log.info(
            "Built %s: %d cut(s), %d pane(s), %d hardware item(s)",
            opening.element_id,
            len(build.cuts),
            len(build.glass),
            len(build.hardware),
        )
        return build

    # -- frame --------------------------------------------------------------- #
    def _add_frame(self, build: ElementBuild, opening: Opening, rules: SystemRules) -> None:
        profile = rules.profile_for("frame")
        allowance = rules.frame.corner_allowance
        # A mitred frame is measured outer-to-outer, so each member is the full
        # element dimension; a butt-jointed frame needs the corner allowance.
        angle = 45.0 if rules.frame.mitred_corners else 90.0

        build.cuts.append(
            MemberCut(
                profile_id=profile,
                length=opening.width + allowance,
                quantity=2,
                angle_left=angle,
                angle_right=angle,
                role="frame_horizontal",
                mark=f"{opening.name} head/sill",
            )
        )
        build.cuts.append(
            MemberCut(
                profile_id=profile,
                length=opening.height + allowance,
                quantity=2,
                angle_left=angle,
                angle_right=angle,
                role="frame_vertical",
                mark=f"{opening.name} jambs",
            )
        )

    # -- mullions and transoms ---------------------------------------------- #
    def _add_divisions(
        self,
        build: ElementBuild,
        opening: Opening,
        rules: SystemRules,
        inner: Rect,
        rects: dict[tuple[int, int], Rect],
    ) -> None:
        if opening.mullion_positions:
            profile = rules.profile_for("mullion")
            build.cuts.append(
                MemberCut(
                    profile_id=profile,
                    length=inner.height - 2.0 * rules.mullion.end_deduction,
                    quantity=len(opening.mullion_positions),
                    role="mullion",
                    mark=f"{opening.name} mullion",
                )
            )

        if not opening.transom_positions:
            return

        # A transom spans one bay, so its length depends on the columns either
        # side of it — which is why unequal bays produce different transom
        # lengths rather than one repeated part.
        profile = rules.profile_for("transom")
        lengths: dict[float, int] = {}
        for column in range(opening.column_count):
            rect = rects[(column, 0)]
            length = round(rect.width - 2.0 * rules.mullion.end_deduction, 2)
            lengths[length] = lengths.get(length, 0) + len(opening.transom_positions)

        for length, quantity in sorted(lengths.items(), reverse=True):
            build.cuts.append(
                MemberCut(
                    profile_id=profile,
                    length=length,
                    quantity=quantity,
                    role="transom",
                    mark=f"{opening.name} transom",
                )
            )

    # -- cells --------------------------------------------------------------- #
    def _add_cell(
        self,
        build: ElementBuild,
        opening: Opening,
        rules: SystemRules,
        cell: Cell,
        rect: Rect,
        sill_height: float,
    ) -> None:
        if cell.panel:
            # A solid infill panel: no glass, no bead, just the panel itself.
            build.hardware.append(
                HardwareItem(
                    code="PANEL",
                    name=f"Infill panel {rect.width:.0f} x {rect.height:.0f} mm",
                    quantity=1,
                    unit="pc",
                    cell_key=cell.key,
                )
            )
            return

        glazing_rect = rect
        is_door_leaf = False

        if cell.sash is not None:
            glazing_rect = self._add_sash(build, opening, rules, cell, rect)
            is_door_leaf = cell.sash.opening_type is OpeningType.DOOR
            self._add_hardware(build, rules, cell.sash, cell.key, glazing_rect)

        # Glass sizing: the pane spans the daylight opening plus the edge cover
        # on each side, less the setting clearance on each side.
        deduction = rules.glass.deduction()
        pane_width = glazing_rect.width - deduction
        pane_height = glazing_rect.height - deduction

        build_up = self._resolve_glass(cell, opening, build)
        if build_up.total_thickness > rules.glass.max_glass_thickness:
            build.warnings.append(
                f"Cell {cell.key}: glass is {build_up.total_thickness:.0f} mm thick but the "
                f"system accepts at most {rules.glass.max_glass_thickness:.0f} mm."
            )

        required, reason = safety_glass_required(
            rect,
            sill_height=sill_height,
            is_door=is_door_leaf,
            is_overhead=opening.kind is ElementKind.CURTAIN_WALL and rect.y > 2500.0,
        )
        panel = GlassPanel(
            width=round(pane_width, 1),
            height=round(pane_height, 1),
            build_up=build_up,
            cell_key=cell.key,
            mark=cell.label or f"{opening.name} {cell.column + 1}-{cell.row + 1}",
            safety_required=required,
            safety_reason=reason,
        )
        build.glass.append(panel)

        # Glazing beads: four mitred pieces sized to the glazing rebate.
        bead_profile = rules.profile_for("bead")
        build.cuts.append(
            MemberCut(
                profile_id=bead_profile,
                length=round(glazing_rect.width, 1),
                quantity=2,
                angle_left=45.0,
                angle_right=45.0,
                role="bead_horizontal",
                cell_key=cell.key,
            )
        )
        build.cuts.append(
            MemberCut(
                profile_id=bead_profile,
                length=round(glazing_rect.height, 1),
                quantity=2,
                angle_left=45.0,
                angle_right=45.0,
                role="bead_vertical",
                cell_key=cell.key,
            )
        )

        # Gaskets: one run inside and one outside the pane.
        allowance = rules.gasket.corner_allowance
        run_length = round(panel.perimeter + allowance, 1)
        for code, name in (("GK-IN", "Inner glazing gasket"), ("GK-OUT", "Outer glazing gasket")):
            build.gaskets.append(GasketRun(code=code, name=name, length=run_length, quantity=1))

    def _add_sash(
        self,
        build: ElementBuild,
        opening: Opening,
        rules: SystemRules,
        cell: Cell,
        rect: Rect,
    ) -> Rect:
        """Add the sash members and return the sash's own daylight rectangle."""
        sash_width = rect.width - rules.sash.width_deduction()
        sash_height = rect.height - rules.sash.height_deduction()

        profile = rules.profile_for("sash")
        build.cuts.append(
            MemberCut(
                profile_id=profile,
                length=round(sash_width, 1),
                quantity=2,
                angle_left=45.0,
                angle_right=45.0,
                role="sash_horizontal",
                cell_key=cell.key,
                mark=f"{opening.name} sash {cell.column + 1}-{cell.row + 1}",
            )
        )
        build.cuts.append(
            MemberCut(
                profile_id=profile,
                length=round(sash_height, 1),
                quantity=2,
                angle_left=45.0,
                angle_right=45.0,
                role="sash_vertical",
                cell_key=cell.key,
                mark=f"{opening.name} sash {cell.column + 1}-{cell.row + 1}",
            )
        )

        face = rules.sash.sash_face_width
        inner_width = sash_width - 2.0 * face
        inner_height = sash_height - 2.0 * face
        if inner_width <= 0 or inner_height <= 0:
            build.warnings.append(
                f"Cell {cell.key}: sash profile leaves no daylight opening."
            )
            return Rect(rect.x, rect.y, max(inner_width, 1.0), max(inner_height, 1.0))

        # Weatherstrip around the sash perimeter.
        build.gaskets.append(
            GasketRun(
                code="GK-WS",
                name="Sash weatherstrip",
                length=round(2.0 * (sash_width + sash_height) + rules.gasket.corner_allowance, 1),
            )
        )
        return Rect(rect.x, rect.y, inner_width, inner_height)

    def _add_hardware(
        self,
        build: ElementBuild,
        rules: SystemRules,
        sash: Sash,
        cell_key: tuple[int, int],
        rect: Rect,
    ) -> None:
        """Select hardware for a sash from the system's rules."""
        group = sash.opening_type.hardware_group
        entries = rules.hardware.get(group, [])
        if not entries and sash.opening_type.is_operable:
            build.warnings.append(
                f"Cell {cell_key}: no hardware rules for opening type "
                f"{sash.opening_type.value!r} in system {rules.id!r}."
            )

        for entry in entries:
            build.hardware.append(
                HardwareItem(
                    code=str(entry.get("code", "HW")),
                    name=str(entry.get("name", "Hardware")),
                    quantity=int(entry.get("quantity", 1)),
                    unit=str(entry.get("unit", "pc")),
                    cell_key=cell_key,
                    supplier=entry.get("supplier"),
                )
            )

        # Hinge count scales with sash height and weight, not just type.
        if sash.opening_type in (OpeningType.CASEMENT, OpeningType.TILT_TURN, OpeningType.DOOR):
            extra = 1 if rect.height > 1600.0 else 0
            if extra:
                build.hardware.append(
                    HardwareItem(
                        code="HW-HINGE-EXTRA",
                        name="Additional hinge for tall sash",
                        quantity=extra,
                        cell_key=cell_key,
                        notes=f"sash height {rect.height:.0f} mm exceeds 1600 mm",
                    )
                )

    # -- helpers ------------------------------------------------------------- #
    def _resolve_glass(
        self, cell: Cell, opening: Opening, build: "ElementBuild | None" = None
    ) -> GlassBuildUp:
        """The glass this cell asked for, or the default — never silently.

        A cell that names a build-up this installation does not have used to
        fall through to the default without a word. That is how a balustrade
        specified in laminated glass is quoted, cut and fitted in ordinary
        double glazing: nothing anywhere said the specification was dropped.
        """
        asked: list[str] = []
        for spec_id in (
            cell.glass_spec_id,
            cell.sash.glass_spec_id if cell.sash else None,
            opening.glass_spec_id,
        ):
            if not spec_id:
                continue
            if spec_id in self.glass_catalogue:
                return self.glass_catalogue[spec_id]
            asked.append(spec_id)

        if asked and build is not None:
            build.warnings.append(
                f"Cell {cell.key}: glass {asked[0]!r} is not in this "
                f"installation's catalogue; {self.default_glass.id!r} was used "
                "instead. Check the specification before ordering."
            )
        return self.default_glass

    def _check_compliance(self, build: ElementBuild) -> None:
        for panel in build.non_compliant_glass:
            build.warnings.append(
                f"Pane {panel.mark} ({panel.describe()}) requires safety glass "
                f"({panel.safety_reason}) but the specified build-up is not safety glass."
            )


@timed("elements.build_many")
def build_elements(
    openings: list[Opening],
    *,
    rules: SystemRules | None = None,
    glass_catalogue: dict[str, GlassBuildUp] | None = None,
    sill_height: float = 0.0,
) -> list[ElementBuild]:
    """Build a whole schedule of elements."""
    builder = ElementBuilder(rules, glass_catalogue=glass_catalogue)
    return [builder.build(opening, sill_height=sill_height) for opening in openings]


def collect_cut_items(builds: list[ElementBuild]) -> list[CutItem]:
    """Flatten every element's cut list into nesting demand."""
    return [item for build in builds for item in build.cut_items()]


__all__ = [
    "Rect",
    "MemberCut",
    "GlassPanel",
    "GasketRun",
    "HardwareItem",
    "ElementBuild",
    "ElementBuilder",
    "safety_glass_required",
    "build_elements",
    "collect_cut_items",
    "CRITICAL_HEIGHT_MM",
    "LARGE_PANE_AREA_M2",
]
