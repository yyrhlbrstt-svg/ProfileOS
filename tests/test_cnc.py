"""CNC engine tests: IR, macros, clamp planning, toolpaths and drivers."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pytest

from profileos.core.errors import CncError, PostProcessorError, ToolingError
from profileos.cnc import (
    CircularPocket,
    Compensation,
    Contour,
    Drill,
    EndNotch,
    MachiningJob,
    OperationSet,
    PieceProgram,
    RectangularPocket,
    SawCut,
    Slot,
    available_drivers,
    detect_collisions,
    expand_macros,
    generate_toolpath,
    get_driver,
    offset_polyline,
    reposition_clamps,
    resolve_macro,
    resolve_tools,
)
from profileos.cnc.clamps import complement, merge_intervals
from profileos.cnc.toolpath import MoveType, depth_passes
from profileos.models.machines import Clamp, MachineDefinition, Tool, ToolLibrary, ToolType
from profileos.models.profile import Face, MachiningMacro


@pytest.fixture
def tool_library() -> ToolLibrary:
    return ToolLibrary(
        id="std",
        name="Standard",
        tools=[
            Tool(number=3, name="D5 drill", tool_type=ToolType.DRILL, diameter=5.0, flute_length=40),
            Tool(number=4, name="D8.5 drill", tool_type=ToolType.DRILL, diameter=8.5, flute_length=40),
            Tool(number=5, name="D8 endmill", tool_type=ToolType.END_MILL, diameter=8.0, flute_length=35),
            Tool(number=7, name="D6 slotmill", tool_type=ToolType.SLOT_MILL, diameter=6.0, flute_length=30),
            Tool(number=9, name="D12 endmill", tool_type=ToolType.END_MILL, diameter=12.0, flute_length=50),
        ],
    )


@pytest.fixture
def machine() -> MachineDefinition:
    return MachineDefinition(
        id="sbz151",
        name="SBZ 151",
        vendor="Elumatec",
        model="SBZ151",
        post_processor="elumatec.ncx",
        axis_count=5,
        machinable_faces=set(Face),
        clamps=[Clamp(id=f"C{i}", position=p, width=120) for i, p in enumerate([400, 1200, 2000], 1)],
    )


@pytest.fixture
def piece() -> PieceProgram:
    operations = OperationSet(
        [
            Drill(face=Face.TOP, x=150.0, y=22.5, diameter=8.5, depth=12.0, tool_number=4),
            RectangularPocket(
                face=Face.FRONT, x=1200.0, y=0.0, length=120.0, width=40.0, depth=3.5, tool_number=5
            ),
        ]
    )
    return PieceProgram(
        piece_id="PC-101",
        profile_id="MB70-MULLION",
        length=2450.0,
        angle_left=45.0,
        angle_right=45.0,
        operations=operations,
        mark="W-04",
    )


@pytest.fixture
def job(machine, piece, tool_library) -> MachiningJob:
    built = MachiningJob(machine=machine, name="Tower A", pieces=[piece], tool_library=tool_library)
    built.plan_all_clamps()
    return built


# --------------------------------------------------------------------------- #
# Intermediate representation
# --------------------------------------------------------------------------- #

class TestOperations:
    def test_drill_extent_covers_the_tool_not_just_the_centre(self):
        drill = Drill(face=Face.TOP, x=100.0, diameter=10.0, depth=5.0)
        assert drill.extent_x() == pytest.approx((95.0, 105.0))

    def test_rotating_a_pocket_changes_its_footprint_along_the_bar(self):
        flat = RectangularPocket(face=Face.TOP, x=0.0, length=120.0, width=40.0, depth=2.0)
        turned = RectangularPocket(
            face=Face.TOP, x=0.0, length=120.0, width=40.0, depth=2.0, rotation=90.0
        )
        assert flat.extent_x() == pytest.approx((-60.0, 60.0))
        assert turned.extent_x() == pytest.approx((-20.0, 20.0))

    def test_right_end_notch_resolves_against_the_bar_length(self):
        notch = EndNotch(face=Face.TOP, length=25.0, depth=18.0, from_right=True, bar_length=2450.0)
        assert notch.extent_x() == pytest.approx((2425.0, 2450.0))

    def test_right_end_notch_without_a_bar_length_is_invalid(self):
        notch = EndNotch(face=Face.TOP, length=25.0, depth=18.0, from_right=True)
        assert any("bar length" in problem for problem in notch.validate())

    def test_negative_depth_is_rejected(self):
        assert Drill(face=Face.TOP, diameter=5.0, depth=0.0).validate()

    def test_corner_radius_larger_than_the_pocket_is_rejected(self):
        pocket = RectangularPocket(
            face=Face.TOP, length=20.0, width=10.0, depth=2.0, corner_radius=8.0
        )
        assert any("corner radius" in p for p in pocket.validate())

    def test_operations_sort_by_face_then_tool(self):
        operations = OperationSet(
            [
                Drill(face=Face.TOP, x=900.0, diameter=5.0, depth=5.0, tool_number=9),
                Drill(face=Face.TOP, x=100.0, diameter=5.0, depth=5.0, tool_number=3),
                Drill(face=Face.TOP, x=500.0, diameter=5.0, depth=5.0, tool_number=3),
            ]
        )
        order = [op.tool_number for op in operations.sorted_for_machining()]
        assert order == [3, 3, 9]  # tool changes grouped, then left to right


class TestToolResolution:
    def test_drill_gets_a_matching_diameter(self, tool_library):
        drill = Drill(face=Face.TOP, x=10.0, diameter=8.5, depth=10.0)
        resolve_tools([drill], tool_library)
        assert drill.tool_number == 4

    def test_pocket_gets_the_largest_cutter_that_fits(self, tool_library):
        pocket = RectangularPocket(
            face=Face.TOP, x=10.0, length=40.0, width=20.0, depth=3.0, corner_radius=5.0
        )
        resolve_tools([pocket], tool_library)
        # corner radius 5 allows a 10 mm cutter; the 8 mm end mill is the largest that fits
        assert pocket.tool_number == 5

    def test_missing_tool_raises_in_strict_mode(self, tool_library):
        drill = Drill(face=Face.TOP, x=10.0, diameter=99.0, depth=10.0)
        with pytest.raises(ToolingError):
            resolve_tools([drill], tool_library)

    def test_tool_too_short_for_the_depth_is_rejected(self, tool_library):
        deep = Drill(face=Face.TOP, x=10.0, diameter=8.5, depth=200.0)
        with pytest.raises(ToolingError):
            resolve_tools([deep], tool_library)


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #

class TestMacros:
    def test_euro_cylinder_expands_to_faceplate_bore_and_cam_slot(self):
        macro = MachiningMacro(
            macro_id="lock.euro_cylinder", face=Face.FRONT, position_x=1000.0,
            position_y=30.0, depth=25.0, tool_id=5,
        )
        operations = expand_macros([macro])
        assert len(operations.operations) == 3
        assert {op.op_type.value for op in operations.operations} == {
            "rectangular_pocket", "drill", "slot",
        }

    def test_drill_row_spacing(self):
        macro = MachiningMacro(
            macro_id="drill.row", face=Face.TOP, position_x=100.0, position_y=10.0,
            depth=8.0, tool_id=3, parameters={"count": 4, "pitch": 50.0},
        )
        xs = [op.x for op in expand_macros([macro]).operations]
        assert xs == [100.0, 150.0, 200.0, 250.0]

    def test_from_right_end_referencing(self):
        macro = MachiningMacro(
            macro_id="drill.simple", face=Face.TOP, position_x=100.0, position_y=10.0,
            depth=8.0, tool_id=3, from_right_end=True,
        )
        operations = expand_macros([macro], bar_length=2000.0)
        assert operations.operations[0].x == pytest.approx(1900.0)

    def test_disabled_macro_is_skipped(self):
        macro = MachiningMacro(
            macro_id="drill.simple", face=Face.TOP, position_x=100.0, position_y=0.0,
            depth=8.0, tool_id=3, enabled=False,
        )
        assert len(expand_macros([macro]).operations) == 0

    def test_unknown_macro_raises(self):
        with pytest.raises(CncError):
            resolve_macro("no.such.macro")

    def test_macro_provenance_is_recorded(self):
        macro = MachiningMacro(
            macro_id="hinge.standard", face=Face.FRONT, position_x=300.0, position_y=30.0,
            depth=12.0, tool_id=9, label="upper hinge",
        )
        for op in expand_macros([macro]).operations:
            assert op.metadata["macro_id"] == "hinge.standard"
            assert op.metadata["macro_label"] == "upper hinge"


# --------------------------------------------------------------------------- #
# Clamps
# --------------------------------------------------------------------------- #

class TestIntervals:
    def test_merge_joins_overlapping(self):
        assert merge_intervals([(0, 10), (5, 15), (20, 25)]) == [(0, 15), (20, 25)]

    def test_complement(self):
        assert complement([(10, 20)], (0, 50)) == [(0, 10), (20, 50)]

    def test_complement_of_full_cover_is_empty(self):
        assert complement([(0, 50)], (0, 50)) == []


class TestClampPlanning:
    def test_collision_is_detected(self):
        operations = [Drill(face=Face.TOP, x=500.0, diameter=8.5, depth=12.0)]
        clamps = [Clamp(id="C1", position=500.0, width=120.0)]
        assert len(detect_collisions(operations, clamps, clearance=15.0)) == 1

    def test_clamp_far_away_does_not_collide(self):
        operations = [Drill(face=Face.TOP, x=500.0, diameter=8.5, depth=12.0)]
        clamps = [Clamp(id="C1", position=2000.0, width=120.0)]
        assert detect_collisions(operations, clamps, clearance=15.0) == []

    def test_clamp_that_does_not_block_the_face_is_ignored(self):
        operations = [Drill(face=Face.TOP, x=500.0, diameter=8.5, depth=12.0)]
        clamps = [Clamp(id="C1", position=500.0, width=120.0, blocks_faces={Face.BOTTOM})]
        assert detect_collisions(operations, clamps, clearance=15.0) == []

    def test_repositioning_resolves_the_collision(self):
        operations = [
            Drill(face=Face.TOP, x=500.0, diameter=8.5, depth=12.0),
            RectangularPocket(face=Face.TOP, x=2000.0, length=120.0, width=40.0, depth=3.5),
        ]
        clamps = [Clamp(id=f"C{i}", position=p, width=120.0) for i, p in enumerate([500, 2000, 3500], 1)]
        plan = reposition_clamps(4000.0, operations, clamps, clearance=15.0)
        assert plan.ok
        assert plan.moved_count == 2

    def test_repositioning_achieves_exactly_the_requested_clearance(self):
        """The clearance must be charged once, not once per side."""
        operations = [Drill(face=Face.TOP, x=500.0, diameter=10.0, depth=12.0)]
        clamps = [Clamp(id="C1", position=500.0, width=120.0)]
        plan = reposition_clamps(4000.0, operations, clamps, clearance=15.0)
        assert plan.ok

        clamp = plan.active_clamps()[0]
        lo, hi = operations[0].extent_x()
        gap = max(lo - clamp.end, clamp.start - hi)
        assert gap == pytest.approx(15.0, abs=1e-6)

    def test_immovable_clamp_stays_put(self):
        operations = [Drill(face=Face.TOP, x=500.0, diameter=8.5, depth=12.0)]
        clamps = [Clamp(id="C1", position=500.0, width=120.0, movable=False)]
        plan = reposition_clamps(4000.0, operations, clamps, clearance=15.0)
        assert plan.moved_count == 0
        assert not plan.ok  # honestly reported rather than silently ignored

    def test_unsupported_span_is_warned_about(self):
        clamps = [Clamp(id="C1", position=100.0, width=120.0), Clamp(id="C2", position=3900.0, width=120.0)]
        plan = reposition_clamps(4000.0, [], clamps, clearance=15.0)
        assert any("Unsupported span" in w for w in plan.warnings)

    def test_clamp_with_nowhere_to_go_is_disabled_not_hidden(self):
        # Operations blanket the whole bar, leaving no free interval.
        operations = [RectangularPocket(face=Face.TOP, x=1000.0, length=2000.0, width=40.0, depth=3.0)]
        clamps = [Clamp(id="C1", position=1000.0, width=120.0)]
        plan = reposition_clamps(2000.0, operations, clamps, clearance=15.0)
        assert "C1" in plan.disabled
        assert plan.warnings


# --------------------------------------------------------------------------- #
# Toolpaths
# --------------------------------------------------------------------------- #

class TestToolpath:
    def test_depth_passes_are_equal_and_reach_the_full_depth(self):
        passes = depth_passes(10.0, 3.0)
        assert passes[-1] == pytest.approx(10.0)
        increments = [b - a for a, b in zip([0.0] + passes, passes)]
        assert all(inc == pytest.approx(increments[0]) for inc in increments)

    def test_no_step_down_means_a_single_pass(self):
        assert depth_passes(10.0, None) == [10.0]

    def test_offset_of_a_ccw_square_moves_inward(self):
        square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        offset = offset_polyline(square, 1.0, closed=True)
        assert offset == [
            pytest.approx((1.0, 1.0)), pytest.approx((9.0, 1.0)),
            pytest.approx((9.0, 9.0)), pytest.approx((1.0, 9.0)),
        ]

    def test_offset_of_a_straight_line(self):
        assert offset_polyline([(0.0, 0.0), (10.0, 0.0)], 2.0) == [(0.0, 2.0), (10.0, 2.0)]

    def test_zero_offset_is_a_no_op(self):
        points = [(0.0, 0.0), (10.0, 5.0)]
        assert offset_polyline(points, 0.0) == points

    def test_drill_emits_a_canned_cycle(self):
        path = generate_toolpath(Drill(face=Face.TOP, x=100.0, y=20.0, diameter=8.5, depth=12.0))
        assert any(move.move_type is MoveType.DRILL_CYCLE for move in path)

    def test_pocket_path_stays_inside_the_pocket(self):
        pocket = RectangularPocket(
            face=Face.TOP, x=0.0, y=0.0, length=100.0, width=40.0, depth=3.0
        )
        path = generate_toolpath(pocket, tool=Tool(number=1, name="d8", tool_type=ToolType.END_MILL, diameter=8.0))
        radius = 4.0
        for move in path:
            if move.x is not None:
                assert abs(move.x) <= 50.0 - radius + 1e-6
            if move.y is not None:
                assert abs(move.y) <= 20.0 - radius + 1e-6

    def test_unsupported_operation_has_no_toolpath(self):
        with pytest.raises(CncError):
            generate_toolpath(SawCut(face=Face.TOP, position=100.0, angle=45.0))


# --------------------------------------------------------------------------- #
# Drivers
# --------------------------------------------------------------------------- #

class TestDrivers:
    ALL_KEYS = [
        "elumatec.ncx", "elumatec.ecx", "elumatec.ncw", "elumatec.dgx",
        "schueco.mco", "kaban.kbn", "emmegi.campro", "fom.cam",
        "iso.gcode", "iso.gcode.siemens",
    ]

    def test_every_driver_is_registered(self):
        keys = {d["key"] for d in available_drivers()}
        assert set(self.ALL_KEYS) <= keys

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_driver_posts_without_error(self, job, key):
        results = get_driver(key).post(job)
        assert results
        assert all(result.size > 0 for result in results)

    @pytest.mark.parametrize("key", ["elumatec.ncx", "elumatec.ecx", "schueco.mco", "emmegi.campro", "elumatec.dgx"])
    def test_xml_drivers_emit_well_formed_xml(self, job, key):
        for result in get_driver(key).post(job):
            ET.fromstring(result.content)  # raises on malformed XML

    def test_ncx_carries_the_clamp_plan(self, job):
        content = get_driver("elumatec.ncx").post(job)[0].content
        root = ET.fromstring(content)
        assert root.findall(".//Clamp")
        assert root.findall(".//ClampMove")

    def test_ecx_strips_machine_specific_data(self, job):
        root = ET.fromstring(get_driver("elumatec.ecx").post(job)[0].content)
        assert root.tag == "ECX_Document"
        assert not root.findall(".//Clamps")
        assert not root.findall(".//SpindleSpeed")

    def test_dgx_emits_the_cut_list_and_warns_about_milling(self, job):
        result = get_driver("elumatec.dgx").post(job)[0]
        root = ET.fromstring(result.content)
        cuts = root.findall(".//Cut")
        assert len(cuts) == 1
        assert cuts[0].get("AngleLeft") == "45"
        assert any("machining centre" in w for w in result.warnings)

    def test_gcode_has_a_safety_block_and_program_end(self, job):
        content = get_driver("iso.gcode").post(job)[0].content
        assert "G21 G90 G17 G40 G80" in content
        assert content.strip().endswith("%")
        assert "M30" in content

    def test_fanuc_uses_canned_cycles_siemens_uses_cycle_calls(self, job):
        fanuc = "".join(r.content for r in get_driver("iso.gcode").post(job))
        siemens = "".join(r.content for r in get_driver("iso.gcode.siemens").post(job))
        assert "G81" in fanuc and "G80" in fanuc
        assert "CYCLE81" in siemens

    def test_driver_refuses_an_unreachable_face(self, machine, tool_library):
        limited = machine.model_copy(update={"machinable_faces": {Face.TOP}})
        piece = PieceProgram(
            piece_id="P1", profile_id="X", length=1000.0,
            operations=OperationSet([Drill(face=Face.BOTTOM, x=100.0, diameter=5.0, depth=5.0, tool_number=3)]),
        )
        job = MachiningJob(machine=limited, name="j", pieces=[piece], tool_library=tool_library)
        with pytest.raises(PostProcessorError):
            get_driver("elumatec.ncx").post(job)

    def test_driver_refuses_an_operation_it_cannot_execute(self, machine, tool_library):
        """NCW has no contour support; posting one must fail loudly."""
        piece = PieceProgram(
            piece_id="P1", profile_id="X", length=1000.0,
            operations=OperationSet([
                Contour(face=Face.TOP, points=[(0.0, 0.0), (50.0, 0.0), (50.0, 20.0)],
                        depth=2.0, tool_number=5),
            ]),
        )
        job = MachiningJob(machine=machine, name="j", pieces=[piece], tool_library=tool_library)
        with pytest.raises(PostProcessorError):
            get_driver("elumatec.ncw").post(job)

    def test_operation_outside_the_piece_fails_validation(self, machine, tool_library):
        piece = PieceProgram(
            piece_id="P1", profile_id="X", length=500.0,
            operations=OperationSet([Drill(face=Face.TOP, x=900.0, diameter=5.0, depth=5.0, tool_number=3)]),
        )
        job = MachiningJob(machine=machine, name="j", pieces=[piece], tool_library=tool_library)
        assert any("outside the" in problem for problem in job.validate())

    def test_results_write_to_disk(self, job, tmp_path):
        for result in get_driver("elumatec.ncx").post(job):
            path = result.write(tmp_path)
            assert path.exists() and path.stat().st_size > 0
