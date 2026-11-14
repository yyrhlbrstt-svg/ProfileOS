"""The plumbing engine, as a plumbing office actually works.

Five stages, in the order a job moves through them:

*Fixtures.* Count what is connected. Loading units convert to a simultaneous
demand through the Hunter curve; drainage fixture units size the waste side
from tables. A fixture carries both, because it is modest on one and heavy on
the other.

*Supply.* Size each run against velocity, friction and the pressure actually
available, from a real pipe catalogue rather than a nominal diameter.

*Drainage and venting.* Size the branch, the stack, its vent and the house
drain, with the three rules that beat the tables: never reduce downstream,
never smaller than the trap, and a WC gets 100 mm.

*Hot water.* Insulate it, circulate it, and check the dead legs — the tail
nobody circulates is what makes somebody run the tap to waste.

*Take-off.* Turn the sizes into the list the merchant is phoned with: stock
lengths rather than metres, insulation counted, waste named rather than
buried.

The tabulated capacities are the conventional trade figures, consistent with
ת"י 1205 practice and the model codes those tables descend from. The authority
having jurisdiction over a given job is the one whose table governs, so every
result says what decided it.
"""

from __future__ import annotations

from .hydraulics import (
    FITTING_K,
    ROUGHNESS_MM,
    FlowRegime,
    Fluid,
    colebrook_white,
    darcy_weisbach_loss,
    fitting_loss,
    friction_factor,
    hazen_williams_loss,
    pressure_to_head,
    reynolds_number,
    static_head,
    swamee_jain,
    total_k,
    velocity,
    water_at,
)
from .network import Loop, NetworkResult, Node, Pipe, PipeNetwork
from .pipes import (
    BUILTIN_CATALOGUES,
    COPPER_EN1057,
    PIPE_CATALOGUE_SCHEMA,
    PPR_PN20,
    STEEL_EN10255,
    DesignLimits,
    PipeCatalogue,
    PipeSize,
    ServiceType,
    SizingResult,
    get_catalogue,
    register_catalogue,
    size_pipe,
)

from .drainage import (
    DrainResult,
    DrainageDesign,
    DrainageError,
    StackResult,
    VentResult,
    design_drainage,
    size_horizontal_drain,
    size_stack,
    size_vent,
)
from .fixtures import (
    FIXTURES,
    TYPICAL_DWELLING,
    Fixture,
    FixtureError,
    FixtureSchedule,
    SupplyKind,
    demand_flow,
    fixture,
    typical_dwelling,
)
from .hotwater import (
    CirculationDesign,
    DeadLeg,
    HotWaterError,
    INSULATION,
    circulation_flow,
    design_circulation,
    heat_loss_per_metre,
)
from .takeoff import PipeRun, Takeoff, TakeoffError, TakeoffLine, take_off

__all__ = [
    "Fluid", "water_at", "FlowRegime", "ROUGHNESS_MM", "FITTING_K",
    "velocity", "reynolds_number", "swamee_jain", "colebrook_white",
    "friction_factor", "darcy_weisbach_loss", "hazen_williams_loss",
    "fitting_loss", "total_k", "static_head", "pressure_to_head",
    "ServiceType", "DesignLimits", "PipeSize", "PipeCatalogue", "SizingResult",
    "size_pipe", "COPPER_EN1057", "PPR_PN20", "STEEL_EN10255",
    "BUILTIN_CATALOGUES", "get_catalogue", "register_catalogue",
    "PIPE_CATALOGUE_SCHEMA",
    "Node", "Pipe", "Loop", "NetworkResult", "PipeNetwork",
    # Fixtures and demand
    "FIXTURES", "TYPICAL_DWELLING", "Fixture", "FixtureError",
    "FixtureSchedule", "SupplyKind",
    "demand_flow", "fixture", "typical_dwelling",
    # Drainage and venting
    "DrainResult", "DrainageDesign", "DrainageError", "StackResult",
    "VentResult", "design_drainage", "size_horizontal_drain", "size_stack",
    "size_vent",
    # Hot water
    "CirculationDesign", "DeadLeg", "HotWaterError", "INSULATION",
    "circulation_flow", "design_circulation", "heat_loss_per_metre",
    # Take-off
    "PipeRun", "Takeoff", "TakeoffError", "TakeoffLine", "take_off",
]
