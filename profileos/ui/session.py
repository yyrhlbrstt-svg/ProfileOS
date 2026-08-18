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

    # Machining
    machining_job: Any = None
    post_results: list[Any] = field(default_factory=list)

    # Commercial
    bom: Any = None
    quote: Any = None

    # Production
    work_order: Any = None

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

    def add_build(self, build: Any) -> None:
        """Add an element, replacing any earlier build of the same element id."""
        element_id = build.opening.element_id
        self.builds = [b for b in self.builds if b.opening.element_id != element_id]
        self.builds.append(build)
        # A new element invalidates anything derived from the old cut list.
        self.nesting_report = None
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

    def describe(self) -> str:
        """One-line status summary for the status bar."""
        parts: list[str] = []
        if self.section_path:
            parts.append(f"profile {self.section_path.stem}")
        if self.builds:
            parts.append(f"{len(self.builds)} element(s), {self.total_area:.1f} m²")
        if self.nesting_report:
            parts.append(
                f"{self.nesting_report.total_bars} bars at "
                f"{self.nesting_report.overall_yield_pct:.1f}%"
            )
        if self.quote:
            parts.append(f"{self.quote.net_price:,.0f} {self.quote.currency}")
        if self.work_order:
            parts.append(f"{len(self.work_order)} items released")
        return "  ·  ".join(parts) or "Ready"


__all__ = ["Session"]
