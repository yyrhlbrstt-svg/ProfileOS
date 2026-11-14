"""One sheet that says what this window is, against what it is judged.

The pieces exist separately because they are separate physics. They are read
together because nobody buys a U-value: they buy a window that has to be warm
enough, quiet enough, strong enough and legal, and the four answers argue with
each other. Thicker glass for the noise makes the sash heavier than its
hardware; a corner unit needs a class the system was never tested to; a
shutter box quietly gives back the acoustic performance that was paid for.

Every finding carries the standard it belongs to and how far the figure behind
it can be trusted. A finding whose figure is not confirmed is reported as
something to check, never as a pass — the software's job here is to stop a
compliance statement being made on a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .acoustic import AcousticEstimate, SealClass, estimate_acoustic
from .standards import Confidence, Standard, standard
from .thermal import FrameClass, Spacer, WindowThermal, window_u_value
from .wind import (
    FacadeZone,
    PerformanceClasses,
    Terrain,
    WindCase,
    design_pressure,
    required_classes,
)


class Verdict(StrEnum):
    """What a finding is telling somebody to do."""

    PASS = "pass"
    CHECK = "check"
    FAIL = "fail"

    @property
    def hebrew(self) -> str:
        return {"pass": "תקין", "check": "לבדיקה", "fail": "לא עומד"}[self.value]


@dataclass
class Finding:
    """One statement about the element, and what it rests on."""

    verdict: Verdict
    subject: str
    text: str
    standard: Standard | None = None
    confidence: Confidence = Confidence.TYPICAL
    measured: str = ""
    required: str = ""

    @property
    def citation(self) -> str:
        return self.standard.number if self.standard else ""


@dataclass
class ComplianceReport:
    """Everything computable about one element's performance."""

    element_id: str
    element_name: str
    thermal: WindowThermal | None = None
    acoustic: AcousticEstimate | None = None
    wind: WindCase | None = None
    classes: PerformanceClasses | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.FAIL]

    @property
    def checks(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.CHECK]

    @property
    def may_be_certified(self) -> bool:
        """Whether anything here could be put in front of a building inspector.

        Only when nothing failed and every figure behind the findings was
        confirmed against its standard. That is a high bar and it is meant to
        be: the alternative is a certificate resting on a typical value.
        """
        return not self.failures and all(
            finding.confidence.may_be_certified for finding in self.findings
        )

    def verdict(self) -> str:
        if self.failures:
            return f"לא עומד ב-{len(self.failures)} דרישות"
        if self.checks:
            return f"אין כשל, {len(self.checks)} נקודות לאימות מול התקן"
        return "עומד בכל מה שנבדק"

    def summary_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if self.thermal:
            rows.append(("בידוד תרמי", self.thermal.describe()))
        if self.acoustic:
            rows.append(("אקוסטיקה", self.acoustic.describe()))
        if self.wind:
            rows.append(("עומס רוח", self.wind.describe()))
        if self.classes:
            rows.append((
                "סיווגים נדרשים",
                f"{self.classes.wind} · {self.classes.water} · {self.classes.air}",
            ))
        return rows


@dataclass
class Site:
    """Where the building is, as far as the window is concerned.

    ``basic_velocity`` and its ``wind_source`` travel together on purpose. A
    velocity without a source is the one input this software refuses to treat
    as design data, and keeping them in one object makes that hard to lose.
    """

    basic_velocity: float = 30.0
    wind_source: str = ""
    height: float = 10.0
    terrain: Terrain = Terrain.SUBURBAN
    zone: FacadeZone = FacadeZone.FIELD
    exposed: bool = True
    #: What the project requires, when somebody has read it off the spec.
    required_u: float | None = None
    required_rw: float | None = None
    requirement_source: str = ""


def check_compliance(
    build: Any,
    site: Site | None = None,
    *,
    frame_class: FrameClass | None = None,
    spacer: Spacer = Spacer.WARM_EDGE,
    seal: SealClass = SealClass.DOUBLE,
) -> ComplianceReport:
    """Assemble the performance of one built element."""
    site = site or Site()
    report = ComplianceReport(
        element_id=build.opening.element_id,
        element_name=build.opening.name,
    )

    # -- thermal ---------------------------------------------------------- #
    try:
        report.thermal = window_u_value(build, frame_class=frame_class, spacer=spacer)
    except Exception as exc:  # noqa: BLE001 - a missing pane is not a crash
        report.findings.append(Finding(
            Verdict.CHECK, "בידוד תרמי", f"לא ניתן לחשב: {exc}",
            standard(" ת״י 1045".strip()), Confidence.UNKNOWN,
        ))
    else:
        thermal = report.thermal
        if site.required_u is not None:
            passed = thermal.u_window <= site.required_u
            report.findings.append(Finding(
                Verdict.PASS if passed else Verdict.FAIL,
                "בידוד תרמי",
                (
                    f"⁦U_w = {thermal.u_window:.2f}⁩ מול דרישה "
                    f"⁦{site.required_u:.2f}⁩ W/m²K"
                ),
                standard("1045"),
                Confidence.CONFIRMED if site.requirement_source else Confidence.TYPICAL,
                f"⁦{thermal.u_window:.2f}⁩", f"⁦{site.required_u:.2f}⁩",
            ))
        else:
            report.findings.append(Finding(
                Verdict.CHECK, "בידוד תרמי",
                f"⁦U_w = {thermal.u_window:.2f}⁩ W/m²K — לא הוזנה דרישה לפרויקט",
                standard("1045"), Confidence.TYPICAL,
                measured=f"⁦{thermal.u_window:.2f}⁩",
            ))
        for note in thermal.notes:
            report.findings.append(Finding(
                Verdict.CHECK, "בידוד תרמי", note, standard("1045"), Confidence.TYPICAL,
            ))

    # -- acoustic --------------------------------------------------------- #
    report.acoustic = estimate_acoustic(build, seal=seal)
    if site.required_rw is not None:
        passed = report.acoustic.r_window >= site.required_rw
        report.findings.append(Finding(
            Verdict.PASS if passed else Verdict.FAIL,
            "אקוסטיקה",
            (
                f"⁦R_w ≈ {report.acoustic.r_window:.0f}⁩ מול דרישה "
                f"⁦{site.required_rw:.0f}⁩ dB — אומדן, לא דוח בדיקה"
            ),
            None, Confidence.TYPICAL,
            f"⁦{report.acoustic.r_window:.0f}⁩", f"⁦{site.required_rw:.0f}⁩",
        ))
    for note in report.acoustic.notes:
        report.findings.append(Finding(
            Verdict.CHECK, "אקוסטיקה", note, None, Confidence.TYPICAL,
        ))

    # -- wind and the classes it calls for --------------------------------- #
    report.wind = design_pressure(
        site.basic_velocity,
        height=site.height,
        terrain=site.terrain,
        zone=site.zone,
        source=site.wind_source,
    )
    report.classes = required_classes(report.wind, exposed=site.exposed)
    report.findings.append(Finding(
        Verdict.CHECK if not report.wind.is_verified else Verdict.PASS,
        "עומס רוח",
        (
            f"לחץ תכן ⁦{report.wind.pressure:.2f}⁩ kN/m² — "
            + (report.wind.source or "מהירות הרוח היסודית לא אומתה מול המפה בת״י 414")
        ),
        standard("414"),
        Confidence.CONFIRMED if report.wind.is_verified else Confidence.UNKNOWN,
        measured=f"⁦{report.wind.pressure:.2f}⁩ kN/m²",
    ))
    report.findings.append(Finding(
        Verdict.CHECK, "סיווג ביצועים",
        (
            f"יש לדרוש מהספק ⁦{report.classes.wind}⁩ לעומס רוח, "
            f"⁦{report.classes.water}⁩ לאטימות מים, ⁦{report.classes.air}⁩ לאוויר — "
            "הסיווג ניתן בבדיקת מעבדה על הדגם"
        ),
        standard("1068"), Confidence.UNKNOWN,
    ))

    # -- safety glazing, which the builder already knows about ------------- #
    non_compliant = build.non_compliant_glass
    if non_compliant:
        report.findings.append(Finding(
            Verdict.FAIL, "זיגוג בטיחותי",
            f"⁦{len(non_compliant)}⁩ שמשות במיקום קריטי ללא זכוכית בטיחותית",
            standard("1099"), Confidence.TYPICAL,
        ))
    elif build.glass:
        report.findings.append(Finding(
            Verdict.PASS, "זיגוג בטיחותי",
            "כל השמשות במיקום קריטי מפרט זכוכית בטיחותית",
            standard("1099"), Confidence.TYPICAL,
        ))

    # -- a protected space is not an ordinary window ----------------------- #
    fitted = getattr(build.opening, "metadata", {})
    if str(fitted.get("usage", "")).startswith("mamad") or "ממ" in build.opening.name:
        report.findings.append(Finding(
            Verdict.CHECK, "ממ״ד",
            "פתח ממ״ד — הדגם, הזיגוג והפרזול לפי אישור פיקוד העורף ליצרן, "
            "לא לפי חישוב כאן",
            standard("הג״א — ממ״ד"), Confidence.UNKNOWN,
        ))
    return report


__all__ = [
    "ComplianceReport",
    "Finding",
    "Site",
    "Verdict",
    "check_compliance",
]
