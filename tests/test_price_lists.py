"""Supplier price lists: read from Excel, kept by edition, put to work."""

from __future__ import annotations

import os
import zipfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from profileos.core.errors import ProfileOSError
from profileos.quoting.pricelists import (
    ListState,
    PriceListBook,
    PriceRow,
    compare,
    cost_change,
    impact,
    rows_from_file,
    unit_key,
)


def rows(prices: dict[str, float], unit: str = "מטר") -> list[PriceRow]:
    return [PriceRow(code, price, unit, f"פרופיל {code}") for code, price in prices.items()]


@pytest.fixture
def book(tmp_path):
    return PriceListBook(tmp_path / "lists.json").load()


def accepted(book, prices, *, supplier="קליל", valid_from="", unit="מטר"):
    edition = book.record(rows(prices, unit), supplier=supplier, source="מחירון.xlsx",
                          valid_from=valid_from)
    book.accept(edition.edition_id, by="דאדי")
    return edition


def write_xlsx(path: Path, table: list[list[object]]) -> Path:
    """A real .xlsx, as Excel writes one: shared strings, numbers, a sheet."""
    strings: list[str] = []

    def cell(ref: str, value: object) -> str:
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        if value not in strings:
            strings.append(str(value))
        return f'<c r="{ref}" t="s"><v>{strings.index(str(value))}</v></c>'

    body = []
    for r, row in enumerate(table, start=1):
        cells = "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row) if v != "")
        body.append(f'<row r="{r}">{cells}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/workbook.xml",
                      f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                      f'<sheet name="מחירון" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels",
                      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                      'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                      '</Relationships>')
        book.writestr("xl/sharedStrings.xml",
                      f'<sst xmlns="{main}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        book.writestr("xl/worksheets/sheet1.xml",
                      f'<worksheet xmlns="{main}"><sheetData>{"".join(body)}</sheetData></worksheet>')
    return path


class TestReadingTheFile:
    def test_an_excel_price_list_with_a_title_above_the_header(self, tmp_path):
        path = write_xlsx(tmp_path / "klil.xlsx", [
            ["מחירון קליל — מרץ 2026"],
            [],
            ['מק"ט', "תיאור", "מחיר", "יחידה"],
            ["4301", "משקוף", 42.5, "מטר"],
            ["4302", "כנף", 38.9, "מטר"],
            ["4303", "סרגל", "", "מטר"],
        ])
        found, skipped, matched = rows_from_file(path)
        assert [(r.code, r.price, r.unit) for r in found] == [("4301", 42.5, "מטר"), ("4302", 38.9, "מטר")]
        assert skipped and "4303" in skipped[0]
        assert matched["price"] == "מחיר"

    def test_an_old_excel_file_is_explained(self, tmp_path):
        path = tmp_path / "old.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0 old excel")
        with pytest.raises(ProfileOSError, match="xlsx"):
            rows_from_file(path)

    def test_a_broken_excel_file(self, tmp_path):
        path = tmp_path / "cut.xlsx"
        path.write_bytes(b"PK\x03\x04 cut off")
        with pytest.raises(ProfileOSError):
            rows_from_file(path)

    def test_units_however_they_are_written(self):
        assert unit_key("מטר") == unit_key("מ'") == unit_key("m") == "m"
        assert unit_key('ק"ג') == unit_key("ק״ג") == "kg"
        assert unit_key("יח'") == unit_key("pc") == "pc"


class TestEditions:
    def test_the_first_list_and_what_changed_in_the_next(self, book):
        first = accepted(book, {"4301": 42.5, "4302": 38.9, "4303": 12.0})
        assert compare(None, first).first
        new = book.record(rows({"4301": 44.0, "4302": 37.0, "4304": 50.0}),
                          supplier="קליל", source="מחירון אפריל.xlsx")
        diff = book.diff(new)
        assert [c.code for c in diff.up] == ["4301"] and [c.code for c in diff.down] == ["4302"]
        assert [r.code for r in diff.added] == ["4304"] and [r.code for r in diff.missing] == ["4303"]
        assert "התייקרו" in diff.summary() and "ממוצע" in diff.summary()
        assert "היה ⁦42.50 ₪⁩, עכשיו ⁦44.00 ₪⁩" in diff.up[0].describe()

    def test_nothing_changes_until_it_is_accepted(self, book):
        from profileos.quoting.suppliers import find_price

        accepted(book, {"4301": 42.5})
        assert find_price("4301", 1.0, unit="m")[1] == pytest.approx(42.5)
        pending = book.record(rows({"4301": 50.0}), supplier="קליל", source="חדש")
        assert find_price("4301", 1.0, unit="m")[1] == pytest.approx(42.5)
        book.accept(pending.edition_id)
        assert find_price("4301", 1.0, unit="m")[1] == pytest.approx(50.0)

    def test_a_future_list_takes_effect_on_its_day(self, book):
        from profileos.quoting.suppliers import find_price

        accepted(book, {"4301": 42.5})
        later = (date.today() + timedelta(days=10)).isoformat()
        accepted(book, {"4301": 50.0}, valid_from=later)
        assert find_price("4301", 1.0, unit="m")[1] == pytest.approx(42.5)
        assert book.upcoming("קליל").valid_from == later
        assert sorted(book.state_text(e) for e in book) == ["בתוקף", "ייכנס לתוקף"]
        assert book.current("קליל", date.today() + timedelta(days=11)).prices["4301"].price == 50.0

    def test_a_unit_change_is_not_compared_as_a_price(self, book):
        accepted(book, {"4301": 42.5})
        new = book.record(rows({"4301": 30.0}, unit='ק"ג'), supplier="קליל", source="לפי משקל")
        diff = book.diff(new)
        assert diff.unit_changed == ["4301"] and not diff.changed

    def test_supplier_and_source_are_required(self, book):
        with pytest.raises(ProfileOSError):
            book.record(rows({"1": 1.0}), supplier="", source="x")
        with pytest.raises(ProfileOSError):
            book.record(rows({"1": 1.0}), supplier="קליל", source=" ")
        with pytest.raises(ProfileOSError):
            book.record([], supplier="קליל", source="x")
        with pytest.raises(ProfileOSError):
            book.record(rows({"1": 1.0}), supplier="קליל", source="x", valid_from="31/02")

    def test_kept_and_decided_once(self, book, tmp_path):
        edition = book.record(rows({"4301": 42.5}), supplier="קליל", source="x")
        PriceListBook(tmp_path / "lists.json").load().reject(edition.edition_id, by="א")
        with pytest.raises(ProfileOSError):
            book.accept(edition.edition_id)
        kept = PriceListBook(tmp_path / "lists.json").load().get(edition.edition_id)
        assert kept.state == ListState.REJECTED and kept.prices["4301"].price == 42.5

    def test_a_damaged_file_is_never_written_over(self, tmp_path):
        path = tmp_path / "lists.json"
        path.write_text("{oops", encoding="utf-8")
        damaged = PriceListBook(path).load()
        with pytest.raises(Exception):
            damaged.record(rows({"1": 1.0}), supplier="קליל", source="x")
        assert path.read_text(encoding="utf-8") == "{oops"


class TestPricingByTheRightUnit:
    def test_a_price_per_kilo_is_never_multiplied_by_metres(self, book):
        from profileos.quoting.pricelists import apply_price_lists
        from profileos.quoting.suppliers import find_price

        accepted(book, {"4301": 30.0}, unit='ק"ג')
        assert find_price("4301", 10.0, unit="m") is None   # unpriced, not wrong

    def test_per_kilo_becomes_per_metre_with_the_catalogue_weight(self, book, monkeypatch):
        from profileos.quoting import pricelists
        from profileos.quoting.suppliers import find_price

        monkeypatch.setattr(pricelists, "catalogue_weights",
                            lambda: (lambda code: 1.4 if code == "4301" else None))
        accepted(book, {"4301": 30.0}, unit='ק"ג')
        supplier, total = find_price("4301", 10.0, unit="m")
        assert total == pytest.approx(10.0 * 1.4 * 30.0)
        assert supplier.entry("4301").metadata["price_per_kg"] == 30.0


class TestWhatItDoesToTheJobs:
    def line(self, code, quantity, unit="m"):
        return SimpleNamespace(code=code, quantity=quantity, unit=SimpleNamespace(value=unit))

    def test_a_job_costs_more_on_the_new_list(self, book):
        old = accepted(book, {"4301": 40.0, "4302": 30.0})
        new = book.record(rows({"4301": 44.0, "4302": 30.0}), supplier="קליל", source="חדש")
        job = SimpleNamespace(job_id="J-1", name="וילה", quote_total=10000.0)
        change = cost_change(job, old, new, materials=[self.line("4301", 50), self.line("4302", 20)])
        assert change.delta == pytest.approx(200.0) and change.lines == 1
        assert "⁦200 ₪⁩ יותר" in change.describe() and "+2.0%" in change.describe()

    def test_lines_in_another_unit_are_left_out_and_counted(self, book):
        old = accepted(book, {"4301": 30.0}, unit='ק"ג')
        new = book.record(rows({"4301": 33.0}, unit='ק"ג'), supplier="קליל", source="חדש")
        job = SimpleNamespace(job_id="J-1", name="וילה", quote_total=0.0)
        change = cost_change(job, old, new, materials=[self.line("4301", 50)])
        assert change.delta == 0 and change.unmatched_units == 1
        weighed = cost_change(job, old, new, materials=[self.line("4301", 50)],
                              mass_per_metre=lambda code: 1.4)
        assert weighed.delta == pytest.approx(50 * 1.4 * 3.0)

    def test_quotes_out_and_jobs_to_order(self, book, monkeypatch):
        from profileos.quoting import pricelists

        accepted(book, {"4301": 40.0})
        later = (date.today() + timedelta(days=14)).isoformat()
        new = book.record(rows({"4301": 44.0}), supplier="קליל", source="חדש", valid_from=later)
        monkeypatch.setattr(pricelists, "job_materials", lambda job: [self.line("4301", 100)])

        def job(job_id, status, quoted_on=""):
            return SimpleNamespace(job_id=job_id, name=job_id, quote_total=20000.0,
                                   status=SimpleNamespace(value=status), quoted_on=quoted_on)

        jobs = [job("J-1", "quoted", "2020-01-01"), job("J-2", "won"),
                job("J-3", "in_production"), job("J-4", "enquiry")]
        book.accept(new.edition_id)
        effect = impact(book, book.get(new.edition_id), jobs, mass_per_metre=lambda c: None)
        assert [c.job.job_id for c in effect.quoted] == ["J-1"]
        assert [c.job.job_id for c in effect.to_order] == ["J-2"]
        assert effect.buy_before == later
        # Accepted today but in force in two weeks: a quotation made tomorrow
        # is still priced on the old list, and loses margin like the rest.
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        effect = impact(book, book.get(new.edition_id), [job("J-5", "quoted", tomorrow)],
                        mass_per_metre=lambda c: None)
        assert [c.job.job_id for c in effect.quoted] == ["J-5"]
        # Made once the new list is in force: priced on it.
        after = (date.today() + timedelta(days=15)).isoformat()
        effect = impact(book, book.get(new.edition_id), [job("J-6", "quoted", after)],
                        mass_per_metre=lambda c: None)
        assert effect.quoted == []

    def test_a_quotation_two_lists_back_counts_both_increases(self, book, monkeypatch):
        from profileos.quoting import pricelists

        monkeypatch.setattr(pricelists, "job_materials", lambda job: [self.line("4301", 100)])
        accepted(book, {"4301": 40.0}, valid_from="2025-01-01")
        accepted(book, {"4301": 42.0}, valid_from="2025-06-01")
        latest = accepted(book, {"4301": 45.0}, valid_from="2025-12-01")
        old_quote = SimpleNamespace(job_id="J-1", name="x", quote_total=0.0,
                                    status=SimpleNamespace(value="quoted"), quoted_on="2025-03-01")
        [change] = impact(book, book.get(latest.edition_id), [old_quote],
                          mass_per_metre=lambda c: None).quoted
        assert change.delta == pytest.approx(500.0)   # 40 → 45, not 42 → 45

    def test_the_shops_discount_is_what_it_pays(self, book):
        old = book.record(rows({"4301": 40.0}), supplier="קליל", source="א", discount_pct=40)
        book.accept(old.edition_id)
        new = book.record(rows({"4301": 44.0}), supplier="קליל", source="ב")
        assert new.discount_pct == 40          # agreed once, carried over
        job = SimpleNamespace(job_id="J-1", name="x", quote_total=0.0)
        change = cost_change(job, old, new, materials=[self.line("4301", 100)])
        assert change.delta == pytest.approx(100 * 4.0 * 0.6)
        better = book.record(rows({"4301": 44.0}), supplier="קליל", source="ג", discount_pct=45)
        assert "התנאים היו" in book.diff(better).summary()

    def test_a_percentage_letter_is_an_edition(self, book):
        accepted(book, {"4301": 40.0, "4302": 10.0})
        letter = book.record_uplift("קליל", 4.0, source="מכתב מקליל 20/11",
                                    valid_from=(date.today() + timedelta(days=30)).isoformat())
        assert letter.prices["4301"].price == pytest.approx(41.6)
        assert letter.state == ListState.PENDING and letter.source == "מכתב מקליל 20/11"
        with pytest.raises(ProfileOSError):
            book.record_uplift("ספק אחר", 4.0, source="x")
        with pytest.raises(ProfileOSError):
            book.record_uplift("קליל", 0, source="x")

    def test_prices_per_finish(self, book, tmp_path):
        from profileos.quoting.suppliers import find_price

        path = tmp_path / "f.csv"
        path.write_text('מק"ט;גוון;מחיר;יחידה\n4301;טבעי;40;מטר\n4301;לבן;46;מטר\n4302;טבעי;9;מטר\n',
                        encoding="utf-8")
        found, skipped, matched = rows_from_file(path)
        assert len(found) == 3 and not skipped and "finish" in matched
        edition = book.record(found, supplier="קליל", source="f.csv")
        assert len(edition.prices) == 3
        assert any("לפי גוון" in w for w in edition.warnings)
        book.accept(edition.edition_id)
        # Two finishes and nothing says which: unpriced, never the wrong one.
        assert find_price("4301", 1.0, unit="m") is None
        assert find_price("4302", 1.0, unit="m")[1] == pytest.approx(9.0)

    def test_the_same_code_twice_at_two_prices_is_named(self, book):
        edition = book.record([PriceRow("4301", 40.0, "מטר"), PriceRow("4301", 46.0, "מטר")],
                              supplier="קליל", source="x")
        assert any("במחירים שונים" in w for w in edition.warnings)

    def test_small_changes_are_not_worth_a_warning(self):
        from profileos.quoting.pricelists import JobCostChange

        job = SimpleNamespace(job_id="J", name="x", quote_total=50000.0)
        assert not JobCostChange(job, 120.0, 1).worth_saying
        assert JobCostChange(job, 600.0, 1).worth_saying


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


def test_a_quoted_job_on_old_prices_is_on_the_morning_list(shop, monkeypatch):
    from profileos.insights import scan
    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.quoting import pricelists
    from profileos.quoting.pricelists import default_price_lists

    store = default_store()
    job = JobFile(job_id="J-2026-0001", name="וילה", status=JobStatus.QUOTED)
    job.quote_total = 30000.0
    job.quoted_on = "2020-01-01"
    store.save(job)
    won = JobFile(job_id="J-2026-0002", name="בניין", status=JobStatus.WON)
    store.save(won)
    monkeypatch.setattr(pricelists, "job_materials",
                        lambda job: [SimpleNamespace(code="4301", quantity=100.0,
                                                     unit=SimpleNamespace(value="m"))])
    book = default_price_lists()
    accepted(book, {"4301": 40.0})
    later = (date.today() + timedelta(days=10)).isoformat()
    accepted(book, {"4301": 46.0}, valid_from=later)
    waiting = book.record(rows({"4301": 47.0}), supplier="קליל", source="עוד אחד")

    found = scan()
    titles = [i.title for i in found]
    assert any("ממתין לבדיקה" in t for t in titles)
    quoted = next(i for i in found if "תומחרה לפני ההתייקרות" in i.title)
    assert "⁦600 ₪⁩" in quoted.detail and "J-2026-0001" in quoted.detail
    order = next(i for i in found if i.title.startswith("להזמין מ"))
    assert "⁦600 ₪⁩" in order.detail and "J-2026-0002" in order.detail
    assert waiting.edition_id


def test_the_command_line_import_keeps_an_edition_and_prices_from_it(shop):
    from profileos.migration import import_prices, plan_prices
    from profileos.quoting.pricelists import default_price_lists
    from profileos.quoting.suppliers import find_price

    path = shop / "p.csv"
    path.write_text('מק"ט;מחיר;יחידה;ספק\nKL-1;42.5;מטר;קליל\n', encoding="utf-8")
    import_prices(plan_prices(path))
    [edition] = list(default_price_lists())
    assert edition.state == ListState.ACCEPTED and edition.supplier == "קליל"
    assert find_price("KL-1", 2.0, unit="m")[1] == pytest.approx(85.0)


def test_the_price_lists_tab(shop, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

    from profileos.quoting.pricelists import default_price_lists
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    page = window.go_to_page("Catalogue")
    path = write_xlsx(tmp_path / "klil.xlsx", [
        ['מק"ט', "תיאור", "מחיר", "יחידה"], ["4301", "משקוף", 42.5, "מטר"],
    ])
    monkeypatch.setattr(dialogs.PriceListDialog, "exec", lambda self: (
        self.supplier.setCurrentText("קליל"), QDialog.DialogCode.Accepted)[-1])
    page.import_price_list(path)
    assert page.tabs.currentWidget() is page.prices_page
    assert page.price_lists_table.rowCount() == 1
    assert "מחירון ראשון" in page.price_list_detail.toPlainText()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("דאדי", True)))
    page._decide_price_list(True)
    [edition] = list(default_price_lists())
    assert edition.state == ListState.ACCEPTED and edition.decided_by == "דאדי"

    # The supplier's "+4%" letter, without a new file.
    monkeypatch.setattr(dialogs.PriceLetterDialog, "exec", lambda self: (
        self.pct.setValue(4.0), self.source.setText("מכתב מקליל"),
        QDialog.DialogCode.Accepted)[-1])
    page.record_price_letter()
    letter = default_price_lists().pending()[0]
    assert letter.prices["4301"].price == pytest.approx(44.2)
    assert "התייקרו" in page.price_list_detail.toPlainText()

    # A file with no unit column: the unit is asked for, never assumed.
    bare = tmp_path / "bare.csv"
    bare.write_text('מק"ט;מחיר\n4301;30\n', encoding="utf-8")

    def answer(self):
        assert self.unit is not None and not self.ok_button.isEnabled()
        self.supplier.setCurrentText("אקסטל")
        self.unit.setCurrentIndex(self.unit.findData("kg"))
        assert self.ok_button.isEnabled()
        self.discount.setValue(35.0)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.PriceListDialog, "exec", answer)
    page.import_price_list(bare)
    extal = next(e for e in default_price_lists() if e.supplier == "אקסטל")
    assert extal.prices["4301"].unit == "kg" and extal.discount_pct == 35.0
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()
