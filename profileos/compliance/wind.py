"""The wind the window has to take, and the class that follows from it.

A facade element is sized for a pressure, and the pressure is the part of the
job most often carried over from the last job. It depends on where the
building is, how tall it is, how exposed it is, and where on the face the
element sits — a corner takes far more suction than the middle of the same
wall, which is why corner units come back cracked when everything was
"designed for the same wind".

The method here is the standard one: a basic wind velocity, a terrain and
height factor giving the peak velocity pressure, and a pressure coefficient
for the zone the element sits in.

The basic velocity is the one number this software will not invent. It comes
off a map in the standard, and a map is not something to remember
approximately. It has to be entered, it is recorded with who entered it, and
until it is, the result says the pressure is unverified rather than showing a
number somebody might build to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..core.errors import ProfileOSError

#: Density of air [kg/m^3].
AIR_DENSITY = 1.25


class Terrain(StrEnum):
    """How much the ground upwind slows the wind down."""

    SEA = "sea"
    OPEN = "open"
    SUBURBAN = "suburban"
    URBAN = "urban"

    @property
    def hebrew(self) -> str:
        return {
            "sea": "חוף הים או שטח פתוח מול הים",
            "open": "שטח פתוח, שדות",
            "suburban": "פרברים, בנייה נמוכה מפוזרת",
            "urban": "עיר צפופה, בניינים גבוהים סביב",
        }[self.value]

    @property
    def roughness(self) -> tuple[float, float]:
        """Roughness length ``z_0`` [m] and minimum height ``z_min`` [m]."""
        return {
            "sea": (0.003, 1.0),
            "open": (0.05, 2.0),
            "suburban": (0.3, 5.0),
            "urban": (1.0, 10.0),
        }[self.value]


class FacadeZone(StrEnum):
    """Where on the face the element sits."""

    FIELD = "field"
    EDGE = "edge"
    CORNER = "corner"
    PARAPET = "parapet"

    @property
    def hebrew(self) -> str:
        return {
            "field": "מרכז הקיר",
            "edge": "קרוב לקצה הקיר",
            "corner": "פינת הבניין",
            "parapet": "מתחת למעקה גג",
        }[self.value]

    @property
    def coefficient(self) -> float:
        """Net pressure coefficient, pressure and suction combined.

        Suction at a corner is the governing case for a facade element and is
        far larger than the pressure on the windward face — which is why the
        corner unit is the one that fails.
        """
        return {"field": 1.2, "edge": 1.6, "corner": 2.2, "parapet": 2.6}[self.value]


def roughness_factor(height: float, terrain: Terrain) -> float:
    """The height and terrain factor ``c_r(z)``, logarithmic profile."""
    import math

    z_0, z_min = terrain.roughness
    z = max(height, z_min)
    # Terrain factor normalised to open country, the usual reference.
    k_r = 0.19 * (z_0 / 0.05) ** 0.07
    return k_r * math.log(z / z_0)


def peak_velocity_pressure(
    basic_velocity: float, height: float, terrain: Terrain
) -> float:
    """Peak velocity pressure ``q_p`` [kN/m^2].

    Includes the ordinary turbulence allowance: the peak gust is taken as
    roughly seven times the turbulence intensity above the mean, which is what
    puts the ``1 + 7 I_v`` factor into the standard form.
    """
    if basic_velocity <= 0:
        raise ProfileOSError("מהירות הרוח היסודית חייבת להיות חיובית")
    import math

    z_0, z_min = terrain.roughness
    z = max(height, z_min)
    c_r = roughness_factor(height, terrain)
    mean_velocity = c_r * basic_velocity
    turbulence = 1.0 / math.log(z / z_0)
    q_p = (1.0 + 7.0 * turbulence) * 0.5 * AIR_DENSITY * mean_velocity**2
    return q_p / 1000.0  # Pa -> kN/m^2


@dataclass
class WindCase:
    """The pressure this element is designed for, and where it came from."""

    pressure: float
    basic_velocity: float
    height: float
    terrain: Terrain
    zone: FacadeZone
    peak_pressure: float
    #: Who said the basic velocity, and from what. Empty means nobody has.
    source: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        """Whether this pressure may be designed to.

        The whole calculation rests on one number off a map. Without a
        recorded source for it, the arithmetic is right and the answer is
        still not something to build to.
        """
        return bool(self.source.strip())

    def describe(self) -> str:
        state = "" if self.is_verified else " — לא מאומת"
        return (
            f"⁦{self.pressure:.2f}⁩ kN/m² ({self.zone.hebrew}, "
            f"⁦{self.height:.0f}⁩ מ׳, {self.terrain.hebrew}){state}"
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("לחץ תכן", f"⁦{self.pressure:.2f}⁩ kN/m²"),
            ("לחץ מהירות שיא", f"⁦{self.peak_pressure:.2f}⁩ kN/m²"),
            ("מהירות רוח יסודית", f"⁦{self.basic_velocity:.0f}⁩ m/s"),
            ("גובה מעל הקרקע", f"⁦{self.height:.0f}⁩ מ׳"),
            ("חספוס השטח", self.terrain.hebrew),
            ("אזור בחזית", f"{self.zone.hebrew} · ⁦c_p = {self.zone.coefficient}⁩"),
            ("מקור מהירות הרוח", self.source or "לא הוזן — יש לקרוא מהמפה בת״י 414"),
        ]


def design_pressure(
    basic_velocity: float,
    *,
    height: float = 10.0,
    terrain: Terrain = Terrain.SUBURBAN,
    zone: FacadeZone = FacadeZone.FIELD,
    source: str = "",
) -> WindCase:
    """The design wind pressure on one facade element."""
    q_p = peak_velocity_pressure(basic_velocity, height, terrain)
    pressure = q_p * zone.coefficient

    notes: list[str] = []
    if zone is FacadeZone.FIELD and height > 20:
        notes.append(
            "מעל ⁦20⁩ מ׳ כדאי לבדוק גם את פינות הבניין — שם היניקה גדולה בהרבה"
        )
    if not source:
        notes.append(
            "מהירות הרוח היסודית לא אומתה מול המפה בת״י 414 — "
            "התוצאה לתכנון ראשוני בלבד"
        )
    return WindCase(
        pressure=pressure,
        basic_velocity=basic_velocity,
        height=height,
        terrain=terrain,
        zone=zone,
        peak_pressure=q_p,
        source=source,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# What the pressure means for the window's tested classes
# --------------------------------------------------------------------------- #

#: Wind resistance classes: the test pressure each class is proved at [Pa].
#: A window is only in a class once a laboratory has put it there.
WIND_CLASSES: tuple[tuple[str, float], ...] = (
    ("C1", 400.0), ("C2", 800.0), ("C3", 1200.0),
    ("C4", 1600.0), ("C5", 2000.0),
)
#: Watertightness classes and the pressure each is proved to hold out [Pa].
WATER_CLASSES: tuple[tuple[str, float], ...] = (
    ("3A", 100.0), ("4A", 150.0), ("5A", 200.0), ("6A", 250.0),
    ("7A", 300.0), ("8A", 450.0), ("9A", 600.0),
)
#: Air permeability classes, best last.
AIR_CLASSES: tuple[str, ...] = ("Class 1", "Class 2", "Class 3", "Class 4")


@dataclass
class PerformanceClasses:
    """The classes this element would have to be tested to."""

    wind: str
    water: str
    air: str
    pressure_pa: float
    notes: list[str] = field(default_factory=list)

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("עמידות בעומס רוח", f"{self.wind} · ⁦{self.pressure_pa:.0f}⁩ Pa"),
            ("אטימות למים", self.water),
            ("חדירות אוויר", self.air),
        ]


def required_classes(case: WindCase, *, exposed: bool = True) -> PerformanceClasses:
    """Which tested classes the design pressure calls for.

    This says what to *ask the supplier for*, not what the window achieves. No
    calculation can put a window in a class; only a test on the actual model
    can, and the sentence that says so travels with the answer.
    """
    pressure_pa = case.pressure * 1000.0

    # Above the top lettered class the standards designate the class by the
    # pressure it was actually tested at, hence the E prefix rather than a
    # made-up C6.
    wind = next(
        (name for name, limit in WIND_CLASSES if limit >= pressure_pa),
        f"E{pressure_pa:.0f} — סיווג לפי לחץ הבדיקה",
    )
    # Watertightness is tested well below the wind pressure: the rain arrives
    # with the gust, not with the design load. An exposed elevation with no
    # overhang above it needs the higher end of the range.
    water_demand = pressure_pa * (0.2 if exposed else 0.15)
    water = next(
        (name for name, limit in WATER_CLASSES if limit >= water_demand),
        f"E{water_demand:.0f} — סיווג מוגבר, מעבר ל-9A",
    )
    air = "Class 4" if pressure_pa >= 1200 else "Class 3"

    notes = [
        "הסיווג נקבע בבדיקת מעבדה על הדגם — כאן נאמר רק מה צריך לדרוש מהספק",
    ]
    if not case.is_verified:
        notes.append("לחץ התכן עצמו עדיין לא אומת מול התקן")
    return PerformanceClasses(
        wind=wind, water=water, air=air, pressure_pa=pressure_pa, notes=notes
    )


__all__ = [
    "AIR_CLASSES",
    "AIR_DENSITY",
    "FacadeZone",
    "PerformanceClasses",
    "Terrain",
    "WATER_CLASSES",
    "WIND_CLASSES",
    "WindCase",
    "design_pressure",
    "peak_velocity_pressure",
    "required_classes",
    "roughness_factor",
]
