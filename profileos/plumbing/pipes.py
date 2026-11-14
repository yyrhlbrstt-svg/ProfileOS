"""Pipe catalogues and sizing.

A catalogue lists the sizes a material is actually made in, which is what turns
a computed bore into a specifiable product. Sizing then walks the catalogue for
the smallest pipe that satisfies the design constraints — velocity, pressure
loss per metre, and total available pressure.

Sizing on velocity as well as pressure loss matters: a pipe can be
hydraulically adequate and still be wrong, because water above roughly 2 m/s in
copper erodes the pipe wall and is audible through a wall. Codes therefore cap
velocity independently of pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import HydraulicsError
from ..core.hotreload import DataSchema
from ..core.registry import PIPE_CATALOGUES
from .hydraulics import (
    FITTING_K,
    ROUGHNESS_MM,
    Fluid,
    darcy_weisbach_loss,
    fitting_loss,
    friction_factor,
    static_head,
    total_k,
    velocity,
)


class ServiceType(StrEnum):
    """What the pipe carries, which sets the applicable design limits."""

    COLD_WATER = "cold_water"
    HOT_WATER = "hot_water"
    HEATING_FLOW = "heating_flow"
    HEATING_RETURN = "heating_return"
    CHILLED_WATER = "chilled_water"
    DRAINAGE = "drainage"
    FIRE_SPRINKLER = "fire_sprinkler"
    COMPRESSED_AIR = "compressed_air"
    GAS = "gas"


@dataclass(frozen=True)
class DesignLimits:
    """Design constraints for a service."""

    #: Maximum mean velocity [m/s].
    max_velocity: float = 2.0
    #: Minimum velocity, to keep the line self-scouring [m/s].
    min_velocity: float = 0.5
    #: Maximum friction loss per metre of run [Pa/m].
    max_loss_per_m: float = 300.0

    @classmethod
    def for_service(cls, service: ServiceType) -> "DesignLimits":
        """Conventional limits by service type.

        Riser and distribution mains tolerate more velocity than branches near
        occupied rooms, and drainage is gravity-driven so it has a *minimum*
        velocity to stay self-cleansing rather than a tight maximum.
        """
        return {
            ServiceType.COLD_WATER: cls(max_velocity=2.0, min_velocity=0.5, max_loss_per_m=300.0),
            ServiceType.HOT_WATER: cls(max_velocity=1.5, min_velocity=0.5, max_loss_per_m=300.0),
            ServiceType.HEATING_FLOW: cls(max_velocity=1.5, min_velocity=0.3, max_loss_per_m=250.0),
            ServiceType.HEATING_RETURN: cls(max_velocity=1.5, min_velocity=0.3, max_loss_per_m=250.0),
            ServiceType.CHILLED_WATER: cls(max_velocity=2.5, min_velocity=0.5, max_loss_per_m=250.0),
            ServiceType.DRAINAGE: cls(max_velocity=4.0, min_velocity=0.7, max_loss_per_m=1e9),
            ServiceType.FIRE_SPRINKLER: cls(max_velocity=6.0, min_velocity=0.0, max_loss_per_m=1e9),
            ServiceType.COMPRESSED_AIR: cls(max_velocity=9.0, min_velocity=0.0, max_loss_per_m=100.0),
            ServiceType.GAS: cls(max_velocity=6.0, min_velocity=0.0, max_loss_per_m=100.0),
        }[service]


class PipeSize(BaseModel):
    """One size in a catalogue."""

    model_config = ConfigDict(extra="forbid")

    designation: str = Field(description="Trade name, e.g. 'DN50' or '22 mm'")
    outer_diameter: float = Field(gt=0, description="[mm]")
    wall_thickness: float = Field(gt=0, description="[mm]")
    #: Maximum working pressure at 20 degC [bar].
    pressure_rating: float | None = Field(default=None, gt=0)
    mass_per_metre: float | None = Field(default=None, gt=0, description="[kg/m]")
    price_per_metre: float | None = Field(default=None, ge=0)
    stock_length: float = Field(default=6000.0, gt=0, description="[mm]")

    @property
    def internal_diameter(self) -> float:
        """Bore [mm]."""
        bore = self.outer_diameter - 2.0 * self.wall_thickness
        if bore <= 0:
            raise HydraulicsError(
                "Wall thickness leaves no bore",
                designation=self.designation,
                outer=self.outer_diameter,
                wall=self.wall_thickness,
            )
        return bore

    @property
    def bore_area(self) -> float:
        """Cross-sectional flow area [m^2]."""
        import math

        return math.pi * (self.internal_diameter / 1000.0) ** 2 / 4.0

    def water_content(self) -> float:
        """Litres of water per metre of pipe, for system fill and expansion."""
        return self.bore_area * 1000.0


class PipeCatalogue(BaseModel):
    """A material's available sizes, hot-reloadable as ``kind: "pipe_catalogue"``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    material: str
    version: str = "1.0"
    kind: str = "pipe_catalogue"
    #: Absolute roughness [mm]; defaults from the material name.
    roughness: float | None = Field(default=None, ge=0)
    sizes: list[PipeSize] = Field(default_factory=list)
    supplier_id: str | None = None
    notes: str | None = None

    @property
    def effective_roughness(self) -> float:
        if self.roughness is not None:
            return self.roughness
        return ROUGHNESS_MM.get(self.material.strip().lower(), 0.045)

    def sorted_sizes(self) -> list[PipeSize]:
        return sorted(self.sizes, key=lambda s: s.internal_diameter)

    def by_designation(self, designation: str) -> PipeSize | None:
        target = designation.strip().lower()
        return next(
            (s for s in self.sizes if s.designation.strip().lower() == target), None
        )

    def smallest_at_least(self, bore_mm: float) -> PipeSize | None:
        for size in self.sorted_sizes():
            if size.internal_diameter >= bore_mm:
                return size
        return None


@dataclass
class SizingResult:
    """The outcome of sizing one pipe run."""

    size: PipeSize | None
    catalogue: PipeCatalogue
    flow_lps: float
    length_m: float
    velocity: float = 0.0
    reynolds: float = 0.0
    friction_factor: float = 0.0
    friction_loss: float = 0.0
    fitting_loss: float = 0.0
    static_loss: float = 0.0
    reasons: list[str] = field(default_factory=list)
    #: Sizes that were tried and rejected, with why.
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_loss(self) -> float:
        """Total pressure loss over the run [Pa]."""
        return self.friction_loss + self.fitting_loss + self.static_loss

    @property
    def loss_per_metre(self) -> float:
        return self.friction_loss / self.length_m if self.length_m > 0 else 0.0

    @property
    def ok(self) -> bool:
        return self.size is not None

    def describe(self) -> str:
        if self.size is None:
            return f"No size in {self.catalogue.name} satisfies the constraints"
        return (
            f"{self.size.designation} ({self.size.internal_diameter:.1f} mm bore) "
            f"at {self.velocity:.2f} m/s, {self.loss_per_metre:.0f} Pa/m, "
            f"{self.total_loss / 1000.0:.1f} kPa total"
        )


def size_pipe(
    flow_lps: float,
    length_m: float,
    catalogue: PipeCatalogue,
    *,
    service: ServiceType = ServiceType.COLD_WATER,
    limits: DesignLimits | None = None,
    fittings: dict[str, int] | None = None,
    height_gain_m: float = 0.0,
    available_pressure: float | None = None,
    fluid: Fluid | None = None,
) -> SizingResult:
    """Select the smallest catalogue size satisfying every constraint.

    Constraints applied, in order: maximum velocity, maximum friction loss per
    metre, and (when given) the total pressure available for the run.

    The minimum-velocity limit is reported as a warning rather than a rejection,
    because an oversized pipe still works — it just wastes money and may not
    scour — whereas an undersized one does not.
    """
    limits = limits or DesignLimits.for_service(service)
    fluid = fluid or Fluid()
    fitting_k = total_k(fittings or {})
    roughness = catalogue.effective_roughness

    result = SizingResult(
        size=None, catalogue=catalogue, flow_lps=flow_lps, length_m=length_m
    )
    if flow_lps <= 0:
        raise HydraulicsError("Flow rate must be positive to size a pipe", flow=flow_lps)

    for size in catalogue.sorted_sizes():
        bore = size.internal_diameter
        v = velocity(flow_lps, bore)

        if v > limits.max_velocity:
            result.rejected.append(
                (size.designation, f"velocity {v:.2f} m/s exceeds {limits.max_velocity:.2f} m/s")
            )
            continue

        friction = darcy_weisbach_loss(
            flow_lps, bore, length_m, roughness_mm=roughness, fluid=fluid
        )
        per_metre = friction / length_m if length_m > 0 else 0.0
        if per_metre > limits.max_loss_per_m:
            result.rejected.append(
                (size.designation, f"{per_metre:.0f} Pa/m exceeds {limits.max_loss_per_m:.0f} Pa/m")
            )
            continue

        minor = fitting_loss(flow_lps, bore, fitting_k, fluid)
        elevation = static_head(height_gain_m, fluid)
        total = friction + minor + elevation

        if available_pressure is not None and total > available_pressure:
            result.rejected.append(
                (
                    size.designation,
                    f"total loss {total / 1000.0:.1f} kPa exceeds the "
                    f"{available_pressure / 1000.0:.1f} kPa available",
                )
            )
            continue

        f, reynolds, _ = friction_factor(flow_lps, bore, roughness, fluid)
        result.size = size
        result.velocity = v
        result.reynolds = reynolds
        result.friction_factor = f
        result.friction_loss = friction
        result.fitting_loss = minor
        result.static_loss = elevation

        if v < limits.min_velocity:
            result.reasons.append(
                f"Velocity {v:.2f} m/s is below the {limits.min_velocity:.2f} m/s "
                "minimum; the line may not stay self-scouring."
            )
        return result

    result.reasons.append(
        "No catalogue size satisfies the constraints; increase the available "
        "pressure, shorten the run, or split the flow."
    )
    return result


# --------------------------------------------------------------------------- #
# Built-in catalogues
# --------------------------------------------------------------------------- #

def _sizes(rows: Iterable[tuple[str, float, float]]) -> list[PipeSize]:
    return [
        PipeSize(designation=d, outer_diameter=od, wall_thickness=wall)
        for d, od, wall in rows
    ]


#: EN 1057 copper tube, table X (the common installation wall).
COPPER_EN1057 = PipeCatalogue(
    id="copper-en1057",
    name="Copper EN 1057 (table X)",
    material="copper",
    sizes=_sizes(
        [
            ("12 mm", 12.0, 0.6), ("15 mm", 15.0, 0.7), ("22 mm", 22.0, 0.9),
            ("28 mm", 28.0, 0.9), ("35 mm", 35.0, 1.2), ("42 mm", 42.0, 1.2),
            ("54 mm", 54.0, 1.2), ("76 mm", 76.1, 1.5), ("108 mm", 108.0, 1.5),
        ]
    ),
)

#: PPR pipe, SDR 7.4 (PN20), typical for hot and cold domestic services.
PPR_PN20 = PipeCatalogue(
    id="ppr-pn20",
    name="PPR PN20 (SDR 7.4)",
    material="ppr",
    sizes=_sizes(
        [
            ("20 mm", 20.0, 2.8), ("25 mm", 25.0, 3.5), ("32 mm", 32.0, 4.4),
            ("40 mm", 40.0, 5.5), ("50 mm", 50.0, 6.9), ("63 mm", 63.0, 8.6),
            ("75 mm", 75.0, 10.3), ("90 mm", 90.0, 12.3), ("110 mm", 110.0, 15.1),
        ]
    ),
)

#: Galvanised steel to EN 10255 (medium series), still common on risers.
STEEL_EN10255 = PipeCatalogue(
    id="steel-en10255",
    name="Steel EN 10255 medium",
    material="steel_galvanised",
    sizes=_sizes(
        [
            ("DN15", 21.3, 2.6), ("DN20", 26.9, 2.6), ("DN25", 33.7, 3.2),
            ("DN32", 42.4, 3.2), ("DN40", 48.3, 3.2), ("DN50", 60.3, 3.6),
            ("DN65", 76.1, 3.6), ("DN80", 88.9, 4.0), ("DN100", 114.3, 4.5),
            ("DN150", 165.1, 4.5),
        ]
    ),
)

BUILTIN_CATALOGUES: dict[str, PipeCatalogue] = {
    catalogue.id: catalogue
    for catalogue in (COPPER_EN1057, PPR_PN20, STEEL_EN10255)
}


def get_catalogue(catalogue_id: str) -> PipeCatalogue:
    """Look up a catalogue, preferring registry (plugin) entries."""
    registered = PIPE_CATALOGUES.get_or_none(catalogue_id)
    if isinstance(registered, PipeCatalogue):
        return registered
    if catalogue_id in BUILTIN_CATALOGUES:
        return BUILTIN_CATALOGUES[catalogue_id]
    raise HydraulicsError(
        "Unknown pipe catalogue",
        catalogue=catalogue_id,
        available=sorted(set(BUILTIN_CATALOGUES) | set(PIPE_CATALOGUES.keys())),
    )


def register_catalogue(catalogue: PipeCatalogue) -> None:
    PIPE_CATALOGUES.add(catalogue.id, catalogue, version=catalogue.version, source="api")


def _validate_catalogue(document: dict[str, Any]) -> PipeCatalogue:
    return PipeCatalogue.model_validate(document)


PIPE_CATALOGUE_SCHEMA = DataSchema(
    kind="pipe_catalogue",
    model=_validate_catalogue,
    registry=PIPE_CATALOGUES,
    key_field="id",
    document_model=PipeCatalogue,
)


__all__ = [
    "ServiceType",
    "DesignLimits",
    "PipeSize",
    "PipeCatalogue",
    "SizingResult",
    "size_pipe",
    "COPPER_EN1057",
    "PPR_PN20",
    "STEEL_EN10255",
    "BUILTIN_CATALOGUES",
    "get_catalogue",
    "register_catalogue",
    "PIPE_CATALOGUE_SCHEMA",
]
