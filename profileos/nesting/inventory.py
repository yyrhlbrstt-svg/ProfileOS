"""Remnant (off-cut) inventory.

Real material yield is decided across jobs, not within one. A plan that leaves
a 1.8 m off-cut is only efficient if that off-cut is actually found and used on
the next order — otherwise it is scrap with extra steps. This module keeps the
persistent record of reusable off-cuts and offers them to the optimiser before
fresh stock.

The store is a JSON file so it is trivially inspectable, diffable and
recoverable, and it is written atomically (temp file plus replace) so a crash
mid-write cannot corrupt a plant's inventory.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..models.orders import RemnantBar
from .model import StockDefinition

_log = get_logger("nesting.inventory")


@dataclass
class InventoryStats:
    """Headline numbers for one profile's remnant stock."""

    profile_id: str
    count: int
    total_length: float
    longest: float
    shortest: float

    @property
    def average_length(self) -> float:
        return self.total_length / self.count if self.count else 0.0


class RemnantInventory:
    """A persistent, thread-safe collection of reusable off-cuts."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._remnants: list[RemnantBar] = []
        if self.path is not None and self.path.is_file():
            self.load()

    # -- persistence -------------------------------------------------------- #
    def load(self, path: str | os.PathLike[str] | None = None) -> int:
        """Read remnants from disk, replacing the in-memory set."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ProfileOSError("No inventory path configured")
        if not target.is_file():
            with self._lock:
                self._remnants = []
            return 0

        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileOSError(
                f"Cannot read remnant inventory: {exc}", path=str(target)
            ) from exc

        entries = raw.get("remnants", raw) if isinstance(raw, dict) else raw
        loaded: list[RemnantBar] = []
        for entry in entries:
            try:
                loaded.append(RemnantBar.model_validate(entry))
            except Exception as exc:  # noqa: BLE001 - skip a bad row, keep the rest
                _log.warning("Skipping malformed remnant record: %s", exc)

        with self._lock:
            self._remnants = loaded
        _log.info("Loaded %d remnant(s) from %s", len(loaded), target)
        return len(loaded)

    def save(self, path: str | os.PathLike[str] | None = None) -> Path:
        """Write the inventory atomically and return the path written."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ProfileOSError("No inventory path configured")
        target.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            payload = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "remnants": [r.model_dump(mode="json") for r in self._remnants],
            }

        # Write to a sibling temp file then replace, so an interrupted write
        # cannot leave a half-written inventory behind.
        handle, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

        self.path = target
        _log.info("Saved %d remnant(s) to %s", len(payload["remnants"]), target)
        return target

    # -- mutation ----------------------------------------------------------- #
    def add(self, remnant: RemnantBar) -> None:
        with self._lock:
            self._remnants.append(remnant)

    def extend(self, remnants: Iterable[RemnantBar]) -> int:
        added = list(remnants)
        with self._lock:
            self._remnants.extend(added)
        return len(added)

    def consume(self, remnant_id: str, quantity: int = 1) -> bool:
        """Remove ``quantity`` from a remnant record, deleting it when empty."""
        with self._lock:
            for index, remnant in enumerate(self._remnants):
                if remnant.remnant_id != remnant_id:
                    continue
                if remnant.quantity <= quantity:
                    del self._remnants[index]
                else:
                    self._remnants[index] = remnant.model_copy(
                        update={"quantity": remnant.quantity - quantity}
                    )
                return True
        return False

    def purge_shorter_than(self, length: float, profile_id: str | None = None) -> int:
        """Drop remnants below ``length`` — housekeeping after a threshold change."""
        with self._lock:
            before = len(self._remnants)
            self._remnants = [
                r
                for r in self._remnants
                if r.length >= length or (profile_id and r.profile_id != profile_id)
            ]
            return before - len(self._remnants)

    def clear(self, profile_id: str | None = None) -> int:
        with self._lock:
            if profile_id is None:
                removed = len(self._remnants)
                self._remnants = []
                return removed
            before = len(self._remnants)
            self._remnants = [r for r in self._remnants if r.profile_id != profile_id]
            return before - len(self._remnants)

    # -- queries ------------------------------------------------------------ #
    def all(self) -> list[RemnantBar]:
        with self._lock:
            return list(self._remnants)

    def for_profile(self, profile_id: str, *, min_length: float = 0.0) -> list[RemnantBar]:
        """Available remnants for a profile, longest first."""
        with self._lock:
            matches = [
                r
                for r in self._remnants
                if r.profile_id == profile_id and r.is_available and r.length >= min_length
            ]
        return sorted(matches, key=lambda r: r.length, reverse=True)

    def stats(self, profile_id: str) -> InventoryStats:
        remnants = self.for_profile(profile_id)
        if not remnants:
            return InventoryStats(profile_id, 0, 0.0, 0.0, 0.0)
        lengths = [r.length for r in remnants for _ in range(r.quantity)]
        return InventoryStats(
            profile_id=profile_id,
            count=len(lengths),
            total_length=sum(lengths),
            longest=max(lengths),
            shortest=min(lengths),
        )

    def profiles(self) -> list[str]:
        with self._lock:
            return sorted({r.profile_id for r in self._remnants})

    def __len__(self) -> int:
        with self._lock:
            return len(self._remnants)

    # -- integration with the optimiser ------------------------------------- #
    def as_stock(
        self, profile_id: str, *, min_length: float = 0.0, limit: int | None = None
    ) -> list[StockDefinition]:
        """Expose remnants as stock definitions the optimiser can select.

        Remnants carry zero cost, so the objective naturally prefers them over
        buying a fresh bar.
        """
        stock: list[StockDefinition] = []
        for remnant in self.for_profile(profile_id, min_length=min_length):
            stock.append(
                StockDefinition(
                    length=remnant.length,
                    available=remnant.quantity,
                    cost=0.0,
                    is_remnant=True,
                    remnant_id=remnant.remnant_id,
                    label=f"remnant {remnant.length:g} mm"
                    + (f" @ {remnant.location}" if remnant.location else ""),
                )
            )
            if limit is not None and len(stock) >= limit:
                break
        return stock


__all__ = ["RemnantInventory", "InventoryStats"]
