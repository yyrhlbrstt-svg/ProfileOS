"""Tests for the catalogue ingestion engine.

The engine's job is to build a profile library the fabricator owns and to say
honestly how far each figure in it can be trusted. So the tests concentrate on
the two ways that goes wrong: a number read out of a table incorrectly, and a
disagreement between the table and the drawing being missed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from profileos.catalogue import (
    CatalogueError,
    CheckStatus,
    PropertyCheck,
    TableSpec,
    code_candidates,
    cross_check,
    detect_decimal,
    ingest,
    normalise_code,
    numbers_in,
    parse_lines,
    parse_number,
    read_table,
    rows_from_csv,
    rows_from_pdf,
    scale_for,
    to_plugin,
)
from profileos.catalogue.ingest import analyse_drawing, measured_values

SAMPLES = Path(__file__).resolve().parents[1] / "profileos" / "data" / "samples"

EU_TABLE = """Klil 4300 series - frame profiles
Code    Description        kg/m    A cm2   Ix cm4   Iy cm4   b mm   h mm
4301    Outer frame        1,842   6,82    38,45    25,10    62,5   70,0
4302    Sash               2,105   7,79    44,20    30,05    54,0   78,0
Notes: weights are nominal
"""

UK_TABLE = """Code    Description        kg/m    A cm2      Ix cm4     Iy cm4   b mm   h mm
4301    Outer frame        1.842   6.82       1,238.45   25.10    62.5   70.0
"""


def write_pdf(lines: list[str], path: Path) -> Path:
    """A minimal one-page text PDF, so the PDF path is exercised for real."""
    content = ["BT", "/F1 9 Tf", "12 TL", "40 780 Td"]
    content += [f"({line}) Tj T*" for line in lines]
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


# --------------------------------------------------------------------------- #
# Numbers
# --------------------------------------------------------------------------- #
class TestNumbers:
    @pytest.mark.parametrize(
        "text,expected",
        [("1.234,56", ","), ("1,234.56", "."), ("6,82 und 38,45", ","), ("6.82 and 38.45", ".")],
    )
    def test_decimal_convention_is_detected_per_document(self, text, expected):
        assert detect_decimal(text) == expected

    def test_ambiguous_token_follows_the_document(self):
        """``1,842`` is 1842 in London and 1.842 in Milan; only context decides.

        Guessing per token is what produces a profile weighing 1842 kg/m.
        """
        assert parse_number("1,842", ".") == 1842.0
        assert parse_number("1,842", ",") == pytest.approx(1.842)

    def test_grouped_thousands_are_not_split(self):
        """Without the trailing guard the regex bites "430" out of "4300"."""
        assert numbers_in("Klil 4300 series") == [4300.0]

    def test_unit_suffixes_are_not_read_as_data(self):
        """A "cm2" heading must not put a 2 into the numbers on that line."""
        assert numbers_in("Code kg/m A cm2 Ix cm4 Iy cm4 b mm h mm") == []

    def test_unknown_units_are_refused(self):
        with pytest.raises(CatalogueError):
            scale_for("furlong")

    def test_centimetre_units_convert_to_millimetres(self):
        assert scale_for("cm2") == 100.0
        assert scale_for("cm4") == 10_000.0
        assert scale_for("cm3") == 1_000.0


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
class TestTableParsing:
    def test_european_table_parses_to_canonical_units(self):
        rows = parse_lines(EU_TABLE.splitlines())
        assert [row.code for row in rows] == ["4301", "4302"]
        first = rows[0]
        assert first.description == "Outer frame"
        assert first.values["mass_per_metre"] == pytest.approx(1.842)
        assert first.values["area"] == pytest.approx(682.0)          # 6.82 cm2
        assert first.values["ixx"] == pytest.approx(384_500.0)       # 38.45 cm4
        assert first.values["width"] == pytest.approx(62.5)

    def test_anglo_table_parses_to_the_same_canonical_units(self):
        rows = parse_lines(UK_TABLE.splitlines())
        assert rows[0].values["mass_per_metre"] == pytest.approx(1.842)
        assert rows[0].values["ixx"] == pytest.approx(12_384_500.0)  # 1238.45 cm4

    def test_headings_and_footnotes_are_not_rows(self):
        rows = parse_lines(EU_TABLE.splitlines())
        assert "Klil" not in {row.code for row in rows}
        assert "Notes" not in {row.code for row in rows}

    def test_numbers_inside_a_description_do_not_shift_the_columns(self):
        """"Mullion 70/100" carries two numbers that are not data.

        Reading the line left to right puts 70 into the weight column and every
        real figure one place out — which is the failure this guards.
        """
        lines = [
            "Code   Description       kg/m   A cm2   Ix cm4   Iy cm4   b mm   h mm",
            "MB70   Mullion 70/100    4,642  17,192  122,518  95,975   70,0   100,0",
        ]
        row = parse_lines(lines)[0]
        assert row.description == "Mullion 70/100"
        assert row.values["mass_per_metre"] == pytest.approx(4.642)
        assert row.values["area"] == pytest.approx(1719.2)
        assert row.values["height"] == pytest.approx(100.0)

    def test_short_rows_are_flagged(self):
        lines = [
            "Code   Description   kg/m   A cm2   Ix cm4   Iy cm4   b mm   h mm",
            "X1     Full          1,00   2,00    3,00     4,00     5,0    6,0",
            "X2     Short         1,00   2,00    3,00",
        ]
        rows = parse_lines(lines)
        assert rows[0].partial is False
        assert rows[1].partial is True

    def test_each_table_relearns_its_columns(self):
        """A catalogue with two tables must not read the second with the first's
        header, which would mislabel every figure in it."""
        lines = [
            "Code  Description  kg/m   A cm2",
            "A1    Frame        1,00   2,00",
            "Code  Description  Ix cm4  Iy cm4",
            "B1    Sash         3,00    4,00",
        ]
        rows = parse_lines(lines)
        assert set(rows[0].values) == {"mass_per_metre", "area"}
        assert set(rows[1].values) == {"ixx", "iyy"}

    def test_csv_is_read_with_its_own_delimiter(self, tmp_path):
        path = tmp_path / "cat.csv"
        path.write_text(
            "code;description;kg/m;A;Ix\n4301;Outer frame;1,842;6,82;38,45\n",
            encoding="utf-8",
        )
        rows = rows_from_csv(path)
        assert rows[0].code == "4301"
        assert rows[0].description == "Outer frame"
        assert rows[0].values["mass_per_metre"] == pytest.approx(1.842)

    def test_pdf_is_read_page_by_page(self, tmp_path):
        path = write_pdf(EU_TABLE.splitlines(), tmp_path / "cat.pdf")
        rows = rows_from_pdf(path)
        assert [row.code for row in rows] == ["4301", "4302"]
        assert all(row.page == 1 for row in rows)

    def test_unsupported_format_is_refused(self, tmp_path):
        path = tmp_path / "cat.docx"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(CatalogueError):
            read_table(path)

    def test_missing_file_is_refused(self, tmp_path):
        with pytest.raises(CatalogueError):
            read_table(tmp_path / "nothing.csv")

    def test_fixed_column_order_skips_header_matching(self):
        from profileos.catalogue import Column

        spec = TableSpec(
            columns=(Column("mass_per_metre", "kg/m"), Column("area", "cm2")),
            fixed_order=True,
            decimal=",",
        )
        row = parse_lines(["4301  Frame  1,842  6,82"], spec)[0]
        assert row.values == pytest.approx(
            {"mass_per_metre": 1.842, "area": 682.0}, rel=1e-9
        )


# --------------------------------------------------------------------------- #
# Code matching
# --------------------------------------------------------------------------- #
class TestCodeMatching:
    @pytest.mark.parametrize(
        "code", ["MB-70.1234", "MB70 1234", "mb70/1234", "MB70_1234"]
    )
    def test_one_article_written_four_ways_normalises_alike(self, code):
        assert normalise_code(code) == "MB701234"

    def test_different_articles_stay_different(self):
        assert normalise_code("4301") != normalise_code("4302")

    def test_filename_offers_the_whole_stem_first(self):
        candidates = code_candidates(Path("4301_outer_frame.dxf"))
        assert candidates[0] == "4301_outer_frame"
        assert "4301" in candidates


# --------------------------------------------------------------------------- #
# Cross-checking
# --------------------------------------------------------------------------- #
class TestCrossCheck:
    def test_agreement_within_tolerance(self):
        check = PropertyCheck("ixx", published=100.0, measured=103.0, tolerance=0.05)
        assert check.status is CheckStatus.AGREE
        assert check.deviation == pytest.approx(0.03)

    def test_disagreement_beyond_tolerance(self):
        check = PropertyCheck("ixx", published=100.0, measured=130.0, tolerance=0.05)
        assert check.status is CheckStatus.DISAGREE

    def test_one_sided_data_is_not_a_verdict(self):
        assert (
            PropertyCheck("ixx", published=100.0, measured=None, tolerance=0.05).status
            is CheckStatus.UNCHECKED
        )
        assert (
            PropertyCheck("ixx", published=None, measured=100.0, tolerance=0.05).status
            is CheckStatus.UNCHECKED
        )

    def test_measured_properties_come_from_the_drawing(self):
        result = analyse_drawing(SAMPLES / "frame_thermal.dxf", system_series="test")
        assert result.ok
        values = measured_values(result.properties)
        assert values["area"] == pytest.approx(1060.17, rel=1e-4)
        assert values["width"] == pytest.approx(62.0)
        assert values["height"] == pytest.approx(80.0)

    def test_a_planted_error_is_caught(self):
        """The drawing says Ix = 5,352 mm4; the table is told 7,350."""
        result = analyse_drawing(SAMPLES / "glazing_bead.dxf", system_series="test")
        checks = cross_check({"ixx": 7350.0, "area": 152.5}, result.properties)
        by_name = {check.name: check for check in checks}
        assert by_name["ixx"].status is CheckStatus.DISAGREE
        assert by_name["area"].status is CheckStatus.AGREE

    def test_a_correct_table_passes(self):
        result = analyse_drawing(SAMPLES / "glazing_bead.dxf", system_series="test")
        checks = cross_check({"ixx": 5351.6, "area": 152.5}, result.properties)
        assert all(check.status is not CheckStatus.DISAGREE for check in checks)


# --------------------------------------------------------------------------- #
# The whole run
# --------------------------------------------------------------------------- #
@pytest.fixture
def catalogue(tmp_path):
    """A table matching the sample drawings, with one figure deliberately wrong."""
    path = tmp_path / "supplier.csv"
    path.write_text(
        "code;description;kg/m;A;Ix;Iy;b;h\n"
        "mullion_mb70;Mullion 70/100;4,642;17,192;122,518;95,975;70,0;100,0\n"
        "frame_thermal;Thermal frame;2,862;10,602;56,751;46,114;62,0;80,0\n"
        "glazing_bead;Glazing bead;0,412;1,525;0,735;0,255;18,0;22,0\n"
        "cover_cap;Cover cap, no drawing;0,190;0,703;0,180;0,090;20,0;10,0\n",
        encoding="utf-8",
    )
    return path


class TestIngestion:
    def test_a_full_run_sorts_the_trustworthy_from_the_rest(self, catalogue):
        report = ingest(table=catalogue, drawings=SAMPLES, system_series="MB-70")
        by_id = {entry.profile_id: entry for entry in report.entries}

        assert by_id["mullion_mb70"].status == "verified"
        assert by_id["frame_thermal"].status == "verified"
        # The planted Ix error.
        assert by_id["glazing_bead"].status == "conflict"
        assert [c.name for c in by_id["glazing_bead"].disagreements] == ["ixx"]
        # A row with no drawing keeps its figures and says they are unchecked.
        assert by_id["cover_cap"].status == "table only"
        assert by_id["cover_cap"].published["mass_per_metre"] == pytest.approx(0.19)
        # A drawing with no row is still measured, and says nothing checked it.
        assert by_id["gapped_box"].status == "unverified"
        assert report.errors == []

    def test_drawings_alone_produce_unverified_geometry(self):
        report = ingest(drawings=SAMPLES, system_series="MB-70")
        assert report.entries
        assert all(entry.status == "unverified" for entry in report.entries)
        assert report.verified == []

    def test_a_table_alone_produces_geometry_free_entries(self, catalogue):
        report = ingest(table=catalogue, system_series="MB-70")
        assert len(report.entries) == 4
        assert all(not entry.has_geometry for entry in report.entries)
        assert len(report.unmatched_rows) == 4

    def test_neither_input_is_an_empty_report_not_a_crash(self):
        report = ingest()
        assert report.entries == []
        assert report.summary()["entries"] == 0

    def test_a_bad_drawing_does_not_abort_the_run(self, tmp_path):
        """One unreadable file in a pack of four hundred must not stop the rest."""
        (tmp_path / "broken.dxf").write_text("not a dxf at all", encoding="utf-8")
        for name in ("frame_thermal.dxf", "glazing_bead.dxf"):
            (tmp_path / name).write_bytes((SAMPLES / name).read_bytes())
        report = ingest(drawings=tmp_path, system_series="test")
        assert len(report.entries) == 2
        assert len(report.errors) == 1
        assert "broken.dxf" in report.errors[0]

    def test_limit_stops_early(self):
        report = ingest(drawings=SAMPLES, system_series="test", limit=1)
        assert len(report.entries) == 1

    def test_missing_drawing_folder_is_reported_not_raised(self, tmp_path):
        report = ingest(drawings=tmp_path / "nowhere", system_series="test")
        assert report.errors and "not found" in report.errors[0]


class TestPluginEmission:
    def test_conflicting_profiles_are_withheld_by_default(self, catalogue):
        report = ingest(table=catalogue, drawings=SAMPLES, system_series="MB-70")
        plugin = to_plugin(report, plugin_id="mb70", name="MB-70")
        shipped = {p["profile_id"] for p in plugin["profiles"]}
        assert "glazing_bead" not in shipped
        assert plugin["excluded_for_conflict"] == ["glazing_bead"]
        assert "mullion_mb70" in shipped

    def test_conflicts_can_be_shipped_deliberately(self, catalogue):
        report = ingest(table=catalogue, drawings=SAMPLES, system_series="MB-70")
        plugin = to_plugin(
            report, plugin_id="mb70", name="MB-70", include_conflicts=True
        )
        shipped = {p["profile_id"] for p in plugin["profiles"]}
        assert "glazing_bead" in shipped

    def test_the_published_figures_travel_with_the_profile(self, catalogue):
        report = ingest(table=catalogue, drawings=SAMPLES, system_series="MB-70")
        plugin = to_plugin(report, plugin_id="mb70", name="MB-70")
        entry = next(p for p in plugin["profiles"] if p["profile_id"] == "mullion_mb70")
        assert entry["metadata"]["verification"] == "verified"
        assert entry["metadata"]["published"]["mass_per_metre"] == pytest.approx(4.642)

    def test_the_plugin_is_json_serialisable(self, catalogue):
        import json

        report = ingest(table=catalogue, drawings=SAMPLES, system_series="MB-70")
        json.dumps(to_plugin(report, plugin_id="mb70", name="MB-70"))

    def test_emitted_profiles_reload_as_profile_definitions(self, catalogue):
        """The library is only useful if the rest of the suite can read it."""
        from profileos.models.profile import ProfileDefinition

        report = ingest(table=catalogue, drawings=SAMPLES, system_series="MB-70")
        plugin = to_plugin(report, plugin_id="mb70", name="MB-70")
        for payload in plugin["profiles"]:
            definition = ProfileDefinition.model_validate(payload)
            assert definition.outer_dimensions.width > 0
