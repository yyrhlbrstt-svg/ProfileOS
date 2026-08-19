"""Hardware fingerprinting for machine-bound licences.

A fingerprint is a stable, non-identifying digest of several machine traits. It
answers one question — "is this the same machine the licence was issued for?" —
without storing anything that identifies a person.

Robustness matters more than uniqueness here. A fingerprint that changes when a
network card is swapped locks a customer out of software they paid for, so
traits are collected as a **set** and matched by threshold: enough must agree,
not all. Traits that survive ordinary maintenance (machine id, DMI product
UUID) are weighted above ones that do not (MAC address, hostname).
"""

from __future__ import annotations

import hashlib
import os
import platform
import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging_setup import get_logger

_log = get_logger("security.hwid")


@dataclass
class Trait:
    """One machine characteristic contributing to the fingerprint."""

    name: str
    value: str
    #: How much this trait counts toward a match. Stable traits weigh more.
    weight: float = 1.0

    @property
    def digest(self) -> str:
        """Hashed value — the raw trait never leaves the machine."""
        return hashlib.sha256(f"{self.name}:{self.value}".encode("utf-8")).hexdigest()[:16]


def _read_first_line(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
        return text or None
    except OSError:
        return None


def collect_traits() -> list[Trait]:
    """Gather what this platform can offer.

    Every probe is individually guarded: a machine that hides its DMI tables, or
    a container with no machine-id, must still produce a usable fingerprint from
    whatever remains.
    """
    traits: list[Trait] = []

    def add(name: str, value: Any, weight: float) -> None:
        if value:
            traits.append(Trait(name=name, value=str(value), weight=weight))

    # Stable across reboots, reinstalls of hardware, and most maintenance.
    if os.name == "posix":
        add("machine_id", _read_first_line("/etc/machine-id"), 3.0)
        add("dmi_product_uuid", _read_first_line("/sys/class/dmi/id/product_uuid"), 3.0)
        add("dmi_board_serial", _read_first_line("/sys/class/dmi/id/board_serial"), 2.5)
        add("dmi_product_name", _read_first_line("/sys/class/dmi/id/product_name"), 1.0)
        add("cpu_model", _cpu_model(), 1.5)
    elif os.name == "nt":  # pragma: no cover - platform specific
        add("machine_guid", os.environ.get("COMPUTERNAME"), 1.0)
        add("processor_id", os.environ.get("PROCESSOR_IDENTIFIER"), 1.5)

    # Weaker: these change with ordinary maintenance.
    add("mac_address", _primary_mac(), 1.0)
    add("hostname", socket.gethostname(), 0.5)
    add("platform", f"{platform.system()}-{platform.machine()}", 1.0)
    add("cpu_count", os.cpu_count(), 0.5)

    return traits


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _primary_mac() -> str | None:
    node = uuid.getnode()
    # getnode() sets bit 40 of a random value when it cannot find a real MAC,
    # and a random value is worse than no trait at all.
    if (node >> 40) & 1:
        return None
    return f"{node:012x}"


@dataclass
class HardwareFingerprint:
    """A machine identity that tolerates ordinary hardware changes."""

    traits: list[Trait] = field(default_factory=collect_traits)

    @property
    def total_weight(self) -> float:
        return sum(trait.weight for trait in self.traits)

    @property
    def fingerprint(self) -> str:
        """A single digest over every trait, for display and logging."""
        combined = "|".join(sorted(trait.digest for trait in self.traits))
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    @property
    def short(self) -> str:
        return self.fingerprint[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialise for embedding in a licence."""
        return {
            "fingerprint": self.fingerprint,
            "traits": [{"n": t.name, "d": t.digest, "w": t.weight} for t in self.traits],
        }

    def match_score(self, recorded: dict[str, Any]) -> float:
        """Fraction of the recorded weight that still matches, 0..1.

        Compares trait digests rather than values, so a licence file never
        carries a serial number or MAC address in the clear.
        """
        entries = recorded.get("traits") or []
        if not entries:
            # An old licence with only a flat fingerprint: all or nothing.
            return 1.0 if recorded.get("fingerprint") == self.fingerprint else 0.0

        current = {t.name: t.digest for t in self.traits}
        matched = 0.0
        total = 0.0
        for entry in entries:
            weight = float(entry.get("w", 1.0))
            total += weight
            if current.get(entry.get("n")) == entry.get("d"):
                matched += weight
        return matched / total if total else 0.0

    def matches(self, recorded: dict[str, Any], threshold: float = 0.6) -> bool:
        """True when enough of the recorded machine is still present.

        The default 0.6 tolerates a replaced network card or a renamed host
        while still refusing an entirely different machine.
        """
        score = self.match_score(recorded)
        if score < threshold:
            _log.warning(
                "Hardware fingerprint mismatch: %.0f%% of traits matched, %.0f%% required",
                score * 100,
                threshold * 100,
            )
        return score >= threshold


def current_fingerprint() -> HardwareFingerprint:
    return HardwareFingerprint()


__all__ = ["Trait", "HardwareFingerprint", "collect_traits", "current_fingerprint"]
