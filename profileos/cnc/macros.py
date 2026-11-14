"""Parametric machining macros.

A macro is a named, parameterised recipe that expands into concrete IR
operations. Fabricators think in macros — "put a euro-cylinder lock here", not
"drill 17 mm, then mill a 24 x 240 faceplate recess" — and every serious system
in this market ships a macro library. Macros live in a hot-reloadable registry,
so a plant can add its own hardware preparations as plugins without touching
the application.

A macro is any callable with the signature::

    def my_macro(params: dict, ctx: MacroContext) -> list[Operation]

registered under a dotted key. :func:`expand_macros` resolves a list of
:class:`~profileos.models.profile.MachiningMacro` references into operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..core.errors import CncError
from ..core.logging_setup import get_logger
from ..core.registry import MACROS
from ..models.profile import Face, MachiningMacro
from .operations import (
    Contour,
    Counterbore,
    Drill,
    EndNotch,
    Engrave,
    Operation,
    OperationSet,
    RectangularPocket,
    Slot,
    Thread,
)

_log = get_logger("cnc.macros")

MacroFunc = Callable[[dict[str, Any], "MacroContext"], list[Operation]]


@dataclass
class MacroContext:
    """Placement information handed to a macro when it expands."""

    face: Face
    x: float
    y: float
    depth: float
    tool_number: int | None = None
    rotation: float = 0.0
    bar_length: float = 0.0
    from_right: bool = False

    def resolved_x(self) -> float:
        """Absolute X along the bar, honouring right-end referencing."""
        return self.bar_length - self.x if self.from_right else self.x


def _param(params: dict[str, Any], key: str, default: Any) -> Any:
    value = params.get(key, default)
    return default if value is None else value


# --------------------------------------------------------------------------- #
# Built-in macros
# --------------------------------------------------------------------------- #

def macro_drill(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A single hole. Parameters: ``diameter``, ``through``, ``peck_depth``."""
    return [
        Drill(
            face=ctx.face,
            x=ctx.resolved_x(),
            y=ctx.y,
            diameter=float(_param(params, "diameter", 5.0)),
            depth=ctx.depth,
            through=bool(_param(params, "through", False)),
            peck_depth=params.get("peck_depth"),
            tool_number=ctx.tool_number,
            comment="drill",
        )
    ]


def macro_drill_row(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A row of equally spaced holes. Parameters: ``count``, ``pitch``, ``diameter``."""
    count = int(_param(params, "count", 2))
    pitch = float(_param(params, "pitch", 50.0))
    diameter = float(_param(params, "diameter", 5.0))
    along_y = bool(_param(params, "along_y", False))

    if count < 1:
        raise CncError("drill_row needs a count of at least 1", count=count)

    holes: list[Operation] = []
    base_x, base_y = ctx.resolved_x(), ctx.y
    for index in range(count):
        offset = index * pitch
        holes.append(
            Drill(
                face=ctx.face,
                x=base_x if along_y else base_x + offset,
                y=base_y + offset if along_y else base_y,
                diameter=diameter,
                depth=ctx.depth,
                tool_number=ctx.tool_number,
                comment=f"drill row {index + 1}/{count}",
            )
        )
    return holes


def macro_euro_cylinder(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A euro-profile cylinder lock preparation.

    The standard DIN 18251 preparation: a keyhole-shaped cylinder cut-out plus
    the faceplate recess. Parameters: ``faceplate_length``, ``faceplate_width``,
    ``faceplate_depth``, ``cylinder_diameter``, ``spindle_height``.
    """
    x, y = ctx.resolved_x(), ctx.y
    faceplate_length = float(_param(params, "faceplate_length", 240.0))
    faceplate_width = float(_param(params, "faceplate_width", 24.0))
    faceplate_depth = float(_param(params, "faceplate_depth", 3.0))
    cylinder_diameter = float(_param(params, "cylinder_diameter", 17.0))
    spindle_height = float(_param(params, "spindle_height", 8.0))

    return [
        RectangularPocket(
            face=ctx.face,
            x=x,
            y=y,
            length=faceplate_length,
            width=faceplate_width,
            depth=faceplate_depth,
            corner_radius=2.0,
            tool_number=ctx.tool_number,
            comment="lock faceplate recess",
        ),
        # The cylinder body: a through cut-out on the same centre.
        Drill(
            face=ctx.face,
            x=x,
            y=y,
            diameter=cylinder_diameter,
            depth=ctx.depth,
            through=True,
            comment="euro cylinder bore",
        ),
        # The cam slot below the cylinder, which is what makes the keyhole shape.
        Slot(
            face=ctx.face,
            x1=x,
            y1=y - spindle_height,
            x2=x,
            y2=y - spindle_height - 6.0,
            width=10.0,
            depth=ctx.depth,
            through=True,
            comment="cylinder cam slot",
        ),
    ]


def macro_espagnolette_handle(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A window handle preparation: square spindle hole plus fixing holes.

    Parameters: ``spindle_size`` (square across flats), ``fixing_pitch``,
    ``fixing_diameter``.
    """
    x, y = ctx.resolved_x(), ctx.y
    spindle = float(_param(params, "spindle_size", 7.0))
    pitch = float(_param(params, "fixing_pitch", 43.0))
    fixing_diameter = float(_param(params, "fixing_diameter", 5.0))
    half = pitch / 2.0

    return [
        RectangularPocket(
            face=ctx.face,
            x=x,
            y=y,
            length=spindle,
            width=spindle,
            depth=ctx.depth,
            through=True,
            tool_number=ctx.tool_number,
            comment="handle square spindle",
        ),
        Drill(
            face=ctx.face,
            x=x,
            y=y - half,
            diameter=fixing_diameter,
            depth=ctx.depth,
            comment="handle fixing 1",
        ),
        Drill(
            face=ctx.face,
            x=x,
            y=y + half,
            diameter=fixing_diameter,
            depth=ctx.depth,
            comment="handle fixing 2",
        ),
    ]


def macro_hinge(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A hinge preparation: a shallow recess with fixing holes.

    Parameters: ``length``, ``width``, ``recess_depth``, ``screw_pitch``,
    ``screw_diameter``.
    """
    x, y = ctx.resolved_x(), ctx.y
    length = float(_param(params, "length", 120.0))
    width = float(_param(params, "width", 20.0))
    recess_depth = float(_param(params, "recess_depth", 2.0))
    screw_pitch = float(_param(params, "screw_pitch", 80.0))
    screw_diameter = float(_param(params, "screw_diameter", 5.0))
    half = screw_pitch / 2.0

    return [
        RectangularPocket(
            face=ctx.face,
            x=x,
            y=y,
            length=length,
            width=width,
            depth=recess_depth,
            corner_radius=1.5,
            tool_number=ctx.tool_number,
            comment="hinge recess",
        ),
        Drill(
            face=ctx.face,
            x=x - half,
            y=y,
            diameter=screw_diameter,
            depth=ctx.depth,
            comment="hinge screw 1",
        ),
        Drill(
            face=ctx.face,
            x=x + half,
            y=y,
            diameter=screw_diameter,
            depth=ctx.depth,
            comment="hinge screw 2",
        ),
    ]


def macro_drainage_slots(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """Water drainage slots in a frame or sash bottom.

    Parameters: ``count``, ``slot_length``, ``slot_width``, ``spacing``.
    """
    count = int(_param(params, "count", 2))
    slot_length = float(_param(params, "slot_length", 25.0))
    slot_width = float(_param(params, "slot_width", 5.0))
    spacing = float(_param(params, "spacing", 400.0))

    x, y = ctx.resolved_x(), ctx.y
    slots: list[Operation] = []
    for index in range(count):
        start = x + index * spacing
        slots.append(
            Slot(
                face=ctx.face,
                x1=start,
                y1=y,
                x2=start + slot_length,
                y2=y,
                width=slot_width,
                depth=ctx.depth,
                through=True,
                tool_number=ctx.tool_number,
                comment=f"drainage slot {index + 1}/{count}",
            )
        )
    return slots


def macro_end_notch(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """An end notch (AKM) for a transom-to-mullion connection.

    Parameters: ``length``, ``width``, ``corner_radius``.
    """
    return [
        EndNotch(
            face=ctx.face,
            length=float(_param(params, "length", 20.0)),
            depth=ctx.depth,
            width=float(_param(params, "width", 0.0)),
            from_right=ctx.from_right,
            corner_radius=float(_param(params, "corner_radius", 0.0)),
            bar_length=ctx.bar_length,
            tool_number=ctx.tool_number,
            comment="end notch (AKM)",
        )
    ]


def macro_screw_port(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A screw-port preparation: pilot hole, optionally tapped.

    Parameters: ``diameter``, ``thread`` (e.g. ``"M5"``), ``pitch``.
    """
    x, y = ctx.resolved_x(), ctx.y
    thread = params.get("thread")
    if thread:
        nominal = float(str(thread).lstrip("Mm") or 5.0)
        return [
            Thread(
                face=ctx.face,
                x=x,
                y=y,
                nominal_diameter=nominal,
                pitch=float(_param(params, "pitch", 0.8)),
                depth=ctx.depth,
                tool_number=ctx.tool_number,
                comment=f"tapped screw port {thread}",
            )
        ]
    return [
        Drill(
            face=ctx.face,
            x=x,
            y=y,
            diameter=float(_param(params, "diameter", 4.2)),
            depth=ctx.depth,
            tool_number=ctx.tool_number,
            comment="screw port pilot",
        )
    ]


def macro_counterbore(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """A screw-head seat. Parameters: ``pilot_diameter``, ``bore_diameter``, ``bore_depth``."""
    return [
        Counterbore(
            face=ctx.face,
            x=ctx.resolved_x(),
            y=ctx.y,
            pilot_diameter=float(_param(params, "pilot_diameter", 5.5)),
            bore_diameter=float(_param(params, "bore_diameter", 10.0)),
            bore_depth=float(_param(params, "bore_depth", 4.0)),
            pilot_depth=ctx.depth,
            tool_number=ctx.tool_number,
            comment="counterbore",
        )
    ]


def macro_mark(params: dict[str, Any], ctx: MacroContext) -> list[Operation]:
    """Engraved text. Parameters: ``text``, ``height``."""
    return [
        Engrave(
            face=ctx.face,
            x=ctx.resolved_x(),
            y=ctx.y,
            text=str(_param(params, "text", "")),
            height=float(_param(params, "height", 5.0)),
            depth=ctx.depth,
            rotation=ctx.rotation,
            tool_number=ctx.tool_number,
            comment="position mark",
        )
    ]


#: Macros shipped with ProfileOS. Plugins may add to or override these.
BUILTIN_MACROS: dict[str, MacroFunc] = {
    "drill.simple": macro_drill,
    "drill.row": macro_drill_row,
    "lock.euro_cylinder": macro_euro_cylinder,
    "handle.espagnolette": macro_espagnolette_handle,
    "hinge.standard": macro_hinge,
    "drainage.slots": macro_drainage_slots,
    "notch.akm": macro_end_notch,
    "screwport.pilot": macro_screw_port,
    "counterbore.standard": macro_counterbore,
    "mark.text": macro_mark,
}


def register_builtin_macros() -> None:
    """Populate the ``MACROS`` registry with the built-in library."""
    for key, func in BUILTIN_MACROS.items():
        if key not in MACROS:
            MACROS.add(key, func, version="1.0", source="builtin")


def resolve_macro(macro_id: str) -> MacroFunc:
    """Look up a macro, preferring registry (plugin) entries over built-ins.

    Raises
    ------
    CncError
        No macro is registered under ``macro_id``.
    """
    register_builtin_macros()
    func = MACROS.get_or_none(macro_id)
    if func is None:
        func = BUILTIN_MACROS.get(macro_id.strip().lower())
    if func is None:
        raise CncError(
            f"Unknown machining macro {macro_id!r}",
            available=sorted(set(BUILTIN_MACROS) | set(MACROS.keys())),
        )
    return func


def expand_macros(
    macros: Sequence[MachiningMacro],
    *,
    bar_length: float = 0.0,
    strict: bool = True,
) -> OperationSet:
    """Expand macro references into concrete operations.

    Parameters
    ----------
    strict:
        Raise on an unknown macro. When false, unknown macros are skipped with
        a logged warning, which is what a batch import wants.
    """
    operations = OperationSet()
    for macro in macros:
        if not macro.enabled:
            continue
        try:
            func = resolve_macro(macro.macro_id)
        except CncError:
            if strict:
                raise
            _log.warning("Skipping unknown macro %r", macro.macro_id)
            continue

        ctx = MacroContext(
            face=macro.face,
            x=macro.position_x,
            y=macro.position_y,
            depth=macro.depth,
            tool_number=macro.tool_id,
            rotation=macro.rotation_deg,
            bar_length=bar_length,
            from_right=macro.from_right_end,
        )
        produced = func(dict(macro.parameters), ctx)
        for operation in produced:
            if macro.label:
                operation.metadata.setdefault("macro_label", macro.label)
            operation.metadata.setdefault("macro_id", macro.macro_id)
            operations.add(operation)

    return operations


__all__ = [
    "MacroContext",
    "MacroFunc",
    "BUILTIN_MACROS",
    "register_builtin_macros",
    "resolve_macro",
    "expand_macros",
    "macro_drill",
    "macro_drill_row",
    "macro_euro_cylinder",
    "macro_espagnolette_handle",
    "macro_hinge",
    "macro_drainage_slots",
    "macro_end_notch",
    "macro_screw_port",
    "macro_counterbore",
    "macro_mark",
]
