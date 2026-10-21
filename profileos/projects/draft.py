"""Work in progress that does not belong to a job yet.

A window designed before anybody opened a project for it is still the
shop's work. It is written here, a moment after every change, so that a
crash, a power cut or a closed window costs nothing: the next launch finds
it and offers it back. Opening or saving a job takes it over and the draft
is cleared.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.logging_setup import get_logger

_log = get_logger("projects.draft")


def draft_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "draft.json"


def write_draft(schedule: Any) -> Path:
    """Save the unfiled design durably, replacing the previous draft."""
    from ..core.storage import write_json

    path = draft_path()
    write_json(path, {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "schedule": schedule.model_dump(mode="json"),
    })
    return path


def read_draft() -> tuple[Any, str] | None:
    """The saved draft and when it was saved, or ``None`` if there is none."""
    from ..core.storage import read_json
    from ..elements.model import ElementSchedule

    path = draft_path()
    raw = read_json(path, default=None)
    if not raw or not raw.get("schedule"):
        return None
    schedule = ElementSchedule.model_validate(raw["schedule"])
    if not schedule.openings:
        return None
    return schedule, str(raw.get("saved_at", ""))


def clear_draft() -> None:
    """The work now lives in a job; the draft has done its job.

    Moved to the recycle bin with its previous version — unlinking the file
    alone leaves the ``.bak``, which the next read would bring back.
    """
    from ..core.storage import discard

    discard(draft_path())


def retire_draft(schedule: Any) -> Path | None:
    """The work is now filed in a job: drop the draft — but only if every
    opening in it really is in that job. Otherwise it is set aside, never
    deleted: opening some other job must not cost an unfiled design.
    """
    try:
        found = read_draft()
    except Exception:  # noqa: BLE001 - an unreadable draft is kept as it is
        return None
    if found is None:
        clear_draft()
        return None
    filed = {opening.element_id for opening in getattr(schedule, "openings", []) or []}
    if all(opening.element_id in filed for opening in found[0].openings):
        clear_draft()
        return None
    return set_draft_aside()


def set_draft_aside() -> Path | None:
    """Keep a draft the person chose not to restore — renamed, never deleted."""
    from ..core.storage import backup_path

    path = draft_path()
    if not path.is_file():
        return None
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    target = path.with_name(f"draft-{stamp}.json")
    serial = 2
    # Never over an earlier draft set aside in the same second.
    while target.exists():
        target = path.with_name(f"draft-{stamp}-{serial}.json")
        serial += 1
    path.replace(target)
    # Its previous version goes to the bin, or it would come back as the draft.
    bak = backup_path(path)
    if bak.exists():
        trash = path.parent / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        bak.replace(trash / f"draft-{stamp}-{serial}.json.bak")
    _log.info("Draft set aside as %s", target.name)
    return target


def drafts_set_aside() -> list[Path]:
    """Drafts kept aside, newest first."""
    folder = draft_path().parent
    return sorted(folder.glob("draft-*.json"), reverse=True) if folder.is_dir() else []


__all__ = [
    "clear_draft",
    "draft_path",
    "drafts_set_aside",
    "read_draft",
    "retire_draft",
    "set_draft_aside",
    "write_draft",
]
