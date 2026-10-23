"""After-sales: the calls that come back, and what they say about the work."""

from __future__ import annotations

from .model import (
    SYMPTOM_WARRANTY,
    WARRANTY_HEBREW,
    WARRANTY_MONTHS,
    CallState,
    Cause,
    ServiceCall,
    Severity,
    Symptom,
    Visit,
    warranty_expires,
)
from .register import ServiceRegister, default_register, default_register_path

__all__ = [
    "SYMPTOM_WARRANTY",
    "WARRANTY_HEBREW",
    "WARRANTY_MONTHS",
    "CallState",
    "Cause",
    "ServiceCall",
    "ServiceRegister",
    "Severity",
    "Symptom",
    "Visit",
    "default_register",
    "default_register_path",
    "warranty_expires",
]
