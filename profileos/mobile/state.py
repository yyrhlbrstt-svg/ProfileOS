"""What the phone can see: one place, set by the machine that is unlocked.

The mobile side is deliberately not given its own copy of anything. It reads
the work order the office is running, writes into the measurement file the
office reads, and nothing else — so there is no second source of truth to get
out of step, and shutting the office machine down takes the phone's access with
it rather than leaving it running against a stale cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging_setup import get_logger
from .measure import MeasurementStore, default_store_path
from .pairing import DeviceRegistry, default_registry_path

_log = get_logger("mobile.state")


@dataclass
class MobileState:
    """Everything the mobile routes are allowed to reach."""

    registry: DeviceRegistry = field(default_factory=lambda: DeviceRegistry.load(default_registry_path()))
    measurements: MeasurementStore = field(default_factory=lambda: MeasurementStore.load(default_store_path()))
    #: The work order the shop is currently running, if one has been loaded.
    work_order: Any = None
    #: Element builds, keyed by element reference, for drawings and job cards.
    builds: dict[str, Any] = field(default_factory=dict)
    #: Shown on the phone so nobody wonders which computer they are talking to.
    station: str = ""
    #: The system series new checks are made against.
    system_id: str = "generic"

    def set_work_order(self, work_order: Any) -> None:
        self.work_order = work_order
        _log.info("Mobile work order set: %s", getattr(work_order, "work_order_id", "?"))

    def set_builds(self, builds: Any) -> None:
        self.builds = {
            build.opening.element_id: build for build in builds
        } | {
            (build.opening.name or build.opening.element_id): build for build in builds
        }

    def reload(self) -> None:
        """Re-read both files, so a change made in the office is picked up."""
        self.registry = DeviceRegistry.load(self.registry.path)
        self.measurements = MeasurementStore.load(self.measurements.path)


#: The state the running server serves. Replaced wholesale in tests.
STATE = MobileState()


def configure(
    *,
    registry_path: str | Path | None = None,
    measurement_path: str | Path | None = None,
    station: str | None = None,
    system_id: str | None = None,
) -> MobileState:
    """Point the mobile side at particular files. Used by the CLI and by tests."""
    global STATE
    STATE = MobileState(
        registry=DeviceRegistry.load(registry_path or default_registry_path()),
        measurements=MeasurementStore.load(measurement_path or default_store_path()),
        station=station or STATE.station,
        system_id=system_id or STATE.system_id,
    )
    return STATE


__all__ = ["MobileState", "STATE", "configure"]
