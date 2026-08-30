"""Wall sections: where the aluminium meets the building.

This is the drawing the architect actually looks at, because it is the one that
says whether the water gets in. A head detail is not a picture of a profile —
it is a statement about where the membrane turns, what the flashing laps over,
which side of the insulation the frame sits on, and what the anchor is fixed to.

The building fabric is data (:class:`WallBuildUp`), listed from the room
outwards, so a stone-clad insulated concrete wall and a rendered block wall
produce genuinely different details rather than the same picture with different
labels. The aluminium is drawn from the real profile outline when one has been
imported, and schematically when none has — and the drawing says which, because
a schematic detail issued as a real one is how a frame ends up 12 mm too far
forward of the insulation line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Sequence

from . import dimension as dim
from .model import (
    Anchor,
    Drawing,
    Hatch,
    HatchPattern,
    Line,
    Point,
    Polyline,
    Text,
    rectangle,
)


class Detail(StrEnum):
    """Which junction is being cut."""

    HEAD = "head"
    SILL = "sill"
    JAMB = "jamb"
    MULLION = "mullion"
    TRANSOM = "transom"

    def label(self, language: Any = None) -> str:
        """What this detail is called, in the language the sheet is issued in."""
        from ..i18n import translate

        return translate(f"drawing.{self.value}", language)

    @property
    def hebrew(self) -> str:
        return self.label("he")

    @property
    def english(self) -> str:
        return self.label("en").capitalize()

    @property
    def is_vertical_cut(self) -> bool:
        """True when the section is cut vertically, so the wall runs across."""
        return self in (Detail.HEAD, Detail.SILL, Detail.TRANSOM)


@dataclass(frozen=True)
class _Axes:
    """How a detail's two directions map onto the page.

    Every detail has the same two directions — *through* the wall (inside to
    outside) and *along* it (away from the opening) — but they land on
    different page axes depending on whether the cut is vertical or horizontal,
    and the opening is on a different side for a head than for a sill. Keeping
    that in one place is what stops a head detail coming out drawn as a sill,
    which is a mistake that looks entirely plausible on the sheet.
    """

    #: True when the wall's thickness runs across the page (a head, sill or
    #: transom, cut vertically); False for a jamb or mullion seen in plan.
    vertical: bool
    #: +1 when the opening is above (or to the right of) the wall, -1 below.
    sense: float

    def xy(self, along: float, through: float) -> Point:
        """``along`` measures away from the opening; ``through`` inside-out."""
        run = along * self.sense
        return (through, run) if self.vertical else (run, through)


_AXES: dict["Detail", _Axes] = {}


def axes_for(detail: "Detail") -> _Axes:
    return _AXES[detail]


@dataclass(frozen=True)
class WallLayer:
    """One layer of the wall, with the thickness it is built at."""

    name: str
    hebrew: str
    thickness: float
    pattern: HatchPattern
    layer: str = "STRUCTURE"
    #: A membrane is drawn as a line rather than a band; it has no useful
    #: thickness at 1:5 and drawing it as one makes the detail unreadable.
    is_membrane: bool = False


@dataclass
class WallBuildUp:
    """The wall, listed from the room outwards."""

    name: str = "Insulated concrete with stone cladding"
    hebrew: str = "בטון מבודד עם חיפוי אבן"
    layers: tuple[WallLayer, ...] = ()

    @property
    def thickness(self) -> float:
        return sum(layer.thickness for layer in self.layers)

    def offsets(self) -> list[tuple[WallLayer, float, float]]:
        """Each layer with the inside and outside face it occupies [mm]."""
        result: list[tuple[WallLayer, float, float]] = []
        position = 0.0
        for layer in self.layers:
            result.append((layer, position, position + layer.thickness))
            position += layer.thickness
        return result


#: A wall the way it is actually built in Israel: reinforced concrete or block,
#: insulation outside it, a ventilated cavity and stone cladding on the face.
STONE_CLAD_CONCRETE = WallBuildUp(
    name="Concrete, insulation, cavity, stone cladding",
    hebrew="בטון, בידוד, מרווח מאוורר, חיפוי אבן",
    layers=(
        WallLayer("Internal plaster", "טיח פנים", 15.0, HatchPattern.NONE, "CLADDING"),
        WallLayer("Reinforced concrete", "בטון מזוין", 200.0, HatchPattern.CONCRETE),
        WallLayer("Waterproof membrane", "קרום איטום", 3.0, HatchPattern.NONE, "MEMBRANE",
                  is_membrane=True),
        WallLayer("Thermal insulation", "בידוד תרמי", 50.0, HatchPattern.INSULATION,
                  "INSULATION"),
        WallLayer("Ventilated cavity", "מרווח מאוורר", 30.0, HatchPattern.NONE, "CLADDING"),
        WallLayer("Stone cladding", "חיפוי אבן", 30.0, HatchPattern.STONE, "CLADDING"),
    ),
)

#: The lighter alternative: hollow block, rendered outside.
RENDERED_BLOCK = WallBuildUp(
    name="Block wall, rendered",
    hebrew="קיר בלוקים בטיח",
    layers=(
        WallLayer("Internal plaster", "טיח פנים", 15.0, HatchPattern.NONE, "CLADDING"),
        WallLayer("Hollow blockwork", "בלוקים", 200.0, HatchPattern.BLOCKWORK),
        WallLayer("Thermal insulation", "בידוד תרמי", 50.0, HatchPattern.INSULATION,
                  "INSULATION"),
        WallLayer("External render", "טיח חוץ", 20.0, HatchPattern.NONE, "CLADDING"),
    ),
)


@dataclass
class SectionStyle:
    """How a detail is drawn and how much of it is annotated."""

    scale: int = 5
    #: How far the wall is drawn either side of the frame [mm].
    wall_run: float = 220.0
    #: Frame depth when no real profile has been imported [mm].
    schematic_depth: float = 65.0
    #: Frame face when no real profile has been imported [mm].
    schematic_face: float = 52.0
    glass_thickness: float = 24.0
    #: How far the frame sits back from the outer face of the structure [mm].
    #: Positive moves it outward. Aligning the frame with the insulation line
    #: is what keeps the thermal break continuous.
    frame_setback: float = 0.0
    #: Perimeter joint between frame and structure, per side [mm].
    perimeter_joint: float = 10.0
    show_annotation: bool = True
    show_dimensions: bool = True
    #: Whether the detail titles itself. A detail issued on its own needs the
    #: title inside the drawing; one placed on a sheet is already labelled by
    #: the viewport under it, and two titles in the same place read as a fault
    #: in the drawing rather than a caption.
    show_title: bool = True
    text_height: float = 2.2
    #: The language the labels and the title are written in.
    language: Any = "he"

    @property
    def dim_style(self) -> dim.DimensionStyle:
        return dim.DimensionStyle(text_height=self.text_height)


@dataclass
class SectionResult:
    """A detail, and an honest note about how much of it is real."""

    drawing: Drawing
    detail: Detail
    schematic: bool
    notes: list[str] = field(default_factory=list)


def _wall_band(
    build_up: WallBuildUp, axes: _Axes, *, along: tuple[float, float]
) -> list[Any]:
    """The wall layers as bands running through the section.

    ``along`` is the stretch of wall drawn, measured away from the opening. A
    membrane is drawn as a line rather than a band: at 1:5 its three
    millimetres are a smear, and a smear is not a detail anybody can build to.
    """
    entities: list[Any] = []
    start, end = along
    for layer, inner, outer in build_up.offsets():
        if layer.is_membrane:
            position = (inner + outer) / 2.0
            entities.append(
                Line(
                    layer=layer.layer,
                    start=axes.xy(start, position),
                    end=axes.xy(end, position),
                )
            )
            continue
        entities.append(
            Hatch(
                layer=layer.layer,
                boundary=(
                    axes.xy(start, inner),
                    axes.xy(end, inner),
                    axes.xy(end, outer),
                    axes.xy(start, outer),
                ),
                pattern=layer.pattern,
                spacing=2.0,
            )
        )
    return entities


def _profile_outline(profile: Any, axes: _Axes, along: float, through: float) -> list[Any]:
    """The real profile outline from an imported section, placed in the detail.

    The imported section's own x runs through the wall and its y along it, so
    the two are handed to the axis helper the same way every other coordinate
    in this module is.
    """
    from ..geometry import section_from_profile

    section = section_from_profile(profile)
    entities: list[Any] = []
    for region in section.topology.regions:
        if region.depth % 2:
            continue
        for index, ring in enumerate([region.shell, *region.holes]):
            entities.append(
                Polyline(
                    layer="ALU-CUT" if index == 0 else "ALU-SEEN",
                    points=tuple(
                        axes.xy(along + y, through + x) for x, y in ring.points
                    ),
                    closed=True,
                )
            )
    return entities


def wall_section(
    detail: Detail,
    *,
    build_up: WallBuildUp = STONE_CLAD_CONCRETE,
    style: SectionStyle | None = None,
    profile: Any = None,
    label: str = "",
) -> SectionResult:
    """One junction between the aluminium and the building, drawn to scale."""
    style = style or SectionStyle()
    if detail in (Detail.MULLION, Detail.TRANSOM):
        return _profile_junction(detail, style=style, profile=profile, label=label)

    axes = axes_for(detail)
    scale = float(style.scale)
    drawing = Drawing(name=f"{detail.value}-detail")
    notes: list[str] = []
    schematic = profile is None

    depth = style.schematic_depth
    face = style.schematic_face
    joint = style.perimeter_joint
    run = style.wall_run

    # The wall stops short of the frame by the perimeter joint, so the frame is
    # shown sitting in a hole rather than butted against a solid.
    drawing.extend(_wall_band(build_up, axes, along=(-run, -joint - face)))

    frame_inner = build_up.thickness - depth - style.frame_setback
    if profile is not None:
        drawing.extend(_profile_outline(profile, axes, -face, frame_inner))
    else:
        drawing.add(
            Hatch(
                layer="ALU-CUT",
                boundary=(
                    axes.xy(-face, frame_inner),
                    axes.xy(0.0, frame_inner),
                    axes.xy(0.0, frame_inner + depth),
                    axes.xy(-face, frame_inner + depth),
                ),
                pattern=HatchPattern.ALUMINIUM,
                spacing=1.2,
            )
        )
        notes.append(_schematic_note(style.language))

    # The glass runs out of the frame into the opening.
    glass_centre = frame_inner + depth / 2.0
    half = style.glass_thickness / 2.0
    drawing.add(
        Hatch(
            layer="GLASS",
            boundary=(
                axes.xy(0.0, glass_centre - half),
                axes.xy(90.0, glass_centre - half),
                axes.xy(90.0, glass_centre + half),
                axes.xy(0.0, glass_centre + half),
            ),
            pattern=HatchPattern.NONE,
            fill="#cfe4f2",
        )
    )

    drawing.extend(_junction(detail, axes, build_up, style, frame_inner, depth, face))
    if style.show_annotation:
        drawing.extend(_annotate(axes, build_up, style, frame_inner, depth, face))
    if style.show_dimensions:
        drawing.extend(_dimension(axes, build_up, style, frame_inner, depth, face))

    _add_title(drawing, detail, style, label)
    return SectionResult(drawing=drawing, detail=detail, schematic=schematic, notes=notes)


def _detail_title(detail: Detail, language: Any) -> str:
    local = detail.label(language)
    english = detail.label("en").capitalize()
    return local if local == english else f"{local} / {english}"


def _schematic_note(language: Any) -> str:
    """Said in the reader's language and in English, since both read the sheet."""
    from ..i18n import translate

    local = translate("drawing.schematic_profile", language)
    english = translate("drawing.schematic_profile", "en")
    return local if local == english else f"{local}. {english}."


def _add_title(drawing: Drawing, detail: Detail, style: SectionStyle, label: str) -> None:
    if not style.show_title:
        return
    left, bottom, right, _ = drawing.bounds()
    drawing.add(
        Text(
            layer="TEXT",
            position=((left + right) / 2.0, bottom - 18.0 * style.scale / 5.0),
            value=label or _detail_title(detail, style.language),
            height=style.text_height * 1.6,
            anchor=Anchor.CENTRE,
            bold=True,
        )
    )


def _profile_junction(
    detail: Detail, *, style: SectionStyle, profile: Any, label: str
) -> SectionResult:
    """A mullion or transom: aluminium to aluminium, with no wall in the cut.

    Drawn separately because it has no building fabric at all — treating it as
    a wall detail with the wall omitted produces a drawing with annotation
    leaders pointing at nothing.
    """
    axes = axes_for(detail)
    drawing = Drawing(name=f"{detail.value}-detail")
    notes: list[str] = []
    schematic = profile is None

    depth = style.schematic_depth
    face = style.schematic_face
    half_face = face / 2.0
    half_glass = style.glass_thickness / 2.0
    centre = depth / 2.0

    if profile is not None:
        drawing.extend(_profile_outline(profile, axes, -half_face, 0.0))
    else:
        drawing.add(
            Hatch(
                layer="ALU-CUT",
                boundary=(
                    axes.xy(-half_face, 0.0),
                    axes.xy(half_face, 0.0),
                    axes.xy(half_face, depth),
                    axes.xy(-half_face, depth),
                ),
                pattern=HatchPattern.ALUMINIUM,
                spacing=1.2,
            )
        )
        notes.append(_schematic_note(style.language))

    for direction in (1.0, -1.0):
        near = (half_face + 4.0) * direction
        far = (half_face + 110.0) * direction
        drawing.add(
            Hatch(
                layer="GLASS",
                boundary=(
                    axes.xy(near, centre - half_glass),
                    axes.xy(far, centre - half_glass),
                    axes.xy(far, centre + half_glass),
                    axes.xy(near, centre + half_glass),
                ),
                pattern=HatchPattern.NONE,
                fill="#cfe4f2",
            )
        )
        # The gasket that seals the pane against the profile, both sides.
        for offset in (centre - half_glass - 3.0, centre + half_glass + 3.0):
            drawing.add(
                Hatch(
                    layer="GASKET",
                    boundary=(
                        axes.xy(half_face * direction, offset - 2.5),
                        axes.xy(near + 12.0 * direction, offset - 2.5),
                        axes.xy(near + 12.0 * direction, offset + 2.5),
                        axes.xy(half_face * direction, offset + 2.5),
                    ),
                    pattern=HatchPattern.NONE,
                    fill="#6b6b6b",
                )
            )

    if style.show_annotation:
        drawing.extend(
            dim.leader(
                axes.xy(0.0, centre),
                axes.xy(style.wall_run * 0.7, centre + depth),
                "פרופיל עמוד / mullion profile"
                if detail is Detail.MULLION
                else "פרופיל קורה / transom profile",
                scale=float(style.scale),
                style=style.dim_style,
            )
        )
        drawing.extend(
            dim.leader(
                axes.xy(half_face + 60.0, centre),
                axes.xy(style.wall_run * 0.7, centre - depth),
                "יחידת זיגוג / glazing unit",
                scale=float(style.scale),
                style=style.dim_style,
            )
        )
    if style.show_dimensions:
        drawing.extend(
            dim.linear(
                axes.xy(-half_face, 0.0),
                axes.xy(half_face, 0.0),
                -style.wall_run * 0.3,
                scale=float(style.scale),
                style=style.dim_style,
            )
        )
    _add_title(drawing, detail, style, label)
    return SectionResult(drawing=drawing, detail=detail, schematic=schematic, notes=notes)


def _junction(
    detail: Detail,
    axes: _Axes,
    build_up: WallBuildUp,
    style: SectionStyle,
    frame_inner: float,
    depth: float,
    face: float,
) -> list[Any]:
    """The parts that make the junction weather-tight, which is the point."""
    entities: list[Any] = []
    joint = style.perimeter_joint
    frame_outer = frame_inner + depth

    # Perimeter sealant on both faces, filling the joint the frame sits in.
    for through in (frame_inner, frame_outer):
        entities.append(
            Hatch(
                layer="SEALANT",
                boundary=(
                    axes.xy(-face - joint, through - 6.0),
                    axes.xy(-face, through - 6.0),
                    axes.xy(-face, through + 6.0),
                    axes.xy(-face - joint, through + 6.0),
                ),
                pattern=HatchPattern.NONE,
                fill="#9a9a9a",
            )
        )

    # The anchor goes back into whatever is structural, not into insulation.
    structural = next(
        (
            (inner, outer)
            for layer, inner, outer in build_up.offsets()
            if layer.pattern in (HatchPattern.CONCRETE, HatchPattern.BLOCKWORK)
        ),
        (0.0, build_up.thickness),
    )
    anchor_at = (structural[0] + structural[1]) / 2.0
    entities.append(
        Polyline(
            layer="FIXING",
            points=(
                axes.xy(-face - joint - 45.0, anchor_at - 3.0),
                axes.xy(-face + 6.0, anchor_at - 3.0),
                axes.xy(-face + 6.0, anchor_at + 3.0),
                axes.xy(-face - joint - 45.0, anchor_at + 3.0),
            ),
            closed=True,
        )
    )

    if detail is Detail.SILL:
        # The sill is the junction that leaks. A sub-sill flashing carries the
        # water out past the frame, and the membrane turns up behind it so
        # anything that gets past the seal still drains outwards.
        entities.append(
            Polyline(
                layer="FLASHING",
                points=(
                    axes.xy(-face - joint - 70.0, frame_inner + 6.0),
                    axes.xy(8.0, frame_inner + 6.0),
                    axes.xy(8.0, frame_inner - 8.0),
                    axes.xy(2.0, frame_inner - 8.0),
                ),
                closed=False,
            )
        )
        entities.append(
            Line(
                layer="MEMBRANE",
                start=axes.xy(-face - joint - 70.0, frame_inner + 6.0),
                end=axes.xy(-face - joint - 70.0, frame_inner + 70.0),
            )
        )
    elif detail is Detail.HEAD:
        # Without a drip over the head the water runs back along the soffit and
        # in over the top of the perimeter seal.
        entities.append(
            Polyline(
                layer="FLASHING",
                points=(
                    axes.xy(-face - joint - 45.0, frame_outer - 6.0),
                    axes.xy(12.0, frame_outer - 6.0),
                    axes.xy(12.0, frame_outer + 12.0),
                ),
                closed=False,
            )
        )
    return entities


def _annotate(
    axes: _Axes,
    build_up: WallBuildUp,
    style: SectionStyle,
    frame_inner: float,
    depth: float,
    face: float,
) -> list[Any]:
    """Leaders naming every layer, because an unlabelled band is a guess."""
    entities: list[Any] = []
    scale = float(style.scale)
    run = style.wall_run

    # The elbows are stepped along the wall so the notes stack instead of
    # landing on top of one another — six labels on one line is six labels
    # nobody can read.
    layers = build_up.offsets()
    for index, (layer, inner, outer) in enumerate(layers):
        middle = (inner + outer) / 2.0
        step = -run * (0.55 + 0.16 * (len(layers) - 1 - index))
        entities.extend(
            dim.leader(
                axes.xy(step * 0.55, middle),
                axes.xy(step, middle),
                f"{layer.hebrew} / {layer.name} {layer.thickness:g}",
                scale=scale,
                style=style.dim_style,
            )
        )

    entities.extend(
        dim.leader(
            axes.xy(-face / 2.0, frame_inner + depth / 2.0),
            axes.xy(run * 0.75, frame_inner + depth + 30.0),
            "פרופיל אלומיניום / aluminium frame",
            scale=scale,
            style=style.dim_style,
        )
    )
    entities.extend(
        dim.leader(
            axes.xy(55.0, frame_inner + depth / 2.0),
            axes.xy(run * 0.75, frame_inner - 30.0),
            "יחידת זיגוג / glazing unit",
            scale=scale,
            style=style.dim_style,
        )
    )
    return entities


def _dimension(
    axes: _Axes,
    build_up: WallBuildUp,
    style: SectionStyle,
    frame_inner: float,
    depth: float,
    face: float,
) -> list[Any]:
    """The three numbers a fitter needs: joint, frame depth, wall thickness."""
    scale = float(style.scale)
    run = style.wall_run
    offset = run * 0.32

    entities = list(
        dim.linear(
            axes.xy(-face - style.perimeter_joint, frame_inner),
            axes.xy(-face, frame_inner),
            offset,
            scale=scale,
            style=style.dim_style,
        )
    )
    entities.extend(
        dim.linear(
            axes.xy(-face, frame_inner),
            axes.xy(-face, frame_inner + depth),
            offset,
            scale=scale,
            style=style.dim_style,
        )
    )
    # Placed well clear of the annotation, which runs down the same side.
    entities.extend(
        dim.linear(
            axes.xy(-run * 1.12, 0.0),
            axes.xy(-run * 1.12, build_up.thickness),
            -offset,
            scale=scale,
            style=style.dim_style,
        )
    )
    return entities


_AXES.update(
    {
        # A sill is cut vertically with the opening above it; a head is the
        # same cut with the opening below, which is the only difference.
        Detail.SILL: _Axes(vertical=True, sense=1.0),
        Detail.HEAD: _Axes(vertical=True, sense=-1.0),
        Detail.TRANSOM: _Axes(vertical=True, sense=1.0),
        # A jamb is seen in plan, with the opening to the right of the wall.
        Detail.JAMB: _Axes(vertical=False, sense=1.0),
        Detail.MULLION: _Axes(vertical=False, sense=1.0),
    }
)


__all__ = [
    "Detail",
    "RENDERED_BLOCK",
    "STONE_CLAD_CONCRETE",
    "SectionResult",
    "SectionStyle",
    "WallBuildUp",
    "WallLayer",
    "wall_section",
]
