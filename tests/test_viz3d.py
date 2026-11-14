"""3D tests.

A render is the one output nobody checks against a drawing, so the tests check
the geometry instead: a swept solid's volume against ``area × length``, a
mitre's effect on that volume, the glTF file's declared bounds against the
bytes actually written. If those hold, what the customer sees is what the shop
will make.
"""

from __future__ import annotations

import json
import math
import struct

import pytest

from profileos.elements import (
    Cell,
    ElementBuilder,
    Opening,
    OpeningType,
    Sash,
    build_elements,
)
from profileos.viz3d import (
    Camera,
    Mesh,
    RenderOptions,
    Scene,
    ViewStyle,
    box_section,
    build_element_scene,
    build_elevation_scene,
    build_gltf,
    extrude_section,
    plate,
    render_svg,
    render_viewer,
    render_views,
    to_glb,
    triangulate,
    validate,
)
from profileos.viz3d.mesh import MeshError, fuse_rings, signed_area


# --------------------------------------------------------------------------- #
# Triangulation
# --------------------------------------------------------------------------- #
class TestTriangulation:
    @pytest.mark.parametrize(
        "name,outer,holes,area",
        [
            ("square", [(0, 0), (10, 0), (10, 10), (0, 10)], [], 100.0),
            ("L", [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)], [], 20.0),
            (
                "one hole",
                [(0, 0), (10, 0), (10, 10), (0, 10)],
                [[(3, 3), (3, 7), (7, 7), (7, 3)]],
                84.0,
            ),
            (
                "two holes",
                [(0, 0), (20, 0), (20, 10), (0, 10)],
                [[(2, 2), (2, 8), (6, 8), (6, 2)], [(12, 2), (12, 8), (16, 8), (16, 2)]],
                152.0,
            ),
        ],
    )
    def test_triangles_cover_exactly_the_polygon(self, name, outer, holes, area):
        ring = fuse_rings(outer, holes)
        triangles = triangulate(outer, holes)
        covered = sum(
            abs(signed_area([ring[i], ring[j], ring[k]])) for i, j, k in triangles
        )
        assert covered == pytest.approx(area, abs=1e-6)

    def test_a_self_intersecting_outline_is_refused(self):
        bowtie = [(0, 0), (10, 10), (10, 0), (0, 10)]
        with pytest.raises(MeshError):
            triangulate(bowtie)

    def test_a_degenerate_outline_is_refused(self):
        with pytest.raises(MeshError):
            triangulate([(0, 0), (1, 1)])


# --------------------------------------------------------------------------- #
# Sweeping
# --------------------------------------------------------------------------- #
class TestExtrusion:
    def test_a_square_cut_solid_has_the_volume_of_its_prism(self):
        outer, holes = box_section(60.0, 70.0)
        mesh = extrude_section(outer, (0, 0, 0), (1000, 0, 0), holes=holes)
        assert mesh.is_closed()
        assert mesh.volume() == pytest.approx(60 * 70 * 1000, rel=1e-9)

    def test_a_hollow_section_removes_exactly_its_chamber(self):
        """60x70 outside, 2 mm wall: 4200 - 56x66 = 504 mm^2 of metal."""
        outer, holes = box_section(60.0, 70.0, wall=2.0)
        mesh = extrude_section(outer, (0, 0, 0), (1000, 0, 0), holes=holes)
        assert mesh.is_closed()
        assert mesh.volume() == pytest.approx(504 * 1000, rel=1e-9)

    def test_a_symmetric_mitre_takes_as_much_as_it_adds(self):
        """The wedge the saw removes at one end is added at the other."""
        outer, holes = box_section(60.0, 70.0)
        square = extrude_section(outer, (0, 0, 0), (1000, 0, 0), holes=holes)
        mitred = extrude_section(
            outer, (0, 0, 0), (1000, 0, 0), holes=holes,
            start_angle=45.0, end_angle=45.0,
        )
        assert mitred.volume() == pytest.approx(square.volume(), rel=1e-9)

    def test_a_mitre_lengthens_the_outer_face_by_the_section_width(self):
        """A 45 degree cut on a 60 mm section reaches 60 mm past the centreline."""
        outer, holes = box_section(60.0, 70.0)
        mesh = extrude_section(
            outer, (0, 0, 0), (1000, 0, 0), holes=holes,
            start_angle=45.0, end_angle=45.0,
        )
        low, high = mesh.bounds
        assert high[0] - low[0] == pytest.approx(1060.0, abs=1e-6)

    def test_the_winding_faces_outward(self):
        """A negative volume means the surface is inside out and lights wrongly."""
        outer, holes = box_section(60.0, 70.0, wall=2.0)
        assert extrude_section(outer, (0, 0, 0), (500, 0, 0), holes=holes).volume() > 0

    def test_a_member_shorter_than_its_mitres_is_refused(self):
        outer, holes = box_section(200.0, 70.0)
        with pytest.raises(MeshError):
            extrude_section(
                outer, (0, 0, 0), (50, 0, 0), holes=holes,
                start_angle=45.0, end_angle=45.0,
            )

    def test_a_cut_parallel_to_the_member_is_refused(self):
        outer, holes = box_section(60.0, 70.0)
        with pytest.raises(MeshError):
            extrude_section(outer, (0, 0, 0), (1000, 0, 0), holes=holes, end_angle=180.0)

    def test_a_member_needs_two_distinct_ends(self):
        outer, holes = box_section(60.0, 70.0)
        with pytest.raises(MeshError):
            extrude_section(outer, (0, 0, 0), (0, 0, 0), holes=holes)

    def test_a_plate_is_its_own_box(self):
        mesh = plate(1000.0, 800.0, 24.0, (0.0, 0.0, 0.0))
        assert mesh.is_closed()
        assert mesh.volume() == pytest.approx(1000 * 800 * 24, rel=1e-9)

    def test_orientation_follows_the_member(self):
        """A vertical member is as tall as it is long, not as wide."""
        outer, holes = box_section(60.0, 70.0)
        mesh = extrude_section(outer, (0, 0, 0), (0, 1500, 0), holes=holes)
        low, high = mesh.bounds
        assert high[1] - low[1] == pytest.approx(1500.0)
        assert high[0] - low[0] == pytest.approx(60.0)


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #
class TestScene:
    def _scene(self) -> Scene:
        opening = Opening(
            element_id="W1", name="Living room", width=2400.0, height=2400.0,
            mullion_positions=[1200.0], transom_positions=[1700.0],
        )
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.TILT_TURN)))
        return build_element_scene(ElementBuilder().build(opening))

    def test_every_solid_is_closed_and_outward(self):
        for mesh in self._scene().meshes:
            assert mesh.is_closed(), mesh.name
            assert mesh.volume() > 0, mesh.name

    def test_the_model_is_the_size_of_the_element(self):
        scene = self._scene()
        size = scene.size
        assert size[0] == pytest.approx(2400.0, abs=1.0)
        assert size[1] == pytest.approx(2400.0, abs=1.0)
        # Depth is the profile, not the elevation: a window is thin.
        assert size[2] < 150.0

    def test_glass_and_metal_are_both_modelled(self):
        materials = self._scene().by_material()
        assert "aluminium" in materials and "glass" in materials

    def test_a_mullion_lands_where_the_rules_put_it(self):
        scene = self._scene()
        mullions = [m for m in scene.meshes if m.metadata.get("role") == "mullion"]
        assert len(mullions) == 1
        low, high = mullions[0].bounds
        assert (low[0] + high[0]) / 2 == pytest.approx(1200.0, abs=0.5)

    def test_glass_is_smaller_than_its_daylight_opening(self):
        """The pane sits in the rebate; a pane the size of the hole would fall out."""
        scene = self._scene()
        panes = [m for m in scene.meshes if m.material == "glass"]
        assert panes
        for pane in panes:
            low, high = pane.bounds
            assert high[0] - low[0] < 2400.0
            assert high[1] - low[1] < 2400.0

    def test_turning_glass_off_leaves_only_metal(self):
        opening = Opening(element_id="W", name="W", width=1500.0, height=1200.0)
        scene = build_element_scene(
            ElementBuilder().build(opening), style=ViewStyle(show_glass=False)
        )
        assert all(mesh.material != "glass" for mesh in scene.meshes)

    def test_an_elevation_lays_elements_out_side_by_side(self):
        builds = build_elements([
            Opening(element_id="A", name="A", width=1200.0, height=1400.0),
            Opening(element_id="B", name="B", width=1800.0, height=1400.0),
        ])
        scene = build_elevation_scene(builds, gap=200.0)
        size = scene.size
        assert size[0] == pytest.approx(1200.0 + 200.0 + 1800.0, abs=2.0)

    def test_the_metal_volume_is_in_the_right_order(self):
        """Sanity, not precision: a 2.4 m window holds kilograms, not tonnes.

        At 2.70 g/cm^3 the modelled metal must weigh something a person could
        lift onto a bench — a check that catches a unit error by a factor of a
        thousand, which is the error that actually happens.
        """
        volume_mm3 = self._scene().aluminium_volume()
        kilograms = volume_mm3 * 2.70e-6
        assert 3.0 < kilograms < 400.0, kilograms


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
class TestRender:
    def _scene(self) -> Scene:
        opening = Opening(element_id="W", name="W", width=1600.0, height=1400.0)
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.TILT_TURN)))
        return build_element_scene(ElementBuilder().build(opening))

    def test_svg_is_self_contained(self):
        svg = render_svg(self._scene())
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert "http://www.w3.org/2000/svg" in svg
        for forbidden in ("<image", "xlink:href", "<script", "http://fonts"):
            assert forbidden not in svg

    def test_both_standard_views_render(self):
        views = render_views(self._scene())
        assert set(views) == {"elevation", "presentation"}
        assert all(len(svg) > 2000 for svg in views.values())

    def test_hidden_surfaces_are_removed(self):
        """Culling has to actually remove something, or it is not culling."""
        scene = self._scene()
        with_culling = render_svg(scene)
        faces = with_culling.count("<path")
        # Every mesh has at least six faces; a 12-mesh scene showing all of
        # them would draw far more than a culled one does.
        assert faces < sum(len(m.triangles) for m in scene.meshes)
        assert faces > 0

    def test_the_elevation_has_no_ground_shadow(self):
        """A head-on orthographic view has no ground for a shadow to fall on."""
        from profileos.viz3d import elevation_camera

        scene = self._scene()
        svg = render_svg(scene, elevation_camera(scene), RenderOptions(shadow=True))
        assert 'opacity="0.10"' not in svg

    def test_an_empty_scene_renders_rather_than_crashing(self):
        assert render_svg(Scene(name="nothing")).startswith("<svg")

    def test_the_camera_looks_at_its_target(self):
        camera = Camera(target=(0.0, 0.0, 0.0), azimuth=0.0, elevation=0.0, distance=100.0)
        assert camera.position == pytest.approx((0.0, 0.0, 100.0), abs=1e-9)
        # The target sits straight ahead, 100 away, down the negative z axis.
        assert camera.view((0.0, 0.0, 0.0)) == pytest.approx((0.0, 0.0, -100.0), abs=1e-9)

    def test_a_point_above_the_target_projects_upward(self):
        camera = Camera(target=(0.0, 0.0, 0.0), azimuth=0.0, elevation=0.0, distance=100.0)
        assert camera.view((0.0, 10.0, 0.0))[1] > 0


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class TestGltf:
    def _scene(self) -> Scene:
        opening = Opening(element_id="W", name="W", width=1600.0, height=1400.0)
        return build_element_scene(ElementBuilder().build(opening))

    def test_the_document_passes_its_own_checks(self):
        document, buffer = build_gltf(self._scene())
        assert validate(document, buffer) == []

    def test_bounds_come_from_the_bytes_that_were_written(self):
        """A file whose accessor bounds are wrong loads as an empty scene."""
        document, buffer = build_gltf(self._scene())
        accessor = document["accessors"][0]
        view = document["bufferViews"][accessor["bufferView"]]
        values = struct.unpack_from(
            f"<{accessor['count'] * 3}f", buffer, view["byteOffset"]
        )
        for axis in range(3):
            column = values[axis::3]
            assert accessor["min"][axis] == pytest.approx(min(column), abs=1e-6)
            assert accessor["max"][axis] == pytest.approx(max(column), abs=1e-6)

    def test_millimetres_become_metres(self):
        """glTF's unit is the metre; exporting millimetres gives a giant window."""
        document, _ = build_gltf(self._scene())
        widest = max(a["max"][0] for a in document["accessors"] if "max" in a)
        assert 1.0 < widest < 3.0

    def test_glb_declares_its_own_length(self):
        data = to_glb(self._scene())
        magic, version, total = struct.unpack_from("<III", data, 0)
        assert magic == 0x46546C67       # "glTF"
        assert version == 2
        assert total == len(data)

    def test_glb_chunks_are_four_byte_aligned(self):
        data = to_glb(self._scene())
        json_length, json_type = struct.unpack_from("<II", data, 12)
        assert json_type == 0x4E4F534A   # "JSON"
        assert json_length % 4 == 0
        binary_length, binary_type = struct.unpack_from("<II", data, 20 + json_length)
        assert binary_type == 0x004E4942  # "BIN"
        assert binary_length % 4 == 0

    def test_glass_is_a_dielectric_not_a_mirror(self):
        """Marking glass metallic is the common shortcut and it looks wrong."""
        document, _ = build_gltf(self._scene())
        glass = next(m for m in document["materials"] if m["name"] == "glass")
        assert glass["pbrMetallicRoughness"]["metallicFactor"] == 0.0
        assert glass["alphaMode"] == "BLEND"
        assert glass["pbrMetallicRoughness"]["baseColorFactor"][3] < 1.0

    def test_a_broken_document_is_caught(self):
        document, buffer = build_gltf(self._scene())
        document["accessors"][0]["max"][0] += 5.0
        assert validate(document, buffer)

    def test_writing_picks_the_format_from_the_extension(self, tmp_path):
        from profileos.viz3d import write_gltf

        scene = self._scene()
        gltf = write_gltf(scene, tmp_path / "w.gltf")
        glb = write_gltf(scene, tmp_path / "w.glb")
        assert json.loads(gltf.read_text(encoding="utf-8"))["asset"]["version"] == "2.0"
        assert glb.read_bytes()[:4] == b"glTF"


class TestViewer:
    def test_the_viewer_needs_nothing_from_the_network(self):
        opening = Opening(element_id="W", name="W", width=1600.0, height=1400.0)
        html = render_viewer(build_element_scene(ElementBuilder().build(opening)))
        assert "<canvas" in html and "webgl" in html
        for forbidden in ("http://", "https://", "<link", "import "):
            assert forbidden not in html

    def test_the_geometry_travels_with_the_page(self):
        opening = Opening(element_id="W", name="W", width=1600.0, height=1400.0)
        scene = build_element_scene(ElementBuilder().build(opening))
        html = render_viewer(scene)
        start = html.index('<script id="scene"')
        payload = json.loads(html[html.index(">", start) + 1: html.index("</script>", start)])
        assert len(payload["parts"]) == len(scene.meshes)
        assert sum(len(p["indices"]) for p in payload["parts"]) == scene.triangle_count * 3

    def test_indices_fit_the_sixteen_bit_buffer_the_viewer_uses(self):
        """A part with more than 65,535 vertices would silently wrap."""
        builds = build_elements([
            Opening(element_id=f"W{n}", name=f"W{n}", width=1600.0, height=1400.0)
            for n in range(4)
        ])
        scene = build_elevation_scene(builds)
        for mesh in scene.meshes:
            assert len(mesh.vertices) < 65_536, mesh.name
