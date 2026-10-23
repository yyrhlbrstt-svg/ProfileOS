"""Shutters, screens, sills — what is fitted to a window besides the window."""

from __future__ import annotations

import math

import pytest

from profileos.accessories import (
    SLATS,
    AccessoryKind,
    AccessorySpec,
    BoxPosition,
    Drive,
    MeshKind,
    ScreenKind,
    ScreenSpec,
    ShutterSpec,
    SillKind,
    accessories_for,
    choose_box,
    choose_motor,
    coil_diameter,
    size_screen,
    size_shutter,
    size_sill,
    size_trim,
    slat,
)
from profileos.core.errors import ProfileOSError
from profileos.elements import ElementBuilder, Opening


class TestTheRoll:
    """The box is decided by the coil, and the coil is a spiral."""

    def test_a_curtain_wound_on_a_shaft_has_the_area_it_started_with(self):
        """The wound strip fills an annulus of its own cross-section."""
        shaft, thickness, length = 60.0, 9.0, 2000.0
        coil = coil_diameter(length, thickness, shaft)
        annulus = math.pi / 4.0 * (coil**2 - shaft**2)
        assert annulus == pytest.approx(thickness * length, rel=1e-9)

    def test_a_longer_curtain_needs_a_bigger_box(self):
        small = coil_diameter(1500, 9.0, 60)
        large = coil_diameter(3000, 9.0, 60)
        assert large > small
        assert choose_box(large) >= choose_box(small)

    def test_a_thicker_slat_needs_a_bigger_box(self):
        thin = coil_diameter(2000, 9.0, 60)
        thick = coil_diameter(2000, 19.0, 60)
        assert thick > thin

    def test_the_box_is_a_size_the_shop_can_buy(self):
        from profileos.accessories import BOX_SIZES

        assert choose_box(140.0) in BOX_SIZES

    def test_nothing_is_wound_from_nothing(self):
        with pytest.raises(ProfileOSError):
            coil_diameter(0, 9.0, 60)


class TestShutters:
    def test_a_bedroom_window_gets_a_box_a_lintel_can_take(self):
        fitted = size_shutter(1800, 1400)
        assert fitted.kind is AccessoryKind.SHUTTER
        assert 137 <= fitted.metadata["box_mm"] <= 250
        assert fitted.metadata["slat_count"] == math.ceil(1400 / 45)

    def test_the_builder_is_told_the_hole_to_leave(self):
        fitted = size_shutter(1800, 1400)
        width, height = fitted.structural_opening(1800, 1400)
        assert width > 1800
        assert height == 1400 + fitted.metadata["box_mm"]

    def test_the_curtain_is_wider_than_the_window_because_of_the_guides(self):
        fitted = size_shutter(1800, 1400)
        assert fitted.width > 1800

    def test_a_heavy_curtain_refuses_to_be_lifted_by_hand(self):
        fitted = size_shutter(3000, 2400, ShutterSpec(slat_id="alu_77", drive=Drive.STRAP))
        assert any("מנוע" in warning for warning in fitted.warnings)

    def test_a_curtain_too_wide_for_its_slat_says_so_rather_than_being_made(self):
        fitted = size_shutter(3000, 1400, ShutterSpec(slat_id="pvc_39"))
        assert any("רחב מדי" in warning for warning in fitted.warnings)

    def test_the_motor_is_chosen_for_the_torque_the_curtain_needs(self):
        light = size_shutter(1000, 1200, ShutterSpec(slat_id="pvc_39"))
        heavy = size_shutter(3000, 2400, ShutterSpec(slat_id="alu_77"))
        light_motor = next(p for p in light.parts if p.code.startswith("MOT-"))
        heavy_motor = next(p for p in heavy.parts if p.code.startswith("MOT-"))
        assert float(heavy_motor.code.split("-")[1]) > float(light_motor.code.split("-")[1])

    def test_a_motor_asks_for_the_power_before_the_plaster(self):
        fitted = size_shutter(1800, 1400, ShutterSpec(drive=Drive.MOTOR_REMOTE))
        assert any("230V" in note or "חשמל" in note for note in fitted.notes)

    def test_a_built_in_box_gets_its_service_hatch(self):
        fitted = size_shutter(1800, 1400, ShutterSpec(box=BoxPosition.BUILT_IN))
        assert any(part.code == "HATCH" for part in fitted.parts)

    def test_the_cut_list_covers_guides_box_slats_and_bottom_rail(self):
        roles = {cut.role for cut in size_shutter(1800, 1400).cuts}
        assert roles == {"guide", "box", "bottom_rail", "slats"}

    @pytest.mark.parametrize("profile", SLATS, ids=lambda s: s.slat_id)
    def test_every_curtain_in_the_range_sizes(self, profile):
        fitted = size_shutter(1200, 1400, ShutterSpec(slat_id=profile.slat_id))
        assert fitted.metadata["box_mm"] > 0
        assert fitted.mass > 0

    def test_a_curtain_that_is_not_stocked_is_refused_by_name(self):
        with pytest.raises(ProfileOSError):
            slat("titanium_99")

    def test_every_slat_says_where_its_figures_come_from(self):
        assert all(profile.source for profile in SLATS)


class TestScreens:
    def test_a_wide_screen_is_split_into_leaves_that_still_slide(self):
        fitted = size_screen(2700, 1400)
        assert fitted.metadata["leaves"] == 3
        assert fitted.metadata["leaf_width_mm"] <= 1200

    def test_a_sliding_screen_brings_its_own_rail(self):
        roles = {cut.role for cut in size_screen(1800, 1400).cuts}
        assert "screen_rail" in roles

    def test_a_hinged_screen_brings_hinges_instead(self):
        fitted = size_screen(800, 1400, ScreenSpec(kind=ScreenKind.HINGED))
        assert any(part.code == "SCR-HINGE" for part in fitted.parts)

    def test_mesh_is_bought_with_its_cutting_waste(self):
        fitted = size_screen(1000, 1000)
        mesh = next(part for part in fitted.parts if part.code.startswith("MESH-"))
        assert mesh.quantity > 1.0

    @pytest.mark.parametrize("kind", list(ScreenKind))
    def test_every_kind_of_screen_sizes(self, kind):
        fitted = size_screen(1600, 1400, ScreenSpec(kind=kind))
        assert fitted.cuts or fitted.parts

    def test_a_screen_taller_than_it_is_made_says_so(self):
        fitted = size_screen(900, 2800, ScreenSpec(kind=ScreenKind.SLIDING))
        assert any("גבוה מדי" in warning for warning in fitted.warnings)

    def test_a_cat_proof_mesh_weighs_more_than_fibreglass(self):
        light = size_screen(1000, 1000, ScreenSpec(mesh=MeshKind.FIBREGLASS))
        heavy = size_screen(1000, 1000, ScreenSpec(mesh=MeshKind.STAINLESS))
        assert heavy.mass > light.mass


class TestSillsAndTrims:
    def test_a_flat_sill_is_refused_because_the_water_would_sit_on_it(self):
        fitted = size_sill(1800, fall_deg=2.0)
        assert any("שיפוע" in warning for warning in fitted.warnings)

    def test_a_short_projection_is_refused_because_the_drip_runs_back(self):
        fitted = size_sill(1800, projection=20.0)
        assert any("בליטה" in warning for warning in fitted.warnings)

    def test_the_sill_runs_past_the_opening_on_both_sides(self):
        assert size_sill(1800).width > 1800

    def test_a_three_sided_trim_has_no_bottom_length(self):
        roles = {cut.role for cut in size_trim(1800, 1400, three_sided=True).cuts}
        assert "trim_sill" not in roles
        assert "trim_head" in roles


class TestFittingOutAnOpening:
    def test_a_typical_dwelling_window_arrives_fitted(self):
        opening = Opening(name="W-01", width=1800, height=1400, quantity=4)
        fitted = accessories_for(opening, AccessorySpec.typical_dwelling())
        assert {a.kind for a in fitted} == {
            AccessoryKind.SHUTTER, AccessoryKind.SCREEN, AccessoryKind.SILL
        }
        assert all(a.quantity == 4 for a in fitted)

    def test_nothing_asked_for_means_nothing_fitted(self):
        opening = Opening(name="W-01", width=1800, height=1400)
        assert len(accessories_for(opening, AccessorySpec())) == 0

    def test_the_specification_survives_being_written_to_a_job_file(self):
        spec = AccessorySpec.typical_dwelling()
        again = AccessorySpec.from_dict(spec.to_dict())
        assert again.shutter.slat_id == spec.shutter.slat_id
        assert again.screen.kind is spec.screen.kind
        assert again.sill is spec.sill

    def test_a_specification_on_the_opening_is_the_one_that_is_used(self):
        opening = Opening(name="W-01", width=1800, height=1400)
        opening.metadata["accessories"] = AccessorySpec(
            shutter=ShutterSpec(slat_id="pvc_45")
        ).to_dict()
        fitted = accessories_for(opening)
        assert len(fitted) == 1
        assert fitted.accessories[0].metadata["slat"] == "pvc_45"

    def test_the_hole_in_the_wall_accounts_for_every_fitting(self):
        opening = Opening(name="W-01", width=1800, height=1400)
        fitted = accessories_for(opening, AccessorySpec.typical_dwelling())
        width, height = fitted.structural_opening(1800, 1400)
        assert width > 1800 and height > 1400


class TestAccessoriesReachTheBill:
    def _bom(self, spec: AccessorySpec):
        from profileos.quoting.bom import build_bom

        opening = Opening(name="W-01", width=1800, height=1400, quantity=4)
        opening.metadata["accessories"] = spec.to_dict()
        build = ElementBuilder().build(opening, sill_height=900)
        return build_bom([build])

    def test_a_shutter_is_bought_with_the_window(self):
        from profileos.quoting.bom import BomCategory

        bom = self._bom(AccessorySpec.typical_dwelling())
        lines = [line for line in bom.lines if line.category is BomCategory.ACCESSORY]
        assert any(line.code.startswith("SHUT-") for line in lines)
        assert any(line.code.startswith("MESH-") for line in lines)
        assert any(line.code.startswith("SILL-") for line in lines)

    def test_quantities_are_multiplied_by_the_openings(self):
        from profileos.quoting.bom import BomCategory

        bom = self._bom(AccessorySpec(shutter=ShutterSpec(slat_id="alu_45")))
        motor = next(
            line for line in bom.lines
            if line.category is BomCategory.ACCESSORY and line.code.startswith("MOT-")
        )
        assert motor.quantity == 4

    def test_a_fitting_that_cannot_be_made_warns_on_the_bill(self):
        bom = self._bom(AccessorySpec(shutter=ShutterSpec(slat_id="pvc_39")))
        assert any("רחב מדי" in warning for warning in bom.warnings)

    def test_a_window_with_no_fittings_adds_no_lines(self):
        from profileos.quoting.bom import BomCategory

        bom = self._bom(AccessorySpec())
        assert not [line for line in bom.lines if line.category is BomCategory.ACCESSORY]
