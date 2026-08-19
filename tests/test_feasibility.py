"""Feasibility tests.

Two things are being checked here. The first is arithmetic: does a 7 m pane get
caught, does a 40 kg sash pass. The second matters more — that a guessed limit
can only warn, and a confirmed one blocks. Get that backwards and either the
software stops production over a number nobody stands behind, or it lets a job
through on one.
"""

from __future__ import annotations

import pytest

from profileos.elements.builder import ElementBuilder, GlassPanel
from profileos.elements.feasibility import (
    DEFAULT_LIMITS,
    FabricationLimits,
    LimitKind,
    Severity,
    check_element,
    check_glass,
)
from profileos.elements.model import Cell, Opening, OpeningType, Sash
from profileos.glazing.glass import make_double_glazing, make_monolithic
from profileos.systems.model import Provenance


def opening(width=1200.0, height=1400.0, **sash_kwargs) -> Opening:
    sash = Sash(**sash_kwargs) if sash_kwargs else None
    return Opening(
        element_id="W1",
        width=width,
        height=height,
        cells=[Cell(column=0, row=0, sash=sash)],
    )


def build_for(op: Opening, system: str = "klil-7300"):
    return ElementBuilder.for_system(system).build(op)


def aluminium(_profile_id: str) -> float:
    """A stand-in profile library: 1.8 kg/m, a plausible thermal sash section."""
    return 1.8


def codes(report) -> list[str]:
    return [finding.code for finding in report.sorted()]


class TestSeverityLadder:
    """The rule that decides whether a check stops the job."""

    def test_a_physical_limit_blocks_even_as_a_stand_in(self):
        limit = DEFAULT_LIMITS.glass_max_width
        assert limit.kind is LimitKind.PHYSICAL
        assert limit.provenance is Provenance.TYPICAL
        assert limit.severity() is Severity.BLOCKER

    def test_an_equipment_limit_only_warns_until_it_is_confirmed(self):
        limit = DEFAULT_LIMITS.glass_max_mass
        assert limit.severity() is Severity.WARNING
        confirmed = limit.confirm(45.0, source="our two-man crew, no cups")
        assert confirmed.severity() is Severity.BLOCKER
        assert confirmed.value == 45.0

    def test_a_confirmed_limit_must_say_where_it_came_from(self):
        with pytest.raises(ValueError, match="source"):
            DEFAULT_LIMITS.glass_max_mass.confirm(45.0, source="")

    def test_nothing_ships_confirmed(self):
        assert FabricationLimits().confirmed_count() == 0


class TestGlass:
    def test_a_pane_larger_than_any_sheet_cannot_be_made(self):
        panel = GlassPanel(width=7000.0, height=2000.0, build_up=make_monolithic(6.0))
        finding, = [f for f in check_glass(panel, DEFAULT_LIMITS, "P1") if f.code == "glass.oversize"]
        assert finding.blocks

    def test_a_pane_that_only_fits_turned_is_accepted(self):
        """Panes are cut from a sheet; the sheet can be used either way round."""
        panel = GlassPanel(width=3100.0, height=5000.0, build_up=make_monolithic(6.0))
        assert not [f for f in check_glass(panel, DEFAULT_LIMITS, "P1") if f.code == "glass.oversize"]

    def test_a_heavy_pane_warns_about_the_lift(self):
        # 2500 x 2000 double glazed is comfortably past two people.
        panel = GlassPanel(width=2500.0, height=2000.0, build_up=make_double_glazing())
        finding, = [f for f in check_glass(panel, DEFAULT_LIMITS, "P1") if f.code == "glass.mass"]
        assert finding.severity is Severity.WARNING
        assert finding.measured == pytest.approx(panel.mass)

    def test_the_lift_limit_blocks_once_the_shop_confirms_it(self):
        limits = FabricationLimits()
        limits.glass_max_mass = limits.glass_max_mass.confirm(50.0, source="crew of two")
        panel = GlassPanel(width=2500.0, height=2000.0, build_up=make_double_glazing())
        finding, = [f for f in check_glass(panel, limits, "P1") if f.code == "glass.mass"]
        assert finding.blocks

    def test_a_sliver_is_only_a_problem_if_it_has_to_be_toughened(self):
        thin = GlassPanel(width=120.0, height=1800.0, build_up=make_monolithic(6.0))
        assert not [f for f in check_glass(thin, DEFAULT_LIMITS, "P1") if "glass." in f.code]
        toughened = GlassPanel(
            width=120.0, height=1800.0, build_up=make_monolithic(6.0, toughened=True)
        )
        found = {f.code for f in check_glass(toughened, DEFAULT_LIMITS, "P1")}
        assert "glass.too_small_to_toughen" in found
        assert "glass.aspect" in found

    def test_safety_glass_required_by_law_blocks_whatever_the_catalogue_says(self):
        panel = GlassPanel(
            width=800.0,
            height=1200.0,
            build_up=make_monolithic(6.0),
            safety_required=True,
            safety_reason="door leaf",
        )
        finding, = [f for f in check_glass(panel, DEFAULT_LIMITS, "P1")]
        assert finding.blocks
        assert "door leaf" in finding.english


class TestSash:
    def test_an_ordinary_casement_raises_nothing_about_its_sash(self):
        report = check_element(build_for(opening(900.0, 1200.0, opening_type=OpeningType.CASEMENT)))
        assert not [code for code in codes(report) if code.startswith("sash.")]

    def test_a_sash_past_its_hardware_class_warns(self):
        report = check_element(
            build_for(opening(1400.0, 2400.0, opening_type=OpeningType.CASEMENT)),
            mass_lookup=aluminium,
        )
        finding, = [f for f in report.findings if f.code == "sash.mass"]
        assert finding.severity is Severity.WARNING

    def test_the_shop_s_own_hinge_rating_blocks_instead(self):
        limits = FabricationLimits()
        limits.sash_max_mass[OpeningType.CASEMENT.value] = limits.sash_max_mass[
            OpeningType.CASEMENT.value
        ].confirm(45.0, source="Fapim hinge sheet, held on file")
        report = check_element(
            build_for(opening(1400.0, 2400.0, opening_type=OpeningType.CASEMENT)),
            limits=limits,
            mass_lookup=aluminium,
        )
        finding, = [f for f in report.findings if f.code == "sash.mass"]
        assert finding.blocks
        assert not report.can_be_made

    def test_tilt_turn_gear_carries_more_than_a_friction_hinge(self):
        heavy = opening(1400.0, 2400.0, opening_type=OpeningType.TILT_TURN)
        light = opening(1400.0, 2400.0, opening_type=OpeningType.CASEMENT)
        assert not [
            f for f in check_element(build_for(heavy), mass_lookup=aluminium).findings
            if f.code == "sash.mass"
        ]
        assert [
            f for f in check_element(build_for(light), mass_lookup=aluminium).findings
            if f.code == "sash.mass"
        ]

    def test_a_missing_profile_mass_is_admitted_rather_than_assumed(self):
        """Under-reporting a sash weight is the dangerous direction to be wrong in."""
        report = check_element(
            build_for(opening(1200.0, 2200.0, opening_type=OpeningType.CASEMENT))
        )
        notes = [f for f in report.findings if f.code == "sash.mass_incomplete"]
        assert notes and "higher" in notes[0].english
        # With the profile masses available the same sash is simply over.
        with_mass = check_element(
            build_for(opening(1200.0, 2200.0, opening_type=OpeningType.CASEMENT)),
            mass_lookup=aluminium,
        )
        assert "sash.mass_incomplete" not in codes(with_mass)

    def test_the_sash_size_is_read_off_the_parts_that_will_be_cut(self):
        """So the check and the saw cannot disagree about how big it is."""
        build = build_for(opening(1600.0, 1400.0, opening_type=OpeningType.CASEMENT))
        horizontal, = [c for c in build.cuts if c.role == "sash_horizontal"]
        report = check_element(build)
        widths = [f.measured for f in report.findings if f.code == "sash.width"]
        assert widths == [pytest.approx(horizontal.length)]


class TestHandle:
    def test_a_handle_on_top_of_the_corner_drive_is_caught(self):
        report = check_element(
            build_for(opening(900.0, 1400.0, opening_type=OpeningType.CASEMENT, handle_height=60.0))
        )
        finding, = [f for f in report.findings if f.code == "handle.fouls_rail"]
        assert "corner drive" in finding.english

    def test_a_centred_handle_is_fine(self):
        report = check_element(
            build_for(opening(900.0, 1400.0, opening_type=OpeningType.CASEMENT))
        )
        assert "handle.fouls_rail" not in codes(report)

    def test_a_handle_out_of_reach_is_flagged_once_the_sill_is_known(self):
        op = opening(900.0, 1400.0, opening_type=OpeningType.CASEMENT, handle_height=1200.0)
        without = check_element(build_for(op))
        assert "handle.out_of_reach" not in codes(without)
        with_sill = check_element(build_for(op), sill_height=1500.0)
        assert "handle.out_of_reach" in codes(with_sill)

    def test_a_sliding_sash_has_no_corner_drive_to_foul(self):
        report = check_element(
            build_for(
                opening(1200.0, 2000.0, opening_type=OpeningType.SLIDING, handle_height=50.0),
                system="klil-7300",
            )
        )
        assert "handle.fouls_rail" not in codes(report)


class TestReport:
    def test_the_worst_finding_sorts_first(self):
        report = check_element(
            build_for(opening(1600.0, 2600.0, opening_type=OpeningType.CASEMENT, handle_height=60.0)),
            sill_height=900.0,
        )
        severities = [f.severity for f in report.sorted()]
        assert severities == sorted(severities, reverse=True)

    def test_a_blocker_means_it_cannot_be_made(self):
        report = check_element(
            build_for(opening(1600.0, 2600.0, opening_type=OpeningType.CASEMENT)), sill_height=0.0
        )
        assert report.blockers
        assert not report.can_be_made

    def test_a_pane_is_not_reported_twice_for_the_same_fault(self):
        """A blocker and a weaker duplicate read as two problems, and teach skimming."""
        report = check_element(
            build_for(opening(1600.0, 2600.0, opening_type=OpeningType.CASEMENT))
        )
        safety = [f for f in report.findings if "safety glass" in f.english]
        assert len(safety) == 1
        assert safety[0].blocks

    def test_working_from_stand_ins_is_said_once_at_the_element_level(self):
        report = check_element(build_for(opening(900.0, 1200.0)))
        notes = [f for f in report.findings if f.code == "system.unconfirmed"]
        assert len(notes) == 1
        assert notes[0].severity is Severity.NOTE

    def test_a_confirmed_system_does_not_carry_that_note(self):
        from profileos.elements.rules import SystemRules
        from profileos.systems import SystemDirectory
        from profileos.systems.israel import MANUFACTURERS, SERIES

        directory = SystemDirectory(SERIES, MANUFACTURERS)
        directory.confirm(
            "klil-7300",
            SystemRules(id="klil-7300-live", name="קליל 7300"),
            source="catalogue",
        )
        build = ElementBuilder.for_system("klil-7300", directory=directory).build(
            opening(900.0, 1200.0)
        )
        assert "system.unconfirmed" not in codes(check_element(build))
