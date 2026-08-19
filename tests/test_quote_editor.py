"""Quote editor tests.

A quotation is negotiated, and the editor's job is to hold two truths at once:
the arithmetic (which must recompute completely on every change) and the
negotiation (which must survive every recompute). Most of these tests push on
the seam between the two, because that seam is where a printed number and a
computed number quietly part company.
"""

from __future__ import annotations

import pytest

from profileos.elements.model import Cell, Opening, OpeningType, Sash
from profileos.quoting.editor import (
    FINISHES,
    OverrideKind,
    QuoteDraft,
    QuoteEditError,
)

RATES = {"profile": 5.5, "glass_m2": 60.0, "hardware": 12.0, "gasket_m": 1.1}


def openings() -> list[Opening]:
    return [
        Opening(
            element_id="W-01", name="W-01", width=1600.0, height=1400.0, quantity=4,
            cells=[Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.TILT_TURN))],
        ),
        Opening(element_id="W-02", name="W-02", width=1200.0, height=1200.0, quantity=2),
    ]


@pytest.fixture
def draft(monkeypatch) -> QuoteDraft:
    """A draft priced from the declared estimating rates alone.

    The supplier registry is process-wide and other tests register price lists
    into it; a price found there instead of the fallback changes every number
    below depending on test order, which is the worst kind of flake.
    """
    from profileos.core.registry import SUPPLIERS

    monkeypatch.setattr(SUPPLIERS, "_entries", {}, raising=False)
    return QuoteDraft.start(
        openings(), project_name="בית פרטי", customer="כהן",
        system_id="klil-7300", fallback_rates=RATES,
    )


class TestRecomputation:
    def test_a_resize_moves_the_price(self, draft):
        before = draft.totals()["net"]
        draft.resize_opening("W-02", width=1800.0)
        assert draft.totals()["net"] > before

    def test_a_resize_moves_the_cut_list_too(self, draft):
        """The one-button propagation: no partial update to get stale."""
        draft.resize_opening("W-02", width=1800.0)
        build = next(
            b for b in draft.variant().builds if b.opening.element_id == "W-02"
        )
        assert max(cut.length for cut in build.cuts) == pytest.approx(1800.0)

    def test_a_glass_swap_reprices(self, draft):
        before = draft.totals()["net"]
        draft.set_glass("dgu-6-16-6")
        assert draft.totals()["net"] != before

    def test_a_finish_swap_charges_by_the_kilogram(self, draft):
        draft.set_finish("mill")
        bare = draft.variant().finish_cost
        draft.set_finish("wood")
        assert bare == 0.0
        assert draft.variant().finish_cost == pytest.approx(
            draft.variant().aluminium_kg * FINISHES["wood"].rate_per_kg, rel=1e-6
        )

    def test_vat_comes_from_the_statute_book_not_a_default(self, draft):
        """18% since 2025-01-01; a 17% default would be a year out of date."""
        assert draft.variant().policy.tax_pct == pytest.approx(18.0)

    def test_margin_must_be_a_possible_number(self, draft):
        with pytest.raises(QuoteEditError):
            draft.set_margin(120.0)

    def test_an_unknown_opening_is_an_error_not_a_no_op(self, draft):
        with pytest.raises(QuoteEditError):
            draft.resize_opening("W-99", width=1000.0)


class TestOverrides:
    def test_a_pinned_price_survives_a_reprice(self, draft):
        draft.set_line_price("W-01", 5000.0, by="דאדי")
        draft.set_margin(30.0)
        line = next(l for l in draft.customer_lines() if l["code"] == "W-01")
        assert line["unit_price"] == 5000.0
        assert line["edited"]

    def test_the_drift_under_a_pin_is_flagged_not_buried(self, draft):
        draft.set_line_price("W-01", 5000.0)
        assert ("W-01", "unit_price") not in draft.stale_overrides
        draft.set_glass("dgu-6-16-6")  # the ground moves
        assert ("W-01", "unit_price") in draft.stale_overrides

    def test_a_discount_is_relative_and_recomputes_with_the_base(self, draft):
        base = next(l for l in draft.customer_lines() if l["code"] == "W-02")["unit_price"]
        draft.set_line_discount("W-02", 10.0)
        discounted = next(l for l in draft.customer_lines() if l["code"] == "W-02")["unit_price"]
        assert discounted == pytest.approx(base * 0.9, rel=1e-3)

    def test_clearing_an_override_restores_the_arithmetic(self, draft):
        computed = next(l for l in draft.customer_lines() if l["code"] == "W-01")["unit_price"]
        draft.set_line_price("W-01", 9000.0)
        draft.clear_override("W-01", OverrideKind.UNIT_PRICE)
        restored = next(l for l in draft.customer_lines() if l["code"] == "W-01")["unit_price"]
        assert restored == pytest.approx(computed, rel=1e-6)

    def test_a_negative_price_is_refused(self, draft):
        with pytest.raises(QuoteEditError):
            draft.set_line_price("W-01", -1.0)

    def test_totals_are_the_sum_of_what_is_printed(self, draft):
        """Once a line is pinned, any other total will one day disagree with
        its own lines in front of the client."""
        draft.set_line_price("W-01", 5000.0)
        printed = sum(row["total"] for row in draft.customer_lines())
        assert draft.totals()["net"] == pytest.approx(printed, abs=0.01)
        assert draft.totals()["net"] != pytest.approx(draft.quotation.net_price)


class TestUndo:
    def test_undo_reverses_the_last_edit(self, draft):
        before = draft.totals()["net"]
        draft.set_margin(40.0)
        assert draft.totals()["net"] != before
        draft.undo()
        assert draft.totals()["net"] == pytest.approx(before, abs=0.01)

    def test_undo_restores_a_replaced_pin(self, draft):
        draft.set_line_price("W-01", 5000.0)
        draft.set_line_price("W-01", 4500.0)
        draft.undo()
        line = next(l for l in draft.customer_lines() if l["code"] == "W-01")
        assert line["unit_price"] == 5000.0

    def test_undo_on_a_fresh_draft_is_a_no_op(self, draft):
        assert draft.undo() is None

    def test_the_journal_reads_like_minutes(self, draft):
        draft.set_margin(30.0, by="דאדי")
        draft.set_line_price("W-01", 5000.0, by="דאדי", reason="סגירה")
        entries = [entry.describe() for entry in draft.journal]
        assert any("margin" in entry for entry in entries)
        assert any("unit_price" in entry for entry in entries)


class TestOptions:
    def test_an_option_shares_the_openings_but_not_the_spec(self, draft):
        draft.add_variant("תרמי", glass_id="dgu-6-16-6", finish_id="anodized")
        first, second = draft.variants
        assert len(first.builds) == len(second.builds)
        assert second.glass_id == "dgu-6-16-6"
        assert first.glass_id == "dgu-6-16-4"

    def test_the_comparison_agrees_with_the_printed_total(self, draft):
        draft.add_variant("תרמי", glass_id="dgu-6-16-6")
        draft.set_line_price("W-01", 5000.0)
        rows = draft.compare()
        assert rows[0]["net"] == pytest.approx(draft.totals()["net"], abs=0.01)

    def test_a_pin_carries_to_the_other_option_plus_the_spec_premium(self, draft):
        """Carrying the bare pin would swallow the thermal premium; not
        carrying it would compare a negotiated A against a list-price B."""
        draft.add_variant("תרמי", glass_id="dgu-6-16-6", finish_id="anodized")
        computed = {row["id"]: row["net"] for row in draft.compare()}
        premium = computed["B"] - computed["A"]
        assert premium > 0
        draft.set_line_price("W-01", 5000.0)
        pinned = {row["id"]: row["net"] for row in draft.compare()}
        assert pinned["B"] - pinned["A"] == pytest.approx(premium, abs=1.0)

    def test_a_discount_carries_to_every_option_as_a_percentage(self, draft):
        draft.add_variant("תרמי", glass_id="dgu-6-16-6")
        before = {row["id"]: row["net"] for row in draft.compare()}
        draft.set_line_discount("W-02", 10.0)
        after = {row["id"]: row["net"] for row in draft.compare()}
        assert after["A"] < before["A"] and after["B"] < before["B"]

    def test_an_unknown_option_is_an_error(self, draft):
        with pytest.raises(QuoteEditError):
            draft.variant("Z")


class TestFinishes:
    def test_the_shipped_rates_admit_they_are_stand_ins(self, draft):
        draft.set_finish("anodized")
        assert any("stand-in" in warning for warning in draft.variant().warnings)

    def test_a_confirmed_rate_needs_a_source(self):
        with pytest.raises(QuoteEditError):
            FINISHES["ral"].confirm(8.5, source=" ")

    def test_mill_finish_costs_nothing(self, draft):
        draft.set_finish("mill")
        assert draft.variant().finish_cost == 0.0


class TestDocument:
    def test_the_customer_copy_never_carries_cost_or_margin(self, draft):
        from profileos.quoting.document import render_quotation

        draft.set_line_price("W-01", 5000.0, by="דאדי", reason="ללחוץ יד")
        page = render_quotation(draft, language="he")
        for word in ("Margin", "Overhead", "Total cost", "Materials", "ללחוץ יד"):
            assert word not in page, word

    def test_the_internal_sheet_carries_all_of_it(self, draft):
        from profileos.quoting.document import render_quotation

        draft.set_line_price("W-01", 5000.0, by="דאדי", reason="סגירה")
        page = render_quotation(draft, language="he", internal=True)
        assert "Margin after edits" in page
        assert "סגירה" in page and "דאדי" in page

    def test_line_descriptions_follow_the_documents_language(self, draft):
        from profileos.quoting.document import render_quotation

        hebrew = render_quotation(draft, language="he")
        russian = render_quotation(draft, language="ru")
        assert "חלון" in hebrew
        assert "окно" in russian

    def test_an_operators_description_is_printed_verbatim(self, draft):
        from profileos.quoting.document import render_quotation

        draft.set_line_description("W-01", "חלון סלון כולל רשת")
        page = render_quotation(draft, language="ru")
        assert "חלון סלון כולל רשת" in page

    def test_the_document_is_self_contained(self, draft):
        from profileos.quoting.document import render_quotation

        page = render_quotation(draft, language="he")
        # Nothing is fetched: no external stylesheets, scripts or images. The
        # SVG xmlns URL is a namespace identifier, not a request.
        for fetch in ("<link", "<script src", "src=\"http", "url(http", "@import"):
            assert fetch not in page, fetch
        assert "<svg" in page  # the elevations are embedded, not linked

    def test_the_totals_on_the_page_are_the_line_sum(self, draft):
        from profileos.quoting.document import render_quotation

        draft.set_line_price("W-01", 5000.0)
        page = render_quotation(draft, language="en")
        printed = draft.totals()
        assert f"{printed['gross']:,.2f}" in page


class TestApi:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from profileos.api.server import app

        return TestClient(app)

    def _create(self, client):
        response = client.post("/quote/draft", json={
            "project_name": "בית פרטי", "system_id": "klil-7300",
            "elements": [
                {"name": "W-01", "width": 1600, "height": 1400, "quantity": 4},
                {"name": "W-02", "width": 1200, "height": 1200},
            ],
        })
        assert response.status_code == 200
        return response.json()

    def test_a_draft_round_trips_through_the_service(self, client):
        state = self._create(client)
        fetched = client.get(f"/quote/draft/{state['quote_id']}").json()
        assert fetched["totals"] == state["totals"]

    def test_an_edit_returns_the_repriced_state(self, client):
        state = self._create(client)
        code = state["lines"][0]["code"]
        edited = client.post(
            f"/quote/draft/{state['quote_id']}/edit",
            json={"action": "set_line_price", "element_id": code, "value": 5000},
        ).json()
        line = next(l for l in edited["lines"] if l["code"] == code)
        assert line["unit_price"] == 5000.0

    def test_a_bad_edit_is_a_422_with_the_editors_own_words(self, client):
        state = self._create(client)
        response = client.post(
            f"/quote/draft/{state['quote_id']}/edit",
            json={"action": "set_glass", "glass_id": "nonsense"},
        )
        assert response.status_code == 422
        assert "nonsense" in response.json()["detail"]

    def test_an_empty_project_cannot_be_quoted(self, client):
        assert client.post("/quote/draft", json={"elements": []}).status_code == 422

    def test_the_document_endpoint_serves_both_copies(self, client):
        state = self._create(client)
        customer = client.get(f"/quote/draft/{state['quote_id']}/document?lang=he")
        internal = client.get(
            f"/quote/draft/{state['quote_id']}/document?lang=he&internal=true"
        )
        assert "Margin" not in customer.text
        assert "Margin after edits" in internal.text
