"""Getting the work onto the lorry, into the wall, and coated for the right area."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.accessories import AccessorySpec
from profileos.core.errors import ProfileOSError
from profileos.delivery import (
    Access,
    Crew,
    Handling,
    SiteCondition,
    handling_for,
    pack,
    plan_installation,
    unit_minutes,
    units_from_builds,
    vehicle,
)
from profileos.delivery.installation import InstallTimes
from profileos.delivery.packing import PackedUnit
from profileos.elements import ElementBuilder, Opening
from profileos.finishing import (
    FinishKind,
    FinishPrices,
    FinishSpec,
    coating_area_per_metre,
    order_finish,
)


def _unit(mark="W-01", width=1800, height=1400, mass=40.0, quantity=1,
          floor=0, sequence=0, accessories=()):
    return PackedUnit(
        mark=mark, description="", width=width, height=height, mass=mass,
        quantity=quantity, floor=floor, sequence=sequence,
        accessories=list(accessories),
    )


class TestHowItGetsCarried:
    def test_a_small_window_is_a_one_person_lift(self):
        assert handling_for(mass=18.0, area=1.0) is Handling.ONE_PERSON

    def test_a_heavy_unit_needs_a_crew(self):
        assert handling_for(mass=100.0, area=2.0) is Handling.FOUR_PEOPLE

    def test_a_big_pane_is_a_vacuum_lifter_job_however_light(self):
        assert handling_for(mass=30.0, area=5.0) is Handling.CRANE

    def test_the_crew_size_follows_from_the_handling(self):
        assert Handling.CRANE.people == 4
        assert Handling.ONE_PERSON.people == 1


class TestPacking:
    def test_the_ground_floor_comes_off_first(self):
        packing = pack([
            _unit("W-03", floor=2, sequence=1),
            _unit("W-01", floor=0, sequence=1),
            _unit("W-02", floor=1, sequence=1),
        ])
        assert [unit.mark for unit in packing.units] == ["W-01", "W-02", "W-03"]

    def test_the_site_order_wins_inside_a_floor(self):
        packing = pack([
            _unit("W-02", floor=0, sequence=2),
            _unit("D-01", floor=0, sequence=0),
        ])
        assert [unit.mark for unit in packing.units] == ["D-01", "W-02"]

    def test_the_heaviest_on_a_floor_is_loaded_last_so_it_comes_off_first(self):
        packing = pack([
            _unit("W-a", mass=20.0, floor=0, sequence=1),
            _unit("W-b", mass=90.0, floor=0, sequence=1),
        ])
        assert [unit.mark for unit in packing.units] == ["W-b", "W-a"]

    def test_a_load_is_split_when_the_payload_runs_out(self):
        units = [_unit(f"W-{i:02d}", mass=500.0) for i in range(12)]
        packing = pack(units, vehicle_name="van")
        assert len(packing.loads) > 1
        assert all(load.mass <= vehicle("van").payload_kg for load in packing.loads)

    def test_a_unit_longer_than_the_lorry_is_reported_not_loaded_quietly(self):
        packing = pack([_unit("CW-01", width=9000, height=3000)], vehicle_name="van")
        assert any("ארוך" in warning for warning in packing.warnings)

    def test_a_crane_unit_is_flagged_before_the_lorry_leaves(self):
        packing = pack([_unit("S-01", width=3600, height=2400, mass=200.0)])
        assert any("מנוף" in warning for warning in packing.warnings)
        assert packing.summary()["crane"]

    def test_an_unknown_vehicle_is_refused_by_name(self):
        with pytest.raises(ProfileOSError):
            pack([_unit()], vehicle_name="spaceship")

    def test_units_come_from_the_builds_with_their_fittings(self):
        opening = Opening(name="W-01", width=1800, height=1400, quantity=2)
        opening.divide_evenly(2, 1)
        opening.metadata["accessories"] = AccessorySpec.typical_dwelling().to_dict()
        build = ElementBuilder().build(opening, sill_height=900)
        units = units_from_builds([build])
        assert len(units) == 1
        assert units[0].quantity == 2
        assert units[0].accessories
        assert units[0].mass > 0


class TestInstallation:
    def test_a_renovation_takes_longer_than_a_new_build(self):
        times = InstallTimes()
        new = unit_minutes(_unit(), times, SiteCondition.NEW_BUILD, Access.GROUND)
        old = unit_minutes(_unit(), times, SiteCondition.RENOVATION, Access.GROUND)
        assert old > new

    def test_stairs_cost_more_than_a_lift_and_the_ground_floor_costs_neither(self):
        times = InstallTimes()
        ground = unit_minutes(_unit(floor=0), times, SiteCondition.NEW_BUILD, Access.STAIRS)
        lift = unit_minutes(_unit(floor=3), times, SiteCondition.NEW_BUILD, Access.LIFT)
        stairs = unit_minutes(_unit(floor=3), times, SiteCondition.NEW_BUILD, Access.STAIRS)
        assert stairs > lift > ground

    def test_a_shutter_adds_its_own_time(self):
        times = InstallTimes()
        plain = unit_minutes(_unit(), times, SiteCondition.NEW_BUILD, Access.GROUND)
        fitted = unit_minutes(
            _unit(accessories=["תריס גלילה"]), times,
            SiteCondition.NEW_BUILD, Access.GROUND,
        )
        assert fitted > plain

    def test_the_plan_never_lands_on_a_festival(self):
        plan = plan_installation(
            [_unit(f"W-{i:02d}") for i in range(12)],
            crew=Crew("צוות", people=2), start=date(2026, 9, 20),
        )
        days = {day for day, _tasks in plan.days}
        assert date(2026, 9, 21) not in days   # Yom Kippur
        assert date(2026, 9, 26) not in days   # Shabbat

    def test_no_day_is_given_more_work_than_it_has_hours(self):
        from profileos.erp.scheduling import Calendar

        calendar = Calendar.israeli()
        times = InstallTimes()
        plan = plan_installation(
            [_unit(f"W-{i:02d}", mass=25.0) for i in range(10)],
            crew=Crew("צוות", people=2), start=date(2026, 9, 20), times=times,
        )
        for day, tasks in plan.days:
            if len(tasks) == 1:
                continue  # a single unit longer than the day is reported, not split
            planned = sum(task.minutes for task in tasks)
            assert planned <= calendar.hours_on(day) * 60.0

    def test_a_crew_too_small_for_the_unit_is_told_before_the_day(self):
        plan = plan_installation(
            [_unit("S-01", width=3600, height=2400, mass=200.0)],
            crew=Crew("צוות", people=2),
        )
        assert any("אנשים" in warning for warning in plan.warnings)

    def test_the_same_warning_is_not_repeated_for_every_identical_unit(self):
        plan = plan_installation(
            [_unit("W-01", mass=200.0, quantity=4)], crew=Crew("צוות", people=2),
        )
        assert len(plan.warnings) == len(set(plan.warnings))

    def test_a_crew_of_nobody_is_refused(self):
        with pytest.raises(ProfileOSError):
            plan_installation([_unit()], crew=Crew("ריק", people=0))

    def test_the_fitting_order_matches_the_loading_order(self):
        units = [
            _unit("W-03", floor=2, sequence=1),
            _unit("D-01", floor=0, sequence=0),
            _unit("W-01", floor=0, sequence=1),
        ]
        loaded = [unit.mark for unit in pack(units).units]
        fitted: list[str] = []
        for _day, tasks in plan_installation(units).days:
            fitted.extend(task.unit.mark for task in tasks)
        assert fitted == loaded


class TestCoatingArea:
    def _properties(self, stem="mullion_mb70"):
        from profileos.core.config import samples_dir
        from profileos.structural import analyse_dxf

        properties, _section = analyse_dxf(
            str(samples_dir() / f"{stem}.dxf"), profile_id=stem
        )
        return properties

    def test_the_chambers_are_not_coated(self):
        """The bath never enters them, so they are not surface area."""
        properties = self._properties()
        assert properties.outer_perimeter < properties.perimeter
        assert coating_area_per_metre(properties) == pytest.approx(
            properties.outer_perimeter / 1000.0
        )

    def test_a_solid_section_has_no_difference_to_find(self):
        properties = self._properties("glazing_bead")
        assert properties.outer_perimeter == pytest.approx(properties.perimeter)

    def test_a_section_that_was_never_analysed_is_refused(self):
        class Empty:
            perimeter = 0.0
            outer_perimeter = 0.0

        with pytest.raises(ProfileOSError):
            coating_area_per_metre(Empty())

    def test_an_old_analysis_says_so_rather_than_guessing(self):
        class Old:
            perimeter = 800.0
            outer_perimeter = 0.0

        with pytest.raises(ProfileOSError, match="שוב"):
            coating_area_per_metre(Old())


class TestCoatingOrders:
    def _setup(self):
        from profileos.core.config import samples_dir
        from profileos.structural import analyse_dxf

        properties = {}
        mass = {}
        for stem, code in (
            ("mullion_mb70", "GEN-MULLION"),
            ("frame_thermal", "GEN-FRAME"),
            ("glazing_bead", "GEN-BEAD"),
        ):
            found, _section = analyse_dxf(
                str(samples_dir() / f"{stem}.dxf"), profile_id=code
            )
            properties[code] = found
            mass[code] = found.mass_per_metre
        opening = Opening(name="W-01", width=1800, height=1400, quantity=4)
        opening.divide_evenly(2, 1)
        build = ElementBuilder().build(opening, sill_height=900)
        return build.cuts, properties, mass

    def test_an_order_with_no_price_list_is_not_priced_at_zero(self):
        cuts, properties, mass = self._setup()
        order = order_finish(cuts, properties, mass_by_profile=mass)
        assert order.price == 0.0
        assert any("מחירון" in warning for warning in order.warnings)

    def test_a_wood_effect_is_two_passes_and_twice_the_area(self):
        cuts, properties, mass = self._setup()
        powder = order_finish(
            cuts, properties, FinishSpec(kind=FinishKind.POWDER), mass_by_profile=mass
        )
        wood = order_finish(
            cuts, properties, FinishSpec(kind=FinishKind.WOOD_EFFECT),
            mass_by_profile=mass,
        )
        assert wood.area == pytest.approx(powder.area * 2, rel=1e-3)
        assert any("שני מעברים" in warning for warning in wood.warnings)

    def test_the_higher_of_area_and_weight_is_charged_as_the_invoice_does(self):
        cuts, properties, mass = self._setup()
        by_area = order_finish(
            cuts, properties, prices=FinishPrices(per_m2=100.0, source="x"),
            mass_by_profile=mass,
        )
        by_both = order_finish(
            cuts, properties,
            prices=FinishPrices(per_m2=100.0, per_kg=1000.0, source="x"),
            mass_by_profile=mass,
        )
        assert by_both.price > by_area.price
        assert "משקל" in by_both.priced_on

    def test_a_minimum_charge_is_applied_and_named(self):
        cuts, properties, mass = self._setup()
        order = order_finish(
            cuts, properties,
            prices=FinishPrices(per_m2=1.0, minimum_charge=500.0, source="x"),
            mass_by_profile=mass,
        )
        assert order.price == 500.0
        assert "מינימום" in order.priced_on

    def test_no_finish_means_no_order(self):
        cuts, properties, mass = self._setup()
        order = order_finish(
            cuts, properties, FinishSpec(kind=FinishKind.MILL), mass_by_profile=mass
        )
        assert order.area == 0.0

    def test_a_profile_with_no_analysis_is_reported_not_skipped_silently(self):
        cuts, properties, mass = self._setup()
        order = order_finish(cuts, {}, mass_by_profile=mass)
        assert any("אין חתך מנותח" in warning for warning in order.warnings)

    def test_a_bar_longer_than_the_line_is_flagged(self):
        cuts, properties, mass = self._setup()

        class LongCut:
            profile_id = "GEN-MULLION"
            length = 7200.0
            quantity = 1

        order = order_finish([LongCut()], properties, mass_by_profile=mass)
        assert any("קו הצביעה" in warning for warning in order.warnings)


class TestAttachments:
    def test_a_photograph_is_copied_into_the_job(self, tmp_path):
        from profileos.projects.attachments import AttachmentKind, AttachmentStore

        source = tmp_path / "opening.jpg"
        source.write_bytes(b"\xff\xd8\xff" + b"x" * 100)
        store = AttachmentStore(tmp_path / "job")
        attachment = store.add(
            source, kind=AttachmentKind.SURVEY_PHOTO, caption="לפני פירוק",
            added_by="דני", element="W-01",
        )
        assert store.path_of(attachment).is_file()
        assert attachment.is_image
        assert store.for_element("W-01")

    def test_it_survives_the_program_closing(self, tmp_path):
        from profileos.projects.attachments import AttachmentStore

        source = tmp_path / "note.pdf"
        source.write_bytes(b"%PDF-1.4")
        AttachmentStore(tmp_path / "job").add(source)
        assert len(AttachmentStore(tmp_path / "job")) == 1

    def test_a_file_that_is_not_a_document_is_refused(self, tmp_path):
        from profileos.projects.attachments import AttachmentStore

        source = tmp_path / "tool.exe"
        source.write_bytes(b"MZ")
        with pytest.raises(ProfileOSError):
            AttachmentStore(tmp_path / "job").add(source)

    def test_evidence_is_not_deleted_by_accident(self, tmp_path):
        from profileos.projects.attachments import AttachmentKind, AttachmentStore

        source = tmp_path / "signed.pdf"
        source.write_bytes(b"%PDF")
        store = AttachmentStore(tmp_path / "job")
        attachment = store.add(source, kind=AttachmentKind.SIGNED_DELIVERY)
        with pytest.raises(ProfileOSError):
            store.remove(attachment.name)
        store.remove(attachment.name, force=True)
        assert len(store) == 0

    def test_a_document_replaced_after_filing_is_visible(self, tmp_path):
        """The whole reason a checksum is kept."""
        from profileos.projects.attachments import AttachmentKind, AttachmentStore

        source = tmp_path / "signed.pdf"
        source.write_bytes(b"%PDF original")
        store = AttachmentStore(tmp_path / "job")
        attachment = store.add(source, kind=AttachmentKind.SIGNED_QUOTE)
        assert not store.changed()
        store.path_of(attachment).write_bytes(b"%PDF something else")
        assert store.changed()

    def test_a_missing_file_is_reported(self, tmp_path):
        from profileos.projects.attachments import AttachmentStore

        source = tmp_path / "photo.png"
        source.write_bytes(b"\x89PNG")
        store = AttachmentStore(tmp_path / "job")
        attachment = store.add(source)
        store.path_of(attachment).unlink()
        assert store.missing()
