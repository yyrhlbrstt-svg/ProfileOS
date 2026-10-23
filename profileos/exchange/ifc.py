"""IFC export: the openings, where they sit, in a file an architect can open.

An architect coordinating a building wants to know where every window is, how
big it is, and what it is — so that the ductwork does not run through one and
the lintel schedule matches. They do not want, and no BIM tool will thank you
for, a hundred-thousand-face solid model of a mitre joint.

So this exports what is genuinely useful and says plainly what it is: each
opening as a placed block at its real size, on a storey, in a building, on a
site, carrying a property set with the system, the glazing, the U-value and
the shop's own mark. That is what almost every window IFC export actually
contains, including the expensive ones — the difference here is that it is
written down rather than implied.

What it is not: this is not the profile geometry. A mullion's cross-section,
its chambers and its thermal break do not survive into the IFC, and an
engineer who needs those should be sent the DXF this suite already produces.
Nothing in the export should be measured for fabrication.

The file is IFC2x3 in STEP physical file form (ISO 10303-21), which is what
the widest range of tools still reads. Units are metres, because IFC is a
metric-SI schema and a file in millimetres is the single most common reason an
imported model turns up a thousand times too big.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.logging_setup import get_logger

_log = get_logger("exchange.ifc")

SCHEMA = "IFC2X3"

#: IFC's own base64 alphabet, which is not the standard one: the last two
#: characters are '_' and '$' rather than '+' and '/'.
_IFC_CHARS = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "_$"
)


def compress_guid(value: uuid.UUID | None = None) -> str:
    """A ⁦128⁩-bit GUID in IFC's ⁦22⁩-character base64 form.

    IFC stores globally unique identifiers compressed into 22 characters using
    its own alphabet. A file with plain hex GUIDs is rejected by strict
    readers, and quietly mis-keyed by lenient ones.
    """
    number = (value or uuid.uuid4()).int
    digits: list[str] = []
    for _ in range(22):
        number, remainder = divmod(number, 64)
        digits.append(_IFC_CHARS[remainder])
    return "".join(reversed(digits))


def _text(value: Any) -> str:
    """One STEP string literal, escaped the way the format requires.

    Non-ASCII is written with the \\X2\\ extended encoding: Hebrew marks and
    room names are the normal case here, not an edge case, and a file that
    drops them arrives with every window called nothing.
    """
    if value is None:
        return "$"
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            out.append("\\X2\\" + "".join(buffer) + "\\X0\\")
            buffer.clear()

    for character in text:
        if ord(character) < 128:
            flush()
            out.append(character)
        else:
            buffer.append(f"{ord(character):04X}")
    flush()
    return "'" + "".join(out) + "'"


def _number(value: float) -> str:
    """A STEP real, which always carries a decimal point."""
    text = f"{float(value):.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


@dataclass
class IfcOptions:
    """What the exported model says about itself."""

    project_name: str = "ProfileOS project"
    site_name: str = "Site"
    building_name: str = "Building"
    storey_name: str = "Ground floor"
    author: str = ""
    organisation: str = ""
    #: Storey elevation above the project datum [mm].
    storey_elevation: float = 0.0
    #: Wall thickness the openings are placed in [mm], used as their depth.
    reveal_depth: float = 100.0
    include_properties: bool = True


class _Step:
    """A STEP physical file being written, one numbered entity at a time."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._next = 0

    def add(self, body: str) -> int:
        self._next += 1
        self._lines.append(f"#{self._next}={body};")
        return self._next

    @property
    def lines(self) -> list[str]:
        return self._lines


def _point(step: _Step, x: float, y: float, z: float | None = None) -> int:
    coordinates = [x, y] if z is None else [x, y, z]
    return step.add(
        "IFCCARTESIANPOINT((" + ",".join(_number(v) for v in coordinates) + "))"
    )


def _direction(step: _Step, x: float, y: float, z: float | None = None) -> int:
    values = [x, y] if z is None else [x, y, z]
    return step.add(
        "IFCDIRECTION((" + ",".join(_number(v) for v in values) + "))"
    )


def _axis(step: _Step, origin: int, *, axis: int | None = None,
          reference: int | None = None) -> int:
    return step.add(
        f"IFCAXIS2PLACEMENT3D(#{origin},"
        f"{f'#{axis}' if axis else '$'},"
        f"{f'#{reference}' if reference else '$'})"
    )


def _owner_history(step: _Step, options: IfcOptions) -> int:
    from .. import __version__
    from ..branding import active_brand

    brand = active_brand()
    person = step.add(
        f"IFCPERSON($,{_text(options.author or brand.display_name)},$,$,$,$,$,$)"
    )
    organisation = step.add(
        "IFCORGANIZATION($,"
        f"{_text(options.organisation or brand.display_name)},$,$,$)"
    )
    person_and_org = step.add(
        f"IFCPERSONANDORGANIZATION(#{person},#{organisation},$)"
    )
    application = step.add(
        f"IFCAPPLICATION(#{organisation},{_text(__version__)},"
        f"{_text('ProfileOS')},{_text('ProfileOS')})"
    )
    stamp = int(datetime.now(timezone.utc).timestamp())
    return step.add(
        f"IFCOWNERHISTORY(#{person_and_org},#{application},$,.ADDED.,$,$,$,{stamp})"
    )


def _units(step: _Step) -> int:
    """Metres, square metres, cubic metres, radians.

    IFC is metric-SI, and a file whose length unit is the millimetre is the
    most common reason an imported model turns up a thousand times too big.
    """
    length = step.add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    area = step.add("IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.)")
    volume = step.add("IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.)")
    angle = step.add("IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.)")
    return step.add(
        f"IFCUNITASSIGNMENT((#{length},#{area},#{volume},#{angle}))"
    )


def _properties(
    step: _Step, history: int, owner: int, name: str,
    values: dict[str, Any],
) -> None:
    """One property set attached to one element.

    Everything a coordinating architect asks about a window that is not its
    size: which system, what glass, what it is worth thermally, and the mark
    the shop calls it by — so a query on the model and a question on the phone
    give the same answer.
    """
    kept = {
        key: value for key, value in values.items()
        if value not in (None, "", 0)
    }
    if not kept:
        return

    identifiers: list[int] = []
    for key, value in kept.items():
        if isinstance(value, bool):
            wrapped = f"IFCBOOLEAN(.{'T' if value else 'F'}.)"
        elif isinstance(value, (int, float)):
            wrapped = f"IFCREAL({_number(value)})"
        else:
            wrapped = f"IFCTEXT({_text(value)})"
        identifiers.append(step.add(
            f"IFCPROPERTYSINGLEVALUE({_text(key)},$,{wrapped},$)"
        ))

    property_set = step.add(
        f"IFCPROPERTYSET({_text(compress_guid())},#{history},{_text(name)},$,"
        "(" + ",".join(f"#{i}" for i in identifiers) + "))"
    )
    step.add(
        f"IFCRELDEFINESBYPROPERTIES({_text(compress_guid())},#{history},$,$,"
        f"(#{owner}),#{property_set})"
    )


@dataclass
class _Placed:
    """One opening as it will be written: name, size and where it goes."""

    name: str
    width: float
    height: float
    sill: float
    x: float
    is_door: bool = False
    properties: dict[str, Any] = field(default_factory=dict)


def _collect(builds: Iterable[Any], options: IfcOptions) -> list[_Placed]:
    """Turn the shop's openings into placed blocks, laid out along the storey.

    Real coordinates would need a site plan this software has never been given.
    Rather than invent them, the openings are laid out in a row in the order
    they were scheduled, a metre apart — which is honest, is obviously a
    schedule rather than a survey, and is still what an architect wants when
    they are checking that the sizes match their own drawing.
    """
    placed: list[_Placed] = []
    cursor = 0.0

    for build in builds:
        opening = getattr(build, "opening", None) or build
        width = float(getattr(opening, "width", 0.0) or 0.0)
        height = float(getattr(opening, "height", 0.0) or 0.0)
        if width <= 0 or height <= 0:
            continue

        kind = str(getattr(getattr(opening, "kind", None), "value", "window"))
        panes = getattr(build, "glass", []) or []
        build_up = getattr(panes[0], "build_up", None) if panes else None

        properties: dict[str, Any] = {
            "Reference": str(getattr(opening, "reference", "") or ""),
            "System": str(getattr(opening, "system_id", "") or ""),
            "Finish": str(getattr(opening, "finish", "") or ""),
            "Glazing": build_up.describe() if build_up is not None else "",
            "NominalWidth": round(width / 1000.0, 4),
            "NominalHeight": round(height / 1000.0, 4),
        }
        if build_up is not None:
            try:
                properties["GlassUValue"] = round(build_up.u_value(), 3)
            except Exception:  # noqa: BLE001 - a build-up may be incomplete
                pass

        quantity = max(1, int(getattr(opening, "quantity", 1) or 1))
        base = str(getattr(opening, "name", "") or "Opening")
        for copy in range(quantity):
            placed.append(_Placed(
                name=base if quantity == 1 else f"{base} ({copy + 1})",
                width=width, height=height,
                sill=0.0 if kind == "door" else 900.0,
                x=cursor,
                is_door=kind == "door",
                properties=dict(properties),
            ))
            cursor += width + 1000.0
    return placed


def render_ifc(builds: Iterable[Any], options: IfcOptions | None = None) -> str:
    """The whole model as one IFC2x3 STEP file."""
    options = options or IfcOptions()
    step = _Step()
    history = _owner_history(step, options)
    units = _units(step)

    world_origin = _point(step, 0.0, 0.0, 0.0)
    world_axis = _axis(step, world_origin)
    plan_direction = _direction(step, 0.0, 1.0)
    context = step.add(
        f"IFCGEOMETRICREPRESENTATIONCONTEXT($,{_text('Model')},3,1.E-05,"
        f"#{world_axis},#{plan_direction})"
    )

    project = step.add(
        f"IFCPROJECT({_text(compress_guid())},#{history},"
        f"{_text(options.project_name)},$,$,$,$,(#{context}),#{units})"
    )

    site_placement = step.add(f"IFCLOCALPLACEMENT($,#{world_axis})")
    site = step.add(
        f"IFCSITE({_text(compress_guid())},#{history},{_text(options.site_name)},"
        f"$,$,#{site_placement},$,$,.ELEMENT.,$,$,$,$,$)"
    )
    building_placement = step.add(
        f"IFCLOCALPLACEMENT(#{site_placement},#{world_axis})"
    )
    building = step.add(
        f"IFCBUILDING({_text(compress_guid())},#{history},"
        f"{_text(options.building_name)},$,$,#{building_placement},$,$,"
        ".ELEMENT.,$,$,$)"
    )
    storey_origin = _point(step, 0.0, 0.0, options.storey_elevation / 1000.0)
    storey_axis = _axis(step, storey_origin)
    storey_placement = step.add(
        f"IFCLOCALPLACEMENT(#{building_placement},#{storey_axis})"
    )
    storey = step.add(
        f"IFCBUILDINGSTOREY({_text(compress_guid())},#{history},"
        f"{_text(options.storey_name)},$,$,#{storey_placement},$,$,.ELEMENT.,"
        f"{_number(options.storey_elevation / 1000.0)})"
    )

    step.add(
        f"IFCRELAGGREGATES({_text(compress_guid())},#{history},$,$,#{project},"
        f"(#{site}))"
    )
    step.add(
        f"IFCRELAGGREGATES({_text(compress_guid())},#{history},$,$,#{site},"
        f"(#{building}))"
    )
    step.add(
        f"IFCRELAGGREGATES({_text(compress_guid())},#{history},$,$,#{building},"
        f"(#{storey}))"
    )

    depth = max(options.reveal_depth, 1.0) / 1000.0
    elements: list[int] = []

    for entry in _collect(builds, options):
        width = entry.width / 1000.0
        height = entry.height / 1000.0

        profile_origin = _point(step, 0.0, 0.0)
        profile_axis = step.add(
            f"IFCAXIS2PLACEMENT2D(#{profile_origin},$)"
        )
        profile = step.add(
            f"IFCRECTANGLEPROFILEDEF(.AREA.,$,#{profile_axis},"
            f"{_number(width)},{_number(depth)})"
        )
        extrude_origin = _point(step, 0.0, 0.0, 0.0)
        extrude_axis = _axis(step, extrude_origin)
        up = _direction(step, 0.0, 0.0, 1.0)
        solid = step.add(
            f"IFCEXTRUDEDAREASOLID(#{profile},#{extrude_axis},#{up},"
            f"{_number(height)})"
        )
        shape = step.add(
            f"IFCSHAPEREPRESENTATION(#{context},{_text('Body')},"
            f"{_text('SweptSolid')},(#{solid}))"
        )
        product = step.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape}))")

        # The rectangle profile is centred on its own origin and the solid
        # extrudes upward from it, so the placement goes at the centre of the
        # opening in plan and at the **sill** in elevation. Centre it in Z as
        # well and every window in the model floats half its own height above
        # where it belongs.
        origin = _point(
            step,
            entry.x / 1000.0 + width / 2.0,
            0.0,
            entry.sill / 1000.0,
        )
        axis = _axis(step, origin)
        placement = step.add(
            f"IFCLOCALPLACEMENT(#{storey_placement},#{axis})"
        )

        entity = "IFCDOOR" if entry.is_door else "IFCWINDOW"
        element = step.add(
            f"{entity}({_text(compress_guid())},#{history},"
            f"{_text(entry.name)},$,$,#{placement},#{product},$,"
            f"{_number(height)},{_number(width)})"
        )
        elements.append(element)

        if options.include_properties:
            _properties(
                step, history, element,
                "Pset_ProfileOS",
                {**entry.properties, "SillHeight": round(entry.sill / 1000.0, 4)},
            )

    if elements:
        step.add(
            f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_text(compress_guid())},"
            f"#{history},$,$,(" + ",".join(f"#{i}" for i in elements) + f"),#{storey})"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    from .. import __version__
    from ..branding import active_brand

    brand = active_brand()
    header = "\n".join([
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION((" + _text("ViewDefinition [CoordinationView]")
        + "),'2;1');",
        "FILE_NAME("
        + _text(options.project_name) + ","
        + _text(stamp) + ",("
        + _text(options.author or brand.display_name) + "),("
        + _text(options.organisation or brand.display_name) + "),"
        + _text(f"ProfileOS {__version__}") + ","
        + _text("ProfileOS") + ",$);",
        f"FILE_SCHEMA(('{SCHEMA}'));",
        "ENDSEC;",
        "DATA;",
    ])

    _log.info("Wrote an IFC model with %d openings", len(elements))
    return "\n".join(
        [header] + step.lines + ["ENDSEC;", "END-ISO-10303-21;", ""]
    )


def write_ifc(
    builds: Iterable[Any], path: Any, options: IfcOptions | None = None
) -> Path:
    """Write the model to disk and return where it went."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_ifc(builds, options), encoding="utf-8")
    return target


#: Said out loud wherever this export is offered. An IFC that looks like a
#: model is trusted like a model.
LIMITATIONS_HE: tuple[str, ...] = (
    "הייצוא הוא מיקום וגודל של הפתחים, לא גיאומטריית הפרופיל. חתך, תאים "
    "ושבר תרמי אינם עוברים ל-IFC.",
    "הפתחים נפרסים בשורה בסדר הרשימה — אין לתוכנה תכנית אתר, והיא לא "
    "תמציא אחת. זהו לוח פתחים ולא מדידה.",
    "אין למדוד מתוך הקובץ הזה לצורך ייצור. לגיאומטריה מדויקת יש את "
    "ייצוא ה-DXF.",
)


__all__ = [
    "LIMITATIONS_HE",
    "SCHEMA",
    "IfcOptions",
    "compress_guid",
    "render_ifc",
    "write_ifc",
]
