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

#: Every figure a supplier's table carries, in the order a catalogue prints
#: them. Each is kept and compared; nothing a table offered is dropped.
FIGURES: tuple[str, ...] = (
    "width", "height", "wall_thickness", "mass_per_metre", "area", "perimeter",
    "ixx", "iyy", "sx", "sy", "j",
)

#: Printed figures are compared exactly. They are the supplier's own digits,
#: read from their table, not retyped: if a printed digit changed, the
#: supplier recalculated, and that deserves a look — not a tolerance.
#:
#: Measured figures (integrated from the DXF) are compared with a tolerance,
#: because two drawings of the same outline can differ in how arcs and
#: fillets were segmented. The tolerance absorbs that and nothing more: a
#: twentieth of a millimetre on a dimension, half a percent on area-like
#: figures, one percent on second moments, five on torsion (the most
#: sensitive to fillets). Past these the outline itself is different.
MEASURED_TOLERANCE: dict[str, tuple[str, float]] = {
    "width": ("abs", 0.05),
    "height": ("abs", 0.05),
    "wall_thickness": ("abs", 0.05),
    "mass_per_metre": ("rel", 0.005),
    "area": ("rel", 0.005),
    "perimeter": ("rel", 0.005),
    "ixx": ("rel", 0.01),
    "iyy": ("rel", 0.01),
    "sx": ("rel", 0.01),
    "sy": ("rel", 0.01),
    "j": ("rel", 0.05),
}
TRACKED = MEASURED_TOLERANCE  # the names an edition keeps

FIGURE_NAMES: dict[str, str] = {
    "width": "רוחב",
    "height": "גובה",
    "wall_thickness": "עובי דופן",
    "mass_per_metre": "משקל למטר",
    "area": "שטח חתך",
    "perimeter": "היקף",
    "ixx": "מומנט התמדה Ixx",
    "iyy": "מומנט התמדה Iyy",
    "sx": "מודול חתך Wx",
    "sy": "מודול חתך Wy",
    "j": "קבוע פיתול",
}

#: Shown in the units a catalogue prints, so a figure can be checked against
#: the page: (unit, factor from the stored millimetres, decimals).
DISPLAY: dict[str, tuple[str, float, int]] = {
    "width": ("מ״מ", 1.0, 2),
    "height": ("מ״מ", 1.0, 2),
    "wall_thickness": ("מ״מ", 1.0, 2),
    "mass_per_metre": ("ק״ג/מ׳", 1.0, 3),
    "area": ("סמ״ר", 0.01, 3),
    "perimeter": ("ס״מ", 0.1, 2),
    "ixx": ("ס״מ⁴", 1e-4, 3),
    "iyy": ("ס״מ⁴", 1e-4, 3),
    "sx": ("ס״מ³", 1e-3, 3),
    "sy": ("ס״מ³", 1e-3, 3),
    "j": ("ס״מ⁴", 1e-4, 3),
}


def _shown(name: str, value: float) -> str:
    unit, factor, decimals = DISPLAY.get(name, ("", 1.0, 3))
    text = f"{value * factor:,.{decimals}f}".rstrip("0").rstrip(".")
    return text


#: Job states in which a change to the series still matters.
OPEN_STATES = ("enquiry", "quoted", "won", "in_production")


class EditionState:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    HEBREW = {"pending": "ממתינה לבדיקה", "accepted": "אושרה", "rejected": "נדחתה"}


def code_key(code: str) -> str:
    """One article number however it was punctuated: '43-10', '43 10', '4310'.

    Separators and case are dropped and nothing else: 'K-4310' and '4310'
    stay different, because a prefix can be the only thing that tells two
    suppliers' articles apart.
    """
    from .ingest import normalise_code

    return normalise_code(code)


def file_hash(path: str | Path) -> str:
    """Which drawing file was read — kept as a record, never compared: the
    same outline saved again has different bytes."""
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
    #: one can say that an article is missing from it.
    complete: bool = False
    #: The supplier's own edition: its date or revision as printed on the
    #: catalogue ("מהדורה 3, 03/2026"). The intake date is not the edition.
    revision: str = ""
    #: What the intake noticed and could not settle, for whoever reviews it.
    warnings: list[str] = field(default_factory=list)
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
        values["warnings"] = [str(w) for w in values.get("warnings") or []]
        values["decision_no"] = int(values.get("decision_no", 0) or 0)
        if values.get("state") not in EditionState.HEBREW:
            values["state"] = EditionState.PENDING
        return cls(profiles=profiles, **values)


def series_key(system_id: str, series: str) -> str:
    """Which library an edition belongs to: the directory entry, else the name."""
    if system_id:
        return system_id
    return " ".join(series.casefold().split())


def snapshots_from_report(report: Any, warnings: list[str] | None = None) -> dict[str, ProfileSnapshot]:
    """The profiles of one ingestion run, as an edition keeps them.

    Two articles that read the same once punctuation is dropped ('43-101'
    and '431-01') are kept as the first and named in ``warnings``, never
    merged in silence.
    """
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
        if snapshot.key in profiles:
            if warnings is not None:
                warnings.append(
                    f"⁦{snapshot.profile_id}⁩ ו־⁦{profiles[snapshot.key].profile_id}⁩ "
                    "נקראים כאותו מק״ט — נשמר רק הראשון. בדקו מול הקטלוג."
                )
            continue
        profiles[snapshot.key] = snapshot
    return profiles


# --------------------------------------------------------------------------- #
# Comparing two editions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FigureChange:
    name: str
    old: float
    new: float
    #: "published" (the supplier's printed figure) or "measured" (the DXF).
    kind: str = "published"

    @property
    def relative(self) -> float:
        return (self.new - self.old) / self.old if self.old else 0.0

    def describe(self) -> str:
        label = FIGURE_NAMES.get(self.name, self.name)
        unit = DISPLAY.get(self.name, ("",))[0]
        if MEASURED_TOLERANCE.get(self.name, ("rel",))[0] == "abs":
            delta = f"⁦{self.new - self.old:+.2f}⁩ {unit}"
        else:
            delta = f"⁦{self.relative * 100:+.1f}%⁩" if self.old else ""
        source = "" if self.kind == "published" else " (נמדד מהשרטוט)"
        return (
            f"{label}: היה ⁦{_shown(self.name, self.old)}⁩, "
            f"עכשיו ⁦{_shown(self.name, self.new)}⁩ {unit}"
            + (f" ({delta})" if delta else "") + source
        )


@dataclass
class ProfileChange:
    profile_id: str
    name: str
    figures: list[FigureChange] = field(default_factory=list)

    def describe(self) -> str:
        return f"⁦{self.profile_id}⁩ — " + "; ".join(c.describe() for c in self.figures)


@dataclass
class EditionDiff:
    """What a new edition changes against the accepted library."""

    #: There was nothing accepted to compare with.
    first: bool
    added: list[ProfileSnapshot] = field(default_factory=list)
    #: In the accepted library, not in this complete edition. Absence is not
    #: proof of discontinuation — a page that did not read leaves the same
    #: gap — so these are to be checked with the supplier, not assumed gone.
    removed: list[ProfileSnapshot] = field(default_factory=list)
    changed: list[ProfileChange] = field(default_factory=list)
    unchanged: int = 0
    #: In both editions, but with no figure of the same kind to compare
    #: (a table against a drawings-only pack): not the same as unchanged.
    uncompared: list[str] = field(default_factory=list)
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
        parts = []
        if self.changed:
            parts.append(f"⁦{len(self.changed)}⁩ השתנו")
        if self.added:
            parts.append(f"⁦{len(self.added)}⁩ חדשים")
        if self.removed:
            parts.append(f"⁦{len(self.removed)}⁩ לא נמצאו")
        if self.uncompared:
            parts.append(f"⁦{len(self.uncompared)}⁩ לא ניתנו להשוואה")
        if not parts:
            return f"אין שינוי מול המהדורה שאושרה (⁦{self.unchanged}⁩ פרופילים זהים)"
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
            out.append("לא נמצאו במהדורה — לברר מול הספק אם יצאו מהקטלוג")
            out += [
                f"  • ⁦{snapshot.profile_id}⁩ {snapshot.name}".rstrip()
                for snapshot in self.removed
            ]
            out.append("")
        if self.uncompared:
            out.append("לא ניתנו להשוואה — באחת המהדורות יש רק טבלה ובשנייה רק שרטוט")
            out.append("  " + ", ".join(f"⁦{code}⁩" for code in self.uncompared))
            out.append("")
        if self.partial and not self.first:
            out.append(
                "המהדורה סומנה כעדכון חלקי, לכן פרופיל שחסר בה לא נחשב כחסר."
            )
        return out


def _moved(name: str, kind: str, old: float, new: float) -> bool:
    if kind == "published":
        return abs(new - old) > 1e-9 * max(1.0, abs(old))
    mode, limit = MEASURED_TOLERANCE.get(name, ("rel", 0.005))
    if mode == "abs":
        return abs(new - old) > limit
    if old == 0.0:
        return new != 0.0
    return abs(new - old) / abs(old) > limit


def _figure_changes(old: ProfileSnapshot, new: ProfileSnapshot) -> tuple[list[FigureChange], int]:
    """Changed figures, and how many figures could be compared at all."""
    figures: list[FigureChange] = []
    compared = 0
    for name in FIGURES:
        # Like is compared with like: the supplier's printed figure with the
        # printed one, a measurement with a measurement.
        for kind in ("published", "measured"):
            before, after = getattr(old, kind).get(name), getattr(new, kind).get(name)
            if before is None or after is None:
                continue
            compared += 1
            if _moved(name, kind, before, after):
                figures.append(FigureChange(name, before, after, kind))
            break
    return figures, compared


def compare_profile(old: ProfileSnapshot, new: ProfileSnapshot) -> ProfileChange | None:
    """What moved in one article between two editions, or ``None``.

    The drawing file itself is not compared: re-saving an unchanged DXF
    changes its bytes (the header carries the save date). A real change in
    the outline shows in what is measured from it — area, perimeter, the
    second moments — and is caught there.
    """
    figures, _ = _figure_changes(old, new)
    if not figures:
        return None
    return ProfileChange(new.profile_id, new.name or old.name, figures)


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
        figures, compared = _figure_changes(before, snapshot)
        if figures:
            diff.changed.append(
                ProfileChange(snapshot.profile_id, snapshot.name or before.name, figures)
            )
        elif compared:
            diff.unchanged += 1
        else:
            diff.uncompared.append(snapshot.profile_id)
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
               complete: bool = False, recorded_by: str = "", revision: str = "") -> Edition:
        """Keep one ingestion run as a pending edition."""
        if not source.strip():
            raise ProfileOSError(
                "כתבו מאיפה הקטלוג — שם הספק, המהדורה או התאריך. בלי מקור "
                "אי אפשר לדעת מאוחר יותר על מה מבוססים המספרים."
            )
        errors = list(getattr(report, "errors", []) or [])
        if complete and errors:
            raise ProfileOSError(
                f"לא כל הקובץ נקרא (⁦{len(errors)}⁩ שגיאות בקליטה), ולכן אי אפשר "
                "לסמן אותו כקטלוג מלא: פרופיל שלא נקרא היה נראה כאילו יצא מהקטלוג. "
                "שמרו אותו כעדכון חלקי, או תקנו את הקליטה."
            )
        warnings: list[str] = []
        profiles = snapshots_from_report(report, warnings)
        if errors:
            warnings.append(f"⁦{len(errors)}⁩ קבצים או עמודים לא נקראו בקליטה")
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
            revision=revision.strip(),
            recorded_by=recorded_by.strip(),
            warnings=warnings,
            profiles=profiles,
        )
        self._editions[edition.edition_id] = edition
        self.save()
        return edition

    def _decide(self, edition_id: str, state: str, by: str, note: str) -> Edition:
        edition = self._editions.get(edition_id)
        if edition is None:
            raise ProfileOSError(f"אין מהדורה {edition_id}")
        if self.path is not None and stored(self.path):
            # Another computer may have decided meanwhile; its decision stands.
            on_disk = EditionBook(self.path).load().get(edition_id)
            if on_disk is not None and on_disk.state != EditionState.PENDING:
                self._editions[edition_id] = on_disk
                self._loaded[edition_id] = on_disk.as_dict()
                edition = on_disk
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
ROLE_NAMES = {
    "frame": "משקוף", "sash": "כנף", "mullion": "זקף", "transom": "קורה",
    "bead": "סרגל",
}


@dataclass(frozen=True)
class ConfirmationHit:
    """A figure the shop confirmed for cutting, on an article that moved."""

    role: str
    article: str
    change: str  # "changed" or "removed"

    def describe(self) -> str:
        what = "השתנה" if self.change == "changed" else "לא נמצא בקטלוג החדש"
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


@dataclass
class Staleness:
    """Why a series' cutting figures need reading again."""

    #: Accepted editions newer than the one the figures were read from.
    newer: list[Edition] = field(default_factory=list)
    #: Of those, the articles the confirmation names that changed or went.
    hits: list[ConfirmationHit] = field(default_factory=list)
    #: The confirmation names no articles, so nothing can be matched.
    no_articles: bool = False

    def __bool__(self) -> bool:
        return bool(self.newer)

    @property
    def latest(self) -> Edition:
        return self.newer[-1]


def stale_confirmation(book: EditionBook, system_id: str, confirmation: Any) -> Staleness:
    """Whether a newer catalogue was accepted than the figures were read from.

    Any newer accepted edition asks for the figures to be read again — not
    only when a tracked article changed. Deductions live in the supplier's
    cutting and glazing tables: a new cleat or gasket changes them with
    every extrusion as it was, and no comparison of sections can see that.
    The articles that did change are named on top, as the urgent part.

    The figures say which edition they were read from (``edition_id``). An
    older record that does not is dated by when it was entered instead; an
    edition accepted the same day it was entered is counted as newer.
    """
    if confirmation is None or not system_id:
        return Staleness()
    accepted = book.accepted(system_id)
    read_from = str(getattr(confirmation, "edition_id", "") or "")
    ids = [edition.edition_id for edition in accepted]
    if read_from and read_from in ids:
        newer = accepted[ids.index(read_from) + 1:]
    else:
        entered_at = str(getattr(confirmation, "entered_at", "") or "")
        entered_on = str(getattr(confirmation, "entered_on", "") or "")
        if entered_at:
            newer = [e for e in accepted if e.decided_at > entered_at]
        else:
            newer = [e for e in accepted if e.decided_at[:10] >= entered_on]
    hits: dict[tuple[str, str], ConfirmationHit] = {}
    for edition in newer:
        for hit in confirmation_hits(book.diff(edition), confirmation):
            hits[(hit.role, hit.article)] = hit
    return Staleness(
        newer=newer, hits=list(hits.values()),
        no_articles=not (getattr(confirmation, "profiles", None) or {}),
    )


@dataclass(frozen=True)
class StockLeft:
    """Bars of an article the supplier dropped, still on the rack."""

    profile_id: str
    name: str
    quantity: float
    unit: str
    counted_on: str

    def describe(self) -> str:
        unit = self.unit or "יח׳"
        return (
            f"⁦{self.profile_id}⁩ — נספרו ⁦{self.quantity:g}⁩ {unit} "
            f"בספירה של ⁦{self.counted_on}⁩"
        )


def stock_of_removed(diff: EditionDiff, stocktakes: Iterable[Any]) -> list[StockLeft]:
    """What the last stocktake found of each article this edition drops.

    A dropped profile is not only a drawing problem: bars of it are money on
    the rack that no future job will be drawn on. The latest posted count of
    each code says how many — figures from the shop's own count, not a guess.
    """
    removed = {snapshot.key: snapshot for snapshot in diff.removed}
    if not removed:
        return []
    latest: dict[str, StockLeft] = {}
    posted = [
        sheet for sheet in stocktakes
        if str(getattr(getattr(sheet, "status", ""), "value", getattr(sheet, "status", ""))) == "posted"
    ]
    posted.sort(key=lambda sheet: str(getattr(sheet, "posted_on", None) or sheet.opened))
    for sheet in posted:
        when = getattr(sheet, "posted_on", None) or sheet.opened
        for line in sheet.lines:
            key = code_key(line.code)
            if key in removed and line.counted is not None:
                latest[key] = StockLeft(
                    removed[key].profile_id, removed[key].name or line.name,
                    float(line.counted), line.unit,
                    f"{when:%d/%m/%Y}" if hasattr(when, "strftime") else str(when),
                )
    return sorted(
        (left for left in latest.values() if left.quantity > 0),
        key=lambda left: left.profile_id,
    )


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
    "Staleness",
    "StockLeft",
    "stock_of_removed",
    "stale_confirmation",
]
