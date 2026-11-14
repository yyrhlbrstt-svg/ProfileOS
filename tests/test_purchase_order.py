"""The purchase order as the supplier actually receives it."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.erp.po_document import (
    DocumentLine,
    PurchaseDocument,
    Specification,
    coating_order,
    document_from_order,
    render_purchase_order,
    write_purchase_order,
)
from profileos.erp.purchasing import OrderLine, PurchaseOrder


@pytest.fixture
def order() -> PurchaseOrder:
    return PurchaseOrder(
        order_id="PO-000123", supplier_id="KLIL", raised=date(2026, 8, 27),
        promised=date(2026, 9, 10), project_id="2026-114",
        lines=[
            # The ledger prices in agorot: 3450 is ⁦34.50⁩ ₪ a metre.
            OrderLine("KL-7300-F", 240.0, 3450.0, unit="m",
                      description="משקוף קליל 7300"),
            OrderLine("KL-7300-S", 180.0, 4100.0, unit="m",
                      description="כנף קליל 7300"),
        ],
    )


class TestFromTheEngine:
    def test_the_price_crosses_in_shekels_not_in_agorot(self, order):
        """⁦3450⁩ where the shop meant ⁦34.50⁩ is a hundredfold error."""
        document = document_from_order(order, supplier_name="קליל")
        assert document.lines[0].unit_price == pytest.approx(34.50)

    def test_the_totals_follow_from_the_lines(self, order):
        document = document_from_order(order, supplier_name="קליל")
        assert document.net == pytest.approx(240 * 34.50 + 180 * 41.00)
        assert document.gross == pytest.approx(document.net * 1.18, abs=0.02)

    def test_the_job_and_the_promised_date_come_across(self, order):
        document = document_from_order(order, supplier_name="קליל")
        assert document.for_job == "2026-114"
        assert document.wanted_by == date(2026, 9, 10)


class TestExtrusionIsNotACommodity:
    def test_a_missing_alloy_is_printed_as_a_question(self, order):
        """A supplier sent a code and a quantity sends what the code means to them."""
        document = document_from_order(order, supplier_name="קליל")
        assert document.open_questions
        assert any("סגסוגת" in " ".join(q) for _code, q in document.open_questions)

    def test_a_complete_specification_asks_nothing(self, order):
        spec = Specification(
            alloy="6063", temper="T6", mill_length=6000.0,
            finish="אנודייז טבעי", marking="קצה כל מוט",
        )
        document = document_from_order(
            order, supplier_name="קליל",
            specifications={"KL-7300-F": spec, "KL-7300-S": spec},
        )
        assert document.open_questions == []
        assert spec.is_complete

    def test_the_question_reaches_the_printed_order(self, order):
        document = document_from_order(order, supplier_name="קליל")
        assert "לאישורכם" in render_purchase_order(document)

    def test_the_specification_reads_as_a_sentence(self):
        spec = Specification(alloy="6063", temper="T6", mill_length=6000.0)
        assert "6063" in spec.describe()
        assert "T6" in spec.describe()


class TestPriceProvenance:
    def test_a_price_with_no_source_is_flagged(self, order):
        document = document_from_order(order, supplier_name="קליל")
        assert len(document.unsourced) == 2
        assert any("בלי מקור" in p for p in document.problems())

    def test_a_sourced_price_is_not_flagged(self, order):
        document = document_from_order(
            order, supplier_name="קליל",
            price_sources={
                "KL-7300-F": "מחירון קליל 8/2026",
                "KL-7300-S": "מחירון קליל 8/2026",
            },
        )
        assert document.unsourced == []

    def test_a_line_with_no_price_at_all_is_a_separate_problem(self):
        document = PurchaseDocument(order_id="PO-1", supplier_name="ס")
        document.lines = [DocumentLine(code="X", quantity=10.0)]
        assert len(document.unpriced) == 1
        assert any("בלי מחיר" in p for p in document.problems())
        assert document.net == pytest.approx(0.0)

    def test_an_unpriced_line_shows_no_total_rather_than_zero(self):
        line = DocumentLine(code="X", quantity=10.0)
        assert line.value is None


class TestChecking:
    def test_no_supplier_is_a_problem(self, order):
        document = document_from_order(order, supplier_name="")
        document.supplier_name = ""
        assert any("ספק" in p for p in document.problems())

    def test_a_delivery_date_before_the_order_date_is_refused(self, order):
        document = document_from_order(order, supplier_name="קליל")
        document.wanted_by = document.raised - timedelta(days=1)
        assert any("מועד האספקה" in p for p in document.problems())

    def test_a_clean_order_may_be_sent(self, order):
        spec = Specification(alloy="6063", temper="T6", mill_length=6000.0)
        document = document_from_order(
            order, supplier_name="קליל",
            specifications={"KL-7300-F": spec, "KL-7300-S": spec},
            price_sources={"KL-7300-F": "מחירון", "KL-7300-S": "מחירון"},
        )
        assert document.may_be_sent
        assert "לבדיקה לפני שליחה" not in render_purchase_order(document)

    def test_an_empty_order_says_so(self):
        assert PurchaseDocument(order_id="PO-1").problems() == ["אין שורות בהזמנה"]


class TestCoating:
    def test_it_is_ordered_on_the_shop_s_own_area(self):
        document = coating_order(
            order_id="PO-COAT-1", supplier_name="מתכת צבע",
            finish="אנודייז", colour="טבעי", area_m2=48.312, pieces=126,
            price_per_m2=41.0, price_source="הצעת מחיר 7/2026",
        )
        assert document.lines[0].quantity == pytest.approx(48.312)
        assert document.lines[0].unit == "m²"
        assert document.net == pytest.approx(48.312 * 41.0, abs=0.02)

    def test_it_says_which_area_it_means(self):
        """The coater bills on theirs; the difference has to be arguable."""
        document = coating_order(
            order_id="PO-COAT-1", supplier_name="מתכת צבע",
            finish="אנודייז", area_m2=48.0, pieces=100,
        )
        assert "היקף החתך החיצוני" in document.lines[0].note

    def test_the_finish_reaches_the_document(self):
        document = coating_order(
            order_id="PO-COAT-1", supplier_name="מתכת צבע",
            finish="צבע", colour="RAL 9016", area_m2=10.0, pieces=20,
            price_per_m2=44.0, price_source="הצעה",
        )
        assert "RAL 9016" in render_purchase_order(document)


class TestTheDocument:
    def test_it_is_written_right_to_left(self, order):
        document = document_from_order(order, supplier_name="קליל")
        assert 'dir="rtl"' in render_purchase_order(document)

    def test_it_carries_a_place_for_an_approval(self, order):
        document = document_from_order(order, supplier_name="קליל")
        assert "אושר על ידי" in render_purchase_order(document)

    def test_it_is_written_where_it_was_asked_for(self, order, tmp_path):
        document = document_from_order(order, supplier_name="קליל")
        target = write_purchase_order(document, tmp_path / "out" / "po.html")
        assert target.exists()
        assert "הזמנת רכש" in target.read_text(encoding="utf-8")
