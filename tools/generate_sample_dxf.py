#!/usr/bin/env python3
"""Generate the sample DXF drawings used by the tests and the demo project.

The drawings deliberately exercise the awkward parts of real profile drawings:

* outlines split into loose ``LINE`` and ``ARC`` entities rather than one tidy
  polyline (``mullion_mb70``),
* arcs encoded as polyline bulges (``frame_thermal``),
* annotation on separate layers that must be ignored,
* a thermally broken section that imports as two disconnected shells,
* a small solid section with no chambers at all (``glazing_bead``).

Run ``python tools/generate_sample_dxf.py`` to (re)create ``profileos/data/samples``.
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "profileos" / "data" / "samples"

PROFILE_LAYER = "PROFILE"
DIM_LAYER = "DIM"
TEXT_LAYER = "TEXT"


def _new_document() -> Drawing:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    for name, colour in ((PROFILE_LAYER, 7), (DIM_LAYER, 3), (TEXT_LAYER, 2)):
        if name not in doc.layers:
            doc.layers.add(name, color=colour)
    return doc


def _rounded_rect_bulges(
    x: float, y: float, width: float, height: float, radius: float
) -> list[tuple[float, float, float]]:
    """``(x, y, bulge)`` vertices for a rectangle with equal corner radii.

    A 90 degree arc has ``bulge = tan(90/4 deg) = tan(pi/8)``. The sign is
    positive because the ring is emitted counter-clockwise.
    """
    r = min(radius, width / 2.0, height / 2.0)
    b = math.tan(math.pi / 8.0)
    if r <= 0:
        return [(x, y, 0.0), (x + width, y, 0.0), (x + width, y + height, 0.0), (x, y + height, 0.0)]
    return [
        (x + r, y, 0.0),
        (x + width - r, y, b),
        (x + width, y + r, 0.0),
        (x + width, y + height - r, b),
        (x + width - r, y + height, 0.0),
        (x + r, y + height, b),
        (x, y + height - r, 0.0),
        (x, y + r, b),
    ]


def _add_annotation(msp, label: str, at: tuple[float, float], span: tuple[float, float]) -> None:
    """Add dimension and text noise that the reader must filter out."""
    msp.add_linear_dim(
        base=(span[0], at[1] - 25.0),
        p1=(span[0], at[1]),
        p2=(span[1], at[1]),
        dxfattribs={"layer": DIM_LAYER},
    ).render()
    msp.add_text(label, height=6.0, dxfattribs={"layer": TEXT_LAYER}).set_placement(
        (span[0], at[1] + 15.0)
    )


def _screw_port(msp, centre: tuple[float, float], outer_r: float, inner_r: float) -> None:
    """A screw port: an annular boss drawn as two concentric circles.

    The boss must sit **inside a chamber**, which is how extrusions actually
    carry one. That makes the boss ring nest at depth 2 (material inside a void)
    and its bore at depth 3 (void again) — the deep-nesting case the topology
    resolver has to classify correctly. Drawing a port in solid web instead
    would make the even-odd rule read the boss outline as a void, which is both
    wrong and not what an extruder produces.
    """
    msp.add_circle(centre, outer_r, dxfattribs={"layer": PROFILE_LAYER})
    msp.add_circle(centre, inner_r, dxfattribs={"layer": PROFILE_LAYER})


def make_mullion(path: Path) -> None:
    """A 70 x 100 curtain-wall mullion: two chambers, drawn as loose entities."""
    doc = _new_document()
    msp = doc.modelspace()

    width, height, wall = 70.0, 100.0, 2.5
    outer = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    for i in range(4):
        msp.add_line(outer[i], outer[(i + 1) % 4], dxfattribs={"layer": PROFILE_LAYER})

    # Two chambers separated by a central web. The web is thick enough to carry
    # the screw ports without the ports touching either chamber.
    web_half = 6.0
    centre_y = height / 2.0
    lower = (wall, wall, width - 2 * wall, centre_y - web_half - wall)
    upper = (
        wall,
        centre_y + web_half,
        width - 2 * wall,
        height - wall - (centre_y + web_half),
    )
    for x, y, w, h in (lower, upper):
        msp.add_lwpolyline(
            [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
            close=True,
            dxfattribs={"layer": PROFILE_LAYER},
        )

    # Screw ports sit inside the chambers, clear of every chamber wall.
    _screw_port(msp, (width / 2.0, lower[1] + lower[3] - 9.0), 5.0, 2.6)
    _screw_port(msp, (width / 2.0, upper[1] + 9.0), 5.0, 2.6)

    _add_annotation(msp, "MB-70 MULLION", (0.0, height), (0.0, width))
    doc.saveas(path)


def make_thermal_frame(path: Path) -> None:
    """A thermally broken frame: two aluminium shells bridged by polyamide.

    Imports as four disconnected regions — two aluminium shells (each with one
    chamber) plus the two insulating strips, which are real material and are
    read as their own regions. This is the multi-region case that the composite
    section analysis needs, and the reason ``SectionTopology.is_multi_part``
    exists.
    """
    doc = _new_document()
    msp = doc.modelspace()
    if "THERMAL" not in doc.layers:
        doc.layers.add("THERMAL", color=1)

    wall = 2.0
    gap = 24.0  # polyamide zone between the two shells

    # Outer shell (room side), 30 mm deep.
    outer_shell = _rounded_rect_bulges(0.0, 0.0, 62.0, 30.0, 3.0)
    msp.add_lwpolyline(
        [(x, y, 0.0, 0.0, b) for x, y, b in outer_shell],
        format="xyseb",
        close=True,
        dxfattribs={"layer": PROFILE_LAYER},
    )
    inner_chamber = _rounded_rect_bulges(wall, wall, 62.0 - 2 * wall, 30.0 - 2 * wall, 1.5)
    msp.add_lwpolyline(
        [(x, y, 0.0, 0.0, b) for x, y, b in inner_chamber],
        format="xyseb",
        close=True,
        dxfattribs={"layer": PROFILE_LAYER},
    )

    # Inner shell (weather side), offset across the thermal gap.
    y0 = 30.0 + gap
    outer_shell2 = _rounded_rect_bulges(0.0, y0, 62.0, 26.0, 3.0)
    msp.add_lwpolyline(
        [(x, y, 0.0, 0.0, b) for x, y, b in outer_shell2],
        format="xyseb",
        close=True,
        dxfattribs={"layer": PROFILE_LAYER},
    )
    inner_chamber2 = _rounded_rect_bulges(wall, y0 + wall, 62.0 - 2 * wall, 26.0 - 2 * wall, 1.5)
    msp.add_lwpolyline(
        [(x, y, 0.0, 0.0, b) for x, y, b in inner_chamber2],
        format="xyseb",
        close=True,
        dxfattribs={"layer": PROFILE_LAYER},
    )

    # Polyamide strips on their own layer (ignored by the default reader).
    for x in (8.0, 46.0):
        msp.add_lwpolyline(
            [(x, 30.0), (x + 8.0, 30.0), (x + 8.0, y0), (x, y0)],
            close=True,
            dxfattribs={"layer": "THERMAL"},
        )

    _add_annotation(msp, "THERMAL FRAME", (0.0, y0 + 26.0), (0.0, 62.0))
    doc.saveas(path)


def make_glazing_bead(path: Path) -> None:
    """A small solid glazing bead: one closed contour, no chambers."""
    doc = _new_document()
    msp = doc.modelspace()

    points = [
        (0.0, 0.0, 0.0),
        (18.0, 0.0, 0.0),
        (18.0, 4.0, 0.0),
        (14.0, 4.0, math.tan(math.pi / 8.0)),
        (10.0, 8.0, 0.0),
        (10.0, 22.0, 0.0),
        (7.0, 22.0, 0.0),
        (7.0, 6.0, 0.0),
        (0.0, 6.0, 0.0),
    ]
    msp.add_lwpolyline(
        [(x, y, 0.0, 0.0, b) for x, y, b in points],
        format="xyseb",
        close=True,
        dxfattribs={"layer": PROFILE_LAYER},
    )
    _add_annotation(msp, "GLAZING BEAD", (0.0, 22.0), (0.0, 18.0))
    doc.saveas(path)


def make_gapped_box(path: Path) -> None:
    """A square tube whose outline has a 0.02 mm gap, to exercise gap repair."""
    doc = _new_document()
    msp = doc.modelspace()

    gap = 0.02
    msp.add_line((0.0, 0.0), (50.0, 0.0), dxfattribs={"layer": PROFILE_LAYER})
    msp.add_line((50.0, 0.0), (50.0, 50.0), dxfattribs={"layer": PROFILE_LAYER})
    msp.add_line((50.0, 50.0), (0.0, 50.0), dxfattribs={"layer": PROFILE_LAYER})
    # Deliberately stops short of the origin.
    msp.add_line((0.0, 50.0), (0.0, gap), dxfattribs={"layer": PROFILE_LAYER})

    msp.add_lwpolyline(
        [(3.0, 3.0), (47.0, 3.0), (47.0, 47.0), (3.0, 47.0)],
        close=True,
        dxfattribs={"layer": PROFILE_LAYER},
    )
    doc.saveas(path)


SAMPLES: dict[str, callable] = {
    "mullion_mb70.dxf": make_mullion,
    "frame_thermal.dxf": make_thermal_frame,
    "glazing_bead.dxf": make_glazing_bead,
    "gapped_box.dxf": make_gapped_box,
}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, builder in SAMPLES.items():
        target = OUTPUT_DIR / filename
        builder(target)
        print(f"wrote {target.relative_to(OUTPUT_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
