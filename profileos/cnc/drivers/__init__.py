"""Machine drivers.

Importing this package registers every built-in driver in the hot-reloadable
``POST_PROCESSORS`` registry, so :func:`get_driver` can resolve them by key.
"""

from __future__ import annotations

from .base import (
    BasePostProcessor,
    PostResult,
    available_drivers,
    get_driver,
    register_driver,
)
from .elumatec import (
    DgxPostProcessor,
    EcxPostProcessor,
    NcwPostProcessor,
    NcxPostProcessor,
)
from .iso_gcode import IsoGCodePostProcessor, SiemensGCodePostProcessor
from .vendors import (
    EmmegiCamProPostProcessor,
    FomCamPostProcessor,
    KabanKbnPostProcessor,
    SchuecoMcoPostProcessor,
)

__all__ = [
    "BasePostProcessor",
    "PostResult",
    "register_driver",
    "get_driver",
    "available_drivers",
    "NcxPostProcessor",
    "EcxPostProcessor",
    "NcwPostProcessor",
    "DgxPostProcessor",
    "SchuecoMcoPostProcessor",
    "KabanKbnPostProcessor",
    "EmmegiCamProPostProcessor",
    "FomCamPostProcessor",
    "IsoGCodePostProcessor",
    "SiemensGCodePostProcessor",
]
