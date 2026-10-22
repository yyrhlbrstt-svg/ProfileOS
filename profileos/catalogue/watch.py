"""Watching where suppliers publish, so a new catalogue is noticed the day it appears.

No Israeli extrusion house offers a feed of its profile changes. What they do
is put a PDF or a DXF pack on their website, or send it by email to whoever is
on their list, and the shop finds out when a section does not match. So this
watches the two places a catalogue actually turns up:

* **a folder** — the one the shop saves supplier attachments into, a shared
  drive, a synced cloud folder. A new or changed PDF, DXF, CSV or ZIP there
  is a finding.
* **a web page** — the supplier's own downloads page, whose address the shop
  enters. A link to a catalogue file that was not there before is a finding,
  and so is a known file the server now reports as different.

A finding is a note — "this appeared, go and look" — never data. Figures only
ever enter the software through ingestion, from the supplier's own file, with
its name recorded, and only become the shop's library when somebody accepts
the edition. Nothing here invents an address either: the shop adds the pages
it wants watched.

The first check of a new source only records what is already there, so
adding a folder of four hundred drawings does not produce four hundred
findings.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.storage import MergingBook, atomic_write_bytes, stored

_log = get_logger("catalogue.watch")

#: What a supplier's catalogue arrives as.
CATALOGUE_SUFFIXES = (".pdf", ".dxf", ".dwg", ".zip", ".csv", ".tsv", ".xlsx", ".xls")

MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 20
#: How many known files one check asks the server about, to stay polite.
MAX_HEAD_REQUESTS = 40
#: How often each kind is checked by itself.
DUE_HOURS = {"folder": 1, "web": 24}

USER_AGENT = "ProfileOS catalogue watch (one shop checking its suppliers' downloads)"


class Kind:
    FOLDER = "folder"
    WEB = "web"
    HEBREW = {"folder": "תיקייה", "web": "אתר"}


class Change:
    NEW = "new"
    CHANGED = "changed"
    HEBREW = {"new": "קובץ חדש", "changed": "קובץ עודכן"}


@dataclass
class WatchedSource:
    """One place a supplier's catalogues turn up."""

    source_id: str
    kind: str
    #: A folder path, or the address of the supplier's downloads page.
    location: str
    supplier: str = ""
    #: The series it is for, when the source is that specific.
    system_id: str = ""
    added_on: str = field(default_factory=lambda: date.today().isoformat())
    last_checked: str = ""
    #: The last check's failure, in words, shown on the page until it passes.
    last_error: str = ""
    #: What was there at the last check: name or address → fingerprint.
    known: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.supplier or (
            Path(self.location).name if self.kind == Kind.FOLDER
            else urlparse(self.location).netloc
        ) or self.location

    def due(self, now: datetime | None = None) -> bool:
        if not self.last_checked:
            return True
        now = now or datetime.now()
        try:
            last = datetime.fromisoformat(self.last_checked)
        except ValueError:
            return True
        return now - last >= timedelta(hours=DUE_HOURS.get(self.kind, 24))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WatchedSource":
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in known}
        if not values.get("source_id") or not values.get("location"):
            raise ProfileOSError("מקור במעקב חייב מזהה ומיקום")
        if values.get("kind") not in Kind.HEBREW:
            raise ProfileOSError(f"סוג מקור לא מוכר: {values.get('kind')}")
        values["known"] = {str(k): str(v) for k, v in (values.get("known") or {}).items()}
        return cls(**values)


@dataclass
class Finding:
    """Something that appeared at a watched source and has not been dealt with."""

    finding_id: str
    source_id: str
    name: str
    #: Full path or address of the file.
    where: str
    change: str = Change.NEW
    found_on: str = field(default_factory=lambda: date.today().isoformat())
    supplier: str = ""
    #: When somebody dealt with it, and how: "נקלט", "לא רלוונטי", "הורד".
    handled_on: str = ""
    handled_how: str = ""
    #: Where it was downloaded to, for a file from a web page.
    local_path: str = ""

    @property
    def open(self) -> bool:
        return not self.handled_on

    @property
    def is_web(self) -> bool:
        return self.where.startswith(("http://", "https://"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Finding":
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in known}
        if not values.get("finding_id") or not values.get("where"):
            raise ProfileOSError("ממצא חייב מזהה ומיקום")
        return cls(**values)


class _Book(MergingBook):
    _record_type: type = WatchedSource

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._items: dict[str, Any] = {}

    def _records(self) -> dict[str, Any]:
        return self._items

    def _to_raw(self, record: Any) -> dict[str, Any]:
        return record.as_dict()

    def _from_raw(self, raw: dict[str, Any]) -> Any:
        return self._record_type.from_dict(raw)

    def load(self):
        if self.path is None or not stored(self.path):
            self._remember_loaded()
            return self
        raw = self._read_or_mark(self.path)
        for entry in raw.get(self._list_key, []):
            try:
                record = self._record_type.from_dict(entry)
            except Exception as exc:  # noqa: BLE001 - kept on disk by the merge
                _log.warning("Skipped an unreadable %s: %s", self._list_key, exc)
                continue
            self._items[getattr(record, self._id_field)] = record
        self._remember_loaded()
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save_merged(self.path)

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str) -> Any:
        return self._items.get(key)


class WatchBook(_Book):
    """The places the shop wants watched."""

    _list_key = "sources"
    _id_field = "source_id"
    _record_type = WatchedSource

    def __iter__(self):
        return iter(sorted(self._items.values(), key=lambda s: (s.supplier, s.location)))

    def add(self, kind: str, location: str, *, supplier: str = "",
            system_id: str = "") -> WatchedSource:
        location = location.strip()
        if kind == Kind.FOLDER:
            if not location:
                raise ProfileOSError("בחרו תיקייה")
            if not Path(location).is_dir():
                raise ProfileOSError(f"התיקייה {location} לא נמצאה")
        elif kind == Kind.WEB:
            location = normalise_address(location)
        else:
            raise ProfileOSError(f"סוג מקור לא מוכר: {kind}")
        for existing in self._items.values():
            if existing.kind == kind and existing.location == location:
                raise ProfileOSError(f"המקור כבר במעקב: {existing.title}")
        source = WatchedSource(
            source_id=f"SRC-{uuid4().hex[:8].upper()}",
            kind=kind,
            location=location,
            supplier=supplier.strip(),
            system_id=system_id,
        )
        self._items[source.source_id] = source
        self.save()
        return source

    def remove(self, source_id: str) -> None:
        if source_id in self._items:
            del self._items[source_id]
            self.save()


class FindingBook(_Book):
    """What the watch found, and what was done about it."""

    _list_key = "findings"
    _id_field = "finding_id"
    _record_type = Finding

    def __iter__(self):
        return iter(sorted(
            self._items.values(), key=lambda f: (f.found_on, f.name), reverse=True,
        ))

    def open_findings(self) -> list[Finding]:
        return [finding for finding in self if finding.open]

    def handle(self, finding_id: str, how: str, *, local_path: str = "") -> Finding:
        finding = self._items.get(finding_id)
        if finding is None:
            raise ProfileOSError("הממצא לא נמצא — ייתכן שטופל כבר במחשב אחר")
        finding.handled_on = date.today().isoformat()
        finding.handled_how = how.strip()
        if local_path:
            finding.local_path = local_path
        self.save()
        return finding

    def set_local_path(self, finding_id: str, local_path: str) -> Finding:
        finding = self._items.get(finding_id)
        if finding is None:
            raise ProfileOSError("הממצא לא נמצא")
        finding.local_path = local_path
        self.save()
        return finding


def default_watch_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "catalogue_watch.json"


def default_findings_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "catalogue_findings.json"


def default_watch() -> WatchBook:
    return WatchBook(default_watch_path()).load()


def default_findings() -> FindingBook:
    return FindingBook(default_findings_path()).load()


def downloads_dir() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "catalogues"


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

#: ``fetch(url, method, limit) -> (body, headers)``; injectable for tests and
#: for a plant whose proxy needs something special.
Fetcher = Callable[[str, str, int], tuple[bytes, dict[str, str]]]


def normalise_address(text: str) -> str:
    """A web address the shop typed, as one this module will fetch."""
    address = text.strip()
    if not address:
        raise ProfileOSError("הקלידו את כתובת דף ההורדות של הספק")
    if "://" not in address:
        address = "https://" + address
    parsed = urlparse(address)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProfileOSError(f"⁦{text.strip()}⁩ אינה כתובת אתר")
    return address


def http_fetch(url: str, method: str = "GET", limit: int = MAX_PAGE_BYTES) -> tuple[bytes, dict[str, str]]:
    """Fetch one address over HTTP(S), refusing anything too large."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ProfileOSError(f"כתובת לא נתמכת: {url}")
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - scheme checked above
        headers = {key.lower(): value for key, value in response.headers.items()}
        if method == "HEAD":
            return b"", headers
        declared = int(headers.get("content-length") or 0)
        if declared > limit:
            raise ProfileOSError(
                f"הקובץ גדול מדי (⁦{declared / 1e6:.0f}⁩ מ״ב) — הורידו אותו ידנית"
            )
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ProfileOSError("הקובץ גדול מדי — הורידו אותו ידנית")
        return body, headers


def in_words(exc: BaseException) -> str:
    """Why a check failed, the way the shop would say it."""
    if isinstance(exc, ProfileOSError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "הדף לא נמצא באתר הספק (⁦404⁩) — אולי הכתובת השתנתה"
        if exc.code in (401, 403):
            return f"האתר לא מאפשר גישה (⁦{exc.code}⁩)"
        return f"האתר החזיר שגיאה ⁦{exc.code}⁩"
    if isinstance(exc, urllib.error.URLError):
        return "אין חיבור לאתר הספק — נבדוק שוב מאוחר יותר"
    if isinstance(exc, TimeoutError):
        return "האתר לא ענה בזמן — נבדוק שוב מאוחר יותר"
    if isinstance(exc, PermissionError):
        return "אין הרשאה לקרוא את התיקייה"
    if isinstance(exc, FileNotFoundError):
        return "התיקייה לא נמצאה — אולי הכונן לא מחובר"
    if isinstance(exc, OSError):
        return f"התיקייה לא נקראה: {exc.strerror or exc}"
    return str(exc) or exc.__class__.__name__


_HREF = re.compile(r"""href\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s>]+))""", re.IGNORECASE)


def catalogue_links(page_url: str, html: str) -> list[str]:
    """Every link on a page that points at a catalogue-type file."""
    found: list[str] = []
    for match in _HREF.finditer(html):
        href = unescape(next(group for group in match.groups() if group)).strip()
        if href.startswith(("mailto:", "javascript:", "#", "tel:")):
            continue
        address = urljoin(page_url, href)
        parsed = urlparse(address)
        if parsed.scheme not in ("http", "https"):
            continue
        if Path(unquote(parsed.path)).suffix.lower() in CATALOGUE_SUFFIXES:
            address = address.split("#", 1)[0]
            if address not in found:
                found.append(address)
    return found


def _signature(headers: dict[str, str]) -> str:
    """What a server says about a file, enough to see that it changed."""
    parts = [
        headers.get("etag", ""),
        headers.get("last-modified", ""),
        headers.get("content-length", ""),
    ]
    return "|".join(parts) if any(parts) else ""


def link_name(address: str) -> str:
    return unquote(Path(urlparse(address).path).name) or address


# --------------------------------------------------------------------------- #
# Checking
# --------------------------------------------------------------------------- #

@dataclass
class CheckResult:
    """What one check of one source saw. Pure: nothing is written yet."""

    source_id: str
    known: dict[str, str] = field(default_factory=dict)
    #: (name, where, change)
    found: list[tuple[str, str, str]] = field(default_factory=list)
    error: str = ""
    baseline: bool = False
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return f"{path.stat().st_size}:{digest.hexdigest()}"


def check_folder(source: WatchedSource) -> CheckResult:
    result = CheckResult(source.source_id, baseline=not source.known and not source.last_checked)
    root = Path(source.location)
    try:
        if not root.is_dir():
            raise FileNotFoundError(str(root))
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in CATALOGUE_SUFFIXES or not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue  # our own trash and hidden folders
            name = str(path.relative_to(root))
            stat = path.stat()
            before = source.known.get(name, "")
            quick = f"{stat.st_size}:{stat.st_mtime_ns}"
            # The cheap fingerprint first; the content only when it moved,
            # so a folder of four hundred drawings is not read every hour.
            if before.startswith(quick + "|"):
                result.known[name] = before
                continue
            content = _file_fingerprint(path)
            result.known[name] = f"{quick}|{content}"
            if result.baseline:
                continue
            if not before:
                result.found.append((name, str(path), Change.NEW))
            elif before.split("|", 1)[-1] != content:
                result.found.append((name, str(path), Change.CHANGED))
    except Exception as exc:  # noqa: BLE001 - shown on the source, not raised
        result.error = in_words(exc)
        result.known = dict(source.known)
    return result


def check_web(source: WatchedSource, fetch: Fetcher | None = None) -> CheckResult:
    fetch = fetch or http_fetch
    result = CheckResult(source.source_id, baseline=not source.known and not source.last_checked)
    try:
        body, headers = fetch(source.location, "GET", MAX_PAGE_BYTES)
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", headers.get("content-type", ""))
        if match:
            charset = match.group(1)
        html = body.decode(charset, errors="replace")
        links = catalogue_links(source.location, html)
        # Files new to us are asked about first, then the known ones in turn,
        # a different slice each day, so a page of three hundred files is
        # covered within the week without three hundred requests at once.
        fresh = [a for a in links if a not in source.known]
        seen = [a for a in links if a in source.known]
        if seen:
            start = (date.today().toordinal() * MAX_HEAD_REQUESTS) % len(seen)
            seen = seen[start:] + seen[:start]
        asked = 0
        for address in fresh + seen:
            before = source.known.get(address)
            signature = before or ""
            if asked < MAX_HEAD_REQUESTS:
                asked += 1
                try:
                    _, head = fetch(address, "HEAD", 0)
                    signature = _signature(head)
                except Exception as exc:  # noqa: BLE001 - one file, not the page
                    _log.info("HEAD %s failed: %s", address, exc)
                    signature = before or ""
            result.known[address] = signature
            if result.baseline:
                continue
            if before is None:
                result.found.append((link_name(address), address, Change.NEW))
            elif before and signature and before != signature:
                result.found.append((link_name(address), address, Change.CHANGED))
        # A link that left the page is remembered, so it is not "new" again
        # the day it comes back with the same file behind it.
        for address, signature in source.known.items():
            result.known.setdefault(address, signature)
    except Exception as exc:  # noqa: BLE001 - shown on the source, not raised
        result.error = in_words(exc)
        result.known = dict(source.known)
    return result


def check_source(source: WatchedSource, fetch: Fetcher | None = None) -> CheckResult:
    if source.kind == Kind.FOLDER:
        return check_folder(source)
    return check_web(source, fetch)


@dataclass
class CheckSummary:
    checked: int = 0
    new: list[Finding] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    baselines: list[tuple[str, int]] = field(default_factory=list)

    def describe(self) -> str:
        if not self.checked:
            return "אין מקורות לבדוק"
        parts = [f"נבדקו ⁦{self.checked}⁩ מקורות"]
        if self.new:
            parts.append(f"⁦{len(self.new)}⁩ קבצים חדשים או מעודכנים")
        else:
            parts.append("אין קבצים חדשים")
        for title, count in self.baselines:
            parts.append(f"{title}: נרשמו ⁦{count}⁩ קבצים קיימים")
        for title, error in self.errors:
            parts.append(f"{title}: {error}")
        return " · ".join(parts)


def apply_results(results: Iterable[CheckResult], *, watch_path: Path,
                  findings_path: Path) -> CheckSummary:
    """Write what the checks saw, re-reading both books first.

    The checks may have taken a minute on a slow line; meanwhile somebody may
    have removed a source or dealt with a finding. So the books are read fresh
    here, a source removed meanwhile stays removed, and only new findings are
    added — an existing one is never touched by a check.
    """
    watch = WatchBook(watch_path).load()
    findings = FindingBook(findings_path).load()
    summary = CheckSummary()
    open_where = {(f.source_id, f.where, f.change) for f in findings.open_findings()}
    for result in results:
        source = watch.get(result.source_id)
        if source is None:
            continue
        summary.checked += 1
        source.last_checked = result.checked_at
        source.last_error = result.error
        source.known = result.known
        if result.error:
            summary.errors.append((source.title, result.error))
            continue
        if result.baseline:
            summary.baselines.append((source.title, len(result.known)))
        for name, where, change in result.found:
            if (source.source_id, where, change) in open_where:
                continue
            finding = Finding(
                finding_id=f"F-{uuid4().hex[:10].upper()}",
                source_id=source.source_id,
                name=name,
                where=where,
                change=change,
                supplier=source.supplier or source.title,
            )
            findings._items[finding.finding_id] = finding
            summary.new.append(finding)
    watch.save()
    if summary.new:
        findings.save()
    return summary


def check_all(*, fetch: Fetcher | None = None, only_due: bool = False,
              watch_path: Path | None = None, findings_path: Path | None = None,
              now: datetime | None = None) -> CheckSummary:
    """Check every watched source (or only those due) and record the results."""
    watch_path = watch_path or default_watch_path()
    findings_path = findings_path or default_findings_path()
    sources = [s for s in WatchBook(watch_path).load() if not only_due or s.due(now)]
    results = [check_source(source, fetch) for source in sources]
    if not results:
        return CheckSummary()
    return apply_results(results, watch_path=watch_path, findings_path=findings_path)


def download(finding: Finding, *, fetch: Fetcher | None = None,
             folder: Path | None = None) -> Path:
    """Save a file a supplier published into the shop's catalogue folder.

    A file of the same name already there is never overwritten: the new one
    is saved beside it with the date, because the old edition is what the
    shop's current figures were read from.
    """
    if not finding.is_web:
        return Path(finding.where)
    fetch = fetch or http_fetch
    body, _ = fetch(finding.where, "GET", MAX_FILE_BYTES)
    if not body:
        raise ProfileOSError("הספק החזיר קובץ ריק")
    supplier = re.sub(r'[\\/:*?"<>|]+', "-", finding.supplier or "ספק").strip() or "ספק"
    target_dir = (folder or downloads_dir()) / supplier
    target_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]+', "-", finding.name) or "catalogue"
    target = target_dir / name
    if target.exists():
        if target.read_bytes() == body:
            return target
        stem, suffix = target.stem, target.suffix
        target = target_dir / f"{stem} {date.today():%Y-%m-%d}{suffix}"
        serial = 2
        while target.exists():
            target = target_dir / f"{stem} {date.today():%Y-%m-%d} ({serial}){suffix}"
            serial += 1
    atomic_write_bytes(target, body)
    return target


#: A drawing pack is a few megabytes; refuse a zip that says it is much more.
MAX_UNPACKED_BYTES = 512 * 1024 * 1024


def unpack(archive: Path, folder: Path | None = None) -> Path:
    """Open a supplier's ZIP of drawings into a folder of its own, beside it.

    Every name in the archive is checked before anything is written: one
    that climbs out of the folder (``../``) or is absolute is refused, and so
    is an archive whose contents add up to more than a drawing pack could.
    An existing folder of the same name is left alone; the pack goes beside it.
    """
    import zipfile

    archive = Path(archive)
    target = (folder or archive.parent) / archive.stem
    serial = 2
    while target.exists():
        target = (folder or archive.parent) / f"{archive.stem} ({serial})"
        serial += 1
    try:
        with zipfile.ZipFile(archive) as pack:
            members = [info for info in pack.infolist() if not info.is_dir()]
            total = sum(info.file_size for info in members)
            if total > MAX_UNPACKED_BYTES:
                raise ProfileOSError("הקובץ הדחוס גדול מדי לחבילת שרטוטים — פתחו אותו ידנית")
            from pathlib import PurePosixPath, PureWindowsPath

            for info in members:
                posix, windows = PurePosixPath(info.filename), PureWindowsPath(info.filename)
                if (posix.is_absolute() or windows.is_absolute() or windows.drive
                        or ".." in posix.parts or ".." in windows.parts):
                    raise ProfileOSError(f"שם לא תקין בתוך הקובץ הדחוס: {info.filename}")
            for info in members:
                destination = target / Path(info.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(destination, pack.read(info))
    except zipfile.BadZipFile as exc:
        raise ProfileOSError("הקובץ הדחוס פגום או שלא הורד עד הסוף") from exc
    return target


__all__ = [
    "CATALOGUE_SUFFIXES",
    "Change",
    "CheckResult",
    "CheckSummary",
    "Finding",
    "FindingBook",
    "Kind",
    "WatchBook",
    "WatchedSource",
    "apply_results",
    "catalogue_links",
    "check_all",
    "check_folder",
    "check_source",
    "check_web",
    "default_findings",
    "default_findings_path",
    "default_watch",
    "default_watch_path",
    "download",
    "downloads_dir",
    "http_fetch",
    "in_words",
    "link_name",
    "normalise_address",
    "unpack",
]
