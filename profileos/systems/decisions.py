"""What this shop has decided about the series it works with, kept on disk.

The directory ships with the series every Israeli fabricator meets, but it
ships them *unclassified*: the software does not know from the name alone
whether קליל 7000 is a casement or a sliding system, and guessing would pick
the wrong hardware and the wrong deductions.

The shop knows. This module remembers what they decided, so the classification
survives a restart and travels with a backup — and it records who decided and
when, because a family attributed to nobody is an assertion rather than a
decision.

Confirmed rules — the supplier's own figures, the ones that unlock cutting —
are deliberately not stored here. Those arrive through catalogue ingestion,
which keeps the evidence beside them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("systems.decisions")


class DecisionError(ProfileOSError):
    """A classification that cannot be recorded as given."""


@dataclass(frozen=True)
class Decision:
    """One series placed in a family by somebody, on a date."""

    entry_id: str
    family: str
    source: str
    decided: str

    def as_dict(self) -> dict[str, str]:
        return {
            "entry_id": self.entry_id, "family": self.family,
            "source": self.source, "decided": self.decided,
        }


class DecisionBook:
    """The shop's classifications, in one file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def all(self) -> list[Decision]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file must not stop startup
            _log.exception("Could not read system decisions at %s", self.path)
            return []
        return [
            Decision(
                entry_id=entry["entry_id"], family=entry["family"],
                source=entry.get("source", ""), decided=entry.get("decided", ""),
            )
            for entry in raw.get("decisions", [])
            if entry.get("entry_id") and entry.get("family")
        ]

    def record(self, entry_id: str, family: str, *, source: str) -> Decision:
        """Write one decision, replacing any earlier one for the same series."""
        if not source.strip():
            raise DecisionError(
                "סיווג סדרה חייב מקור — מי החליט ולפי מה", entry_id=entry_id
            )
        decision = Decision(
            entry_id=entry_id, family=family, source=source.strip(),
            decided=date.today().isoformat(),
        )
        others = [d for d in self.all() if d.entry_id != entry_id]
        payload = {"decisions": [d.as_dict() for d in others + [decision]]}

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, self.path)
        _log.info("Recorded %s as %s (%s)", entry_id, family, source)
        return decision

    def forget(self, entry_id: str) -> None:
        others = [d for d in self.all() if d.entry_id != entry_id]
        payload = {"decisions": [d.as_dict() for d in others]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def apply(self, directory: Iterable | None = None) -> int:
        """Replay every decision onto the directory. Returns how many took."""
        from . import DIRECTORY, SystemFamily

        target = directory if directory is not None else DIRECTORY
        applied = 0
        for decision in self.all():
            try:
                target.classify(
                    decision.entry_id,
                    SystemFamily(decision.family),
                    source=decision.source,
                )
                applied += 1
            except Exception:  # noqa: BLE001 - a stale id must not stop the rest
                _log.warning("Could not apply decision for %s", decision.entry_id)
        return applied


def default_decisions() -> DecisionBook:
    """The decision book this installation keeps."""
    from ..core.config import get_settings

    return DecisionBook(get_settings().data_dir / "system-decisions.json")


def load_decisions() -> int:
    """Apply the shop's saved classifications. Called once at start-up."""
    try:
        return default_decisions().apply()
    except Exception:  # noqa: BLE001 - never stop the application starting
        _log.exception("Could not load system decisions")
        return 0


__all__ = [
    "Decision",
    "DecisionBook",
    "DecisionError",
    "default_decisions",
    "load_decisions",
]
