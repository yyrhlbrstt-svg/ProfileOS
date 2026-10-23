"""Drawings as DXF, which is what the consultant marks up and returns.

An aluminium consultant does not want a PDF to comment on — they want the DXF,
so they can measure it, trace over it and send it back with a cloud round the
bit that is wrong. So this exporter produces a file that opens in AutoCAD,
BricsCAD and every free viewer with its layers intact, its line weights set and
its text the right height.

Two deliberate choices:

**Everything is exploded.** Dimensions arrive here as lines, arrowheads and
text rather than as DXF DIMENSION entities. A DIMENSION carries a style, and a
style that is not in the receiving drawing gets substituted — which silently
changes arrow sizes and text heights on somebody else's screen. Exploded
geometry cannot be reinterpreted.

**Line weights are set per layer, in hundredths of a millimetre**, which is the
DXF unit, and entities inherit them. That is how CAD users expect to control a
plot, and it means the consultant's own pen table still works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from .model import (
    Anchor,
    Arc,
    Circle,
    Drawing,
    Hatch,
    HatchPattern,
    Layer,
    Line,
    LineType,
    Polyline,
    Text,
)

#: DXF line type names, and the dash patterns they are defined with here so a
#: receiving drawing that lacks them still shows the right dashes.
_LINETYPES: dict[LineType, tuple[str, str, list[float]]] = {
    LineType.CONTINUOUS: ("CONTINUOUS", "Solid line", []),
    LineType.DASHED: ("DASHED", "Dashed __ __ __", [6.0, -3.0]),
    LineType.CENTRE: ("CENTER", "Centre ____ _ ____", [16.0, -3.0, 3.0, -3.0]),
    LineType.HIDDEN: ("HIDDEN", "Hidden _ _ _ _", [3.0, -2.0]),
    LineType.SECTION: ("PHANTOM", "Phantom ____ _ _ ____", [24.0, -4.0, 4.0, -4.0, 4.0, -4.0]),
}

#: ezdxf hatch pattern names for the materials that have a standard one.
_HATCH_NAMES: dict[HatchPattern, str] = {
    HatchPattern.CONCRETE: "AR-CONC",
    HatchPattern.BLOCKWORK: "AR-B816",
    HatchPattern.INSULATION: "ANSI37",
    HatchPattern.STONE: "AR-SAND",
    HatchPattern.TIMBER: "ANSI32",
    HatchPattern.EARTH: "EARTH",
    HatchPattern.ALUMINIUM: "ANSI31",
    HatchPattern.SEALANT: "ANSI31",
    HatchPattern.GLASS: "ANSI31",
}


def _require_ezdxf() -> Any:
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - ezdxf is a hard dependency
        raise ProfileOSError(
            "Writing DXF needs ezdxf (pip install ezdxf)."
        ) from exc
    return ezdxf


def _ensure_layers(doc: Any, drawing: Drawing) -> None:
    ezdxf = _require_ezdxf()
    for line_type, (name, description, pattern) in _LINETYPES.items():
        if name in doc.linetypes:
            continue
        try:
            doc.linetypes.add(name, pattern=[sum(abs(p) for p in pattern), *pattern], description=description)
        except (ezdxf.DXFValueError, ezdxf.DXFTableEntryError):  # pragma: no cover
            pass

    for name, layer in drawing.layers.items():
        if not drawing.on_layer(name):
            continue  # do not litter the file with layers nothing is on
        attributes = {
            "color": layer.aci,
            # DXF stores lineweight in 1/100 mm as an integer.
            "lineweight": max(int(round(layer.lineweight * 100)), 0),
            "linetype": _LINETYPES[layer.line_type][0],
        }
        if name in doc.layers:
            existing = doc.layers.get(name)
            existing.dxf.color = attributes["color"]
            existing.dxf.lineweight = attributes["lineweight"]
        else:
            doc.layers.add(name, **attributes)


def _alignment(anchor: Anchor) -> Any:
    """Middle-vertical alignment throughout, so a label sits on its point.

    DXF's default is bottom-left, which would drop every dimension text half a
    character below where the geometry says it goes.
    """
    from ezdxf.enums import TextEntityAlignment

    return {
        Anchor.LEFT: TextEntityAlignment.MIDDLE_LEFT,
        Anchor.CENTRE: TextEntityAlignment.MIDDLE_CENTER,
        Anchor.RIGHT: TextEntityAlignment.MIDDLE_RIGHT,
    }[anchor]


def to_document(drawing: Drawing, *, scale: float = 1.0, version: str = "R2013") -> Any:
    """Build an ezdxf document. ``scale`` sizes the paper-relative text."""
    ezdxf = _require_ezdxf()
    doc = ezdxf.new(version, setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.header["$LWDISPLAY"] = 1  # show line weights, or the layers look flat
    _ensure_layers(doc, drawing)
    space = doc.modelspace()

    for entity in drawing:
        attribs = {"layer": entity.layer}
        if isinstance(entity, Line):
            space.add_line(entity.start, entity.end, dxfattribs=attribs)
        elif isinstance(entity, Polyline):
            polyline = space.add_lwpolyline(
                entity.points, close=entity.closed, dxfattribs=attribs
            )
            if entity.filled:
                # An arrowhead is a solid, and a solid is a hatch of its own
                # outline — an unfilled triangle reads as a "no" symbol.
                hatch = space.add_hatch(color=drawing.layers[entity.layer].aci, dxfattribs=attribs)
                hatch.paths.add_polyline_path(entity.points, is_closed=True)
        elif isinstance(entity, Circle):
            space.add_circle(entity.centre, entity.radius, dxfattribs=attribs)
        elif isinstance(entity, Arc):
            space.add_arc(
                entity.centre, entity.radius, entity.start_angle, entity.end_angle,
                dxfattribs=attribs,
            )
        elif isinstance(entity, Hatch):
            hatch = space.add_hatch(
                color=drawing.layers[entity.layer].aci, dxfattribs=attribs
            )
            pattern = _HATCH_NAMES.get(entity.pattern)
            if entity.fill or pattern is None:
                hatch.set_solid_fill(color=drawing.layers[entity.layer].aci)
            else:
                hatch.set_pattern_fill(
                    pattern, scale=max(entity.spacing * scale / 2.0, 0.1), angle=entity.angle
                )
            hatch.paths.add_polyline_path(entity.boundary, is_closed=True)
            for hole in entity.holes:
                hatch.paths.add_polyline_path(hole, is_closed=True, flags=0)
        elif isinstance(entity, Text):
            text = space.add_text(
                entity.value,
                height=entity.height * scale,
                rotation=entity.rotation,
                dxfattribs=attribs,
            )
            text.set_placement(entity.position, align=_alignment(entity.anchor))
        else:  # pragma: no cover - guarded by the model's closed set of types
            raise TypeError(f"Cannot write {type(entity).__name__} to DXF")
    return doc


def to_dxf(drawing: Drawing, path: str | Path, *, scale: float = 1.0) -> Path:
    """Write the drawing to ``path`` and return it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    to_document(drawing, scale=scale).saveas(str(target))
    return target


__all__ = ["to_document", "to_dxf"]
