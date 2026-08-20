"""Shared application state.

Pages never import one another; they read and write a single
:class:`Session`. That keeps the navigation flat — any page can be opened at
any time and will simply show what is available — and it means the whole
application state is one object to inspect, serialise or reset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.logging_setup import get_logger

_log = get_logger("ui.session")


@dataclass
class Session:
    """Everything the application currently holds."""

    # Profile
    section_properties: Any = None
    loaded_section: Any = None
    section_path: Path | None = None

    # Elements
    builds: list[Any] = field(default_factory=list)

    # Nesting
    project: Any = None
    nesting_report: Any = None
    glass_report: Any = None

    # Catalogue
    catalogue_report: Any = None

    #: What was read off the section geometry: grooves, rebates, channels.
    section_features: Any = None
    #: Why the last element built can or cannot be made.
    feasibility: Any = None
    #: The language the domain vocabulary is shown in. Changing it re-labels
    #: the stages, opening types and findings without restarting.
    language: str = "he"

    # ERP
    company: Any = None

    # Machining
    machining_job: Any = None
    post_results: list[Any] = field(default_factory=list)

    # Commercial
    bom: Any = None
    quote: Any = None

    # Production
    work_order: Any = None

    #: The job file this work belongs to, once one has been opened or saved.
    #: ``None`` means a scratch session — nothing is lost by it, because a job
    #: is created the moment somebody asks for the work to be kept.
    job: Any = None

    #: Callbacks fired whenever the session changes, so pages can refresh.
    _listeners: list[Callable[[str], None]] = field(default_factory=list, repr=False)

    # -- notification -------------------------------------------------------- #
    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._listeners.append(callback)

    def _notify(self, what: str) -> None:
        for listener in list(self._listeners):
            try:
                listener(what)
            except Exception:  # noqa: BLE001 - a listener must not break a page
                _log.exception("Session listener failed for %s", what)

    # -- mutations ------------------------------------------------------------ #
    def set_section(self, properties: Any, section: Any, path: Path | None = None) -> None:
        self.section_properties = properties
        self.loaded_section = section
        self.section_path = path
        self._notify("section")

    def set_features(self, report: Any) -> None:
        self.section_features = report
        self._notify("features")

    def set_language(self, language: str) -> None:
        self.language = language
        self._notify("language")

    def set_feasibility(self, report: Any) -> None:
        self.feasibility = report
        self._notify("feasibility")

    def set_glass(self, report: Any) -> None:
        self.glass_report = report
        self._notify("glass")

    def set_company(self, company: Any) -> None:
        self.company = company
        self._notify("company")

    def set_catalogue(self, report: Any) -> None:
        self.catalogue_report = report
        self._notify("catalogue")

    def add_build(self, build: Any) -> None:
        """Add an element, replacing any earlier build of the same element id."""
        element_id = build.opening.element_id
        self.builds = [b for b in self.builds if b.opening.element_id != element_id]
        self.builds.append(build)
        # A new element invalidates anything derived from the old cut list.
        self.nesting_report = None
        self.glass_report = None
        self.bom = None
        self.quote = None
        self._notify("builds")

    def clear_builds(self) -> None:
        self.builds = []
        self.nesting_report = None
        self.bom = None
        self.quote = None
        self.work_order = None
        self._notify("builds")

    def set_nesting(self, project: Any, report: Any) -> None:
        self.project = project
        self.nesting_report = report
        self._notify("nesting")

    def set_machining(self, job: Any, results: list[Any]) -> None:
        self.machining_job = job
        self.post_results = results
        self._notify("machining")

    def set_quote(self, bom: Any, quote: Any) -> None:
        self.bom = bom
        self.quote = quote
        self._notify("quote")

    def set_work_order(self, order: Any) -> None:
        self.work_order = order
        self._notify("work_order")

    # -- queries --------------------------------------------------------------- #
    @property
    def has_elements(self) -> bool:
        return bool(self.builds)

    @property
    def total_area(self) -> float:
        """Total element area [m^2]."""
        return sum(b.opening.area * b.opening.quantity for b in self.builds)

    # -- the job file ---------------------------------------------------- #
    def set_job(self, job: Any) -> None:
        """Attach this work to a job file, and say so everywhere."""
        self.job = job
        self._notify("job")

    def to_schedule(self, name: str = "", system_id: str = "generic") -> Any:
        """The openings currently designed, as a schedule that can be saved.

        The schedule is what a job file keeps: the elements are rebuilt from it
        on open, so a job saved before a system rule was corrected picks the
        correction up rather than preserving the old cut list.
        """
        from ..elements.model import ElementSchedule

        openings = [build.opening for build in self.builds]
        return ElementSchedule(
            name=name or (self.job.name if self.job else "פרויקט"),
            client=self.job.customer_name if self.job else "",
            reference=self.job.reference if self.job else "",
            system_id=system_id,
            openings=openings,
        )

    def load_schedule(self, schedule: Any) -> list[str]:
        """Rebuild the elements of a saved schedule. Returns any warnings.

        One opening that no longer builds — a system removed, a rule tightened
        — must not stop the rest of the job from opening, so the failure is
        reported and the remaining openings are still built.
        """
        from ..elements.builder import ElementBuilder

        problems: list[str] = []
        builds = []
        for opening in schedule.openings:
            try:
                builder = ElementBuilder.for_system(opening.system_id or schedule.system_id)
            except Exception:  # noqa: BLE001 - fall back to the generic rules
                builder = ElementBuilder()
            try:
                builds.append(builder.build(opening, sill_height=schedule.sill_height))
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                problems.append(f"{opening.element_id}: {exc}")
        self.builds = builds
        self.nesting_report = None
        self.glass_report = None
        self.bom = None
        self.quote = None
        self.work_order = None
        self._notify("builds")
        return problems

    def describe(self) -> str:
        """One-line status summary for the status bar."""
        parts: list[str] = []
        if self.job is not None:
            parts.append(f"{self.job.job_id} · {self.job.name}")
        if self.section_path:
            parts.append(f"פרופיל {self.section_path.stem}")
        if self.builds:
            parts.append(f"{len(self.builds)} פתחים, ⁦{self.total_area:.1f} m²⁩")
        if self.nesting_report:
            parts.append(
                f"{self.nesting_report.total_bars} מוטות בניצולת "
                f"⁦{self.nesting_report.overall_yield_pct:.1f}%⁩"
            )
        if self.quote:
            parts.append(f"⁦{self.quote.net_price:,.0f} {self.quote.currency}⁩")
        if self.work_order:
            parts.append(f"{len(self.work_order)} פריטים שוחררו")
        return "  ·  ".join(parts) or "מוכן"


__all__ = ["Session"]
