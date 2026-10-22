"""Supplier catalogue updates: editions, what changed, where suppliers publish."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from profileos.catalogue.editions import (
    EditionBook,
    EditionState,
    ProfileSnapshot,
    compare_profile,
    confirmation_hits,
    open_jobs_on,
    stale_confirmation,
)
from profileos.catalogue.model import CatalogueEntry, IngestionReport
from profileos.catalogue.watch import (
    Change,
    FindingBook,
    Kind,
    WatchBook,
    apply_results,
    catalogue_links,
    check_all,
    check_folder,
    check_web,
    download,
    normalise_address,
    unpack,
)
from profileos.core.errors import ProfileOSError


def report(rows: dict[str, dict[str, float]], drawings: dict[str, str] | None = None) -> IngestionReport:
    """An ingestion run with the supplier's printed figures per article."""
    out = IngestionReport(source="test")
    for code, values in rows.items():
        out.entries.append(CatalogueEntry(
            profile_id=code, system_series="4300", name=f"פרופיל {code}",
            published=dict(values),
            dxf_path=(drawings or {}).get(code),
        ))
    return out


BASE = {
    "4301": {"mass_per_metre": 1.386, "width": 45.0, "height": 70.0},
    "4302": {"mass_per_metre": 0.954, "width": 30.0, "height": 70.0},
    "4303": {"mass_per_metre": 0.412, "width": 12.0, "height": 22.0},
}


@pytest.fixture
def book(tmp_path):
    return EditionBook(tmp_path / "editions.json").load()


def first_accepted(book: EditionBook) -> None:
    edition = book.record(report(BASE), source="קטלוג 2025", system_id="klil-4300",
                          complete=True)
    book.accept(edition.edition_id, by="דאדי")


class TestEditions:
    def test_the_first_edition_is_the_whole_library(self, book):
        edition = book.record(report(BASE), source="קטלוג 2025", system_id="klil-4300")
        diff = book.diff(edition)
        assert diff.first and len(diff.added) == 3
        assert edition.state == EditionState.PENDING
        assert "ספרייה ראשונה" in diff.summary()

    def test_a_source_and_a_series_are_required(self, book):
        with pytest.raises(ProfileOSError):
            book.record(report(BASE), source="  ", system_id="klil-4300")
        with pytest.raises(ProfileOSError):
            book.record(report(BASE), source="קטלוג")
        with pytest.raises(ProfileOSError):
            book.record(report({}), source="קטלוג", system_id="klil-4300")

    def test_a_heavier_profile_a_new_one_and_a_dropped_one(self, book):
        first_accepted(book)
        changed = dict(BASE)
        changed["4301"] = {"mass_per_metre": 1.412, "width": 45.0, "height": 70.0}
        changed["4304"] = {"mass_per_metre": 0.5, "width": 20.0, "height": 20.0}
        del changed["4303"]
        edition = book.record(report(changed), source="קטלוג 2026", system_id="klil-4300",
                              complete=True)
        diff = book.diff(edition)
        assert [c.profile_id for c in diff.changed] == ["4301"]
        assert diff.changed[0].figures[0].name == "mass_per_metre"
        assert "1.386" in diff.changed[0].describe() and "1.412" in diff.changed[0].describe()
        assert [s.profile_id for s in diff.added] == ["4304"]
        assert [s.profile_id for s in diff.removed] == ["4303"]
        assert diff.unchanged == 1
        assert "חדשים" in diff.summary() and "השתנו" in diff.summary() and "הוסרו" in diff.summary()

    def test_rounding_is_not_a_change(self, book):
        first_accepted(book)
        nearly = {code: dict(values) for code, values in BASE.items()}
        nearly["4301"]["mass_per_metre"] = 1.3865   # within half a percent
        nearly["4301"]["width"] = 45.04              # within a twentieth of a mm
        edition = book.record(report(nearly), source="סריקה חוזרת", system_id="klil-4300",
                              complete=True)
        assert book.diff(edition).empty

    def test_a_partial_pack_does_not_drop_the_rest(self, book):
        first_accepted(book)
        edition = book.record(report({"4301": BASE["4301"]}), source="עדכון 4301",
                              system_id="klil-4300", complete=False)
        diff = book.diff(edition)
        assert diff.removed == [] and diff.partial
        book.accept(edition.edition_id)
        # The library still has all three: a partial edition only overlays.
        assert set(book.library("klil-4300")) == {"4301", "4302", "4303"}

    def test_a_complete_edition_replaces_the_library(self, book):
        first_accepted(book)
        edition = book.record(report({"4301": BASE["4301"]}), source="קטלוג מצומצם",
                              system_id="klil-4300", complete=True)
        book.accept(edition.edition_id)
        assert set(book.library("klil-4300")) == {"4301"}

    def test_codes_are_matched_however_they_are_written(self, book):
        first_accepted(book)
        renamed = {f"K-{code}": values for code, values in BASE.items()}
        edition = book.record(report(renamed), source="קטלוג", system_id="klil-4300",
                              complete=True)
        diff = book.diff(edition)
        # 'K-4301' is not '4301': a different prefix is a different code.
        assert len(diff.added) == 3
        spaced = {f"{code[:2]} {code[2:]}": values for code, values in BASE.items()}
        edition = book.record(report(spaced), source="קטלוג", system_id="klil-4300",
                              complete=True)
        assert book.diff(edition).empty

    def test_a_redrawn_profile_is_noticed(self, tmp_path, book):
        old_dxf = tmp_path / "old.dxf"
        new_dxf = tmp_path / "new.dxf"
        old_dxf.write_text("0\nSECTION\n")
        new_dxf.write_text("0\nSECTION\n999\nchanged\n")
        edition = book.record(report(BASE, {"4301": str(old_dxf)}), source="2025",
                              system_id="klil-4300", complete=True)
        book.accept(edition.edition_id)
        edition = book.record(report(BASE, {"4301": str(new_dxf)}), source="2026",
                              system_id="klil-4300", complete=True)
        diff = book.diff(edition)
        assert [c.profile_id for c in diff.changed] == ["4301"]
        assert diff.changed[0].redrawn and "השרטוט עודכן" in diff.changed[0].describe()

    def test_printed_is_compared_with_printed_only(self):
        printed = ProfileSnapshot("4301", published={"width": 45.0})
        measured = ProfileSnapshot("4301", measured={"width": 47.0})
        assert compare_profile(printed, measured) is None

    def test_a_decision_is_made_once_and_kept(self, book, tmp_path):
        edition = book.record(report(BASE), source="קטלוג", system_id="klil-4300")
        book.reject(edition.edition_id, by="דאדי", note="מהדורה ישנה")
        with pytest.raises(ProfileOSError):
            book.accept(edition.edition_id)
        again = EditionBook(tmp_path / "editions.json").load()
        kept = again.get(edition.edition_id)
        assert kept.state == EditionState.REJECTED and kept.decided_by == "דאדי"
        assert kept.profiles["4301"].published["mass_per_metre"] == 1.386
        # A rejected edition is not part of the library.
        assert again.library("klil-4300") == {}

    def test_two_copies_do_not_erase_each_other(self, tmp_path):
        path = tmp_path / "editions.json"
        first = EditionBook(path).load()
        second = EditionBook(path).load()
        a = first.record(report(BASE), source="א", system_id="klil-4300")
        b = second.record(report(BASE), source="ב", system_id="klil-7300")
        both = EditionBook(path).load()
        assert both.get(a.edition_id) and both.get(b.edition_id)

    def test_a_damaged_file_is_never_written_over(self, tmp_path):
        path = tmp_path / "editions.json"
        path.write_text("{not json", encoding="utf-8")
        damaged = EditionBook(path).load()
        assert damaged.damaged is not None
        with pytest.raises(Exception):
            damaged.record(report(BASE), source="קטלוג", system_id="klil-4300")
        assert path.read_text(encoding="utf-8") == "{not json"


class TestWhoItHurts:
    def job(self, job_id, status, system_id="generic", openings=()):
        schedule = SimpleNamespace(system_id="generic", openings=[
            SimpleNamespace(system_id=s) for s in openings
        ])
        return SimpleNamespace(job_id=job_id, name=job_id,
                               status=SimpleNamespace(value=status),
                               system_id=system_id, schedule=schedule)

    def test_open_jobs_on_the_series(self):
        jobs = [
            self.job("J-1", "won", "klil-4300"),
            self.job("J-2", "installed", "klil-4300"),
            self.job("J-3", "quoted", openings=["klil-4300"]),
            self.job("J-4", "in_production", "klil-7300"),
        ]
        assert [j.job_id for j in open_jobs_on("klil-4300", jobs)] == ["J-1", "J-3"]
        assert open_jobs_on("", jobs) == []

    def test_a_confirmed_article_that_changed(self, book):
        first_accepted(book)
        changed = dict(BASE)
        changed["4301"] = {"mass_per_metre": 1.5, "width": 47.0, "height": 70.0}
        del changed["4302"]
        edition = book.record(report(changed), source="2026", system_id="klil-4300",
                              complete=True)
        confirmation = SimpleNamespace(profiles={"frame": "43-01", "sash": "4302", "bead": "9999"})
        hits = confirmation_hits(book.diff(edition), confirmation)
        described = sorted(hit.describe() for hit in hits)
        assert len(hits) == 2
        assert any("משקוף" in text and "השתנה" in text for text in described)
        assert any("כנף" in text and "הוסר" in text for text in described)

    def test_figures_entered_before_the_new_catalogue_are_stale(self, book):
        confirmation = SimpleNamespace(
            entry_id="klil-4300", profiles={"frame": "4301"},
            entered_on="2020-01-01", entered_at="2020-01-01T09:00:00",
        )
        first_accepted(book)
        changed = dict(BASE)
        changed["4301"] = {"mass_per_metre": 1.5, "width": 45.0, "height": 70.0}
        edition = book.record(report(changed), source="2026", system_id="klil-4300",
                              complete=True)
        assert stale_confirmation(book, "klil-4300", confirmation) == []  # still pending
        book.accept(edition.edition_id)
        hits = stale_confirmation(book, "klil-4300", confirmation)
        assert [hit.article for hit in hits] == ["4301"]
        # Entered again after the decision: no longer stale.
        later = datetime.now() + timedelta(seconds=5)
        confirmation.entered_at = later.isoformat(timespec="seconds")
        confirmation.entered_on = later.date().isoformat()
        assert stale_confirmation(book, "klil-4300", confirmation) == []

    def test_an_old_record_with_only_a_day_is_asked_about(self, book):
        first_accepted(book)
        changed = dict(BASE)
        changed["4301"] = {"mass_per_metre": 1.5, "width": 45.0, "height": 70.0}
        edition = book.record(report(changed), source="2026", system_id="klil-4300",
                              complete=True)
        book.accept(edition.edition_id)
        today = datetime.now().date().isoformat()
        confirmation = SimpleNamespace(profiles={"frame": "4301"}, entered_on=today, entered_at="")
        assert stale_confirmation(book, "klil-4300", confirmation)

    def test_a_confirmation_keeps_its_moment(self):
        from profileos.systems.confirmation import Confirmation

        confirmation = Confirmation(entry_id="klil-4300", source="קטלוג")
        again = Confirmation.from_dict(confirmation.as_dict())
        assert again.entered_at == confirmation.entered_at != ""
        old = Confirmation.from_dict({"entry_id": "x", "entered_on": "2025-01-01"})
        assert old.entered_at == ""


# --------------------------------------------------------------------------- #
# Watching sources
# --------------------------------------------------------------------------- #

class FakeWeb:
    """A supplier's website, as far as the watch can see it."""

    def __init__(self, page: str, files: dict[str, tuple[str, bytes]] | None = None) -> None:
        self.page = page
        self.files = files or {}
        self.requests: list[tuple[str, str]] = []

    def __call__(self, url: str, method: str = "GET", limit: int = 0):
        self.requests.append((method, url))
        if url in self.files:
            signature, body = self.files[url]
            headers = {"etag": signature, "content-length": str(len(body))}
            return (b"" if method == "HEAD" else body), headers
        if method == "GET":
            return self.page.encode("utf-8"), {"content-type": "text/html; charset=utf-8"}
        raise OSError("no such file")


class TestFolders:
    def test_first_look_records_then_new_then_changed(self, tmp_path):
        folder = tmp_path / "supplier"
        folder.mkdir()
        (folder / "old.pdf").write_bytes(b"%PDF old")
        (folder / "notes.docx").write_bytes(b"not a catalogue")
        (folder / ".trash").mkdir()
        (folder / ".trash" / "gone.pdf").write_bytes(b"x")
        watch = WatchBook(tmp_path / "w.json").load()
        source = watch.add(Kind.FOLDER, str(folder), supplier="קליל")
        paths = dict(watch_path=tmp_path / "w.json", findings_path=tmp_path / "f.json")

        summary = check_all(**paths)
        assert summary.new == [] and summary.baselines == [("קליל", 1)]

        (folder / "4300-2026.pdf").write_bytes(b"%PDF new")
        summary = check_all(**paths)
        assert [(f.name, f.change) for f in summary.new] == [("4300-2026.pdf", Change.NEW)]

        (folder / "old.pdf").write_bytes(b"%PDF old, revised")
        summary = check_all(**paths)
        assert [(f.name, f.change) for f in summary.new] == [("old.pdf", Change.CHANGED)]

        # Touched but identical: not a change.
        os.utime(folder / "4300-2026.pdf")
        assert check_all(**paths).new == []
        findings = FindingBook(tmp_path / "f.json").load()
        assert len(findings.open_findings()) == 2
        assert all(f.supplier == "קליל" and f.source_id == source.source_id
                   for f in findings.open_findings())

    def test_a_missing_folder_is_said_in_words(self, tmp_path):
        folder = tmp_path / "usb"
        folder.mkdir()
        watch = WatchBook(tmp_path / "w.json").load()
        watch.add(Kind.FOLDER, str(folder))
        folder.rmdir()
        summary = check_all(watch_path=tmp_path / "w.json", findings_path=tmp_path / "f.json")
        assert summary.errors and "לא נמצאה" in summary.errors[0][1]
        again = WatchBook(tmp_path / "w.json").load()
        assert "לא נמצאה" in next(iter(again)).last_error

    def test_a_folder_that_does_not_exist_cannot_be_added(self, tmp_path):
        with pytest.raises(ProfileOSError):
            WatchBook(tmp_path / "w.json").load().add(Kind.FOLDER, str(tmp_path / "nope"))

    def test_the_same_source_twice_is_refused(self, tmp_path):
        watch = WatchBook(tmp_path / "w.json").load()
        watch.add(Kind.WEB, "klil.example/downloads")
        with pytest.raises(ProfileOSError):
            watch.add(Kind.WEB, "https://klil.example/downloads")

    def test_a_source_removed_during_a_check_stays_removed(self, tmp_path):
        folder = tmp_path / "s"
        folder.mkdir()
        watch = WatchBook(tmp_path / "w.json").load()
        source = watch.add(Kind.FOLDER, str(folder))
        result = check_folder(source)
        watch.remove(source.source_id)
        apply_results([result], watch_path=tmp_path / "w.json", findings_path=tmp_path / "f.json")
        assert len(WatchBook(tmp_path / "w.json").load()) == 0

    def test_a_finding_dealt_with_is_not_reopened_by_the_check(self, tmp_path):
        folder = tmp_path / "s"
        folder.mkdir()
        watch = WatchBook(tmp_path / "w.json").load()
        watch.add(Kind.FOLDER, str(folder))
        paths = dict(watch_path=tmp_path / "w.json", findings_path=tmp_path / "f.json")
        check_all(**paths)
        (folder / "a.dxf").write_text("0\nEOF\n")
        [finding] = check_all(**paths).new
        FindingBook(tmp_path / "f.json").load().handle(finding.finding_id, "נקלט")
        assert check_all(**paths).new == []
        kept = FindingBook(tmp_path / "f.json").load().get(finding.finding_id)
        assert kept.handled_how == "נקלט"


class TestWebPages:
    PAGE = """
        <a href="/files/4300.pdf">קטלוג 4300</a>
        <a href='https://cdn.example/7300%20drawings.zip'>שרטוטים</a>
        <a href="/about">אודות</a> <a href="mailto:x@y">mail</a>
        <a href="/files/4300.pdf#page=2">again</a>
    """

    def test_catalogue_links_only(self):
        links = catalogue_links("https://klil.example/downloads", self.PAGE)
        assert links == [
            "https://klil.example/files/4300.pdf",
            "https://cdn.example/7300%20drawings.zip",
        ]

    def test_new_links_and_a_file_the_server_says_changed(self, tmp_path):
        web = FakeWeb(self.PAGE, {
            "https://klil.example/files/4300.pdf": ("v1", b"%PDF 1"),
            "https://cdn.example/7300%20drawings.zip": ("z1", b"PK"),
        })
        watch = WatchBook(tmp_path / "w.json").load()
        watch.add(Kind.WEB, "https://klil.example/downloads", supplier="קליל")
        paths = dict(watch_path=tmp_path / "w.json", findings_path=tmp_path / "f.json")

        assert check_all(fetch=web, **paths).new == []          # first look

        web.page += '<a href="/files/9000-2026.pdf">חדש</a>'
        web.files["https://klil.example/files/9000-2026.pdf"] = ("n1", b"%PDF new")
        web.files["https://klil.example/files/4300.pdf"] = ("v2", b"%PDF 2")
        found = {(f.name, f.change) for f in check_all(fetch=web, **paths).new}
        assert found == {("9000-2026.pdf", Change.NEW), ("4300.pdf", Change.CHANGED)}

    def test_a_site_that_cannot_be_reached(self, tmp_path):
        import urllib.error

        def offline(url, method="GET", limit=0):
            raise urllib.error.URLError("down")

        watch = WatchBook(tmp_path / "w.json").load()
        watch.add(Kind.WEB, "https://klil.example/downloads")
        summary = check_all(fetch=offline, watch_path=tmp_path / "w.json",
                            findings_path=tmp_path / "f.json")
        assert "אין חיבור" in summary.errors[0][1]

    def test_only_web_addresses(self):
        assert normalise_address("klil.example/x") == "https://klil.example/x"
        for bad in ("", "file:///etc/passwd", "ftp://x/y"):
            with pytest.raises(ProfileOSError):
                normalise_address(bad)

    def test_due_by_kind(self, tmp_path):
        watch = WatchBook(tmp_path / "w.json").load()
        web = watch.add(Kind.WEB, "https://klil.example/d")
        assert web.due()
        web.last_checked = (datetime.now() - timedelta(hours=3)).isoformat()
        assert not web.due()
        web.last_checked = (datetime.now() - timedelta(hours=25)).isoformat()
        assert web.due()


class TestDownloads:
    def finding(self, url):
        from profileos.catalogue.watch import Finding

        return Finding(finding_id="F-1", source_id="S", name=url.rsplit("/", 1)[-1],
                       where=url, supplier="קליל")

    def test_a_download_never_overwrites_the_old_edition(self, tmp_path):
        url = "https://klil.example/files/4300.pdf"
        web = FakeWeb("", {url: ("v1", b"%PDF 2025")})
        first = download(self.finding(url), fetch=web, folder=tmp_path)
        assert first.read_bytes() == b"%PDF 2025" and first.parent.name == "קליל"
        assert download(self.finding(url), fetch=web, folder=tmp_path) == first
        web.files[url] = ("v2", b"%PDF 2026")
        second = download(self.finding(url), fetch=web, folder=tmp_path)
        assert second != first and second.read_bytes() == b"%PDF 2026"
        assert first.read_bytes() == b"%PDF 2025"

    def test_a_drawing_pack_is_unpacked_beside_itself(self, tmp_path):
        archive = tmp_path / "4300.zip"
        with zipfile.ZipFile(archive, "w") as pack:
            pack.writestr("dxf/4301.dxf", "0\nEOF\n")
            pack.writestr("dxf/4302.dxf", "0\nEOF\n")
        folder = unpack(archive)
        assert sorted(p.name for p in folder.rglob("*.dxf")) == ["4301.dxf", "4302.dxf"]
        assert unpack(archive) != folder  # a second time goes beside, not over

    @pytest.mark.parametrize("name", ["../evil.dxf", "/etc/evil.dxf", "..\\evil.dxf", "C:\\evil.dxf"])
    def test_a_pack_that_climbs_out_is_refused(self, tmp_path, name):
        archive = tmp_path / "bad.zip"
        with zipfile.ZipFile(archive, "w") as pack:
            pack.writestr("ok.dxf", "x")
            pack.writestr(name, "x")
        with pytest.raises(ProfileOSError):
            unpack(archive)
        assert not (tmp_path / "evil.dxf").exists()
        assert not any((tmp_path / "bad").rglob("*"))  # nothing half-written

    def test_a_broken_zip(self, tmp_path):
        archive = tmp_path / "cut.zip"
        archive.write_bytes(b"PK\x03\x04 cut off")
        with pytest.raises(ProfileOSError):
            unpack(archive)


# --------------------------------------------------------------------------- #
# The shop sees it
# --------------------------------------------------------------------------- #

@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    data = tmp_path / "data"
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(data))
    reload_settings()
    data.mkdir(parents=True, exist_ok=True)
    yield data
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def test_the_morning_list_names_new_files_and_waiting_editions(shop, tmp_path):
    from profileos.catalogue.editions import default_editions
    from profileos.catalogue.watch import default_watch
    from profileos.insights import scan

    folder = tmp_path / "supplier"
    folder.mkdir()
    default_watch().add(Kind.FOLDER, str(folder), supplier="קליל")
    check_all()
    (folder / "4300-2026.pdf").write_bytes(b"%PDF")
    check_all()

    book = default_editions()
    first_accepted(book)
    changed = dict(BASE)
    changed["4301"] = {"mass_per_metre": 1.5, "width": 45.0, "height": 70.0}
    book.record(report(changed), source="קטלוג 2026", system_id="klil-4300", complete=True)

    found = scan()
    titles = [insight.title for insight in found]
    assert any("קובץ קטלוג חדש" in title for title in titles)
    waiting = next(i for i in found if "ממתינה לבדיקה" in i.title)
    assert "השתנו" in waiting.detail and waiting.page == "Catalogue"


def test_stale_cutting_figures_are_a_danger(shop):
    from profileos.catalogue.editions import default_editions
    from profileos.insights import scan
    from profileos.systems.confirmation import Confirmation, default_confirmations

    confirmations = default_confirmations()
    confirmation = Confirmation(entry_id="klil-4300", source="קטלוג 2025",
                                profiles={"frame": "4301"})
    confirmation.entered_at = "2020-01-01T00:00:00"
    confirmations._entries["klil-4300"] = confirmation
    confirmations.save()

    book = default_editions()
    first_accepted(book)
    changed = dict(BASE)
    changed["4301"] = {"mass_per_metre": 1.5, "width": 45.0, "height": 70.0}
    edition = book.record(report(changed), source="קטלוג 2026", system_id="klil-4300",
                          complete=True)
    book.accept(edition.edition_id)

    stale = [i for i in scan() if "קטלוג ישן" in i.title]
    assert stale and stale[0].severity == "danger"
    assert "משקוף" in stale[0].detail


def test_an_expired_price_list_is_finally_reported(monkeypatch, shop):
    from datetime import date

    from profileos.insights import _paperwork
    from profileos.quoting import suppliers
    from profileos.quoting.suppliers import Supplier

    old = Supplier(id="klil-prices", name="קליל", valid_to=date(2020, 1, 1))
    monkeypatch.setattr(suppliers, "all_suppliers", lambda: [old])
    found = _paperwork({}, date.today())
    assert any("מחירון" in insight.title for insight in found)


def test_the_supplier_updates_tab(shop, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

    from profileos.catalogue.editions import default_editions
    from profileos.catalogue.watch import default_findings, default_watch
    from profileos.ui import background, dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    # The checks run where the test can see them finish.
    monkeypatch.setattr(
        background, "run_in_background",
        lambda work, done: done(*_attempt(work)),
    )

    folder = tmp_path / "supplier"
    folder.mkdir()
    (folder / "drawings.zip").write_bytes(_zip({"4301.dxf": "0\nEOF\n"}))

    window = MainWindow(LIGHT)
    page = window.go_to_page("Catalogue")
    monkeypatch.setattr(dialogs.WatchSourceDialog, "exec", lambda self: (
        self.location.setText(str(folder)), self.supplier.setText("קליל"),
        QDialog.DialogCode.Accepted)[-1])
    page.add_source("folder")
    assert page.sources_table.rowCount() == 1
    assert len(next(iter(default_watch())).known) == 1     # first look only records

    (folder / "4300-2026.zip").write_bytes(_zip({"4302.dxf": "0\nEOF\n"}))
    page.check_sources()
    assert page.findings_table.rowCount() == 1
    assert page.findings_table.item(0, 1).text() == "4300-2026.zip"

    page.findings_table.selectRow(0)
    page.ingest_finding()
    assert page.tabs.currentWidget() is page.ingest_page
    assert page._drawings is not None and (page._drawings / "4302.dxf").is_file()

    # A catalogue was read; keep it as an edition, then accept it.
    page.session.set_catalogue(report(BASE))
    monkeypatch.setattr(dialogs.RecordEditionDialog, "exec", lambda self: (
        self.source.setText("קטלוג קליל 4300 2026"),
        self.system.setCurrentIndex(self.system.findData("klil-4300")),
        QDialog.DialogCode.Accepted)[-1])
    page.record_edition()
    assert page.tabs.currentWidget() is page.updates_page
    [edition] = default_editions().pending()
    assert edition.source == "קטלוג קליל 4300 2026"
    assert not default_findings().open_findings()          # the file was dealt with
    assert page.editions_table.rowCount() == 1
    assert "ספרייה ראשונה" in page.edition_detail.toPlainText()

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("דאדי", True)))
    page.editions_table.selectRow(0)
    page.accept_edition()
    assert default_editions().get(edition.edition_id).state == EditionState.ACCEPTED
    assert page.systems_table.rowCount() > 0
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


def _attempt(work):
    try:
        return work(), None
    except BaseException as exc:  # noqa: BLE001
        return None, exc


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pack:
        for name, text in files.items():
            pack.writestr(name, text)
    return buffer.getvalue()


class TestStockAndTeam:
    def removed_diff(self, book):
        first_accepted(book)
        smaller = {code: values for code, values in BASE.items() if code != "4303"}
        smaller["4301"] = {"mass_per_metre": 1.5, "width": 45.0, "height": 70.0}
        edition = book.record(report(smaller), source="קטלוג 2026", system_id="klil-4300",
                              complete=True)
        return edition, book.diff(edition)

    def test_bars_of_a_dropped_profile_still_in_stock(self, book):
        from datetime import date

        from profileos.catalogue.editions import stock_of_removed
        from profileos.erp.stocktake import CountLine, Status, Stocktake

        _, diff = self.removed_diff(book)
        old = Stocktake(opened=date(2025, 1, 5), status=Status.POSTED,
                        posted_on=date(2025, 1, 6),
                        lines=[CountLine(code="43-03", unit="מוט", counted=30)])
        newer = Stocktake(opened=date(2026, 1, 5), status=Status.POSTED,
                          posted_on=date(2026, 1, 6),
                          lines=[CountLine(code="4303", unit="מוט", counted=14),
                                 CountLine(code="4301", unit="מוט", counted=9)])
        still_open = Stocktake(opened=date(2026, 6, 1),
                               lines=[CountLine(code="4303", counted=0)])
        [left] = stock_of_removed(diff, [newer, still_open, old])
        assert left.profile_id == "4303" and left.quantity == 14
        assert "06/01/2026" in left.describe()

    def test_nothing_counted_nothing_said(self, book):
        from profileos.catalogue.editions import stock_of_removed

        _, diff = self.removed_diff(book)
        assert stock_of_removed(diff, []) == []

    def test_the_team_message(self, book):
        from profileos.sharing import catalogue_change_message

        edition, diff = self.removed_diff(book)
        job = SimpleNamespace(job_id="J-2026-0007", name="וילה בבית אל")
        hit = SimpleNamespace(describe=lambda: "משקוף ⁦4301⁩ השתנה")
        text = catalogue_change_message(edition, diff, brand_name="דאדי בע״מ",
                                        jobs=[job], hits=[hit])
        assert "קטלוג 2026" in text and "יצאו מהקטלוג" in text and "4303" in text
        assert "J-2026-0007" in text and "לקרוא שוב" in text
        assert "היה ⁦1.386⁩, עכשיו ⁦1.5⁩" in text
        assert text.endswith("דאדי בע״מ")
