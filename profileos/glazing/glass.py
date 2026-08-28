"""Glass build-ups: geometry, weight and thermal transmittance.

A glazing unit is a stack of panes separated by gas-filled cavities. This
module models that stack and computes the numbers a facade quotation needs:
pane sizes, weight, and the centre-pane U-value.

Thermal method (EN 673)
-----------------------
The centre-pane transmittance is the reciprocal of the total thermal
resistance:

.. math::
    \\frac{1}{U} = R_{si} + R_{se} + \\sum_i \\frac{t_i}{\\lambda_{glass}}
                   + \\sum_j \\frac{1}{h_{s,j}}

Each cavity's conductance ``h_s`` is the sum of a radiative and a gas term:

.. math::
    h_r = 4 \\sigma T_m^3 \\left(\\frac{1}{\\varepsilon_1}
          + \\frac{1}{\\varepsilon_2} - 1\\right)^{-1}, \\qquad
    h_g = \\mathrm{Nu} \\frac{\\lambda_{gas}}{d}

The radiative term is what a low-emissivity coating attacks: dropping one
surface from ``ε = 0.837`` (uncoated) to ``0.03`` (soft coat) cuts ``h_r`` by
roughly an order of magnitude, which is why a coated double unit outperforms an
uncoated triple.

This is the **centre-pane** value. The whole-window ``U_w`` additionally needs
the frame transmittance and the linear thermal bridge at the glass edge
(EN ISO 10077-1); :func:`window_u_value` composes those.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ..models.base import RoundTrips

from ..core.errors import ProfileOSError

#: Stefan-Boltzmann constant [W/(m^2 K^4)].
SIGMA = 5.67e-8
#: Thermal conductivity of soda-lime glass [W/(m K)].
LAMBDA_GLASS = 1.0
#: Density of soda-lime glass [kg/m^3].
GLASS_DENSITY = 2500.0
#: Density of the PVB/EVA interlayer in laminated glass [kg/m^3].
INTERLAYER_DENSITY = 1070.0
#: Standard internal and external surface resistances [m^2 K/W] (EN 673).
R_SI = 0.13
R_SE = 0.04
#: Mean cavity temperature used by the EN 673 reference conditions [K].
T_MEAN = 283.0
#: Emissivity of an uncoated soda-lime glass surface.
UNCOATED_EMISSIVITY = 0.837


class GasType(StrEnum):
    """Cavity fill gases with their EN 673 property sets."""

    AIR = "air"
    ARGON = "argon"
    KRYPTON = "krypton"
    XENON = "xenon"


#: ``(conductivity [W/mK], density [kg/m^3], viscosity [kg/ms], specific heat [J/kgK])``
#: evaluated at the EN 673 mean cavity temperature.
GAS_PROPERTIES: dict[GasType, tuple[float, float, float, float]] = {
    GasType.AIR: (0.0253, 1.232, 1.761e-5, 1008.0),
    GasType.ARGON: (0.0164, 1.699, 2.164e-5, 519.0),
    GasType.KRYPTON: (0.00900, 3.560, 2.340e-5, 245.0),
    GasType.XENON: (0.00529, 5.689, 2.226e-5, 161.0),
}


class SpacerType(StrEnum):
    """Edge spacer, which sets the linear thermal bridge at the glass edge."""

    ALUMINIUM = "aluminium"
    STAINLESS = "stainless"
    WARM_EDGE = "warm_edge"
    THERMOPLASTIC = "thermoplastic"

    @property
    def psi_value(self) -> float:
        """Linear transmittance ``psi_g`` for an aluminium frame [W/(m K)].

        Representative EN ISO 10077-1 values; a project with certified data
        should override these from the system supplier's declarations.
        """
        return {
            "aluminium": 0.11,
            "stainless": 0.08,
            "warm_edge": 0.05,
            "thermoplastic": 0.04,
        }[self.value]


class CoatingPosition(StrEnum):
    """Which surface carries the low-emissivity coating.

    Surfaces are numbered from the outside in: 1 is the outdoor face of the
    outer pane, 2 its cavity face, and so on.
    """

    NONE = "none"
    SURFACE_2 = "2"
    SURFACE_3 = "3"
    SURFACE_5 = "5"


class Pane(BaseModel):
    """One glass ply."""

    model_config = ConfigDict(extra="forbid")

    thickness: float = Field(gt=0, description="Nominal thickness [mm]")
    laminated: bool = False
    #: Total interlayer thickness for a laminated ply [mm], e.g. 2 x 0.38 PVB.
    interlayer_thickness: float = Field(default=0.0, ge=0)
    toughened: bool = False
    heat_strengthened: bool = False
    #: Emissivity of the outward-facing surface of this pane.
    emissivity_outer: float = Field(default=UNCOATED_EMISSIVITY, gt=0, le=1)
    #: Emissivity of the inward-facing surface of this pane.
    emissivity_inner: float = Field(default=UNCOATED_EMISSIVITY, gt=0, le=1)
    tint: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _check_laminate(self) -> "Pane":
        if self.laminated and self.interlayer_thickness <= 0:
            raise ValueError("a laminated pane needs a positive interlayer thickness")
        return self

    @property
    def total_thickness(self) -> float:
        """Glass plus interlayer [mm]."""
        return self.thickness + self.interlayer_thickness

    @property
    def is_safety_glass(self) -> bool:
        """True when the ply satisfies safety-glass requirements.

        Toughened glass fragments harmlessly; laminated glass retains its
        fragments on the interlayer. Heat-strengthened glass alone does not
        qualify — it is stronger but still breaks into sharp pieces.
        """
        return self.toughened or self.laminated

    @property
    def mass_per_m2(self) -> float:
        """Areal mass [kg/m^2]."""
        glass = self.thickness / 1000.0 * GLASS_DENSITY
        interlayer = self.interlayer_thickness / 1000.0 * INTERLAYER_DENSITY
        return glass + interlayer

    def resistance(self) -> float:
        """Conductive resistance of the ply [m^2 K/W]."""
        # The interlayer's conductivity is close enough to glass that treating
        # the ply as solid glass changes U by well under 1%.
        return self.total_thickness / 1000.0 / LAMBDA_GLASS

    def describe(self) -> str:
        parts = [f"{self.thickness:g}mm"]
        if self.laminated:
            parts.append(f"lam({self.interlayer_thickness:g})")
        if self.toughened:
            parts.append("TGH")
        elif self.heat_strengthened:
            parts.append("HS")
        if min(self.emissivity_inner, self.emissivity_outer) < 0.2:
            parts.append("lowE")
        return " ".join(parts)


class Cavity(BaseModel):
    """A gas-filled gap between two panes."""

    model_config = ConfigDict(extra="forbid")

    width: float = Field(gt=0, description="Cavity width [mm]")
    gas: GasType = GasType.ARGON
    #: Gas fill purity, 0..1. A 90% argon fill is the practical norm.
    fill_ratio: float = Field(default=0.90, ge=0.0, le=1.0)

    def effective_properties(self) -> tuple[float, float, float, float]:
        """Gas properties blended between the fill gas and residual air."""
        fill = GAS_PROPERTIES[self.gas]
        air = GAS_PROPERTIES[GasType.AIR]
        ratio = self.fill_ratio
        return tuple(f * ratio + a * (1.0 - ratio) for f, a in zip(fill, air))  # type: ignore[return-value]

    def conductance(self, emissivity_1: float, emissivity_2: float) -> float:
        """Total cavity conductance ``h_s = h_r + h_g`` [W/(m^2 K)]."""
        # Radiative exchange between the two bounding surfaces.
        h_r = (
            4.0
            * SIGMA
            * T_MEAN**3
            / (1.0 / emissivity_1 + 1.0 / emissivity_2 - 1.0)
        )

        conductivity, density, viscosity, specific_heat = self.effective_properties()
        gap = self.width / 1000.0

        # Nusselt number from the Rayleigh number (EN 673 vertical cavity).
        # dT = 15 K and the gravitational term are the standard reference values.
        rayleigh = (
            density**2
            * gap**3
            * 9.81
            * specific_heat
            * 15.0
            / (T_MEAN * viscosity * conductivity)
        )
        nusselt = max(1.0, 0.035 * rayleigh**0.38)
        h_g = nusselt * conductivity / gap
        return h_r + h_g

    @property
    def mass_per_m2(self) -> float:
        """Gas mass is negligible but included for completeness [kg/m^2]."""
        _, density, _, _ = self.effective_properties()
        return self.width / 1000.0 * density


class GlassBuildUp(RoundTrips):
    """A complete glazing unit: panes separated by cavities."""

    model_config = ConfigDict(extra="forbid")

    id: str = "default"
    name: str = "Glazing"
    panes: list[Pane] = Field(min_length=1)
    cavities: list[Cavity] = Field(default_factory=list)
    spacer: SpacerType = SpacerType.WARM_EDGE
    #: Solar factor (g-value) and light transmittance, from the supplier's data.
    g_value: float | None = Field(default=None, ge=0, le=1)
    light_transmittance: float | None = Field(default=None, ge=0, le=1)
    price_per_m2: float | None = Field(default=None, ge=0)
    currency: str = "EUR"

    @model_validator(mode="after")
    def _check_structure(self) -> "GlassBuildUp":
        if len(self.cavities) != len(self.panes) - 1:
            raise ValueError(
                f"a {len(self.panes)}-pane unit needs {len(self.panes) - 1} "
                f"cavities, got {len(self.cavities)}"
            )
        return self

    # -- geometry ----------------------------------------------------------- #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_thickness(self) -> float:
        """Overall unit thickness [mm] — must fit the system's rebate."""
        return sum(p.total_thickness for p in self.panes) + sum(c.width for c in self.cavities)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mass_per_m2(self) -> float:
        """Areal mass [kg/m^2], which drives hardware and handling limits."""
        return sum(p.mass_per_m2 for p in self.panes) + sum(c.mass_per_m2 for c in self.cavities)

    @property
    def pane_count(self) -> int:
        return len(self.panes)

    @property
    def is_insulating(self) -> bool:
        return len(self.panes) > 1

    def mass(self, width: float, height: float) -> float:
        """Mass of one pane of the given size [kg]."""
        return self.mass_per_m2 * (width * height / 1_000_000.0)

    # -- thermal ------------------------------------------------------------ #
    def u_value(self) -> float:
        """Centre-pane thermal transmittance ``U_g`` [W/(m^2 K)]."""
        resistance = R_SI + R_SE + sum(pane.resistance() for pane in self.panes)

        for index, cavity in enumerate(self.cavities):
            # The cavity is bounded by the inner surface of the pane before it
            # and the outer surface of the pane after it.
            emissivity_1 = self.panes[index].emissivity_inner
            emissivity_2 = self.panes[index + 1].emissivity_outer
            conductance = cavity.conductance(emissivity_1, emissivity_2)
            if conductance <= 0:  # pragma: no cover - defensive
                raise ProfileOSError("Non-physical cavity conductance", cavity=index)
            resistance += 1.0 / conductance

        return 1.0 / resistance

    @property
    def is_safety_glass(self) -> bool:
        """True when **both** outer faces are safety glass.

        A unit is only safe against human impact if the pane a person can
        strike is safety glass on the side they strike it from; for an
        insulating unit accessible from inside and out, that means both.
        """
        if len(self.panes) == 1:
            return self.panes[0].is_safety_glass
        return self.panes[0].is_safety_glass and self.panes[-1].is_safety_glass

    def describe(self) -> str:
        parts: list[str] = []
        for index, pane in enumerate(self.panes):
            parts.append(pane.describe())
            if index < len(self.cavities):
                cavity = self.cavities[index]
                parts.append(f"/{cavity.width:g}{cavity.gas.value[:2].upper()}/")
        return " ".join(parts)


def area_weighted_u(
    *,
    glass_area: float,
    glass_u: float,
    frame_area: float,
    frame_u: float,
    perimeter: float,
    psi: float,
) -> float:
    """Whole-window transmittance ``U_w`` per EN ISO 10077-1.

    .. math::
        U_w = \\frac{A_g U_g + A_f U_f + l_g \\Psi_g}{A_g + A_f}

    Areas in m^2, perimeter in m. The ``psi`` term is the edge-of-glass
    thermal bridge, which is why the spacer choice shows up in the window
    value even though it never appears in the centre-pane one.

    The arguments are already-summed quantities rather than a build-up, so an
    element glazed with several different units weights them all through the
    one formula instead of a second copy of it living somewhere else.
    """
    total_area = glass_area + frame_area
    if total_area <= 0:
        raise ProfileOSError("Window area must be positive to compute U_w")
    return (
        glass_area * glass_u + frame_area * frame_u + perimeter * psi
    ) / total_area


def window_u_value(
    glass: GlassBuildUp,
    *,
    glass_area: float,
    frame_area: float,
    perimeter: float,
    frame_u_value: float = 2.2,
) -> float:
    """``U_w`` for a window glazed throughout with one build-up."""
    return area_weighted_u(
        glass_area=glass_area,
        glass_u=glass.u_value(),
        frame_area=frame_area,
        frame_u=frame_u_value,
        perimeter=perimeter,
        psi=glass.spacer.psi_value,
    )


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #

def _pane(thickness: float, **kwargs: Any) -> Pane:
    return Pane(thickness=thickness, **kwargs)


def make_double_glazing(
    outer: float = 6.0,
    cavity: float = 16.0,
    inner: float = 4.0,
    *,
    gas: GasType = GasType.ARGON,
    low_e_emissivity: float = 0.03,
    spacer: SpacerType = SpacerType.WARM_EDGE,
    toughened: bool = False,
) -> GlassBuildUp:
    """A standard double-glazed unit with a low-E coating on surface 3."""
    return GlassBuildUp(
        id=f"dgu-{outer:g}-{cavity:g}-{inner:g}",
        name=f"זיגוג כפול ⁦{outer:g}/{cavity:g}/{inner:g}⁩",
        panes=[
            _pane(outer, toughened=toughened),
            # Surface 3 is the cavity-facing surface of the inner pane.
            _pane(inner, toughened=toughened, emissivity_outer=low_e_emissivity),
        ],
        cavities=[Cavity(width=cavity, gas=gas)],
        spacer=spacer,
    )


def make_triple_glazing(
    outer: float = 4.0,
    cavity_1: float = 14.0,
    middle: float = 4.0,
    cavity_2: float = 14.0,
    inner: float = 4.0,
    *,
    gas: GasType = GasType.ARGON,
    low_e_emissivity: float = 0.03,
    spacer: SpacerType = SpacerType.WARM_EDGE,
) -> GlassBuildUp:
    """A triple unit with low-E coatings on surfaces 2 and 5."""
    return GlassBuildUp(
        id=f"tgu-{outer:g}-{cavity_1:g}-{middle:g}-{cavity_2:g}-{inner:g}",
        name=f"זיגוג משולש ⁦{outer:g}/{cavity_1:g}/{middle:g}/{cavity_2:g}/{inner:g}⁩",
        panes=[
            _pane(outer, emissivity_inner=low_e_emissivity),
            _pane(middle),
            _pane(inner, emissivity_outer=low_e_emissivity),
        ],
        cavities=[Cavity(width=cavity_1, gas=gas), Cavity(width=cavity_2, gas=gas)],
        spacer=spacer,
    )


def make_laminated(
    plies: tuple[float, float] = (4.0, 4.0), interlayer: float = 0.76
) -> Pane:
    """A laminated safety ply, e.g. 44.2 (two 4 mm plies, 0.76 mm PVB)."""
    return Pane(
        thickness=plies[0] + plies[1],
        laminated=True,
        interlayer_thickness=interlayer,
        name=f"{plies[0]:g}{plies[1]:g}.{round(interlayer / 0.38)}",
    )


def make_monolithic(thickness: float = 6.0, *, toughened: bool = False) -> GlassBuildUp:
    """A single pane, as used for internal screens and shopfront infill."""
    return GlassBuildUp(
        id=f"mono-{thickness:g}",
        name=f"⁦{thickness:g}⁩ מ\"מ חד-שכבתי",
        panes=[_pane(thickness, toughened=toughened)],
        cavities=[],
    )


#: Ready-made build-ups covering the common specification points.
def make_laminated_unit(
    plies: tuple[float, float] = (6.0, 6.0), interlayer: float = 0.76
) -> GlassBuildUp:
    """A single laminated unit, as a balustrade or an overhead pane needs.

    Laminated is not the same safety as toughened and they are not
    interchangeable: toughened glass breaks into blunt pieces, laminated glass
    holds together on its interlayer and keeps standing. A balustrade is a
    barrier, so it has to keep standing — which is why this is here rather
    than being approximated with a toughened monolithic pane.
    """
    ply = make_laminated(plies, interlayer)
    return GlassBuildUp(
        id=f"lam-{plies[0]:g}{plies[1]:g}-{round(interlayer / 0.38)}",
        name=f"למינציה {ply.name}",
        panes=[ply],
        cavities=[],
    )


STANDARD_BUILDUPS: dict[str, GlassBuildUp] = {
    build.id: build
    for build in (
        make_monolithic(6.0, toughened=True),
        make_double_glazing(),
        make_double_glazing(6.0, 16.0, 6.0, toughened=True),
        make_triple_glazing(),
        make_laminated_unit(),
    )
}


__all__ = [
    "SIGMA",
    "LAMBDA_GLASS",
    "GLASS_DENSITY",
    "R_SI",
    "R_SE",
    "GasType",
    "GAS_PROPERTIES",
    "SpacerType",
    "CoatingPosition",
    "Pane",
    "Cavity",
    "GlassBuildUp",
    "window_u_value",
    "area_weighted_u",
    "make_double_glazing",
    "make_triple_glazing",
    "make_laminated",
    "make_monolithic",
    "make_laminated_unit",
    "STANDARD_BUILDUPS",
]
