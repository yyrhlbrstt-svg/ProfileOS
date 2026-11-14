"""CNC engine: machine-neutral IR, clamp planning and machine drivers.

Typical use::

    from profileos.cnc import MachiningJob, PieceProgram, get_driver

    job = MachiningJob(machine=machine, name="Tower A")
    job.add(PieceProgram.from_cut_piece(piece, profile=profile))
    job.plan_all_clamps()

    driver = get_driver(machine.post_processor)
    for result in driver.post(job):
        result.write("output/")
"""

from __future__ import annotations

from .clamps import (
    ClampMove,
    ClampPlan,
    Collision,
    detect_collisions,
    forbidden_zones,
    plan_clamps_for_machine,
    reposition_clamps,
)
from .drivers import (
    BasePostProcessor,
    PostResult,
    available_drivers,
    get_driver,
    register_driver,
)
from .job import MachiningJob, PieceProgram
from .macros import (
    BUILTIN_MACROS,
    MacroContext,
    expand_macros,
    register_builtin_macros,
    resolve_macro,
)
from .operations import (
    CircularPocket,
    Compensation,
    Contour,
    Counterbore,
    Drill,
    EndNotch,
    Engrave,
    Operation,
    OperationSet,
    OperationType,
    RectangularPocket,
    SawCut,
    Slot,
    Thread,
    resolve_tools,
)
from .toolpath import Move, MoveType, Toolpath, generate_toolpath, offset_polyline

# Registering the built-in macro library on import keeps expand_macros() usable
# without an explicit setup call.
register_builtin_macros()

__all__ = [
    # IR
    "Operation",
    "OperationSet",
    "OperationType",
    "Compensation",
    "Drill",
    "Counterbore",
    "Thread",
    "RectangularPocket",
    "CircularPocket",
    "Slot",
    "Contour",
    "EndNotch",
    "SawCut",
    "Engrave",
    "resolve_tools",
    # macros
    "MacroContext",
    "BUILTIN_MACROS",
    "expand_macros",
    "resolve_macro",
    "register_builtin_macros",
    # job
    "MachiningJob",
    "PieceProgram",
    # clamps
    "Collision",
    "ClampMove",
    "ClampPlan",
    "detect_collisions",
    "forbidden_zones",
    "reposition_clamps",
    "plan_clamps_for_machine",
    # toolpath
    "Move",
    "MoveType",
    "Toolpath",
    "generate_toolpath",
    "offset_polyline",
    # drivers
    "BasePostProcessor",
    "PostResult",
    "get_driver",
    "register_driver",
    "available_drivers",
]
