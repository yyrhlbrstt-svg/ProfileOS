"""Which profile systems exist, and how far each of them can be trusted.

The directory names every series the shop works with. The provenance on each
entry says whether its deductions came from the supplier's catalogue or are
family-typical stand-ins, and the readiness check turns that into the only
answer that matters on the floor: may this be cut?
"""

from __future__ import annotations

from .israel import HARDWARE_MAKERS, MANUFACTURERS, SERIES
from .model import (
    Manufacturer,
    Provenance,
    SystemEntry,
    SystemFamily,
    SystemReadiness,
)
from .registry import (
    DIRECTORY,
    FAMILY_RULES,
    SystemDirectory,
    UnclassifiedSystem,
    hardware_makers,
)

__all__ = [
    "DIRECTORY",
    "FAMILY_RULES",
    "HARDWARE_MAKERS",
    "MANUFACTURERS",
    "Manufacturer",
    "Provenance",
    "SERIES",
    "SystemDirectory",
    "SystemEntry",
    "SystemFamily",
    "SystemReadiness",
    "UnclassifiedSystem",
    "hardware_makers",
]
