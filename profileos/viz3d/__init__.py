"""Three-dimensional presentation and technical views.

Typical use::

    from profileos.elements import build_elements
    from profileos.viz3d import build_element_scene, render_views, render_viewer

    build = build_elements([opening])[0]
    scene = build_element_scene(build)
    svgs = render_views(scene)                 # for the job pack
    html = render_viewer(scene)                # for the customer
"""

from __future__ import annotations

from .extrude import (
    Frame,
    Plane,
    box_section,
    build_frame,
    extrude_section,
    mitre_plane,
    plate,
)
from .gltf import MATERIAL_LIBRARY, build_gltf, to_glb, to_gltf, validate, write_gltf
from .mesh import (
    Mesh,
    MeshError,
    Scene,
    merge,
    triangulate,
)
from .render import (
    BRONZE_MATERIALS,
    DEFAULT_MATERIALS,
    Camera,
    Material,
    RenderOptions,
    elevation_camera,
    presentation_camera,
    render_svg,
    render_views,
)
from .scene import ViewStyle, build_element_scene, build_elevation_scene
from .viewer import render_viewer, scene_payload

__all__ = [
    "MeshError", "Mesh", "Scene", "merge", "triangulate",
    "Plane", "Frame", "build_frame", "mitre_plane", "extrude_section",
    "box_section", "plate",
    "ViewStyle", "build_element_scene", "build_elevation_scene",
    "Material", "DEFAULT_MATERIALS", "BRONZE_MATERIALS", "Camera",
    "RenderOptions", "render_svg", "elevation_camera", "presentation_camera",
    "render_views",
    "MATERIAL_LIBRARY", "build_gltf", "to_gltf", "to_glb", "write_gltf", "validate",
    "scene_payload", "render_viewer",
]
