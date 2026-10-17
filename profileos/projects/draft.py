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
    """The work now lives in a job; the draft has done its job."""
    path = draft_path()
    if path.is_file():
        path.unlink()


def set_draft_aside() -> Path | None:
    """Keep a draft the person chose not to restore — renamed, never deleted."""
    path = draft_path()
    if not path.is_file():
        return None
    target = path.with_name(f"draft-{datetime.now():%Y%m%d-%H%M%S}.json")
    path.replace(target)
    _log.info("Draft set aside as %s", target.name)
    return target


__all__ = ["clear_draft", "draft_path", "read_draft", "set_draft_aside", "write_draft"]
