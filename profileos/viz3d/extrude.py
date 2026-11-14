"""Sweeping a profile section along a member, with real mitres.

An aluminium member is a section swept along a straight axis and cut off at
each end by a plane. That is exactly what the shop does — the saw *is* a plane
— so modelling it that way makes the 3D solid the same object the cut list
describes, rather than a decorative approximation of it.

How the mitre is made
---------------------
Not by building a prism and subtracting a wedge. Each vertex of the section is
carried along the axis to wherever the end plane happens to intersect it, so a
45° cut simply gives the far vertices a longer travel than the near ones. The
result is exact, the end cap is planar by construction, and there is no boolean
operation to go wrong.

The consequence worth stating: a mitred member's *volume* is smaller than
``area × nominal length``, by exactly the wedge the saw took off. That is a
number the volume check can be held to, and it is why the check compares
against the centreline length rather than the outer length.

Frames
------
A member is given as two points and an "up" direction. From those:

* **w** runs along the member, start to end,
* **t** is the section's depth axis (into the wall), taken from ``up`` with any
  component along ``w`` removed so the frame stays orthogonal,
* **s** completes the right-handed set and is the section's width axis.

A section point ``(u, v)`` therefore lands at ``origin + u·s + v·t + λ·w``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .mesh import (
    Mesh,
    MeshError,
    Vec2,
    Vec3,
    add,
    cross,
    dot,
    fuse_rings,
    normalise,
    scale,
    signed_area,
    sub,
    triangulate,
)


@dataclass(frozen=True)
class Plane:
    """A cutting plane: every point ``p`` with ``dot(p - point, normal) = 0``."""

    point: Vec3
    normal: Vec3

    def distance(self, p: Vec3) -> float:
        return dot(sub(p, self.point), self.normal)


@dataclass(frozen=True)
class Frame:
    """An orthonormal frame for a swept member."""

    origin: Vec3
    s: Vec3
    t: Vec3
    w: Vec3
    span: float

    def at(self, u: float, v: float, lam: float) -> Vec3:
        return add(
            self.origin,
            add(add(scale(self.s, u), scale(self.t, v)), scale(self.w, lam)),
        )


def build_frame(start: Vec3, end: Vec3, up: Vec3 = (0.0, 0.0, 1.0)) -> Frame:
    """The sweep frame for a member running ``start`` to ``end``."""
    direction = sub(end, start)
    span = math.sqrt(dot(direction, direction))
    if span < 1e-9:
        raise MeshError("A member needs two distinct ends", start=start, end=end)
    w = normalise(direction)

    # Remove the part of `up` that runs along the member, so the frame is
    # orthogonal. If nothing is left, `up` was parallel to the member and the
    # caller has to say which way is up some other way.
    projection = dot(up, w)
    t_raw = sub(up, scale(w, projection))
    if math.sqrt(dot(t_raw, t_raw)) < 1e-9:
        raise MeshError(
            "The 'up' direction runs along the member, so the section has no "
            "orientation; pass a direction across it",
            member=(start, end),
        )
    t = normalise(t_raw)
    s = cross(t, w)
    return Frame(origin=start, s=s, t=t, w=w, span=span)


def mitre_plane(frame: Frame, at_end: bool, angle_deg: float) -> Plane:
    """The cut plane for one end of a member.

    ``angle_deg`` is measured the way a saw is set: 90 is a square cut, 45 the
    standard mitre. The plane is rotated about the section's depth axis, which
    is how a mitre saw actually swings.
    """
    lam = frame.span if at_end else 0.0
    point = frame.at(0.0, 0.0, lam)
    # A square cut's normal is the member axis. Tilting by (90 - angle) swings
    # it towards the width axis, and the two ends tilt opposite ways so a
    # mitred corner closes rather than opening into a V.
    tilt = math.radians(90.0 - angle_deg)
    sign = 1.0 if at_end else -1.0
    normal = add(
        scale(frame.w, sign * math.cos(tilt)),
        scale(frame.s, math.sin(tilt)),
    )
    return Plane(point=point, normal=normalise(normal))


def _lambda_on_plane(frame: Frame, u: float, v: float, plane: Plane) -> float:
    """Where the line through section point ``(u, v)`` meets ``plane``."""
    base = frame.at(u, v, 0.0)
    denominator = dot(frame.w, plane.normal)
    if abs(denominator) < 1e-9:
        raise MeshError(
            "The cut plane is parallel to the member, so it never crosses it; "
            "an end angle of 0 or 180 degrees is not a cut",
        )
    return -plane.distance(base) / denominator


def extrude_section(
    outer: Sequence[Vec2],
    start: Vec3,
    end: Vec3,
    *,
    holes: Sequence[Sequence[Vec2]] = (),
    up: Vec3 = (0.0, 0.0, 1.0),
    start_angle: float = 90.0,
    end_angle: float = 90.0,
    name: str = "member",
    material: str = "aluminium",
    metadata: dict[str, object] | None = None,
) -> Mesh:
    """Sweep a section along a member and cap it with its two cut planes.

    ``outer`` and ``holes`` are the section's rings in millimetres, in the
    section's own ``(width, depth)`` plane. The sweep runs from ``start`` to
    ``end``; those are the *centreline* ends, so a mitre eats into the solid
    either side of them rather than adding to it.
    """
    frame = build_frame(start, end, up)
    plane_start = mitre_plane(frame, at_end=False, angle_deg=start_angle)
    plane_end = mitre_plane(frame, at_end=True, angle_deg=end_angle)

    ring = fuse_rings(list(outer), holes)
    triangles = triangulate(list(outer), holes)
    if signed_area(ring) < 0:
        ring = list(reversed(ring))
        count = len(ring)
        triangles = [
            (count - 1 - a, count - 1 - c, count - 1 - b) for a, b, c in triangles
        ]

    mesh = Mesh(name=name, material=material, metadata=dict(metadata or {}))

    lambdas_start: list[float] = []
    lambdas_end: list[float] = []
    for u, v in ring:
        lambdas_start.append(_lambda_on_plane(frame, u, v, plane_start))
        lambdas_end.append(_lambda_on_plane(frame, u, v, plane_end))

    for index, (u, v) in enumerate(ring):
        if lambdas_end[index] <= lambdas_start[index] + 1e-9:
            raise MeshError(
                "The two end cuts cross inside the member, so the solid would "
                "be inside out; the piece is shorter than its own mitres",
                name=name,
                length=round(frame.span, 2),
                start_angle=start_angle,
                end_angle=end_angle,
            )
        mesh.add_vertex(frame.at(u, v, lambdas_start[index]))
    for index, (u, v) in enumerate(ring):
        mesh.add_vertex(frame.at(u, v, lambdas_end[index]))

    count = len(ring)
    # Sides. The ring runs counter-clockwise seen down +w, so walking it and
    # bridging start-to-end gives outward-facing quads.
    for index in range(count):
        following = (index + 1) % count
        a = index
        b = following
        c = following + count
        d = index + count
        mesh.add_quad(a, b, c, d)

    # Caps. The start cap faces backwards along the member, so its winding is
    # the reverse of the end cap's.
    for a, b, c in triangles:
        mesh.add_triangle(a, c, b)
    for a, b, c in triangles:
        mesh.add_triangle(a + count, b + count, c + count)

    return mesh


def box_section(width: float, depth: float, wall: float = 0.0) -> tuple[list[Vec2], list[list[Vec2]]]:
    """A rectangular section, hollow when ``wall`` is given.

    The stand-in for a profile whose real DXF is not to hand. It is the right
    size and the right shape class — a hollow rectangle is what most frame
    profiles are, once the gasket lips are ignored — so the render reads
    correctly even before the supplier's drawing is loaded.
    """
    half_w, half_d = width / 2.0, depth / 2.0
    outer: list[Vec2] = [
        (-half_w, -half_d),
        (half_w, -half_d),
        (half_w, half_d),
        (-half_w, half_d),
    ]
    holes: list[list[Vec2]] = []
    if wall > 0 and width > 2 * wall and depth > 2 * wall:
        inner_w, inner_d = half_w - wall, half_d - wall
        holes.append(
            [(-inner_w, -inner_d), (-inner_w, inner_d), (inner_w, inner_d), (inner_w, -inner_d)]
        )
    return outer, holes


def plate(width: float, height: float, thickness: float, centre: Vec3,
          *, name: str = "panel", material: str = "glass",
          metadata: dict[str, object] | None = None) -> Mesh:
    """A flat rectangular slab in the XY plane — glass, or an infill panel."""
    half_w, half_h, half_t = width / 2.0, height / 2.0, thickness / 2.0
    outer: list[Vec2] = [
        (-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)
    ]
    return extrude_section(
        outer,
        (centre[0], centre[1], centre[2] - half_t),
        (centre[0], centre[1], centre[2] + half_t),
        up=(0.0, 1.0, 0.0),
        name=name,
        material=material,
        metadata=metadata,
    )


__all__ = [
    "Plane",
    "Frame",
    "build_frame",
    "mitre_plane",
    "extrude_section",
    "box_section",
    "plate",
]
