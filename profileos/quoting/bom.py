"""Bill of materials: aggregation across elements, grouped for purchasing.

An element build lists what one element needs. A project needs the same
information rolled up: how many metres of each profile, how many square metres
of each glass build-up, how many of each hardware item, grouped by supplier so
each one can be sent a single purchase order.

Aggregation is by ``(category, code)``, and quantities carry their unit, so
profile metres never get added to hardware pieces.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from ..elements.builder import ElementBuild
from ..nesting.engine import ProjectNestingReport


class BomCategory(StrEnum):
    """Purchasing categories, which usually map to different suppliers."""

    PROFILE = "profile"
    GLASS = "glass"
    HARDWARE = "hardware"
    GASKET = "gasket"
    CONSUMABLE = "consumable"
    FINISH = "finish"
    LABOUR = "labour"
    #: Shutters, screens, sills and trims — bought as assemblies, not as the
    #: aluminium they are made of, and quoted per opening.
    ACCESSORY = "accessory"

    @property
    def label(self) -> str:
        return {
            "profile": "Aluminium profiles",
            "glass": "Glass",
            "hardware": "Hardware",
            "gasket": "Gaskets and seals",
            "consumable": "Consumables",
            "finish": "Surface finish",
            "labour": "Labour",
            "accessory": "Shutters, screens and sills",
        }[self.value]


class Unit(StrEnum):
    """Units of measure. Quantities are only ever summed within a unit."""

    PIECE = "pc"
    METRE = "m"
    SQUARE_METRE = "m2"
    KILOGRAM = "kg"
    SET = "set"
    PAIR = "pair"
    HOUR = "h"
    LITRE = "l"


@dataclass
class BomLine:
    """One aggregated purchasing line."""

    category: BomCategory
    code: str
    description: str
    quantity: float
    unit: Unit = Unit.PIECE
    supplier_id: str | None = None
    #: Which elements contributed to this line, for traceability.
    element_refs: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    #: Filled in by the pricing engine.
    unit_price: float | None = None
    currency: str = "EUR"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.category.value, self.code, self.unit.value)

    @property
    def total_price(self) -> float | None:
        if self.unit_price is None:
            return None
        return self.unit_price * self.quantity

    def merge(self, other: "BomLine") -> None:
        """Absorb another line with the same key."""
        self.quantity += other.quantity
        self.element_refs |= other.element_refs
        if self.supplier_id is None:
            self.supplier_id = other.supplier_id


@dataclass
class BillOfMaterials:
    """The aggregated bill for a project."""

    project_id: str = ""
    project_name: str = ""
    lines: list[BomLine] = field(default_factory=list)
    currency: str = "EUR"
    warnings: list[str] = field(default_factory=list)

    def add(self, line: BomLine) -> None:
        """Add a line, merging into an existing one with the same key."""
        for existing in self.lines:
            if existing.key == line.key:
                existing.merge(line)
                return
        self.lines.append(line)

    def by_category(self, category: BomCategory) -> list[BomLine]:
        return [line for line in self.lines if line.category is category]

    def by_supplier(self) -> dict[str, list[BomLine]]:
        """Group lines by supplier, for one purchase order each."""
        grouped: dict[str, list[BomLine]] = defaultdict(list)
        for line in self.lines:
            grouped[line.supplier_id or "unassigned"].append(line)
        return dict(grouped)

    def categories(self) -> list[BomCategory]:
        seen: dict[BomCategory, None] = {}
        for line in self.lines:
            seen.setdefault(line.category, None)
        return list(seen)

    @property
    def total_price(self) -> float | None:
        """Sum of priced lines, or ``None`` when nothing is priced."""
        totals = [line.total_price for line in self.lines if line.total_price is not None]
        return sum(totals) if totals else None

    @property
    def unpriced_lines(self) -> list[BomLine]:
        return [line for line in self.lines if line.unit_price is None]

    def category_total(self, category: BomCategory) -> float:
        return sum(
            line.total_price or 0.0 for line in self.by_category(category)
        )

    def sorted_lines(self) -> list[BomLine]:
        """Lines ordered by category then code, for stable printed output."""
        order = list(BomCategory)
        return sorted(self.lines, key=lambda l: (order.index(l.category), l.code))

    def summary(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "lines": len(self.lines),
            "categories": [c.value for c in self.categories()],
            "suppliers": sorted(self.by_supplier()),
            "total_price": round(self.total_price, 2) if self.total_price else None,
            "unpriced_lines": len(self.unpriced_lines),
            "currency": self.currency,
        }


def build_bom(
    builds: Iterable[ElementBuild],
    *,
    project_id: str = "",
    project_name: str = "",
    nesting: ProjectNestingReport | None = None,
    currency: str = "EUR",
) -> BillOfMaterials:
    """Aggregate element builds into a purchasing bill of materials.

    When a ``nesting`` report is supplied, profile quantities come from the
    **bars actually consumed** rather than the sum of finished lengths — the
    difference is the offcut, and it is the fabricator who pays for it.
    """
    bom = BillOfMaterials(project_id=project_id, project_name=project_name, currency=currency)
    builds = list(builds)

    # -- profiles ----------------------------------------------------------- #
    if nesting is not None and nesting.results:
        for profile_id, result in nesting.results.items():
            bars = result.bar_count
            bom.add(
                BomLine(
                    category=BomCategory.PROFILE,
                    code=profile_id,
                    description=f"{profile_id} stock bars",
                    quantity=float(bars),
                    unit=Unit.PIECE,
                    metadata={
                        "stock_length_mm": round(result.total_stock_length, 1),
                        "yield_pct": round(result.yield_pct, 2),
                        "net_length_m": round(result.total_net_length / 1000.0, 2),
                    },
                )
            )
        if nesting.failures:
            bom.warnings.append(
                f"{len(nesting.failures)} profile(s) could not be nested; their "
                "quantities are estimated from finished length instead."
            )
    else:
        lengths: dict[str, float] = defaultdict(float)
        refs: dict[str, set[str]] = defaultdict(set)
        for build in builds:
            for cut in build.cuts:
                total = cut.total_length * build.opening.quantity
                lengths[cut.profile_id] += total
                refs[cut.profile_id].add(build.opening.element_id)
        for profile_id, total in lengths.items():
            bom.add(
                BomLine(
                    category=BomCategory.PROFILE,
                    code=profile_id,
                    description=f"{profile_id} profile",
                    quantity=round(total / 1000.0, 3),
                    unit=Unit.METRE,
                    element_refs=refs[profile_id],
                    metadata={"note": "finished length; add offcut allowance"},
                )
            )
        bom.warnings.append(
            "Profile quantities are finished lengths — nest the project to "
            "price the bars actually consumed."
        )

    # -- glass --------------------------------------------------------------- #
    for build in builds:
        multiplier = build.opening.quantity
        for panel in build.glass:
            bom.add(
                BomLine(
                    category=BomCategory.GLASS,
                    code=panel.build_up.id,
                    description=panel.build_up.describe(),
                    quantity=round(panel.total_area * multiplier, 4),
                    unit=Unit.SQUARE_METRE,
                    element_refs={build.opening.element_id},
                    metadata={
                        "u_value": round(panel.build_up.u_value(), 3),
                        "mass_kg": round(panel.mass * panel.quantity * multiplier, 1),
                        "panes": panel.quantity * multiplier,
                    },
                )
            )

    # -- hardware ------------------------------------------------------------ #
    for build in builds:
        multiplier = build.opening.quantity
        for item in build.hardware:
            bom.add(
                BomLine(
                    category=BomCategory.HARDWARE,
                    code=item.code,
                    description=item.name,
                    quantity=float(item.quantity * multiplier),
                    unit=Unit(item.unit) if item.unit in Unit._value2member_map_ else Unit.PIECE,
                    supplier_id=item.supplier,
                    element_refs={build.opening.element_id},
                )
            )

    # -- gaskets -------------------------------------------------------------- #
    for build in builds:
        multiplier = build.opening.quantity
        waste = build.rules.gasket.waste_factor
        for run in build.gaskets:
            bom.add(
                BomLine(
                    category=BomCategory.GASKET,
                    code=run.code,
                    description=run.name,
                    quantity=round(run.total_length * multiplier * waste / 1000.0, 3),
                    unit=Unit.METRE,
                    element_refs={build.opening.element_id},
                    metadata={"waste_factor": waste},
                )
            )

    # -- accessories ---------------------------------------------------------- #
    # A shutter is a quarter of the price of the window it hangs on, so a bill
    # of materials that leaves it out is not a bill of materials. The parts
    # come through as themselves rather than as the aluminium they are made
    # of, because that is how they are bought.
    for build in builds:
        # The accessory already carries the opening's quantity, so its own
        # parts and lengths are multiplied by it here and nowhere else.
        try:
            from ..accessories import accessories_for

            fitted = accessories_for(build.opening)
        except Exception as exc:  # noqa: BLE001 - never lose a bill over a fitting
            bom.warnings.append(f"{build.opening.name}: accessories not sized ({exc})")
            continue

        for accessory in fitted:
            bom.add(
                BomLine(
                    category=BomCategory.ACCESSORY,
                    code=accessory.code,
                    description=accessory.hebrew,
                    quantity=float(accessory.quantity),
                    unit=Unit.PIECE,
                    element_refs={build.opening.element_id},
                    metadata={
                        "kind": accessory.kind.value,
                        "size_mm": f"{accessory.width:.0f} x {accessory.height:.0f}",
                        "area_m2": round(accessory.total_area, 3),
                        "mass_kg": round(accessory.mass * accessory.quantity, 1),
                        **accessory.metadata,
                    },
                )
            )
            for cut in accessory.cuts:
                bom.add(
                    BomLine(
                        category=BomCategory.ACCESSORY,
                        code=cut.profile_id,
                        description=cut.hebrew,
                        quantity=round(
                            cut.total_length * accessory.quantity / 1000.0, 3
                        ),
                        unit=Unit.METRE,
                        element_refs={build.opening.element_id},
                    )
                )
            for part in accessory.parts:
                bom.add(
                    BomLine(
                        category=BomCategory.ACCESSORY,
                        code=part.code,
                        description=part.hebrew,
                        quantity=float(part.quantity * accessory.quantity),
                        unit=(
                            Unit(part.unit) if part.unit in Unit._value2member_map_
                            else Unit.PIECE
                        ),
                        supplier_id=part.supplier,
                        element_refs={build.opening.element_id},
                    )
                )
            for warning in accessory.warnings:
                bom.warnings.append(f"{build.opening.name}: {warning}")

    # -- element-level warnings roll up into the bill ------------------------ #
    for build in builds:
        for warning in build.warnings:
            bom.warnings.append(f"{build.opening.name}: {warning}")

    return bom


__all__ = ["BomCategory", "Unit", "BomLine", "BillOfMaterials", "build_bom"]
