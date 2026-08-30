"""Unit handling.

ProfileOS works internally in a single canonical system so no engine ever has
to ask "which unit is this?":

======================  ==================
Quantity                Internal unit
======================  ==================
length                  millimetre (mm)
area                    mm^2
second moment of area   mm^4
warping constant        mm^6
angle                   degree (public API) / radian (internal maths)
force                   newton (N)
stress / modulus        megapascal (MPa = N/mm^2)
mass                    kilogram (kg)
density                 kg/m^3
pressure (fluids)       pascal (Pa)
flow rate               litre/second (L/s)
======================  ==================

Conversions in and out of that canonical system live here. DXF files carry an
``$INSUNITS`` header variable, which :func:`dxf_insunits_to_mm` maps to a
scale factor.
"""

from __future__ import annotations

import math
from enum import IntEnum

# --------------------------------------------------------------------------- #
# Length
# --------------------------------------------------------------------------- #

#: Multiply a value in the keyed unit by this factor to obtain millimetres.
LENGTH_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "dm": 100.0,
    "m": 1000.0,
    "km": 1_000_000.0,
    "in": 25.4,
    "inch": 25.4,
    "ft": 304.8,
    "foot": 304.8,
    "yd": 914.4,
    "mil": 0.0254,
    "microinch": 0.0000254,
    "um": 0.001,
    "nm": 1e-6,
}


class DxfUnits(IntEnum):
    """``$INSUNITS`` header codes defined by the DXF specification."""

    UNITLESS = 0
    INCHES = 1
    FEET = 2
    MILES = 3
    MILLIMETERS = 4
    CENTIMETERS = 5
    METERS = 6
    KILOMETERS = 7
    MICROINCHES = 8
    MILS = 9
    YARDS = 10
    ANGSTROMS = 11
    NANOMETERS = 12
    MICRONS = 13
    DECIMETERS = 14
    DECAMETERS = 15
    HECTOMETERS = 16
    GIGAMETERS = 17
    ASTRONOMICAL = 18
    LIGHT_YEARS = 19
    PARSECS = 20


_DXF_SCALE_TO_MM: dict[int, float] = {
    DxfUnits.UNITLESS: 1.0,  # assume the drawing is already in mm
    DxfUnits.INCHES: 25.4,
    DxfUnits.FEET: 304.8,
    DxfUnits.MILES: 1_609_344.0,
    DxfUnits.MILLIMETERS: 1.0,
    DxfUnits.CENTIMETERS: 10.0,
    DxfUnits.METERS: 1000.0,
    DxfUnits.KILOMETERS: 1_000_000.0,
    DxfUnits.MICROINCHES: 2.54e-5,
    DxfUnits.MILS: 0.0254,
    DxfUnits.YARDS: 914.4,
    DxfUnits.ANGSTROMS: 1e-7,
    DxfUnits.NANOMETERS: 1e-6,
    DxfUnits.MICRONS: 1e-3,
    DxfUnits.DECIMETERS: 100.0,
    DxfUnits.DECAMETERS: 10_000.0,
    DxfUnits.HECTOMETERS: 100_000.0,
    DxfUnits.GIGAMETERS: 1e12,
}


def dxf_insunits_to_mm(insunits: int) -> float:
    """Return the scale factor converting a DXF drawing unit to millimetres.

    Unknown or astronomical codes fall back to ``1.0`` (treat as millimetres)
    rather than raising, because a wrong header should not block an import that
    is otherwise fine — the geometry validator will flag implausible sizes.
    """
    return _DXF_SCALE_TO_MM.get(int(insunits), 1.0)


def to_mm(value: float, unit: str) -> float:
    """Convert ``value`` expressed in ``unit`` to millimetres."""
    try:
        return value * LENGTH_TO_MM[unit.strip().lower()]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown length unit: {unit!r}") from exc


def from_mm(value_mm: float, unit: str) -> float:
    """Convert ``value_mm`` (millimetres) into ``unit``."""
    try:
        return value_mm / LENGTH_TO_MM[unit.strip().lower()]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unknown length unit: {unit!r}") from exc


# --------------------------------------------------------------------------- #
# Angles
# --------------------------------------------------------------------------- #

def deg(radians: float) -> float:
    """Radians -> degrees."""
    return math.degrees(radians)


def rad(degrees: float) -> float:
    """Degrees -> radians."""
    return math.radians(degrees)


def normalise_angle_deg(angle: float) -> float:
    """Wrap an angle into ``[0, 360)`` degrees."""
    return angle % 360.0


def normalise_angle_signed_deg(angle: float) -> float:
    """Wrap an angle into ``(-180, 180]`` degrees."""
    a = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


# --------------------------------------------------------------------------- #
# Mass / derived quantities
# --------------------------------------------------------------------------- #

def mass_per_metre_kg(area_mm2: float, density_kg_m3: float) -> float:
    """Linear mass of a prismatic bar in kg/m.

    ``area_mm2`` * 1e-6 gives m^2; multiplied by density (kg/m^3) and by a
    1 m length this is already kg/m.
    """
    return area_mm2 * 1e-6 * density_kg_m3


def mass_kg(area_mm2: float, length_mm: float, density_kg_m3: float) -> float:
    """Mass of a prismatic bar of the given cross-section and length."""
    return mass_per_metre_kg(area_mm2, density_kg_m3) * (length_mm / 1000.0)


# --------------------------------------------------------------------------- #
# Fluids (used by the plumbing engine)
# --------------------------------------------------------------------------- #

#: Multiply by this to convert the keyed flow unit into litres per second.
FLOW_TO_LPS: dict[str, float] = {
    "l/s": 1.0,
    "lps": 1.0,
    "l/min": 1.0 / 60.0,
    "lpm": 1.0 / 60.0,
    "l/h": 1.0 / 3600.0,
    "m3/s": 1000.0,
    "m3/h": 1000.0 / 3600.0,
    "gpm": 0.0630901964,  # US gallons per minute
}

#: Multiply by this to convert the keyed pressure unit into pascals.
PRESSURE_TO_PA: dict[str, float] = {
    "pa": 1.0,
    "kpa": 1e3,
    "mpa": 1e6,
    "bar": 1e5,
    "mbar": 1e2,
    "psi": 6894.757293168,
    "mh2o": 9806.65,  # metre of water column at 4 degC
    "mmh2o": 9.80665,
    "atm": 101_325.0,
}


def to_lps(value: float, unit: str) -> float:
    """Convert a volumetric flow rate to litres per second."""
    try:
        return value * FLOW_TO_LPS[unit.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown flow unit: {unit!r}") from exc


def to_pa(value: float, unit: str) -> float:
    """Convert a pressure to pascals."""
    try:
        return value * PRESSURE_TO_PA[unit.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown pressure unit: {unit!r}") from exc


def from_pa(value_pa: float, unit: str) -> float:
    """Convert a pressure in pascals to ``unit``."""
    try:
        return value_pa / PRESSURE_TO_PA[unit.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown pressure unit: {unit!r}") from exc


__all__ = [
    "DxfUnits",
    "LENGTH_TO_MM",
    "FLOW_TO_LPS",
    "PRESSURE_TO_PA",
    "dxf_insunits_to_mm",
    "to_mm",
    "from_mm",
    "deg",
    "rad",
    "normalise_angle_deg",
    "normalise_angle_signed_deg",
    "mass_per_metre_kg",
    "mass_kg",
    "to_lps",
    "to_pa",
    "from_pa",
]
