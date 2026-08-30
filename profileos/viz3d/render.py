"""A software renderer that produces vector output.

Why not just use the GPU
------------------------
The interactive viewer does. But a presentation drawing has to go in a
quotation, a submittal and a printed job pack, and for those a raster screenshot
is the wrong artefact: it pixelates when the architect zooms in and it cannot be
scaled to a sheet. So the same scene is also rendered here, in software, to SVG
— resolution-independent, printable, and with no dependency to install.

Hidden surfaces
---------------
Back faces are culled — with outward winding that removes everything facing
away — and what remains is drawn far-to-near, the painter's algorithm. That is
exact for convex solids and correct in practice for a window, where the members
are convex prisms that do not interpenetrate. It is not a depth buffer, and a
scene where two faces genuinely pass through each other can order them wrongly;
the interactive viewer, which does have a depth buffer, is the answer there.

Shading
-------
Lambert with an ambient floor, from a light over the viewer's left shoulder —
the convention in architectural drawing, because it makes a reveal read as a
reveal. Glass is translucent and gets a fixed tint plus a specular term, since
a physically-dark pane looks like a hole rather than glazing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .mesh import Mesh, Scene, Vec3, add, cross, dot, length, normalise, scale, sub


@dataclass(frozen=True)
class Material:
    """How one class of surface takes light."""

    base: tuple[int, int, int]
    ambient: float = 0.34
    #: 0 is opaque.
    transparency: float = 0.0
    #: Strength of the highlight; glass and polished metal have one.
    specular: float = 0.0
    edge: tuple[int, int, int] | None = None
    edge_width: float = 0.35


#: Mill-finish aluminium against clear glass, plus a neutral panel.
DEFAULT_MATERIALS: dict[str, Material] = {
    "aluminium": Material(base=(176, 182, 189), ambient=0.36, specular=0.22,
                          edge=(78, 86, 96), edge_width=0.35),
    "glass": Material(base=(150, 196, 208), ambient=0.55, transparency=0.62,
                      specular=0.5, edge=(96, 132, 146), edge_width=0.3),
    "panel": Material(base=(126, 132, 138), ambient=0.4,
                      edge=(70, 76, 82), edge_width=0.35),
    "gasket": Material(base=(46, 48, 52), ambient=0.42),
}

#: Anodised bronze, for a render that matches a common Israeli finish.
BRONZE_MATERIALS: dict[str, Material] = {
    **DEFAULT_MATERIALS,
    "aluminium": Material(base=(150, 112, 66), ambient=0.34, specular=0.26,
                          edge=(74, 54, 30), edge_width=0.35),
}


@dataclass
class Camera:
    """Where the eye is and what it can see."""

    target: Vec3 = (0.0, 0.0, 0.0)
    #: Degrees. Azimuth turns around the vertical, elevation lifts the eye.
    azimuth: float = -28.0
    elevation: float = 16.0
    distance: float = 6000.0
    #: Vertical field of view in degrees; ``None`` renders orthographically,
    #: which is what a technical elevation wants.
    fov: float | None = 32.0
    up: Vec3 = (0.0, 1.0, 0.0)

    @property
    def position(self) -> Vec3:
        azimuth = math.radians(self.azimuth)
        elevation = math.radians(self.elevation)
        return (
            self.target[0] + self.distance * math.cos(elevation) * math.sin(azimuth),
            self.target[1] + self.distance * math.sin(elevation),
            self.target[2] + self.distance * math.cos(elevation) * math.cos(azimuth),
        )

    def basis(self) -> tuple[Vec3, Vec3, Vec3]:
        """Right, up and backward — the view frame, orthonormal."""
        backward = normalise(sub(self.position, self.target))
        right = cross(self.up, backward)
        if length(right) < 1e-9:
            right = (1.0, 0.0, 0.0)
        right = normalise(right)
        up = cross(backward, right)
        return right, up, backward

    def view(self, point: Vec3) -> Vec3:
        """A world point in view space: +x right, +y up, +z towards the eye."""
        right, up, backward = self.basis()
        relative = sub(point, self.position)
        return (dot(relative, right), dot(relative, up), dot(relative, backward))


@dataclass
class RenderOptions:
    width: int = 1200
    height: int = 850
    margin: float = 0.08
    background: str | None = None
    materials: dict[str, Material] = field(default_factory=lambda: dict(DEFAULT_MATERIALS))
    #: Direction the light travels *from*, in world space.
    light: Vec3 = (-0.42, 0.68, 0.6)
    draw_edges: bool = True
    #: Draw a ground shadow under the element.
    shadow: bool = True


@dataclass
class _Face:
    """One flat face, as one or more outlines on the canvas."""

    loops: list[list[tuple[float, float]]]
    depth: float
    fill: str
    opacity: float


@dataclass
class _Edge:
    """A line where two faces genuinely meet at an angle, or a boundary."""

    a: tuple[float, float]
    b: tuple[float, float]
    depth: float
    colour: str
    width: float


#: Adjacent triangles whose normals agree more closely than this are two halves
#: of one flat face, and the line between them is an artefact of triangulation,
#: not an edge of the object.
_COPLANAR = math.cos(math.radians(8.0))


def _feature_edges(mesh: Mesh) -> list[tuple[int, int]]:
    """Edges worth drawing: silhouettes and real creases, never diagonals.

    Stroking every triangle draws the diagonal across each quad, which turns a
    pane of glass into two triangles and a frame face into a lattice. An edge
    survives here only if it bounds the surface or if the two faces meeting
    along it are not coplanar.
    """
    adjacency: dict[tuple[int, int], list[Vec3]] = {}
    for a, b, c in mesh.triangles:
        pa, pb, pc = mesh.vertices[a], mesh.vertices[b], mesh.vertices[c]
        raw = cross(sub(pb, pa), sub(pc, pa))
        if length(raw) < 1e-12:
            continue
        normal = normalise(raw)
        for start, end in ((a, b), (b, c), (c, a)):
            adjacency.setdefault((min(start, end), max(start, end)), []).append(normal)

    edges: list[tuple[int, int]] = []
    for (start, end), normals in adjacency.items():
        if len(normals) != 2 or dot(normals[0], normals[1]) < _COPLANAR:
            edges.append((start, end))
    return edges


def _planar_faces(mesh: Mesh) -> list[tuple[list[list[int]], Vec3]]:
    """Group coplanar triangles into whole faces, with their outlines.

    Emitting one polygon per triangle leaves a seam down every diagonal: on an
    opaque surface it is a hairline, and on glass — where each triangle's edge
    is painted at partial opacity and the two overlap — it is a visible line
    across the pane. Merging the triangles of one flat face and drawing its
    outline once removes the seam rather than hiding it.

    Returns ``(loops, normal)`` per face. A face may have more than one loop —
    the end cap of a hollow section is a ring — so the caller draws it as a
    path with an even-odd fill.
    """
    normals: dict[int, Vec3] = {}
    for index, (a, b, c) in enumerate(mesh.triangles):
        raw = cross(
            sub(mesh.vertices[b], mesh.vertices[a]),
            sub(mesh.vertices[c], mesh.vertices[a]),
        )
        if length(raw) >= 1e-12:
            normals[index] = normalise(raw)

    # Which triangles share an edge.
    shared: dict[tuple[int, int], list[int]] = {}
    for index, (a, b, c) in enumerate(mesh.triangles):
        if index not in normals:
            continue
        for start, end in ((a, b), (b, c), (c, a)):
            shared.setdefault((min(start, end), max(start, end)), []).append(index)

    parent = list(range(len(mesh.triangles)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in shared.values():
        if len(pair) != 2:
            continue
        first, second = pair
        if first not in normals or second not in normals:
            continue
        if dot(normals[first], normals[second]) >= _COPLANAR:
            root_a, root_b = find(first), find(second)
            if root_a != root_b:
                parent[root_a] = root_b

    groups: dict[int, list[int]] = {}
    for index in normals:
        groups.setdefault(find(index), []).append(index)

    result: list[tuple[list[list[int]], Vec3]] = []
    for members in groups.values():
        # A directed edge that appears once bounds the group; one that appears
        # in both directions is interior and gets dropped.
        directed: dict[int, int] = {}
        counts: dict[tuple[int, int], int] = {}
        for index in members:
            a, b, c = mesh.triangles[index]
            for start, end in ((a, b), (b, c), (c, a)):
                counts[(min(start, end), max(start, end))] = (
                    counts.get((min(start, end), max(start, end)), 0) + 1
                )
        for index in members:
            a, b, c = mesh.triangles[index]
            for start, end in ((a, b), (b, c), (c, a)):
                if counts[(min(start, end), max(start, end))] == 1:
                    directed[start] = end

        loops: list[list[int]] = []
        unvisited = set(directed)
        while unvisited:
            start = next(iter(unvisited))
            loop = [start]
            unvisited.discard(start)
            cursor = directed[start]
            while cursor != start and cursor in unvisited:
                loop.append(cursor)
                unvisited.discard(cursor)
                cursor = directed[cursor]
            if len(loop) >= 3:
                loops.append(loop)
        if loops:
            result.append((loops, normals[members[0]]))
    return result


def _project(
    scene_points: Sequence[Vec3], camera: Camera, options: RenderOptions
) -> tuple[list[tuple[float, float, float]], float, tuple[float, float]]:
    """View-space points, plus the scale and offset that fit them on the canvas."""
    viewed = [camera.view(point) for point in scene_points]

    def flatten(v: Vec3) -> tuple[float, float]:
        if camera.fov is None:
            return v[0], v[1]
        # The eye looks down -z, so a visible point has negative z; the guard
        # keeps a point level with the eye from projecting to infinity.
        depth = max(1e-3, -v[2])
        focal = 1.0 / math.tan(math.radians(camera.fov) / 2.0)
        return v[0] * focal / depth, v[1] * focal / depth

    flat = [flatten(v) for v in viewed]
    xs = [p[0] for p in flat]
    ys = [p[1] for p in flat]
    if not xs:
        return [], 1.0, (0.0, 0.0)

    span_x = max(max(xs) - min(xs), 1e-9)
    span_y = max(max(ys) - min(ys), 1e-9)
    usable_w = options.width * (1 - 2 * options.margin)
    usable_h = options.height * (1 - 2 * options.margin)
    ratio = min(usable_w / span_x, usable_h / span_y)

    offset_x = options.width / 2.0 - ratio * (max(xs) + min(xs)) / 2.0
    offset_y = options.height / 2.0 + ratio * (max(ys) + min(ys)) / 2.0
    return (
        [(flat[i][0], flat[i][1], viewed[i][2]) for i in range(len(flat))],
        ratio,
        (offset_x, offset_y),
    )


def _shade(material: Material, normal: Vec3, light: Vec3, to_eye: Vec3) -> str:
    lambert = max(0.0, dot(normal, light))
    intensity = material.ambient + (1.0 - material.ambient) * lambert

    if material.specular > 0:
        half = normalise(add(light, to_eye))
        highlight = max(0.0, dot(normal, half)) ** 28
        intensity += material.specular * highlight

    intensity = max(0.0, min(1.35, intensity))
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(channel * intensity))) for channel in material.base
    )


def render_svg(
    scene: Scene,
    camera: Camera | None = None,
    options: RenderOptions | None = None,
) -> str:
    """Render a scene to a self-contained SVG string."""
    options = options or RenderOptions()
    if camera is None:
        size = scene.size
        camera = Camera(
            target=scene.centre,
            distance=max(size[0], size[1], 1.0) * 2.6,
        )
    light = normalise(options.light)

    all_points: list[Vec3] = [v for mesh in scene.meshes for v in mesh.vertices]
    if not all_points:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
            f'{options.width} {options.height}"></svg>'
        )

    projected, ratio, (offset_x, offset_y) = _project(all_points, camera, options)

    def to_canvas(index: int) -> tuple[float, float]:
        x, y, _ = projected[index]
        return (offset_x + ratio * x, offset_y - ratio * y)

    faces: list[_Face] = []
    edges: list[_Edge] = []
    base = 0
    for mesh in scene.meshes:
        material = options.materials.get(
            mesh.material, options.materials.get("aluminium", DEFAULT_MATERIALS["aluminium"])
        )
        visible: set[int] = set()
        for loops, normal in _planar_faces(mesh):
            centroid_points = [mesh.vertices[i] for loop in loops for i in loop]
            centroid = scale(
                add(add(centroid_points[0], centroid_points[len(centroid_points) // 2]),
                    centroid_points[-1]),
                1.0 / 3.0,
            )
            to_eye = sub(camera.position, centroid)
            if length(to_eye) < 1e-9:
                continue
            to_eye = normalise(to_eye)

            if dot(normal, to_eye) <= 0.0:
                # Facing away. A transparent surface still needs its far side
                # drawn, or the glass reads as a single film rather than a pane.
                if material.transparency <= 0.0:
                    continue
                normal = scale(normal, -1.0)

            indices = [base + i for loop in loops for i in loop]
            visible.update(i - base for i in indices)
            faces.append(
                _Face(
                    loops=[[to_canvas(base + i) for i in loop] for loop in loops],
                    depth=sum(projected[i][2] for i in indices) / len(indices),
                    fill=_shade(material, normal, light, to_eye),
                    opacity=1.0 - material.transparency,
                )
            )

        if options.draw_edges and material.edge and visible:
            colour = "#%02x%02x%02x" % material.edge
            for start, end in _feature_edges(mesh):
                if start not in visible or end not in visible:
                    continue
                i, j = base + start, base + end
                edges.append(
                    _Edge(
                        a=to_canvas(i),
                        b=to_canvas(j),
                        # Bias towards the eye so an edge is not swallowed by
                        # the very face it belongs to.
                        depth=max(projected[i][2], projected[j][2]) + 1e-4,
                        colour=colour,
                        width=material.edge_width,
                    )
                )
        base += len(mesh.vertices)

    # Painter's algorithm: view-space z is negative in front of the eye, so the
    # most negative is furthest away and must be drawn first.
    primitives: list[tuple[float, object]] = [(face.depth, face) for face in faces]
    primitives.extend((edge.depth, edge) for edge in edges)
    primitives.sort(key=lambda item: item[0])

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {options.width} '
        f'{options.height}" width="{options.width}" height="{options.height}">'
    ]
    if options.background:
        parts.append(
            f'<rect width="{options.width}" height="{options.height}" '
            f'fill="{options.background}"/>'
        )

    # A ground shadow belongs to a perspective view of an object standing
    # somewhere. A head-on orthographic elevation has no ground and no
    # vanishing point, and a shadow in it just looks like a smudge.
    if options.shadow and camera.fov is not None:
        low, high = scene.bounds
        floor = low[1]
        corners = [
            (low[0], floor, low[2]), (high[0], floor, low[2]),
            (high[0], floor, high[2]), (low[0], floor, high[2]),
        ]
        shadow_points = []
        for corner in corners:
            v = camera.view(add(corner, (60.0, -4.0, 90.0)))
            if camera.fov is None:
                fx, fy = v[0], v[1]
            else:
                depth = max(1e-3, -v[2])
                focal = 1.0 / math.tan(math.radians(camera.fov) / 2.0)
                fx, fy = v[0] * focal / depth, v[1] * focal / depth
            shadow_points.append((offset_x + ratio * fx, offset_y - ratio * fy))
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in shadow_points)
        parts.append(
            f'<polygon points="{path}" fill="#000" opacity="0.10"/>'
        )

    for _, primitive in primitives:
        if isinstance(primitive, _Face):
            path = " ".join(
                "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in loop) + " Z"
                for loop in primitive.loops
            )
            attributes = (
                f'd="{path}" fill="{primitive.fill}" fill-rule="evenodd" '
                'stroke="none"'
            )
            if primitive.opacity < 1.0:
                attributes += f' fill-opacity="{primitive.opacity:.2f}"'
            parts.append(f"<path {attributes}/>")
        else:
            parts.append(
                f'<line x1="{primitive.a[0]:.1f}" y1="{primitive.a[1]:.1f}" '
                f'x2="{primitive.b[0]:.1f}" y2="{primitive.b[1]:.1f}" '
                f'stroke="{primitive.colour}" stroke-width="{primitive.width}" '
                'stroke-linecap="round"/>'
            )

    parts.append("</svg>")
    return "".join(parts)


def elevation_camera(scene: Scene) -> Camera:
    """Straight on and orthographic — the view a technical drawing wants."""
    size = scene.size
    return Camera(
        target=scene.centre,
        azimuth=0.0,
        elevation=0.0,
        distance=max(size[0], size[1], 1.0) * 3.0,
        fov=None,
    )


def presentation_camera(scene: Scene) -> Camera:
    """Three-quarter view — the one a customer recognises as their window."""
    size = scene.size
    return Camera(
        target=scene.centre,
        azimuth=-30.0,
        elevation=14.0,
        distance=max(size[0], size[1], 1.0) * 2.5,
        fov=30.0,
    )


def render_views(
    scene: Scene, options: RenderOptions | None = None
) -> dict[str, str]:
    """The set of views a job pack carries."""
    return {
        "elevation": render_svg(scene, elevation_camera(scene), options),
        "presentation": render_svg(scene, presentation_camera(scene), options),
    }


__all__ = [
    "Material",
    "DEFAULT_MATERIALS",
    "BRONZE_MATERIALS",
    "Camera",
    "RenderOptions",
    "render_svg",
    "elevation_camera",
    "presentation_camera",
    "render_views",
]
