"""Design verification for facade members.

Implements the checks a curtain-wall or window engineer runs on a mullion,
transom or frame member once its section properties are known:

* **Bending resistance** — EN 1999-1-1 §6.2.5, ``M_Ed <= M_Rd = alpha W f_o / gamma_M1``.
* **Shear resistance** — EN 1999-1-1 §6.2.6.
* **Deflection** — serviceability against the span/ratio and absolute limits
  that facade specifications impose (EN 13830 for curtain walling).
* **Combined bending and axial force** — the linear interaction used for
  mullions carrying dead load from infill panels.

Loads are characteristic values; partial factors are applied here. Wind
pressure is in kN/m^2 (the unit facade specifications use), converted
internally to N/mm^2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from ..core.errors import StructuralError
from ..models.materials import Material
from ..models.results import SectionProperties


class SupportCondition(StrEnum):
    """Span support arrangement, which sets the moment and deflection factors."""

    SIMPLY_SUPPORTED = "simply_supported"
    CANTILEVER = "cantilever"
    FIXED_BOTH_ENDS = "fixed_both_ends"
    PROPPED_CANTILEVER = "propped_cantilever"

    @property
    def moment_factor(self) -> float:
        """``k`` in ``M_max = k w L^2`` for a uniformly distributed load."""
        return {
            "simply_supported": 1.0 / 8.0,
            "cantilever": 1.0 / 2.0,
            "fixed_both_ends": 1.0 / 12.0,
            "propped_cantilever": 9.0 / 128.0,
        }[self.value]

    @property
    def deflection_factor(self) -> float:
        """``k`` in ``delta_max = k w L^4 / (E I)`` for a uniformly distributed load."""
        return {
            "simply_supported": 5.0 / 384.0,
            "cantilever": 1.0 / 8.0,
            "fixed_both_ends": 1.0 / 384.0,
            "propped_cantilever": 1.0 / 185.0,
        }[self.value]

    @property
    def shear_factor(self) -> float:
        """``k`` in ``V_max = k w L``."""
        return {
            "simply_supported": 0.5,
            "cantilever": 1.0,
            "fixed_both_ends": 0.5,
            "propped_cantilever": 5.0 / 8.0,
        }[self.value]


@dataclass
class DeflectionLimit:
    """Serviceability deflection limits for a facade member.

    ``span_ratio`` of 175 means the limit is ``L/175``; the governing limit is
    the smaller of that and ``absolute_mm``. EN 13830 commonly gives L/200 with
    a 15 mm cap for single-span members carrying glass.
    """

    span_ratio: float = 200.0
    absolute_mm: float = 15.0

    def limit_for(self, span_mm: float) -> float:
        return min(span_mm / self.span_ratio, self.absolute_mm)


@dataclass
class CheckResult:
    """One verification: the demand, the capacity and the utilisation."""

    name: str
    demand: float
    capacity: float
    unit: str
    #: ``demand / capacity``; <= 1.0 passes.
    utilisation: float = 0.0
    detail: str = ""

    def __post_init__(self) -> None:
        if self.capacity > 0:
            self.utilisation = self.demand / self.capacity

    @property
    def passes(self) -> bool:
        return self.utilisation <= 1.0 + 1e-9

    def __str__(self) -> str:  # pragma: no cover - presentation only
        verdict = "PASS" if self.passes else "FAIL"
        return (
            f"{self.name}: {self.demand:.4g} / {self.capacity:.4g} {self.unit} "
            f"= {self.utilisation * 100:.1f}% [{verdict}]"
        )


@dataclass
class MemberCheck:
    """The full set of checks for one member under one load case."""

    member: str
    span: float
    results: list[CheckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return all(r.passes for r in self.results)

    @property
    def governing(self) -> CheckResult | None:
        """The check with the highest utilisation."""
        return max(self.results, key=lambda r: r.utilisation, default=None)

    @property
    def max_utilisation(self) -> float:
        governing = self.governing
        return governing.utilisation if governing else 0.0

    def add(self, result: CheckResult) -> None:
        self.results.append(result)


def wind_line_load(pressure_kn_m2: float, tributary_width_mm: float) -> float:
    """Convert a facade wind pressure into a line load on a member.

    Parameters
    ----------
    pressure_kn_m2:
        Characteristic wind pressure [kN/m^2].
    tributary_width_mm:
        The width of facade the member carries — for a mullion, the average of
        the bay widths either side [mm].

    Returns
    -------
    float
        Line load in N/mm (equivalently kN/m), which is the unit the moment and
        deflection formulae below expect.
    """
    # kN/m^2 -> N/mm^2 is 1e-3; multiplied by a width in mm gives N/mm.
    return pressure_kn_m2 * 1e-3 * tributary_width_mm


@dataclass
class LoadCase:
    """A characteristic load case acting on a member."""

    name: str = "Wind"
    #: Lateral line load [N/mm], usually from wind on the tributary width.
    lateral_line_load: float = 0.0
    #: Axial compression [N], e.g. self weight of panels carried by a mullion.
    axial_force: float = 0.0
    #: Partial factor on the lateral action for the ultimate limit state.
    gamma_wind: float = 1.5
    #: Partial factor on permanent actions.
    gamma_permanent: float = 1.35


def check_member(
    properties: SectionProperties,
    material: Material,
    *,
    span: float,
    load: LoadCase,
    support: SupportCondition = SupportCondition.SIMPLY_SUPPORTED,
    deflection_limit: DeflectionLimit | None = None,
    bending_axis: str = "x",
    member_name: str = "member",
) -> MemberCheck:
    """Verify a member for bending, shear, deflection and combined actions.

    Parameters
    ----------
    properties:
        Section properties from :func:`~profileos.structural.analyse_section`.
    span:
        Member span between supports [mm].
    bending_axis:
        ``"x"`` when the load bends the section about its x axis (the usual
        case for a mullion resisting wind), otherwise ``"y"``.

    Raises
    ------
    StructuralError
        The section lacks the modulus or inertia needed for the requested axis.
    """
    check = MemberCheck(member=member_name, span=span)
    limits = deflection_limit or DeflectionLimit()

    if span <= 0:
        raise StructuralError("Member span must be positive", span=span)

    if bending_axis.lower() == "x":
        inertia, modulus, plastic = properties.ixx, properties.sx, properties.zx
    else:
        inertia, modulus, plastic = properties.iyy, properties.sy, properties.zy

    if inertia <= 0 or modulus <= 0:
        raise StructuralError(
            "Section has no bending capacity about the requested axis",
            axis=bending_axis,
            inertia=inertia,
            modulus=modulus,
        )

    w_char = load.lateral_line_load
    w_ultimate = w_char * load.gamma_wind

    # -- bending ------------------------------------------------------------ #
    moment_ed = support.moment_factor * w_ultimate * span**2  # N.mm
    # alpha is the shape factor, capped at the plastic value; class 1/2 sections
    # may use Z, slender ones are limited to the elastic modulus.
    alpha = 1.0
    if plastic is not None and modulus > 0:
        alpha = min(plastic / modulus, 1.25)
    moment_rd = alpha * modulus * material.yield_strength / material.gamma_m1
    check.add(
        CheckResult(
            name=f"Bending about {bending_axis}",
            demand=moment_ed / 1e6,
            capacity=moment_rd / 1e6,
            unit="kN.m",
            detail=(
                f"M_Ed = {support.moment_factor:.4g} w L^2, "
                f"M_Rd = {alpha:.3f} W f_o / {material.gamma_m1}"
            ),
        )
    )

    # -- shear -------------------------------------------------------------- #
    shear_ed = support.shear_factor * w_ultimate * span  # N
    # EN 1999-1-1: V_Rd = A_v f_o / (sqrt(3) gamma_M1). Without a web-area
    # breakdown, half the section area is the conventional estimate for a
    # hollow extrusion with two webs.
    shear_area = properties.area * 0.5
    shear_rd = shear_area * material.yield_strength / (math.sqrt(3.0) * material.gamma_m1)
    check.add(
        CheckResult(
            name="Shear",
            demand=shear_ed / 1e3,
            capacity=shear_rd / 1e3,
            unit="kN",
            detail="A_v taken as 0.5 A; refine with an explicit web area if needed.",
        )
    )
    check.warnings.append(
        "Shear area approximated as 50% of the gross area."
    )

    # -- deflection (serviceability, characteristic loads) ------------------ #
    deflection = (
        support.deflection_factor * w_char * span**4 / (material.elastic_modulus * inertia)
    )
    allowed = limits.limit_for(span)
    check.add(
        CheckResult(
            name="Deflection",
            demand=deflection,
            capacity=allowed,
            unit="mm",
            detail=f"limit = min(L/{limits.span_ratio:g}, {limits.absolute_mm:g} mm)",
        )
    )

    # -- combined bending and axial ----------------------------------------- #
    if load.axial_force > 0:
        axial_ed = load.axial_force * load.gamma_permanent
        axial_rd = properties.area * material.yield_strength / material.gamma_m1
        # Linear interaction, conservative for class 1/2 sections.
        interaction = axial_ed / axial_rd + moment_ed / moment_rd
        check.add(
            CheckResult(
                name="Combined N + M",
                demand=interaction,
                capacity=1.0,
                unit="-",
                detail="N_Ed/N_Rd + M_Ed/M_Rd <= 1.0",
            )
        )

        # Flexural buckling about the weak axis, EN 1999-1-1 Annex.
        slenderness = properties.slenderness(span, axis="y")
        if slenderness is not None and slenderness > 0:
            lambda_1 = math.pi * math.sqrt(material.elastic_modulus / material.yield_strength)
            lambda_rel = slenderness / lambda_1
            # Perry-Robertson style reduction with alpha = 0.20, lambda_0 = 0.10
            # (aluminium buckling class A).
            phi = 0.5 * (1 + 0.20 * (lambda_rel - 0.10) + lambda_rel**2)
            chi = min(1.0, 1.0 / (phi + math.sqrt(max(phi**2 - lambda_rel**2, 0.0))))
            check.add(
                CheckResult(
                    name="Flexural buckling",
                    demand=axial_ed / 1e3,
                    capacity=chi * axial_rd / 1e3,
                    unit="kN",
                    detail=f"lambda_rel = {lambda_rel:.3f}, chi = {chi:.3f}",
                )
            )

    if properties.j is None:
        check.warnings.append(
            "Torsion constant unavailable; lateral-torsional buckling not verified."
        )

    return check


def maximum_span(
    properties: SectionProperties,
    material: Material,
    *,
    pressure_kn_m2: float,
    tributary_width_mm: float,
    support: SupportCondition = SupportCondition.SIMPLY_SUPPORTED,
    deflection_limit: DeflectionLimit | None = None,
    bending_axis: str = "x",
    search_max: float = 12000.0,
    tolerance: float = 0.5,
) -> float:
    """Largest span at which every check still passes [mm].

    Bisects on span, which is valid because utilisation increases monotonically
    with span for a uniformly distributed load.
    """
    load = LoadCase(lateral_line_load=wind_line_load(pressure_kn_m2, tributary_width_mm))

    def passes(span: float) -> bool:
        try:
            return check_member(
                properties,
                material,
                span=span,
                load=load,
                support=support,
                deflection_limit=deflection_limit,
                bending_axis=bending_axis,
            ).passes
        except StructuralError:
            return False

    low, high = 1.0, search_max
    if passes(high):
        return high
    if not passes(low):
        return 0.0

    while high - low > tolerance:
        mid = 0.5 * (low + high)
        if passes(mid):
            low = mid
        else:
            high = mid
    return low


__all__ = [
    "SupportCondition",
    "DeflectionLimit",
    "CheckResult",
    "MemberCheck",
    "LoadCase",
    "wind_line_load",
    "check_member",
    "maximum_span",
]
