"""Building an element's 3D model from the same rules that cut it.

The point of a presentation view is that the customer is looking at what will
actually be made. So this does not draw a picture of a window; it places the
same members, in the same positions, at the same sizes that the element builder
puts on the cut list, using the same system rules. If the mullion moves in the
rules it moves in the render, and nothing has to be kept in step by hand.

Where the sections come from
----------------------------
When the supplier's DXF has been ingested, the profile's real outline is swept
— chambers, gasket lips and all. When it has not, a hollow rectangle of the
right face width and depth stands in. Both paths use the same sweep, so the
picture is correct in size and position either way and gains detail as the
library fills up.

What is drawn
-------------
* Frame members, mitred at the corners the way the rules say they are cut.
* Mullions and transoms, square-cut into the frame.
* Sashes, set into their cell and standing proud of the frame by the rebate.
* Glass, sized by the glazing rules and seated in the rebate rather than
  floating in the middle of the opening.

Coordinates match the element drawing: X across, Y up from the sill, Z out of
the wall towards the outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..core.logging_setup import get_logger
from .extrude import box_section, extrude_section, plate
from .mesh import Mesh, Scene, Vec2, Vec3

_log = get_logger(__name__)


@dataclass(frozen=True)
class ViewStyle:
    """How much of the reality to draw.

    Presentation renders want the outside face and the glass; a technical
    render wants the chambers and the rebates. Both come off the same geometry,
    so this only changes what is emitted, never where anything is.
    """

    #: Sweep the real ingested outline when one is available.
    use_real_sections: bool = True
    #: Wall thickness for the stand-in box section [mm].
    fallback_wall: float = 2.0
    #: Draw the glass. Off gives a frame-only view for a fabrication drawing.
    show_glass: bool = True
    #: Draw sashes proud of the frame, as they sit when closed.
    show_sashes: bool = True
    #: Nominal profile depth when the system rules do not give one [mm].
    default_depth: float = 70.0


def _section_for(
    profile_id: str,
    face_width: float,
    depth: float,
    style: ViewStyle,
) -> tuple[list[Vec2], list[list[Vec2]]]:
    """The section to sweep for one profile, real if we have it.

    A profile drawn in the DXF is in *drawing* coordinates; the sweep wants it
    centred on its own axis, so it is translated to its bounding-box centre.
    Without that every member would be offset by wherever the draughtsman
    happened to put the origin.
    """
    if style.use_real_sections:
        try:
            from ..core.registry import PROFILES

            definition = PROFILES.get_or_none(profile_id)
        except Exception:  # noqa: BLE001 - the registry is optional
            definition = None

        if definition is not None:
            try:
                from ..geometry import section_from_profile

                loaded = section_from_profile(definition)
                polygon = loaded.polygon
                parts = getattr(polygon, "geoms", None) or [polygon]
                largest = max(parts, key=lambda part: part.area)
                xs = [p[0] for p in largest.exterior.coords]
                ys = [p[1] for p in largest.exterior.coords]
                cx = (min(xs) + max(xs)) / 2.0
                cy = (min(ys) + max(ys)) / 2.0
                outer = [(x - cx, y - cy) for x, y in list(largest.exterior.coords)[:-1]]
                holes = [
                    [(x - cx, y - cy) for x, y in list(ring.coords)[:-1]]
                    for ring in largest.interiors
                ]
                return outer, holes
            except Exception as exc:  # noqa: BLE001 - fall back, do not fail
                _log.info(
                    "Could not sweep the real section for %s (%s); using a "
                    "stand-in of the right size",
                    profile_id,
                    exc,
                )

    return box_section(face_width, depth, style.fallback_wall)


def _profile_depth(rules: Any, style: ViewStyle) -> float:
    """The system's construction depth, if the rules record one."""
    for attribute in ("construction_depth", "depth", "frame_depth"):
        value = getattr(getattr(rules, "frame", None), attribute, None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        value = getattr(rules, attribute, None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return style.default_depth


def build_element_scene(
    build: Any,
    *,
    style: ViewStyle | None = None,
    index: int = 0,
) -> Scene:
    """Model one built element.

    ``build`` is an :class:`~profileos.elements.builder.ElementBuild`; it is
    duck-typed so this module does not drag the element package in.
    """
    from ..elements.builder import ElementBuilder

    style = style or ViewStyle()
    opening = build.opening
    rules = build.rules

    scene = Scene(name=opening.name or opening.element_id)
    scene.metadata = {
        "element_id": opening.element_id,
        "size": [opening.width, opening.height],
        "system": opening.system_id,
        "quantity": opening.quantity,
    }

    depth = _profile_depth(rules, style)
    face = rules.frame.face_width
    builder = ElementBuilder(rules=rules)
    inner = builder.inner_opening(opening, rules)
    rects = builder.cell_rects(opening, rules)

    half_face = face / 2.0
    mitred = rules.frame.mitred_corners
    corner_angle = 45.0 if mitred else 90.0

    frame_profile = rules.profile_for("frame")
    outer, holes = _section_for(frame_profile, face, depth, style)

    # -- frame ---------------------------------------------------------------- #
    # Each member runs along its own centreline, which is half a face width in
    # from the element's outer edge. Mitring at 45 then takes the solid out to
    # the corner exactly, which is why the members are not lengthened here.
    members: list[tuple[str, Vec3, Vec3, Vec3, float, float]] = [
        (
            "frame sill",
            (half_face, half_face, 0.0),
            (opening.width - half_face, half_face, 0.0),
            (0.0, 1.0, 0.0),
            corner_angle,
            corner_angle,
        ),
        (
            "frame head",
            (half_face, opening.height - half_face, 0.0),
            (opening.width - half_face, opening.height - half_face, 0.0),
            (0.0, -1.0, 0.0),
            corner_angle,
            corner_angle,
        ),
        (
            "frame jamb left",
            (half_face, half_face, 0.0),
            (half_face, opening.height - half_face, 0.0),
            (1.0, 0.0, 0.0),
            corner_angle,
            corner_angle,
        ),
        (
            "frame jamb right",
            (opening.width - half_face, half_face, 0.0),
            (opening.width - half_face, opening.height - half_face, 0.0),
            (-1.0, 0.0, 0.0),
            corner_angle,
            corner_angle,
        ),
    ]
    for name, start, end, across, angle_a, angle_b in members:
        # The sweep's "up" is the section's depth axis, always out of the wall;
        # `across` only records which way the member faces for the caller.
        scene.add(
            extrude_section(
                outer, start, end, holes=holes, up=(0.0, 0.0, 1.0),
                start_angle=angle_a, end_angle=angle_b,
                name=name, material="aluminium",
                metadata={"profile": frame_profile, "role": "frame",
                          "faces": list(across)},
            )
        )

    # -- mullions and transoms -------------------------------------------------- #
    mullion_face = rules.mullion.face_width
    mullion_profile = rules.profile_for("mullion")
    m_outer, m_holes = _section_for(mullion_profile, mullion_face, depth, style)

    for position in opening.mullion_positions:
        scene.add(
            extrude_section(
                m_outer,
                (position, inner.y, 0.0),
                (position, inner.top, 0.0),
                holes=m_holes, up=(0.0, 0.0, 1.0),
                name=f"mullion @{position:.0f}", material="aluminium",
                metadata={"profile": mullion_profile, "role": "mullion"},
            )
        )

    transom_profile = rules.profile_for("transom")
    t_outer, t_holes = _section_for(transom_profile, mullion_face, depth, style)
    for position in opening.transom_positions:
        # A transom spans one bay at a time, stopping at each mullion.
        edges = [inner.x] + list(opening.mullion_positions) + [inner.right]
        half_m = mullion_face / 2.0
        for bay in range(len(edges) - 1):
            left = edges[bay] + (half_m if bay > 0 else 0.0)
            right = edges[bay + 1] - (half_m if bay + 1 < len(edges) - 1 else 0.0)
            if right - left < 1.0:
                continue
            scene.add(
                extrude_section(
                    t_outer,
                    (left, position, 0.0),
                    (right, position, 0.0),
                    holes=t_holes, up=(0.0, 0.0, 1.0),
                    name=f"transom @{position:.0f} bay {bay + 1}",
                    material="aluminium",
                    metadata={"profile": transom_profile, "role": "transom"},
                )
            )

    # -- cells: sashes and glass ------------------------------------------------ #
    sash_face = rules.sash.sash_face_width
    sash_profile = rules.profile_for("sash")
    s_outer, s_holes = _section_for(sash_profile, sash_face, depth * 0.8, style)

    for cell in opening.all_cells():
        rect = rects.get(cell.key)
        if rect is None or rect.width <= 0 or rect.height <= 0:
            continue

        glazing = rect
        if cell.sash is not None and style.show_sashes:
            overlap = rules.sash.frame_overlap
            sash_rect_x = rect.x - overlap
            sash_rect_y = rect.y - overlap
            sash_w = rect.width + 2 * overlap
            sash_h = rect.height + 2 * overlap
            half_s = sash_face / 2.0
            # A closed sash stands proud of the frame by a little; showing it
            # flush makes an opening light indistinguishable from a fixed one.
            z = depth * 0.12
            for name, start, end in [
                ("sash bottom",
                 (sash_rect_x + half_s, sash_rect_y + half_s, z),
                 (sash_rect_x + sash_w - half_s, sash_rect_y + half_s, z)),
                ("sash top",
                 (sash_rect_x + half_s, sash_rect_y + sash_h - half_s, z),
                 (sash_rect_x + sash_w - half_s, sash_rect_y + sash_h - half_s, z)),
                ("sash left",
                 (sash_rect_x + half_s, sash_rect_y + half_s, z),
                 (sash_rect_x + half_s, sash_rect_y + sash_h - half_s, z)),
                ("sash right",
                 (sash_rect_x + sash_w - half_s, sash_rect_y + half_s, z),
                 (sash_rect_x + sash_w - half_s, sash_rect_y + sash_h - half_s, z)),
            ]:
                scene.add(
                    extrude_section(
                        s_outer, start, end, holes=s_holes, up=(0.0, 0.0, 1.0),
                        start_angle=45.0, end_angle=45.0,
                        name=f"{name} {cell.key}", material="aluminium",
                        metadata={"profile": sash_profile, "role": "sash",
                                  "cell": list(cell.key)},
                    )
                )
            glazing = type(rect)(
                sash_rect_x + sash_face,
                sash_rect_y + sash_face,
                sash_w - 2 * sash_face,
                sash_h - 2 * sash_face,
            )

        if not style.show_glass or cell.panel:
            if cell.panel:
                scene.add(
                    plate(
                        glazing.width, glazing.height, 24.0,
                        (glazing.x + glazing.width / 2.0,
                         glazing.y + glazing.height / 2.0, 0.0),
                        name=f"panel {cell.key}", material="panel",
                        metadata={"cell": list(cell.key)},
                    )
                )
            continue

        # Glass spans the daylight opening plus the edge cover, less the
        # clearance — the same arithmetic the cut list uses.
        deduction = rules.glass.deduction()
        pane_w = glazing.width - deduction
        pane_h = glazing.height - deduction
        if pane_w <= 0 or pane_h <= 0:
            continue
        thickness = 24.0
        scene.add(
            plate(
                pane_w, pane_h, thickness,
                (glazing.x + glazing.width / 2.0,
                 glazing.y + glazing.height / 2.0,
                 (depth * 0.12 if cell.sash is not None and style.show_sashes else 0.0)),
                name=f"glass {cell.key}", material="glass",
                metadata={"cell": list(cell.key),
                          "size": [round(pane_w, 1), round(pane_h, 1)]},
            )
        )

    _log.info(
        "Modelled %s: %d mesh(es), %d triangles",
        opening.element_id, len(scene.meshes), scene.triangle_count,
    )
    return scene


def build_elevation_scene(
    builds: Sequence[Any], *, style: ViewStyle | None = None, gap: float = 250.0
) -> Scene:
    """Lay several elements out side by side, as they sit on the elevation."""
    combined = Scene(name="elevation")
    cursor = 0.0
    for index, build in enumerate(builds):
        part = build_element_scene(build, style=style, index=index)
        for mesh in part.meshes:
            combined.add(mesh.transformed((cursor, 0.0, 0.0)))
        cursor += build.opening.width + gap
    combined.metadata = {"elements": len(builds)}
    return combined


__all__ = ["ViewStyle", "build_element_scene", "build_elevation_scene"]
