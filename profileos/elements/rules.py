"""System rules: deductions, clearances and overlaps.

Every profile system defines how a nominal opening turns into real cut lengths
and glass sizes. Those numbers are not derivable from geometry alone — they
encode the system supplier's design decisions about rebates, overlaps and
tolerances — so they live in a declarative rule set that ships as data and can
be hot-reloaded per system series.

Sign convention
---------------
Every value here is a **deduction unless named otherwise**: a positive number
makes the resulting part smaller. This keeps the arithmetic in
:mod:`profileos.elements.builder` uniform (``size - deduction``) instead of
scattering plus and minus signs that are easy to get backwards.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..core.hotreload import DataSchema
from ..core.registry import PROFILE_SYSTEMS


class GlassRules(BaseModel):
    """How glass is sized inside a daylight opening."""

    model_config = ConfigDict(extra="forbid")

    #: Depth the glass sits into the rebate on each edge [mm].
    edge_cover: float = Field(default=15.0, ge=0)
    #: Clearance left around the pane so it can be set without binding [mm].
    edge_clearance: float = Field(default=3.0, ge=0)
    #: Minimum rebate depth the system's bead can accommodate [mm].
    max_glass_thickness: float = Field(default=52.0, gt=0)
    #: Setting block thickness under the pane [mm].
    setting_block: float = Field(default=5.0, ge=0)

    def deduction(self) -> float:
        """Total size reduction from daylight opening to pane size [mm].

        The pane spans the daylight opening plus the cover on both edges, minus
        the clearance on both edges — so the net change is
        ``2 * (clearance - cover)``, reported as a deduction.
        """
        return 2.0 * (self.edge_clearance - self.edge_cover)


class SashRules(BaseModel):
    """How an opening sash relates to the frame it sits in."""

    model_config = ConfigDict(extra="forbid")

    #: How far the sash overlaps the frame on each side [mm].
    frame_overlap: float = Field(default=8.0, ge=0)
    #: Air gap between sash and frame rebate on each side [mm].
    rebate_clearance: float = Field(default=2.0, ge=0)
    #: Extra gap at the bottom for a threshold or drainage [mm].
    bottom_clearance: float = Field(default=3.0, ge=0)
    #: Sash daylight opening reduction per side, from the sash profile face [mm].
    sash_face_width: float = Field(default=32.0, ge=0)

    def width_deduction(self) -> float:
        """Frame inner width minus sash outer width [mm]."""
        return 2.0 * (self.rebate_clearance - self.frame_overlap)

    def height_deduction(self) -> float:
        return (self.rebate_clearance - self.frame_overlap) + (
            self.bottom_clearance - self.frame_overlap
        )


class FrameRules(BaseModel):
    """How frame members are cut for a given opening size."""

    model_config = ConfigDict(extra="forbid")

    #: Visible face width of the frame profile [mm]; sets the daylight opening.
    face_width: float = Field(default=45.0, gt=0)
    #: Frame members are mitred at the corners by default.
    mitred_corners: bool = True
    #: Length added to each member for the mitre, beyond the nominal size [mm].
    #: Zero for a mitred frame measured outer-to-outer, positive for butt joints.
    corner_allowance: float = Field(default=0.0)
    #: Gap left between frame and structural opening, per side [mm].
    installation_clearance: float = Field(default=10.0, ge=0)


class GasketRules(BaseModel):
    """Gasket and weatherstrip lengths."""

    model_config = ConfigDict(extra="forbid")

    #: Extra length per gasket run to allow for corner turns [mm].
    corner_allowance: float = Field(default=30.0, ge=0)
    #: Waste factor applied when ordering gasket by the metre.
    waste_factor: float = Field(default=1.05, ge=1.0)
    #: Whether the inner gasket is a continuous loop (one length) or four pieces.
    continuous: bool = True


class MullionRules(BaseModel):
    """How dividing members are cut and where they sit."""

    model_config = ConfigDict(extra="forbid")

    #: Visible face width of the mullion/transom [mm].
    face_width: float = Field(default=50.0, gt=0)
    #: Length deduction at each end where the member meets the frame [mm].
    end_deduction: float = Field(default=0.0)
    #: Depth of the end notch (AKM) cut into a transom meeting a mullion [mm].
    notch_depth: float = Field(default=0.0, ge=0)


class SystemRules(BaseModel):
    """The complete parametric rule set for one profile system series.

    Loadable as a hot-reload data plugin with ``kind: "system_rules"``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str = "1.0"
    kind: str = "system_rules"
    supplier: str | None = None

    frame: FrameRules = Field(default_factory=FrameRules)
    sash: SashRules = Field(default_factory=SashRules)
    glass: GlassRules = Field(default_factory=GlassRules)
    gasket: GasketRules = Field(default_factory=GasketRules)
    mullion: MullionRules = Field(default_factory=MullionRules)

    #: Profile article numbers by role, e.g. ``{"frame": "MB70-FRAME"}``.
    profiles: dict[str, str] = Field(default_factory=dict)
    #: Hardware rules keyed by sash opening type.
    hardware: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    notes: str | None = None

    def profile_for(self, role: str, default: str | None = None) -> str:
        return self.profiles.get(role, default or f"{self.id.upper()}-{role.upper()}")


#: A conservative generic rule set, used when a system is not in the catalogue.
DEFAULT_SYSTEM_RULES = SystemRules(
    id="generic",
    name="Generic thermally broken system",
    supplier=None,
    profiles={
        "frame": "GEN-FRAME",
        "sash": "GEN-SASH",
        "mullion": "GEN-MULLION",
        "transom": "GEN-TRANSOM",
        "bead": "GEN-BEAD",
    },
    hardware={
        "casement": [
            {"code": "HW-HINGE", "name": "Friction hinge pair", "quantity": 1, "unit": "pair"},
            {"code": "HW-HANDLE", "name": "Window handle", "quantity": 1, "unit": "pc"},
            {"code": "HW-ESPAG", "name": "Espagnolette lock", "quantity": 1, "unit": "pc"},
        ],
        "tilt_turn": [
            {"code": "HW-TT-KIT", "name": "Tilt-and-turn gear set", "quantity": 1, "unit": "set"},
            {"code": "HW-HANDLE", "name": "Window handle", "quantity": 1, "unit": "pc"},
            {"code": "HW-CORNER", "name": "Corner drive", "quantity": 2, "unit": "pc"},
        ],
        "sliding": [
            {"code": "HW-ROLLER", "name": "Sliding roller", "quantity": 2, "unit": "pc"},
            {"code": "HW-SLIDE-HANDLE", "name": "Sliding handle", "quantity": 1, "unit": "pc"},
        ],
        "door": [
            {"code": "HW-DOOR-HINGE", "name": "Door hinge", "quantity": 3, "unit": "pc"},
            {"code": "HW-MPL", "name": "Multi-point lock", "quantity": 1, "unit": "pc"},
            {"code": "HW-CYL", "name": "Euro cylinder", "quantity": 1, "unit": "pc"},
            {"code": "HW-DOOR-HANDLE", "name": "Door handle set", "quantity": 1, "unit": "set"},
        ],
        "fixed": [],
    },
)


def get_system_rules(system_id: str | None) -> SystemRules:
    """Look up a system rule set, falling back to the generic one."""
    if not system_id:
        return DEFAULT_SYSTEM_RULES
    registered = PROFILE_SYSTEMS.get_or_none(system_id)
    if isinstance(registered, SystemRules):
        return registered
    return DEFAULT_SYSTEM_RULES


def register_system_rules(rules: SystemRules) -> None:
    """Add a rule set to the hot-reloadable registry."""
    PROFILE_SYSTEMS.add(rules.id, rules, version=rules.version, source="api")


def _validate_system_rules(document: dict[str, Any]) -> SystemRules:
    return SystemRules.model_validate(document)


#: Registers ``kind: "system_rules"`` JSON/XML files as hot-reloadable plugins.
SYSTEM_RULES_SCHEMA = DataSchema(
    kind="system_rules",
    model=_validate_system_rules,
    registry=PROFILE_SYSTEMS,
    key_field="id",
    document_model=SystemRules,
)


__all__ = [
    "GlassRules",
    "SashRules",
    "FrameRules",
    "GasketRules",
    "MullionRules",
    "SystemRules",
    "DEFAULT_SYSTEM_RULES",
    "get_system_rules",
    "register_system_rules",
    "SYSTEM_RULES_SCHEMA",
]
