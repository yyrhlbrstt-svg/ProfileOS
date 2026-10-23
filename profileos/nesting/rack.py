"""The offcut rack: the shop's real one, used before a new bar is bought.

Every job leaves offcuts — 1.4 m of frame here, 900 mm of sash there — and
every shop has a rack of them that nobody looks at when ordering, because
nobody remembers what is on it. So the next job's bars are bought new, and the
rack is cleared to the scrap bin twice a year.

The optimiser could always take offcuts as stock; this makes the rack a real
book the screens use:

* the cutting page plans from the rack first, and, once the cut is done,
  takes the offcuts it used off the rack and puts the new long ones on it;
* the material order plans from the rack too, so the bars ordered are only
  the ones still missing, and the offcuts it counted on are set aside for
  that job — another order will not count on them as well;
* offcuts are entered by hand from the rack the shop already has.

Each change re-reads the rack and writes it back under the file's lock, so
two screens never write over each other's changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..models.orders import RemnantBar
from .inventory import RemnantInventory

#: An offcut shorter than this goes in the scrap bin, not on the rack [mm].
SHORTEST_KEPT = 500.0


def rack_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "remnants.json"


def load_rack() -> RemnantInventory:
    """The rack as it is on disk. A damaged file is refused, never read as
    an empty rack (the next save would throw the real one away)."""
    try:
        return RemnantInventory(rack_path())
    except Exception as exc:  # noqa: BLE001
        raise ProfileOSError(f"מדף השאריות לא נקרא — הקובץ לא נדרס: {exc}") from exc


def rack_for(job_id: str = "") -> RemnantInventory:
    """The rack as this job may use it: free offcuts, and the ones already
    set aside for it (in memory only — nothing is written)."""
    rack = load_rack()
    if job_id:
        with rack._lock:
            for index, offcut in enumerate(rack._remnants):
                if offcut.reserved and offcut.metadata.get("job_id") == job_id:
                    rack._remnants[index] = offcut.model_copy(update={"reserved": False})
    return rack


def change_rack(change: Callable[[RemnantInventory], Any]) -> Any:
    """Apply ``change`` to the rack as it is now, and write it back."""
    from ..core.storage import path_lock

    path = rack_path()
    with path_lock(path):
        rack = load_rack()
        result = change(rack)
        rack.save(path)
    return result


def add_offcuts(offcuts: Iterable[RemnantBar]) -> int:
    kept = [offcut for offcut in offcuts if offcut.length >= SHORTEST_KEPT]
    return change_rack(lambda rack: rack.extend(kept))


def used_from_rack(report: Any) -> list[tuple[str, str, float]]:
    """``(remnant_id, profile_id, length)`` for every rack offcut a plan cuts."""
    used = []
    for profile_id, result in getattr(report, "results", {}).items():
        for layout in result.layouts:
            if layout.is_remnant and layout.remnant_id:
                used.append((layout.remnant_id, profile_id, float(layout.stock_length)))
    return used


def reserve(remnant_ids: Iterable[str], *, job_id: str) -> int:
    """Set offcuts aside for a job: no other plan counts on them."""
    wanted = list(remnant_ids)

    def mark(rack: RemnantInventory) -> int:
        done = 0
        with rack._lock:
            for index, offcut in enumerate(rack._remnants):
                if offcut.remnant_id in wanted and not offcut.reserved:
                    rack._remnants[index] = offcut.model_copy(update={
                        "reserved": True,
                        "metadata": {**offcut.metadata, "job_id": job_id},
                    })
                    done += 1
        return done

    return change_rack(mark)


def record_cut(report: Any, *, job_id: str = "") -> tuple[int, int]:
    """The cut was made: the offcuts it used leave the rack, and the new ones
    long enough to keep go onto it. Returns ``(taken, added)``."""
    from ..core.config import get_settings

    used = [remnant_id for remnant_id, _profile, _length in used_from_rack(report)]
    threshold = max(SHORTEST_KEPT, get_settings().nesting.min_reusable_remnant_mm)
    new = [offcut.model_copy(update={"source_project": job_id or offcut.source_project})
           for offcut in report.all_remnants(threshold)]

    def apply(rack: RemnantInventory) -> tuple[int, int]:
        taken = sum(1 for remnant_id in used if rack.consume(remnant_id))
        return taken, rack.extend(new)

    return change_rack(apply)


__all__ = [
    "SHORTEST_KEPT",
    "add_offcuts",
    "change_rack",
    "load_rack",
    "rack_for",
    "rack_path",
    "record_cut",
    "reserve",
    "used_from_rack",
]
