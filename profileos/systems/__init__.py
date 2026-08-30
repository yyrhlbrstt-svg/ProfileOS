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

from .confirmation import (
    FIGURES,
    Confirmation,
    ConfirmationBook,
    Figure,
    default_confirmations,
    load_confirmations,
    read_confirmation,
    template,
    write_template,
)
from .decisions import (
    Decision,
    DecisionBook,
    DecisionError,
    default_decisions,
    load_decisions,
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
    "FIGURES",
    "Confirmation",
    "ConfirmationBook",
    "Figure",
    "default_confirmations",
    "load_confirmations",
    "read_confirmation",
    "template",
    "write_template",
    "hardware_makers",
    "Decision", "DecisionBook", "DecisionError",
    "default_decisions", "load_decisions",
]
