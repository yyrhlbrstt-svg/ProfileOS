"""Pipe hydraulics: friction, fittings and pressure loss.

Implements the standard closed-conduit methods:

**Darcy-Weisbach** is the physically general one and the default here:

.. math:: \\Delta p = f \\frac{L}{D} \\frac{\\rho v^2}{2}

The friction factor ``f`` comes from the **Colebrook-White** equation

.. math::
    \\frac{1}{\\sqrt{f}} = -2 \\log_{10}\\!\\left(
        \\frac{\\varepsilon/D}{3.7} + \\frac{2.51}{Re\\sqrt{f}}\\right)

which is implicit in ``f``. It is solved here by fixed-point iteration seeded
with the **Swamee-Jain** explicit approximation, which puts the start within
about 1% and makes convergence take three or four iterations.

**Hazen-Williams** is also provided because water-services codes still specify
it, but it is only valid for water near 15 degC in the turbulent range, so
:func:`hazen_williams_loss` says so rather than pretending to generality.

Units follow the package convention: lengths in mm for geometry, but SI
internally for the physics (metres, m/s, Pa).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..core.errors import HydraulicsError

#: Acceleration due to gravity [m/s^2].
G = 9.80665


class FlowRegime(StrEnum):
    """Flow regime, which decides which friction law applies."""

    LAMINAR = "laminar"
    TRANSITIONAL = "transitional"
    TURBULENT = "turbulent"

    @classmethod
    def of(cls, reynolds: float) -> "FlowRegime":
        if reynolds < 2300.0:
            return cls.LAMINAR
        if reynolds < 4000.0:
            return cls.TRANSITIONAL
        return cls.TURBULENT


@dataclass(frozen=True)
class Fluid:
    """A working fluid at a stated temperature."""

    name: str = "water"
    #: Density [kg/m^3].
    density: float = 998.2
    #: Dynamic viscosity [Pa.s].
    dynamic_viscosity: float = 1.002e-3
    temperature: float = 20.0
    #: Vapour pressure [Pa], used for cavitation checks on pump suction.
    vapour_pressure: float = 2339.0

    @property
    def kinematic_viscosity(self) -> float:
        """nu = mu / rho [m^2/s]."""
        return self.dynamic_viscosity / self.density


def water_at(temperature_c: float) -> Fluid:
    """Water properties at a given temperature, interpolated from tables.

    Covers 0-100 degC, which spans domestic cold, hot and heating circuits.
    """
    if not (0.0 <= temperature_c <= 100.0):
        raise HydraulicsError(
            "Water property data covers 0-100 degC", temperature=temperature_c
        )

    # (temperature, density kg/m^3, dynamic viscosity Pa.s)
    table = [
        (0.0, 999.8, 1.792e-3),
        (10.0, 999.7, 1.307e-3),
        (20.0, 998.2, 1.002e-3),
        (30.0, 995.6, 0.798e-3),
        (40.0, 992.2, 0.653e-3),
        (50.0, 988.0, 0.547e-3),
        (60.0, 983.2, 0.467e-3),
        (70.0, 977.8, 0.404e-3),
        (80.0, 971.8, 0.355e-3),
        (90.0, 965.3, 0.315e-3),
        (100.0, 958.4, 0.282e-3),
    ]

    for (t0, d0, m0), (t1, d1, m1) in zip(table, table[1:]):
        if t0 <= temperature_c <= t1:
            fraction = (temperature_c - t0) / (t1 - t0)
            return Fluid(
                name="water",
                density=d0 + (d1 - d0) * fraction,
                dynamic_viscosity=m0 + (m1 - m0) * fraction,
                temperature=temperature_c,
            )
    raise HydraulicsError("Temperature outside the table", temperature=temperature_c)


#: Absolute roughness [mm] for common pipe materials.
ROUGHNESS_MM: dict[str, float] = {
    "copper": 0.0015,
    "pex": 0.007,
    "ppr": 0.007,
    "pvc": 0.0015,
    "hdpe": 0.007,
    "stainless": 0.015,
    "steel_galvanised": 0.15,
    "steel_new": 0.045,
    "steel_old": 0.5,
    "cast_iron": 0.26,
    "concrete": 1.0,
}


def velocity(flow_lps: float, internal_diameter_mm: float) -> float:
    """Mean flow velocity [m/s] from flow rate and bore."""
    if internal_diameter_mm <= 0:
        raise HydraulicsError("Internal diameter must be positive", diameter=internal_diameter_mm)
    area = math.pi * (internal_diameter_mm / 1000.0) ** 2 / 4.0
    return (flow_lps / 1000.0) / area


def reynolds_number(
    flow_lps: float, internal_diameter_mm: float, fluid: Fluid | None = None
) -> float:
    """``Re = v D / nu`` [-]."""
    fluid = fluid or Fluid()
    v = velocity(flow_lps, internal_diameter_mm)
    return v * (internal_diameter_mm / 1000.0) / fluid.kinematic_viscosity


def swamee_jain(relative_roughness: float, reynolds: float) -> float:
    """Explicit friction-factor approximation, used to seed Colebrook-White."""
    if reynolds <= 0:
        return 0.0
    denominator = math.log10(relative_roughness / 3.7 + 5.74 / reynolds**0.9)
    return 0.25 / denominator**2


def colebrook_white(
    relative_roughness: float,
    reynolds: float,
    *,
    tolerance: float = 1e-10,
    max_iterations: int = 50,
) -> float:
    """Darcy friction factor from the Colebrook-White equation.

    Laminar flow uses the exact ``f = 64 / Re`` instead, since Colebrook-White
    is only valid in the turbulent range.
    """
    if reynolds <= 0:
        return 0.0
    if reynolds < 2300.0:
        return 64.0 / reynolds

    # Seed with Swamee-Jain, then iterate the implicit form to convergence.
    f = swamee_jain(relative_roughness, reynolds)
    for _ in range(max_iterations):
        inverse_sqrt = -2.0 * math.log10(
            relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(f))
        )
        new_f = 1.0 / inverse_sqrt**2
        if abs(new_f - f) < tolerance:
            return new_f
        f = new_f
    return f


def friction_factor(
    flow_lps: float,
    internal_diameter_mm: float,
    roughness_mm: float,
    fluid: Fluid | None = None,
) -> tuple[float, float, FlowRegime]:
    """Return ``(friction_factor, reynolds, regime)``."""
    reynolds = reynolds_number(flow_lps, internal_diameter_mm, fluid)
    relative = roughness_mm / internal_diameter_mm
    return colebrook_white(relative, reynolds), reynolds, FlowRegime.of(reynolds)


def darcy_weisbach_loss(
    flow_lps: float,
    internal_diameter_mm: float,
    length_m: float,
    *,
    roughness_mm: float = 0.0015,
    fluid: Fluid | None = None,
) -> float:
    """Straight-pipe friction loss [Pa]."""
    fluid = fluid or Fluid()
    if length_m < 0:
        raise HydraulicsError("Pipe length cannot be negative", length=length_m)
    if flow_lps == 0:
        return 0.0

    f, _, _ = friction_factor(flow_lps, internal_diameter_mm, roughness_mm, fluid)
    v = velocity(flow_lps, internal_diameter_mm)
    diameter_m = internal_diameter_mm / 1000.0
    return f * (length_m / diameter_m) * (fluid.density * v * v / 2.0)


def hazen_williams_loss(
    flow_lps: float,
    internal_diameter_mm: float,
    length_m: float,
    *,
    c_factor: float = 130.0,
) -> float:
    """Hazen-Williams friction loss [Pa].

    Empirical and restricted to water at ordinary temperatures in turbulent
    flow. Kept because water-services codes still specify it, but
    :func:`darcy_weisbach_loss` is preferred for anything else.

    .. math::
        h_f = 10.67 \\frac{L Q^{1.852}}{C^{1.852} D^{4.87}}

    with ``Q`` in m^3/s and ``D`` in m, giving head in metres.
    """
    if c_factor <= 0:
        raise HydraulicsError("Hazen-Williams C factor must be positive", c=c_factor)
    if flow_lps == 0:
        return 0.0

    q = flow_lps / 1000.0
    d = internal_diameter_mm / 1000.0
    head_m = 10.67 * length_m * q**1.852 / (c_factor**1.852 * d**4.87)
    # Convert head of water to pressure with the standard reference density.
    return head_m * 1000.0 * G


#: Loss coefficients ``K`` for common fittings, in velocity heads.
FITTING_K: dict[str, float] = {
    "elbow_90_long": 0.30,
    "elbow_90_short": 0.90,
    "elbow_45": 0.40,
    "tee_through": 0.20,
    "tee_branch": 1.00,
    "gate_valve_open": 0.15,
    "globe_valve_open": 10.0,
    "ball_valve_open": 0.05,
    "check_valve_swing": 2.00,
    "check_valve_spring": 4.50,
    "butterfly_valve_open": 0.40,
    "strainer": 2.00,
    "entrance_sharp": 0.50,
    "entrance_rounded": 0.04,
    "exit": 1.00,
    "reducer": 0.25,
    "expander": 0.35,
    "water_meter": 7.00,
}


def fitting_loss(
    flow_lps: float,
    internal_diameter_mm: float,
    k_total: float,
    fluid: Fluid | None = None,
) -> float:
    """Minor (fitting) loss [Pa] from the total K value.

    .. math:: \\Delta p = K \\frac{\\rho v^2}{2}
    """
    fluid = fluid or Fluid()
    v = velocity(flow_lps, internal_diameter_mm)
    return k_total * fluid.density * v * v / 2.0


def total_k(fittings: dict[str, int]) -> float:
    """Sum the K values of a fitting schedule like ``{"elbow_90_long": 4}``.

    Raises
    ------
    HydraulicsError
        A fitting name is not in :data:`FITTING_K`.
    """
    total = 0.0
    for name, count in fittings.items():
        key = name.strip().lower()
        if key not in FITTING_K:
            raise HydraulicsError(
                "Unknown fitting type", fitting=name, known=sorted(FITTING_K)
            )
        total += FITTING_K[key] * count
    return total


def static_head(height_difference_m: float, fluid: Fluid | None = None) -> float:
    """Pressure change from elevation [Pa]; positive means a rise costs pressure."""
    fluid = fluid or Fluid()
    return fluid.density * G * height_difference_m


def pressure_to_head(pressure_pa: float, fluid: Fluid | None = None) -> float:
    """Convert a pressure to a head of the given fluid [m]."""
    fluid = fluid or Fluid()
    return pressure_pa / (fluid.density * G)


__all__ = [
    "G",
    "FlowRegime",
    "Fluid",
    "water_at",
    "ROUGHNESS_MM",
    "FITTING_K",
    "velocity",
    "reynolds_number",
    "swamee_jain",
    "colebrook_white",
    "friction_factor",
    "darcy_weisbach_loss",
    "hazen_williams_loss",
    "fitting_loss",
    "total_k",
    "static_head",
    "pressure_to_head",
]
