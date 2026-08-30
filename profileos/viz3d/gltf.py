"""glTF 2.0 export.

An SVG is a picture of the model; a glTF *is* the model. Exporting it means the
architect can open the element in any viewer, drop it into a Revit or SketchUp
scene, or hand it to a visualiser — none of which is possible with a rendered
image, and all of which is routinely asked for on a curtain-walling job.

The format is written by hand rather than through a library, for one reason
worth stating: glTF requires every accessor to declare the ``min`` and ``max``
of the data it points at, and a file whose bounds are wrong loads into some
viewers as an empty scene and into others as a correct one. Computing them from
the buffer that is actually written — rather than from the mesh that was meant
to be written — is the difference between a file that always opens and a file
that opens on the machine it was tested on.

Two outputs:

``.gltf``
    JSON with the binary buffer embedded as a data URI. One file, readable,
    diff-able, and slightly larger.
``.glb``
    The binary container: JSON chunk then binary chunk, both padded to a
    four-byte boundary as the specification requires. This is what a viewer
    loads fastest and what most tools expect.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

from .mesh import Mesh, Scene, cross, length, normalise, sub

#: glTF component types.
_FLOAT = 5126
_UNSIGNED_INT = 5125
#: Target hints: which buffer binding the data is for.
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963


#: Physically-based values for the materials the scene builder emits.
#:
#: Mill-finish aluminium is a metal with a slightly rough surface; glass is a
#: dielectric with almost no roughness and real transmission. Marking glass as
#: metallic — the common shortcut — makes it render as a mirror.
MATERIAL_LIBRARY: dict[str, dict[str, Any]] = {
    "aluminium": {
        "name": "aluminium",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.72, 0.74, 0.77, 1.0],
            "metallicFactor": 0.9,
            "roughnessFactor": 0.35,
        },
    },
    "bronze": {
        "name": "anodised bronze",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.55, 0.40, 0.22, 1.0],
            "metallicFactor": 0.9,
            "roughnessFactor": 0.4,
        },
    },
    "glass": {
        "name": "glass",
        "doubleSided": True,
        "alphaMode": "BLEND",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.58, 0.77, 0.81, 0.38],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.05,
        },
    },
    "panel": {
        "name": "infill panel",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.49, 0.52, 0.54, 1.0],
            "metallicFactor": 0.1,
            "roughnessFactor": 0.7,
        },
    },
    "gasket": {
        "name": "gasket",
        "pbrMetallicRoughness": {
            "baseColorFactor": [0.16, 0.17, 0.18, 1.0],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.9,
        },
    },
}


def _vertex_normals(mesh: Mesh) -> list[tuple[float, float, float]]:
    """Area-weighted vertex normals.

    Weighting by triangle area rather than averaging unit normals means a face
    split into many small triangles does not outvote a large flat one, which is
    what makes a mitred corner read as a crease instead of a smear.
    """
    accumulated = [[0.0, 0.0, 0.0] for _ in mesh.vertices]
    for a, b, c in mesh.triangles:
        pa, pb, pc = mesh.vertices[a], mesh.vertices[b], mesh.vertices[c]
        raw = cross(sub(pb, pa), sub(pc, pa))
        for index in (a, b, c):
            accumulated[index][0] += raw[0]
            accumulated[index][1] += raw[1]
            accumulated[index][2] += raw[2]

    normals: list[tuple[float, float, float]] = []
    for vector in accumulated:
        magnitude = length(tuple(vector))  # type: ignore[arg-type]
        if magnitude < 1e-12:
            normals.append((0.0, 0.0, 1.0))
        else:
            normals.append(normalise(tuple(vector)))  # type: ignore[arg-type]
    return normals


def build_gltf(scene: Scene, *, scale_to_metres: bool = True) -> tuple[dict[str, Any], bytes]:
    """Build the glTF document and its binary buffer.

    glTF's unit is the metre and everything here is in millimetres, so the
    geometry is scaled on the way out. Exporting millimetres and relying on the
    viewer to guess produces a window the size of a football pitch.
    """
    factor = 0.001 if scale_to_metres else 1.0

    buffer = bytearray()
    accessors: list[dict[str, Any]] = []
    buffer_views: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    materials: list[dict[str, Any]] = []
    material_index: dict[str, int] = {}
    for name in sorted({mesh.material for mesh in scene.meshes}):
        definition = MATERIAL_LIBRARY.get(name, MATERIAL_LIBRARY["aluminium"])
        material_index[name] = len(materials)
        materials.append(dict(definition))

    def add_view(data: bytes, target: int) -> int:
        # Every view starts on a four-byte boundary, which the specification
        # requires for float and uint32 accessors.
        while len(buffer) % 4:
            buffer.append(0)
        offset = len(buffer)
        buffer.extend(data)
        buffer_views.append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target}
        )
        return len(buffer_views) - 1

    for mesh in scene.meshes:
        if not mesh.triangles:
            continue

        positions = [
            (v[0] * factor, v[1] * factor, v[2] * factor) for v in mesh.vertices
        ]
        normals = _vertex_normals(mesh)
        indices = [index for triangle in mesh.triangles for index in triangle]

        position_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
        normal_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
        index_bytes = b"".join(struct.pack("<I", i) for i in indices)

        position_view = add_view(position_bytes, _ARRAY_BUFFER)
        normal_view = add_view(normal_bytes, _ARRAY_BUFFER)
        index_view = add_view(index_bytes, _ELEMENT_ARRAY_BUFFER)

        # Bounds come from the data just written, not from the mesh's own
        # bounds property: if the two ever disagree, the file is what ships.
        low = [min(p[axis] for p in positions) for axis in range(3)]
        high = [max(p[axis] for p in positions) for axis in range(3)]

        accessors.append(
            {
                "bufferView": position_view,
                "componentType": _FLOAT,
                "count": len(positions),
                "type": "VEC3",
                "min": low,
                "max": high,
            }
        )
        position_accessor = len(accessors) - 1
        accessors.append(
            {
                "bufferView": normal_view,
                "componentType": _FLOAT,
                "count": len(normals),
                "type": "VEC3",
            }
        )
        normal_accessor = len(accessors) - 1
        accessors.append(
            {
                "bufferView": index_view,
                "componentType": _UNSIGNED_INT,
                "count": len(indices),
                "type": "SCALAR",
            }
        )
        index_accessor = len(accessors) - 1

        meshes.append(
            {
                "name": mesh.name,
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "NORMAL": normal_accessor,
                        },
                        "indices": index_accessor,
                        "material": material_index[mesh.material],
                        "mode": 4,  # triangles
                    }
                ],
            }
        )
        nodes.append({"name": mesh.name, "mesh": len(meshes) - 1})

    document: dict[str, Any] = {
        "asset": {
            "version": "2.0",
            "generator": "ProfileOS",
            "copyright": str(scene.metadata.get("operator", "")) or None,
        },
        "scene": 0,
        "scenes": [{"name": scene.name, "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buffer)}],
    }
    if not document["asset"]["copyright"]:
        del document["asset"]["copyright"]
    return document, bytes(buffer)


def to_gltf(scene: Scene, **kwargs: Any) -> str:
    """A single ``.gltf`` file with the buffer embedded as a data URI."""
    document, buffer = build_gltf(scene, **kwargs)
    document["buffers"][0]["uri"] = (
        "data:application/octet-stream;base64,"
        + base64.b64encode(buffer).decode("ascii")
    )
    return json.dumps(document, indent=1)


def to_glb(scene: Scene, **kwargs: Any) -> bytes:
    """The binary container, chunk-aligned as the specification requires."""
    document, buffer = build_gltf(scene, **kwargs)
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    # The JSON chunk pads with spaces and the binary chunk with zeros; padding
    # either with the wrong filler makes strict loaders reject the file.
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary = buffer + b"\x00" * ((4 - len(buffer) % 4) % 4)

    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)          # "glTF", version, length
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A)    # "JSON"
    out += json_bytes
    out += struct.pack("<II", len(binary), 0x004E4942)        # "BIN"
    out += binary
    return bytes(out)


def write_gltf(scene: Scene, path: str | Path, **kwargs: Any) -> Path:
    """Write ``.gltf`` or ``.glb``, chosen by the file extension."""
    target = Path(path)
    if target.suffix.lower() == ".glb":
        target.write_bytes(to_glb(scene, **kwargs))
    else:
        target.write_text(to_gltf(scene, **kwargs), encoding="utf-8")
    return target


def validate(document: dict[str, Any], buffer: bytes) -> list[str]:
    """Check the document against the parts of the specification that bite.

    Not a full validator. These are the constraints that produce a file which
    silently loads as an empty scene, which is the failure that wastes an
    afternoon.
    """
    problems: list[str] = []
    if document.get("asset", {}).get("version") != "2.0":
        problems.append("asset.version must be 2.0")

    for index, view in enumerate(document.get("bufferViews", [])):
        end = view["byteOffset"] + view["byteLength"]
        if end > len(buffer):
            problems.append(f"bufferView {index} runs past the end of the buffer")
        if view["byteOffset"] % 4:
            problems.append(f"bufferView {index} is not four-byte aligned")

    sizes = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    widths = {_FLOAT: 4, _UNSIGNED_INT: 4}
    for index, accessor in enumerate(document.get("accessors", [])):
        components = sizes[accessor["type"]]
        width = widths[accessor["componentType"]]
        needed = accessor["count"] * components * width
        view = document["bufferViews"][accessor["bufferView"]]
        if needed > view["byteLength"]:
            problems.append(
                f"accessor {index} needs {needed} bytes but its view holds "
                f"{view['byteLength']}"
            )
        if accessor["type"] == "VEC3" and "min" in accessor:
            values = struct.unpack_from(
                f"<{accessor['count'] * 3}f", buffer, view["byteOffset"]
            )
            for axis in range(3):
                column = values[axis::3]
                if abs(min(column) - accessor["min"][axis]) > 1e-5:
                    problems.append(f"accessor {index} min[{axis}] does not match the data")
                if abs(max(column) - accessor["max"][axis]) > 1e-5:
                    problems.append(f"accessor {index} max[{axis}] does not match the data")

    for index, mesh in enumerate(document.get("meshes", [])):
        for primitive in mesh["primitives"]:
            if primitive["material"] >= len(document.get("materials", [])):
                problems.append(f"mesh {index} refers to a material that is not there")
    return problems


__all__ = [
    "MATERIAL_LIBRARY",
    "build_gltf",
    "to_gltf",
    "to_glb",
    "write_gltf",
    "validate",
]
