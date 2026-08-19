"""Using the software from a phone, without weakening the lock on it.

The installation is bound to a USB key and the machines it was enrolled on. A
phone cannot hold that key, and giving it one would undo the arrangement — so a
phone is never an installation here. It is a client of a machine that is
already unlocked: the office computer issues a pairing code, the phone redeems
it once for a device token, and the office keeps the list and can revoke any
device on its own.

What that gets the shop: measurements typed once on site instead of twice,
production stages logged at the bench, drawings in a pocket, and a feasibility
check answerable while still standing in front of the opening.
"""

from __future__ import annotations

from .measure import MeasurementStore, SiteMeasurement, default_store_path
from .pairing import (
    CODE_LIFETIME,
    DEVICE_LIFETIME,
    Device,
    DeviceRegistry,
    PairingCode,
    PairingError,
    default_registry_path,
)
from .state import STATE, MobileState, configure

__all__ = [
    "CODE_LIFETIME",
    "DEVICE_LIFETIME",
    "Device",
    "DeviceRegistry",
    "MeasurementStore",
    "MobileState",
    "PairingCode",
    "PairingError",
    "STATE",
    "SiteMeasurement",
    "configure",
    "default_registry_path",
    "default_store_path",
]
