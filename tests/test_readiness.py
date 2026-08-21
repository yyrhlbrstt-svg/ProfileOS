"""What the installation can honestly do today."""

from __future__ import annotations

import pytest

from profileos.readiness import CHECKS, State, readiness


@pytest.fixture
def clean(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(tmp_path / "cfg"))
    reload_settings()
    yield tmp_path
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    monkeypatch.delenv("PROFILEOS_CONFIG_DIR", raising=False)
    reload_settings()


class TestTheReport:
    def test_every_check_runs(self, clean):
        report = readiness()
        assert len(report) >= len(CHECKS)

    def test_a_check_that_breaks_is_reported_not_swallowed(self, monkeypatch):
        """A silent check is worse than a failing one."""
        import profileos.readiness as module

        def broken():
            raise RuntimeError("nope")

        monkeypatch.setattr(module, "CHECKS", (broken,))
        report = module.readiness()
        assert len(report) == 1
        assert report.checks[0].state is State.ATTENTION
        assert "נכשלה" in report.checks[0].detail

    def test_every_unfinished_check_says_how_to_finish_it(self, clean):
        for check in readiness():
            if not check.state.is_ready:
                assert check.fix, f"{check.key} has no way out"

    def test_a_fresh_installation_cannot_cut(self, clean):
        """The answer that matters most, and the one wishful thinking gets wrong."""
        report = readiness()
        assert not report.may_cut
        assert "לא לייצור" in next(
            check.blocks for check in report if check.key == "systems_cuttable"
        )

    def test_the_verdict_says_which_of_the_three_states_it_is_in(self, clean):
        verdict = readiness().verdict()
        assert any(word in verdict for word in ("בהקמה", "הצעות מחיר", "ייצור"))

    def test_cutting_becomes_possible_once_a_catalogue_is_confirmed(self, clean):
        """Not simulated: the check reads the directory's own coverage."""
        import profileos.readiness as module

        class Directory:
            @staticmethod
            def coverage():
                return {"total": 3, "unclassified": 0, "confirmed": 2}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("profileos.systems.DIRECTORY", Directory)
        try:
            classify, cut = module._check_systems()
        finally:
            monkeypatch.undo()
        assert classify.state is State.READY
        assert cut.state is State.READY

    def test_the_machine_code_warning_never_goes_away_on_its_own(self, clean):
        """It is only closed by somebody cutting scrap, which software cannot see."""
        check = next(item for item in readiness() if item.key == "post_proven")
        assert check.state is State.ATTENTION
        assert check.critical

    def test_blockers_are_the_critical_unfinished_ones(self, clean):
        report = readiness()
        assert all(
            check.critical and not check.state.is_ready
            for check in report.blockers
        )

    def test_the_summary_counts_add_up(self, clean):
        report = readiness()
        summary = report.summary()
        assert (
            summary["ready"] + summary["partial"] + summary["empty"]
            + summary["attention"] == summary["checks"]
        )
