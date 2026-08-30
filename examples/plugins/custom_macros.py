"""Example plugin: plant-specific machining macros.

Drop this file into the macros directory (``profileos config --show`` prints
where that is) and it loads without restarting the application. Edit it and it
reloads within the watch interval.

Every plugin is statically validated before it is executed: imports are
restricted, and ``eval``, ``exec``, ``subprocess`` and filesystem deletion are
refused outright. Run ``profileos plugin validate custom_macros.py`` to check a
plugin without loading it.

A macro takes ``(params, ctx)`` and returns a list of IR operations.
"""

from __future__ import annotations

__plugin_version__ = "1.0"


def macro_letterplate(params, ctx):
    """A letterplate aperture with its two fixing holes.

    Parameters: ``width`` (aperture width), ``height`` (aperture height),
    ``fixing_pitch`` (centres of the two through-fixings).
    """
    from profileos.cnc.operations import Drill, RectangularPocket

    x, y = ctx.resolved_x(), ctx.y
    width = float(params.get("width", 250.0))
    height = float(params.get("height", 40.0))
    pitch = float(params.get("fixing_pitch", 290.0))
    half = pitch / 2.0

    return [
        RectangularPocket(
            face=ctx.face, x=x, y=y, length=width, width=height, depth=ctx.depth,
            corner_radius=6.0, through=True, tool_number=ctx.tool_number,
            comment="letterplate aperture",
        ),
        Drill(face=ctx.face, x=x - half, y=y, diameter=6.5, depth=ctx.depth,
              through=True, comment="letterplate fixing 1"),
        Drill(face=ctx.face, x=x + half, y=y, diameter=6.5, depth=ctx.depth,
              through=True, comment="letterplate fixing 2"),
    ]


def macro_trickle_vent(params, ctx):
    """A trickle-vent slot set: several short slots on a common centreline.

    Parameters: ``count``, ``slot_length``, ``slot_width``, ``gap``.
    """
    from profileos.cnc.operations import Slot

    count = int(params.get("count", 3))
    slot_length = float(params.get("slot_length", 60.0))
    slot_width = float(params.get("slot_width", 12.0))
    gap = float(params.get("gap", 15.0))

    x, y = ctx.resolved_x(), ctx.y
    pitch = slot_length + gap
    # Centre the whole set on the requested position.
    start = x - ((count - 1) * pitch + slot_length) / 2.0

    return [
        Slot(
            face=ctx.face,
            x1=start + index * pitch, y1=y,
            x2=start + index * pitch + slot_length, y2=y,
            width=slot_width, depth=ctx.depth, through=True,
            tool_number=ctx.tool_number,
            comment=f"trickle vent slot {index + 1}/{count}",
        )
        for index in range(count)
    ]


def register(context):
    """Entry point called by the plugin loader."""
    context.register("macros", "door.letterplate", macro_letterplate)
    context.register("macros", "vent.trickle", macro_trickle_vent)
