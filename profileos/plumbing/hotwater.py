"""Hot water: keeping it hot, and what that costs.

A hot line that is not circulated goes cold between draw-offs, and the person
at the tap runs it to waste until it comes back. Israeli practice under
ת"י 1205 is to insulate every hot line and, where the run is long enough to
matter, to circulate it: a small return pipe brings the cooled water back to
the heater and a small pump keeps it moving.

Sizing that loop is a heat problem before it is a hydraulic one. The pipe
loses heat through its insulation at a rate the geometry fixes; the
circulation flow is whatever carries that heat away at the temperature drop
the design allows; the return pipe and the pump follow from that flow.

The dead leg — the uncirculated tail from the loop to the outlet — is the
number that decides whether anybody actually waits at the tap, and it is
checked here because no amount of circulation fixes a long tail.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core.errors import ProfileOSError

#: Water: density [kg/m³] and specific heat [J/kg·K] near 55 °C. Both move a
#: few percent over the range a hot service works in; at that size the answer
#: is dominated by the insulation, not by these.
WATER_DENSITY = 986.0
WATER_SPECIFIC_HEAT = 4184.0

#: Thermal conductivity [W/m·K] of the insulations the trade actually fits.
INSULATION: dict[str, tuple[str, float]] = {
    "elastomeric": ("בידוד גמיש (סינטטי)", 0.038),
    "mineral-wool": ("צמר סלעים", 0.035),
    "pe-foam": ("קצף פוליאתילן", 0.040),
    "phenolic": ("קצף פנולי", 0.022),
    "none": ("ללא בידוד", 0.60),
}

#: What ת"י 1205 practice expects: hot lines insulated, and the tail from the
#: circulated loop to the outlet kept short enough that nobody runs the tap to
#: waste waiting. The figure is the one the trade works to.
MAX_DEAD_LEG_M = 6.0


class HotWaterError(ProfileOSError):
    """A hot water loop that cannot be designed as asked."""


def heat_loss_per_metre(
    outside_diameter_mm: float,
    *,
    insulation_mm: float = 20.0,
    material: str = "elastomeric",
    hot_c: float = 60.0,
    ambient_c: float = 25.0,
) -> float:
    """Steady heat loss from one metre of insulated pipe [W/m].

    Conduction through the insulation cylinder. The pipe wall and the surface
    films are left out on purpose: against the insulation they are small, and
    including them would imply a precision the ambient temperature of a real
    shaft does not support.
    """
    if outside_diameter_mm <= 0:
        raise HotWaterError("קוטר חיצוני חייב להיות חיובי", diameter=outside_diameter_mm)
    if insulation_mm < 0:
        raise HotWaterError("עובי בידוד לא יכול להיות שלילי", insulation=insulation_mm)

    entry = INSULATION.get(material)
    if entry is None:
        raise HotWaterError(
            f"אין בידוד בשם {material!r}", known=", ".join(sorted(INSULATION))
        )
    _label, conductivity = entry

    inner_radius = outside_diameter_mm / 2000.0
    outer_radius = inner_radius + insulation_mm / 1000.0
    difference = hot_c - ambient_c
    if insulation_mm <= 0 or outer_radius <= inner_radius:
        # A bare pipe is not a conduction problem; it loses heat off its
        # surface. A plain film coefficient is honest enough to show why
        # nobody leaves one bare.
        surface = math.pi * outside_diameter_mm / 1000.0
        return round(surface * 10.0 * difference, 2)

    return round(
        2.0 * math.pi * conductivity * difference / math.log(outer_radius / inner_radius),
        2,
    )


def circulation_flow(watts: float, *, delta_t: float = 5.0) -> float:
    """The flow [l/s] that carries ``watts`` away at a ``delta_t`` drop.

    Five kelvin is the usual allowance: enough that the return is measurably
    cooler and the pump is small, not so much that the far tap is lukewarm.
    """
    if delta_t <= 0:
        raise HotWaterError("הפרש הטמפרטורה חייב להיות חיובי", delta_t=delta_t)
    if watts <= 0:
        return 0.0
    kilograms_per_second = watts / (WATER_SPECIFIC_HEAT * delta_t)
    return round(kilograms_per_second / WATER_DENSITY * 1000.0, 4)


@dataclass
class DeadLeg:
    """One uncirculated tail, and how long somebody waits at its end."""

    name: str
    length_m: float
    bore_mm: float
    flow_lps: float = 0.1

    @property
    def volume_litres(self) -> float:
        area = math.pi * (self.bore_mm / 1000.0) ** 2 / 4.0
        return round(area * self.length_m * 1000.0, 2)

    @property
    def wait_seconds(self) -> float:
        """How long the cold water in the tail takes to run out at the tap."""
        if self.flow_lps <= 0:
            return 0.0
        return round(self.volume_litres / self.flow_lps, 1)

    @property
    def ok(self) -> bool:
        return self.length_m <= MAX_DEAD_LEG_M

    def describe(self) -> str:
        verdict = "תקין" if self.ok else f"ארוך מ־{MAX_DEAD_LEG_M:g} מ'"
        return (
            f"{self.name}: ⁦{self.length_m:.1f}⁩ מ', ⁦{self.volume_litres:.1f}⁩ ליטר, "
            f"המתנה ⁦{self.wait_seconds:.0f}⁩ שנ׳ — {verdict}"
        )


@dataclass
class CirculationDesign:
    """A circulation loop: its heat loss, its flow, its pump and its tails."""

    loop_length_m: float
    flow_diameter_mm: float
    insulation_mm: float
    material: str
    hot_c: float
    ambient_c: float
    delta_t: float
    loss_per_metre: float = 0.0
    total_watts: float = 0.0
    flow_lps: float = 0.0
    return_size: object | None = None
    pump_head_kpa: float = 0.0
    pump_watts: float = 0.0
    dead_legs: list[DeadLeg] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.return_size is not None and all(leg.ok for leg in self.dead_legs)

    @property
    def annual_kwh(self) -> float:
        """What the standing loss costs over a year, running continuously."""
        return round(self.total_watts * 8760.0 / 1000.0, 0)

    def summary(self) -> dict[str, float | str]:
        return {
            "loss_per_metre_w": self.loss_per_metre,
            "total_watts": round(self.total_watts, 1),
            "flow_lps": self.flow_lps,
            "return": getattr(self.return_size, "designation", "—"),
            "pump_head_kpa": round(self.pump_head_kpa, 1),
            "pump_watts": round(self.pump_watts, 1),
            "annual_kwh": self.annual_kwh,
        }

    def describe(self) -> str:
        designation = getattr(self.return_size, "designation", "—")
        return (
            f"איבוד ⁦{self.loss_per_metre:.1f}⁩ ואט/מ' · סה\"כ ⁦{self.total_watts:.0f}⁩ ואט · "
            f"ספיקת מחזור ⁦{self.flow_lps:.3f}⁩ ל'/שנ' · חוזר {designation} · "
            f"משאבה ⁦{self.pump_head_kpa:.0f}⁩ קפ\"א"
        )


def design_circulation(
    loop_length_m: float,
    flow_diameter_mm: float,
    catalogue,
    *,
    insulation_mm: float = 20.0,
    material: str = "elastomeric",
    hot_c: float = 60.0,
    ambient_c: float = 25.0,
    delta_t: float = 5.0,
    return_diameter_mm: float | None = None,
    fittings: dict[str, int] | None = None,
    dead_legs: list[DeadLeg] | None = None,
    pump_efficiency: float = 0.35,
) -> CirculationDesign:
    """Size a hot water circulation loop end to end.

    The heat lost by the flow *and* the return has to be carried, so the loss
    is taken over the whole circuit rather than the one-way run — a loop sized
    on half its own length runs cold at the far end.
    """
    from .pipes import ServiceType, size_pipe

    if loop_length_m <= 0:
        raise HotWaterError("אורך הלולאה חייב להיות חיובי", length=loop_length_m)

    design = CirculationDesign(
        loop_length_m=loop_length_m,
        flow_diameter_mm=flow_diameter_mm,
        insulation_mm=insulation_mm,
        material=material,
        hot_c=hot_c,
        ambient_c=ambient_c,
        delta_t=delta_t,
        dead_legs=list(dead_legs or []),
    )

    return_diameter = return_diameter_mm or max(20.0, flow_diameter_mm * 0.5)
    flow_loss = heat_loss_per_metre(
        flow_diameter_mm, insulation_mm=insulation_mm, material=material,
        hot_c=hot_c, ambient_c=ambient_c,
    )
    return_loss = heat_loss_per_metre(
        return_diameter, insulation_mm=insulation_mm, material=material,
        hot_c=hot_c, ambient_c=ambient_c,
    )
    design.loss_per_metre = flow_loss
    design.total_watts = (flow_loss + return_loss) * loop_length_m
    design.flow_lps = circulation_flow(design.total_watts, delta_t=delta_t)

    if design.flow_lps <= 0:
        design.notes.append("אין איבוד חום — אין צורך במחזור")
        return design

    # The return carries only the circulation flow, so it is sized on the
    # gentle limits of a circulating line rather than a draw-off.
    sizing = size_pipe(
        design.flow_lps,
        loop_length_m * 2.0,
        catalogue,
        service=ServiceType.HOT_WATER,
        fittings=fittings or {"elbow_90_long": 12, "tee_through": 6, "gate_valve_open": 4},
    )
    design.return_size = sizing.size
    if sizing.size is None:
        design.notes.append("לא נמצא קוטר לקו החוזר — הגדילו את הקטלוג או את הפרש הטמפרטורה")
        return design

    design.pump_head_kpa = sizing.total_loss / 1000.0
    hydraulic_watts = design.flow_lps / 1000.0 * sizing.total_loss
    design.pump_watts = hydraulic_watts / max(pump_efficiency, 0.05)

    if insulation_mm <= 0:
        design.notes.append("קו חם ללא בידוד — ת\"י 1205 מחייב בידוד קווי מים חמים")
    for leg in design.dead_legs:
        if not leg.ok:
            design.notes.append(leg.describe())
    return design


__all__ = [
    "CirculationDesign",
    "DeadLeg",
    "HotWaterError",
    "INSULATION",
    "MAX_DEAD_LEG_M",
    "WATER_DENSITY",
    "WATER_SPECIFIC_HEAT",
    "circulation_flow",
    "design_circulation",
    "heat_loss_per_metre",
]
