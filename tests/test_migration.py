"""Bringing a shop's existing records across, from the file they really have."""

from __future__ import annotations

import pytest

from profileos.core.errors import ProfileOSError
from profileos.migration import (
    import_customers,
    import_jobs,
    import_prices,
    match_columns,
    plan_customers,
    plan_jobs,
    plan_prices,
    read_table,
    sniff_encoding,
    to_number,
)

CUSTOMERS = (
    "שם לקוח,איש קשר,טלפון,עיר,ח.פ.\n"
    "משה כהן,משה,02-9973510,בית אל,514123456\n"
    "אבי לוי,,050-1234567,ירושלים,\n"
)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    yield tmp_path
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _write(path, text, encoding="utf-8-sig"):
    path.write_bytes(text.encode(encoding))
    return path


class TestReadingWhatExcelActuallyWrites:
    def test_a_hebrew_windows_export_is_not_utf8_and_is_read_anyway(self, tmp_path):
        """The failure that looks like a successful import until somebody looks."""
        path = _write(tmp_path / "c.csv", CUSTOMERS, "cp1255")
        table = read_table(path)
        assert table.encoding == "cp1255"
        assert "משה כהן" in str(table.rows[0].values())

    def test_a_utf8_export_is_read_as_utf8(self, tmp_path):
        path = _write(tmp_path / "c.csv", CUSTOMERS)
        assert read_table(path).encoding.startswith("utf-8")

    def test_the_encoding_is_chosen_by_which_gives_real_hebrew(self, tmp_path):
        path = _write(tmp_path / "c.csv", CUSTOMERS, "cp1255")
        text, encoding = sniff_encoding(path)
        assert encoding == "cp1255"
        assert "אבי לוי" in text

    def test_a_title_and_a_date_above_the_header_are_skipped(self, tmp_path):
        path = _write(
            tmp_path / "c.csv", "רשימת לקוחות\nהופק 21/08/2026\n\n" + CUSTOMERS
        )
        table = read_table(path, expected=("שם לקוח", "טלפון"))
        assert table.skipped_preamble == 2
        assert len(table) == 2

    def test_a_semicolon_export_is_read_too(self, tmp_path):
        path = _write(tmp_path / "c.csv", CUSTOMERS.replace(",", ";"))
        assert len(read_table(path)) == 2

    def test_an_empty_file_is_refused_by_name(self, tmp_path):
        path = _write(tmp_path / "c.csv", "   \n")
        with pytest.raises(ProfileOSError):
            read_table(path)

    def test_a_missing_file_is_refused_by_name(self, tmp_path):
        with pytest.raises(ProfileOSError):
            read_table(tmp_path / "nothing.csv")


class TestMatchingColumns:
    def test_the_same_field_spelled_three_ways_all_land(self):
        aliases = {"name": ("שם לקוח", "לקוח", "שם")}
        for header in ("שם לקוח", "לקוח", "שם"):
            assert match_columns([header], aliases) == {"name": header}

    def test_an_exact_match_beats_a_longer_one(self):
        aliases = {"name": ("שם",), "contact": ("איש קשר",)}
        found = match_columns(["איש קשר", "שם"], aliases)
        assert found["name"] == "שם"
        assert found["contact"] == "איש קשר"

    def test_one_column_never_feeds_two_fields(self):
        aliases = {"name": ("שם",), "contact": ("שם",)}
        found = match_columns(["שם"], aliases)
        assert len(set(found.values())) == len(found)

    def test_punctuation_and_case_do_not_matter(self):
        aliases = {"tax_id": ("ח.פ.",)}
        assert match_columns(["ח״פ"], aliases) or match_columns(["ח.פ."], aliases)


class TestNumbersAsASpreadsheetWritesThem:
    @pytest.mark.parametrize("text,expected", [
        ("48,500", 48500.0), ("₪ 1,200.50", 1200.5), ("38,90", 3890.0),
        ("12", 12.0), ("", None), ("לא מספר", None), (None, None),
    ])
    def test_read(self, text, expected):
        assert to_number(text) == expected


class TestNothingIsWrittenBeforeItIsShown:
    def test_a_plan_reports_what_it_would_do_and_writes_nothing(self, shop):
        from profileos.projects import default_customers

        path = _write(shop / "c.csv", CUSTOMERS)
        plan = plan_customers(path)
        assert plan.creates == 2
        assert len(default_customers().all()) == 0

    def test_only_the_second_call_writes(self, shop):
        from profileos.projects import default_customers

        plan = plan_customers(_write(shop / "c.csv", CUSTOMERS))
        assert import_customers(plan)["created"] == 2
        assert len(default_customers().all()) == 2

    def test_the_plan_says_which_column_fed_which_field(self, shop):
        plan = plan_customers(_write(shop / "c.csv", CUSTOMERS))
        assert dict(plan.describe_columns())["name"] == "שם לקוח"

    def test_it_says_what_the_file_had_no_column_for(self, shop):
        plan = plan_customers(_write(shop / "c.csv", CUSTOMERS))
        assert "email" in plan.unmatched_fields

    def test_a_file_with_no_name_column_is_refused_with_what_it_did_have(self, shop):
        path = _write(shop / "c.csv", "טלפון,עיר\n02-1,בית אל\n")
        plan = plan_customers(path)
        assert plan.problems
        assert not plan.is_safe
        with pytest.raises(ProfileOSError):
            import_customers(plan)


class TestRowsThatShouldNotBeWritten:
    def test_a_row_with_no_name_is_skipped_not_written_blank(self, shop):
        """A customer called "" is worse than a customer missing."""
        path = _write(shop / "c.csv", CUSTOMERS + ",,03-0000000,,\n")
        plan = plan_customers(path)
        assert plan.skips == 1
        assert any("אין שם" in row.reason for row in plan.of_action("skip"))

    def test_a_name_twice_in_one_file_is_imported_once(self, shop):
        path = _write(shop / "c.csv", CUSTOMERS + "משה כהן,,,,\n")
        plan = plan_customers(path)
        assert plan.creates == 2
        assert any("כפול" in row.reason for row in plan.of_action("skip"))

    def test_a_customer_already_in_the_book_is_updated_not_duplicated(self, shop):
        from profileos.projects import default_customers

        import_customers(plan_customers(_write(shop / "c.csv", CUSTOMERS)))
        plan = plan_customers(_write(shop / "c2.csv", CUSTOMERS))
        assert plan.updates == 2 and plan.creates == 0
        import_customers(plan)
        assert len(default_customers().all()) == 2


class TestJobs:
    def test_a_job_links_to_a_customer_already_imported(self, shop):
        """Otherwise the two lists never join up and every filter misses them."""
        import_customers(plan_customers(_write(shop / "c.csv", CUSTOMERS)))
        path = _write(
            shop / "j.csv",
            'שם הפרויקט,לקוח,אסמכתא,סכום\nדירה,משה כהן,ORD-1,"48,500"\n',
        )
        import_jobs(plan_jobs(path))

        from profileos.projects import default_store

        job = default_store().all()[0]
        assert job.customer_id
        assert job.quote_total == 48500.0

    def test_a_customer_not_in_the_book_keeps_their_name(self, shop):
        path = _write(shop / "j.csv", "שם הפרויקט,לקוח\nוילה,מישהו אחר\n")
        import_jobs(plan_jobs(path))

        from profileos.projects import default_store

        job = default_store().all()[0]
        assert job.customer_name == "מישהו אחר"
        assert not job.customer_id

    def test_a_job_that_is_already_there_is_not_imported_twice(self, shop):
        path = _write(shop / "j.csv", "שם הפרויקט,לקוח\nדירה,משה\n")
        import_jobs(plan_jobs(path))
        assert plan_jobs(path).skips == 1


class TestPriceLists:
    PRICES = (
        'מק"ט;תיאור;מחיר;יחידה;ספק\n'
        "KL-7300-F;משקוף;42.50;מטר;קליל\n"
        "KL-7300-S;כנף;38.90;מטר;קליל\n"
    )

    def test_a_price_list_is_read_and_kept(self, shop):
        plan = plan_prices(_write(shop / "p.csv", self.PRICES))
        assert plan.creates == 2
        assert import_prices(plan)["created"] == 2

    def test_a_row_with_no_readable_price_is_skipped(self, shop):
        plan = plan_prices(_write(shop / "p.csv", self.PRICES + "BAD;;;מטר;קליל\n"))
        assert any("מחיר" in row.reason for row in plan.of_action("skip"))

    def test_a_negative_price_is_refused(self, shop):
        plan = plan_prices(_write(shop / "p.csv", self.PRICES + "NEG;x;-5;מטר;קליל\n"))
        assert any("שלילי" in row.reason for row in plan.of_action("skip"))

    def test_importing_twice_updates_rather_than_duplicates(self, shop):
        path = _write(shop / "p.csv", self.PRICES)
        import_prices(plan_prices(path))
        assert import_prices(plan_prices(path))["updated"] == 2

    def test_a_file_with_no_price_column_is_refused(self, shop):
        plan = plan_prices(_write(shop / "p.csv", 'מק"ט;תיאור\nX;Y\n'))
        assert plan.problems
