"""Catalogue editions: what changed in a supplier's profiles, and who it hurts.

A supplier does not announce that profile 4310 is now ⁦1.412⁩ kg/m instead of
⁦1.386⁩, that the sash was redrawn with a deeper rebate, or that the old
threshold is gone. A new PDF appears on their site, or a DXF pack arrives by
email, and the shop goes on cutting to last year's figures until a window
does not close.

So every intake is kept as an *edition*: the profiles it listed, each with the
supplier's printed figures, the figures measured from the drawing and a
fingerprint of the drawing file. A new edition is compared with what the shop
already accepted for that series, profile by profile, and nothing replaces
the accepted figures until somebody has looked at the differences and said
so. The comparison then answers the only question that matters on the floor:
which open jobs are on this series, and which of the figures the shop
confirmed for cutting came from an article that has since changed.

Nothing here downloads, guesses or fills in a figure. An edition holds exactly
what the supplier's own files said, with the name of the catalogue it came
from.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.storage import MergingBook, stored

_log = get_logger("catalogue.editions")

#: The figures an edition keeps per profile, and how far each may move
#: between two editions before it counts as a change rather than rounding.
#: A printed table gives dimensions to a tenth of a millimetre and weight to
#: three decimals of a kilogram per metre, so anything past these is a real
#: change in the extrusion, not in how it was typed.
TRACKED: dict[str, tuple[str, float]] = {
    "width": ("abs", 0.05),
    "height": ("abs", 0.05),
    "mass_per_metre": ("rel", 0.005),
    "area": ("rel", 0.005),
    "ixx": ("rel", 0.01),
    "iyy": ("rel", 0.01),
}

FIGURE_NAMES: dict[str, str] = {
    "width": "רוחב",
    "height": "גובה",
    "mass_per_metre": "משקל למטר",
    "area": "שטח חתך",
    "ixx": "מומנט התמדה Ixx",
    "iyy": "מומנט התמדה Iyy",
}

FIGURE_UNITS: dict[str, str] = {
    "width": "מ״מ",
    "height": "מ״מ",
    "mass_per_metre": "ק״ג/מ׳",
    "area": "מ״מ²",
    "ixx": "מ״מ⁴",
    "iyy": "מ״מ⁴",
}

#: Job states in which a change to the series still matters.
OPEN_STATES = ("enquiry", "quoted", "won", "in_production")


class EditionState:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    HEBREW = {"pending": "ממתינה לבדיקה", "accepted": "אושרה", "rejected": "נדחתה"}


def code_key(code: str) -> str:
    """One article number however it was typed: '4310', 'K-4310 ', 'k4310'."""
    from .ingest import normalise_code

    return normalise_code(code)


def file_hash(path: str | Path) -> str:
    """A fingerprint of a drawing, so a redrawn section is noticed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #

@dataclass
class ProfileSnapshot:
    """One article as one edition described it."""

    profile_id: str
    name: str = ""
    #: The supplier's printed figures, as read from their table.
    published: dict[str, float] = field(default_factory=dict)
    #: Measured from their drawing.
    measured: dict[str, float] = field(default_factory=dict)
    drawing: str = ""
    drawing_hash: str = ""
    #: The ingestion verdict: verified, conflict, unverified, table only.
    status: str = ""
    page: int | None = None

    @property
    def key(self) -> str:
        return code_key(self.profile_id)

    def figure(self, name: str) -> tuple[float | None, str]:
        """The value to compare, and which kind it is."""
        if name in self.published:
            return self.published[name], "published"
        if name in self.measured:
            return self.measured[name], "measured"
        return None, ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProfileSnapshot":
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in known}
        if not values.get("profile_id"):
            raise ProfileOSError("פרופיל במהדורה חייב מספר פריט")
        for key in ("published", "measured"):
            values[key] = {
                str(name): float(value) for name, value in (values.get(key) or {}).items()
            }
        return cls(**values)


@dataclass
class Edition:
    """One intake of a supplier's catalogue for one series."""

    edition_id: str
    #: Where the edition came from, in the shop's words: which catalogue,
    #: which date or edition number. Required — it is the provenance.
    source: str
    series: str = ""
    #: The systems-directory entry it belongs to, when there is one.
    system_id: str = ""
    #: A complete catalogue of the series, or a partial pack. Only a complete
    #: one can say that an article was dropped.
    complete: bool = False
    received_on: str = field(default_factory=lambda: date.today().isoformat())
    recorded_by: str = ""
    state: str = EditionState.PENDING
    decided_at: str = ""
    decided_by: str = ""
    #: The order decisions were made in — two in the same second still
    #: have a before and an after.
    decision_no: int = 0
    note: str = ""
    profiles: dict[str, ProfileSnapshot] = field(default_factory=dict)

    @property
    def series_key(self) -> str:
        return series_key(self.system_id, self.series)

    @property
    def label(self) -> str:
        if self.system_id:
            try:
                from ..systems import DIRECTORY

                entry = DIRECTORY.get(self.system_id)
                if entry is not None:
                    return entry.display
            except Exception:  # noqa: BLE001 - a label must not fail
                pass
        return self.series or self.system_id or "סדרה לא ידועה"

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["profiles"] = [snapshot.as_dict() for snapshot in self.profiles.values()]
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Edition":
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in known}
        if not values.get("edition_id") or not str(values.get("source", "")).strip():
            raise ProfileOSError("מהדורה חייבת מזהה ומקור")
        profiles: dict[str, ProfileSnapshot] = {}
        for entry in values.pop("profiles", []) or []:
            snapshot = ProfileSnapshot.from_dict(entry)
            profiles[snapshot.key] = snapshot
        values["complete"] = bool(values.get("complete", False))
        values["decision_no"] = int(values.get("decision_no", 0) or 0)
        if values.get("state") not in EditionState.HEBREW:
            values["state"] = EditionState.PENDING
        return cls(profiles=profiles, **values)


def series_key(system_id: str, series: str) -> str:
    """Which library an edition belongs to: the directory entry, else the name."""
    if system_id:
        return system_id
    return " ".join(series.casefold().split())


def snapshots_from_report(report: Any) -> dict[str, ProfileSnapshot]:
    """The profiles of one ingestion run, as an edition keeps them."""
    from .ingest import measured_values

    profiles: dict[str, ProfileSnapshot] = {}
    for entry in report.entries:
        measured: dict[str, float] = {}
        if entry.measured is not None:
            measured = {
                name: round(float(value), 4)
                for name, value in measured_values(entry.measured).items()
                if name in TRACKED
            }
        published = {
            name: float(value) for name, value in (entry.published or {}).items()
            if name in TRACKED
        }
        drawing_hash = ""
        if entry.dxf_path:
            try:
                drawing_hash = file_hash(entry.dxf_path)
            except OSError as exc:
                _log.warning("Could not fingerprint %s: %s", entry.dxf_path, exc)
        snapshot = ProfileSnapshot(
            profile_id=entry.profile_id,
            name=entry.name or "",
            published=published,
            measured=measured,
            drawing=Path(entry.dxf_path).name if entry.dxf_path else "",
            drawing_hash=drawing_hash,
            status=entry.status,
            page=entry.pdf_page,
        )
        profiles.setdefault(snapshot.key, snapshot)
    return profiles


# --------------------------------------------------------------------------- #
# Comparing two editions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FigureChange:
    name: str
    old: float
    new: float

    @property
    def relative(self) -> float:
        return (self.new - self.old) / self.old if self.old else 0.0

    def describe(self) -> str:
        label = FIGURE_NAMES.get(self.name, self.name)
        unit = FIGURE_UNITS.get(self.name, "")
        percent = f" (⁦{self.relative * 100:+.1f}%⁩)" if self.old else ""
        return f"{label}: היה ⁦{self.old:,.4g}⁩, עכשיו ⁦{self.new:,.4g}⁩ {unit}{percent}"


@dataclass
class ProfileChange:
    profile_id: str
    name: str
    figures: list[FigureChange] = field(default_factory=list)
    #: The drawing file is different even though no tracked figure moved.
    redrawn: bool = False

    def describe(self) -> str:
        parts = [change.describe() for change in self.figures]
        if self.redrawn:
            parts.append("השרטוט עודכן")
        return f"⁦{self.profile_id}⁩ — " + "; ".join(parts)


@dataclass
class EditionDiff:
    """What a new edition changes against the accepted library."""

    #: There was nothing accepted to compare with.
    first: bool
    added: list[ProfileSnapshot] = field(default_factory=list)
    removed: list[ProfileSnapshot] = field(default_factory=list)
    changed: list[ProfileChange] = field(default_factory=list)
    unchanged: int = 0
    #: The new edition is partial, so absence from it says nothing.
    partial: bool = False

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def touched_keys(self) -> dict[str, str]:
        """Article key → 'removed' or 'changed', for the confirmations check."""
        touched = {snapshot.key: "removed" for snapshot in self.removed}
        for change in self.changed:
            touched[code_key(change.profile_id)] = "changed"
        return touched

    def summary(self) -> str:
        if self.first:
            return f"ספרייה ראשונה לסדרה: ⁦{len(self.added)}⁩ פרופילים"
        if self.empty:
            return f"אין שינוי מול המהדורה שאושרה (⁦{self.unchanged}⁩ פרופילים זהים)"
        parts = []
        if self.added:
            parts.append(f"⁦{len(self.added)}⁩ חדשים")
        if self.changed:
            parts.append(f"⁦{len(self.changed)}⁩ השתנו")
        if self.removed:
            parts.append(f"⁦{len(self.removed)}⁩ הוסרו")
        return " · ".join(parts)

    def lines(self) -> list[str]:
        """The whole comparison, line by line, for the detail pane."""
        out = [self.summary(), ""]
        if self.changed:
            out.append("השתנו")
            out += [f"  • {change.describe()}" for change in self.changed]
            out.append("")
        if self.added:
            out.append("חדשים")
            out += [
                f"  • ⁦{snapshot.profile_id}⁩ {snapshot.name}".rstrip()
                for snapshot in self.added
            ]
            out.append("")
        if self.removed:
            out.append("לא מופיעים יותר בקטלוג")
            out += [
                f"  • ⁦{snapshot.profile_id}⁩ {snapshot.name}".rstrip()
                for snapshot in self.removed
            ]
            out.append("")
        if self.partial and not self.first:
            out.append(
                "המהדורה סומנה כעדכון חלקי, לכן פרופיל שחסר בה לא נחשב כמוסר."
            )
        return out


def _moved(name: str, old: float, new: float) -> bool:
    kind, limit = TRACKED[name]
    if kind == "abs":
        return abs(new - old) > limit
    if old == 0.0:
        return new != 0.0
    return abs(new - old) / abs(old) > limit


def compare_profile(old: ProfileSnapshot, new: ProfileSnapshot) -> ProfileChange | None:
    """What moved in one article between two editions, or ``None``."""
    figures: list[FigureChange] = []
    for name in TRACKED:
        # Like is compared with like: the supplier's printed figure with the
        # printed one, a measurement with a measurement. A table-only edition
        # against a drawings-only one has nothing to compare, not a change.
        before = after = None
        for kind in ("published", "measured"):
            source_old = getattr(old, kind)
            source_new = getattr(new, kind)
            if name in source_old and name in source_new:
                before, after = source_old[name], source_new[name]
                break
        if before is not None and after is not None and _moved(name, before, after):
            figures.append(FigureChange(name, before, after))
    redrawn = bool(
        old.drawing_hash and new.drawing_hash and old.drawing_hash != new.drawing_hash
        and not figures
    )
    if not figures and not redrawn:
        return None
    return ProfileChange(new.profile_id, new.name or old.name, figures, redrawn)


def compare(library: dict[str, ProfileSnapshot], edition: Edition) -> EditionDiff:
    """A new edition set against the profiles already accepted for its series."""
    if not library:
        return EditionDiff(first=True, added=sorted(
            edition.profiles.values(), key=lambda s: s.profile_id
        ))
    diff = EditionDiff(first=False, partial=not edition.complete)
    for key, snapshot in sorted(edition.profiles.items()):
        before = library.get(key)
        if before is None:
            diff.added.append(snapshot)
            continue
        change = compare_profile(before, snapshot)
        if change is None:
            diff.unchanged += 1
        else:
            diff.changed.append(change)
    if edition.complete:
        diff.removed = sorted(
            (s for key, s in library.items() if key not in edition.profiles),
            key=lambda s: s.profile_id,
        )
    return diff


# --------------------------------------------------------------------------- #
# The book
# --------------------------------------------------------------------------- #

class EditionBook(MergingBook):
    """Every catalogue edition the shop has taken in, kept on disk."""

    _list_key = "editions"
    _id_field = "edition_id"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._editions: dict[str, Edition] = {}

    def _records(self) -> dict[str, Any]:
        return self._editions

    def _to_raw(self, record: Any) -> dict[str, Any]:
        return record.as_dict()

    def _from_raw(self, raw: dict[str, Any]) -> Any:
        return Edition.from_dict(raw)

    def load(self) -> "EditionBook":
        if self.path is None or not stored(self.path):
            self._remember_loaded()
            return self
        raw = self._read_or_mark(self.path)
        for entry in raw.get("editions", []):
            try:
                edition = Edition.from_dict(entry)
            except Exception as exc:  # noqa: BLE001 - kept on disk by the merge
                _log.warning("Skipped an unreadable edition: %s", exc)
                continue
            self._editions[edition.edition_id] = edition
        self._remember_loaded()
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save_merged(self.path)

    # -- reading -------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._editions)

    def __iter__(self):
        """Waiting editions first, then the newest."""
        newest = sorted(
            self._editions.values(),
            key=lambda e: (e.received_on, e.decision_no, e.edition_id), reverse=True,
        )
        return iter(sorted(newest, key=lambda e: e.state != EditionState.PENDING))

    def get(self, edition_id: str) -> Edition | None:
        return self._editions.get(edition_id)

    def pending(self) -> list[Edition]:
        return [e for e in self if e.state == EditionState.PENDING]

    def accepted(self, key: str) -> list[Edition]:
        """The accepted editions of one series, oldest decision first."""
        return sorted(
            (e for e in self._editions.values()
             if e.state == EditionState.ACCEPTED and e.series_key == key),
            key=lambda e: (e.decision_no, e.decided_at, e.received_on, e.edition_id),
        )

    def library(self, key: str, *, before: Edition | None = None) -> dict[str, ProfileSnapshot]:
        """The profiles the shop accepted for a series, edition over edition.

        A complete edition is the whole series at that point; a partial one
        updates the articles it lists and leaves the rest as they were.
        """
        profiles: dict[str, ProfileSnapshot] = {}
        for edition in self.accepted(key):
            if before is not None and edition.edition_id == before.edition_id:
                break
            if edition.complete:
                profiles = {}
            profiles.update(edition.profiles)
        return profiles

    def diff(self, edition: Edition) -> EditionDiff:
        """The edition against everything accepted for its series before it."""
        if edition.state == EditionState.ACCEPTED:
            return compare(self.library(edition.series_key, before=edition), edition)
        return compare(self.library(edition.series_key), edition)

    def keys(self) -> list[str]:
        return sorted({e.series_key for e in self._editions.values()})

    # -- changes -------------------------------------------------------------- #
    def record(self, report: Any, *, source: str, series: str = "", system_id: str = "",
               complete: bool = False, recorded_by: str = "") -> Edition:
        """Keep one ingestion run as a pending edition."""
        if not source.strip():
            raise ProfileOSError(
                "כתבו מאיפה הקטלוג — שם הספק, המהדורה או התאריך. בלי מקור "
                "אי אפשר לדעת מאוחר יותר על מה מבוססים המספרים."
            )
        profiles = snapshots_from_report(report)
        if not profiles:
            raise ProfileOSError("הקליטה לא מצאה אף פרופיל, אין מה לשמור כמהדורה")
        if not series.strip() and not system_id:
            raise ProfileOSError("בחרו לאיזו סדרה שייך הקטלוג")
        edition = Edition(
            edition_id=f"ED-{date.today():%Y%m%d}-{uuid4().hex[:6].upper()}",
            source=source.strip(),
            series=series.strip(),
            system_id=system_id,
            complete=complete,
            recorded_by=recorded_by.strip(),
            profiles=profiles,
        )
        self._editions[edition.edition_id] = edition
        self.save()
        return edition

    def _decide(self, edition_id: str, state: str, by: str, note: str) -> Edition:
        edition = self._editions.get(edition_id)
        if edition is None:
            raise ProfileOSError(f"אין מהדורה {edition_id}")
        if edition.state != EditionState.PENDING:
            raise ProfileOSError(
                f"המהדורה כבר {EditionState.HEBREW[edition.state]} — "
                "אפשר להחליט רק על מהדורה שממתינה"
            )
        edition.state = state
        edition.decided_by = by.strip()
        edition.decided_at = datetime.now().isoformat(timespec="seconds")
        edition.decision_no = 1 + max(
            (e.decision_no for e in self._editions.values()), default=0
        )
        edition.note = note.strip()
        self.save()
        return edition

    def accept(self, edition_id: str, *, by: str = "", note: str = "") -> Edition:
        return self._decide(edition_id, EditionState.ACCEPTED, by, note)

    def reject(self, edition_id: str, *, by: str = "", note: str = "") -> Edition:
        return self._decide(edition_id, EditionState.REJECTED, by, note)


def default_editions_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "catalogue_editions.json"


def default_editions() -> EditionBook:
    return EditionBook(default_editions_path()).load()


# --------------------------------------------------------------------------- #
# Who it hurts
# --------------------------------------------------------------------------- #

def _job_systems(job: Any) -> set[str]:
    systems = {getattr(job, "system_id", "") or ""}
    schedule = getattr(job, "schedule", None)
    if schedule is not None:
        systems.add(getattr(schedule, "system_id", "") or "")
        for opening in getattr(schedule, "openings", []) or []:
            systems.add(getattr(opening, "system_id", "") or "")
    systems.discard("")
    systems.discard("generic")
    return systems


def open_jobs_on(system_id: str, jobs: Iterable[Any]) -> list[Any]:
    """Jobs not yet installed that are drawn on this series."""
    if not system_id:
        return []
    found = []
    for job in jobs:
        status = getattr(getattr(job, "status", ""), "value", str(getattr(job, "status", "")))
        if status in OPEN_STATES and system_id in _job_systems(job):
            found.append(job)
    return found


#: The roles a cutting confirmation names its articles by.
ROLE_NAMES = {"frame": "משקוף", "sash": "כנף", "mullion": "זקף", "bead": "סרגל"}


@dataclass(frozen=True)
class ConfirmationHit:
    """A figure the shop confirmed for cutting, on an article that moved."""

    role: str
    article: str
    change: str  # "changed" or "removed"

    def describe(self) -> str:
        what = "השתנה" if self.change == "changed" else "הוסר מהקטלוג"
        return f"{ROLE_NAMES.get(self.role, self.role)} ⁦{self.article}⁩ {what}"


def confirmation_hits(diff: EditionDiff, confirmation: Any) -> list[ConfirmationHit]:
    """Articles named in a series' confirmation that this edition changes."""
    if confirmation is None:
        return []
    touched = diff.touched_keys()
    hits = []
    for role, article in (getattr(confirmation, "profiles", {}) or {}).items():
        change = touched.get(code_key(str(article)))
        if change:
            hits.append(ConfirmationHit(str(role), str(article), change))
    return hits


def stale_confirmation(book: EditionBook, system_id: str, confirmation: Any) -> list[ConfirmationHit]:
    """Changes accepted *after* the cutting figures were entered.

    The confirmation was read off an older catalogue. Once an accepted
    edition changes one of the articles it names, those figures need reading
    again; entering them again (a newer ``entered_on``) clears this.
    """
    if confirmation is None or not system_id:
        return []
    entered_at = str(getattr(confirmation, "entered_at", "") or "")
    entered_on = str(getattr(confirmation, "entered_on", "") or "")
    hits: list[ConfirmationHit] = []
    for edition in book.accepted(system_id):
        if entered_at:
            if edition.decided_at <= entered_at:
                continue
        # An older record knows only the day: an edition accepted that same
        # day may have come after it, so it is asked about rather than assumed.
        elif edition.decided_at[:10] < entered_on:
            continue
        hits.extend(confirmation_hits(book.diff(edition), confirmation))
    unique: dict[tuple[str, str], ConfirmationHit] = {}
    for hit in hits:
        unique[(hit.role, hit.article)] = hit
    return list(unique.values())


__all__ = [
    "ConfirmationHit",
    "Edition",
    "EditionBook",
    "EditionDiff",
    "EditionState",
    "FigureChange",
    "ProfileChange",
    "ProfileSnapshot",
    "TRACKED",
    "code_key",
    "compare",
    "compare_profile",
    "confirmation_hits",
    "default_editions",
    "default_editions_path",
    "file_hash",
    "open_jobs_on",
    "series_key",
    "snapshots_from_report",
    "stale_confirmation",
]
