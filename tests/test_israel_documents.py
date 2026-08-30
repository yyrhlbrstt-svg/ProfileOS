"""What an Israeli invoice has to carry before it may be issued."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.erp.israel import (
    DEFAULT_ALLOCATION_RULE,
    AllocationRule,
    DocumentKind,
    PaymentTerms,
    TaxDocument,
    TaxIdentity,
    from_invoice,
    render_document,
)


def _identity(**overrides) -> TaxIdentity:
    values = {
        "name": 'דאדי בע"מ',
        "vat_number": "514123456",
        "address": "סולם יעקב 1, בית אל",
        "phone": "02-9973510",
    }
    values.update(overrides)
    return TaxIdentity(**values)


def _document(net: float = 1000.0, **overrides) -> TaxDocument:
    values = dict(
        kind=DocumentKind.INVOICE,
        number="INV-1001",
        issued=date(2026, 8, 21),
        issuer=_identity(),
        customer_name="משה כהן",
        lines=[{
            "description": "חלון הזזה", "quantity": 1, "unit": "יח",
            "unit_price": net, "net": net,
        }],
        net=net,
    )
    values.update(overrides)
    return TaxDocument(**values)


class TestPaymentTerms:
    def test_end_of_month_plus_thirty_is_not_thirty_days(self):
        """The difference is up to a month of the shop's cash."""
        issued = date(2026, 8, 21)
        assert PaymentTerms.NET_30.due(issued) == date(2026, 9, 20)
        assert PaymentTerms.EOM_30.due(issued) == date(2026, 9, 30)

    def test_it_counts_from_the_end_of_the_month_of_invoice(self):
        assert PaymentTerms.EOM_60.due(date(2026, 2, 3)) == date(2026, 4, 29)

    def test_a_february_invoice_uses_february_s_own_length(self):
        assert PaymentTerms.EOM_30.due(date(2028, 2, 1)) == date(2028, 3, 30)

    def test_cash_is_due_the_day_it_is_issued(self):
        issued = date(2026, 8, 21)
        assert PaymentTerms.IMMEDIATE.due(issued) == issued

    def test_the_long_terms_are_flagged_as_long(self):
        assert PaymentTerms.EOM_120.exceeds_statutory_default
        assert not PaymentTerms.EOM_30.exceeds_statutory_default


class TestAllocationNumbers:
    def test_a_large_invoice_needs_one(self):
        assert _document(net=50_000).needs_allocation_number()

    def test_a_small_one_does_not(self):
        assert not _document(net=100).needs_allocation_number()

    def test_a_delivery_note_never_does_because_it_carries_no_tax(self):
        note = _document(net=50_000, kind=DocumentKind.DELIVERY)
        assert not note.needs_allocation_number()

    def test_a_credit_note_does_because_it_moves_tax_back(self):
        assert _document(net=50_000, kind=DocumentKind.CREDIT).needs_allocation_number()

    def test_an_invoice_before_the_rule_started_does_not(self):
        early = _document(net=50_000, issued=date(2023, 1, 1))
        assert not early.needs_allocation_number()

    def test_missing_one_is_reported_in_the_customer_s_terms(self):
        problems = _document(net=50_000).problems()
        assert any("לקזז את המע״מ" in problem for problem in problems)

    def test_supplying_one_clears_it(self):
        rule = AllocationRule(5000.0, date(2025, 1, 1), source="רשות המסים 2026")
        document = _document(net=50_000, allocation_number="2026-4471-8890")
        assert not document.problems(rule)

    def test_an_unconfirmed_threshold_is_itself_reported(self):
        """A rule nobody checked is not a rule to pass invoices against."""
        assert not DEFAULT_ALLOCATION_RULE.is_confirmed
        assert any(
            "רשות המסים" in problem
            for problem in _document(net=50_000, allocation_number="x").problems()
        )


class TestWhatMakesADocumentIssuable:
    def test_a_business_with_no_vat_number_cannot_issue_a_tax_invoice(self):
        document = _document(issuer=_identity(vat_number=""))
        assert not document.may_be_issued
        assert any("עוסק מורשה" in problem for problem in document.problems())

    def test_a_document_with_no_customer_is_refused(self):
        assert not _document(customer_name="").may_be_issued

    def test_an_invoice_with_no_lines_is_refused(self):
        assert not _document(lines=[]).may_be_issued

    def test_an_ordinary_small_invoice_may_be_issued(self):
        assert _document(net=500).may_be_issued

    def test_vat_is_added_to_the_net_and_the_gross_is_their_sum(self):
        document = _document(net=1000.0, vat_rate=0.18)
        assert document.vat == 180.0
        assert document.gross == 1180.0


class TestPrinting:
    def test_the_document_names_itself_in_hebrew(self):
        html = render_document(_document(kind=DocumentKind.INVOICE_RECEIPT, net=500))
        assert "חשבונית מס קבלה" in html

    def test_the_issuing_business_is_on_it(self):
        html = render_document(_document(net=500))
        assert "514123456" in html
        assert 'דאדי בע"מ' in html or "דאדי" in html

    def test_a_document_that_may_not_be_issued_says_so_on_its_face(self):
        html = render_document(_document(net=50_000))
        assert "לא להוצאה עדיין" in html

    def test_a_clean_document_carries_no_banner(self):
        rule = AllocationRule(5000.0, date(2025, 1, 1), source="רשות המסים")
        document = _document(net=500)
        assert not document.problems(rule)
        assert "לא להוצאה עדיין" not in render_document(document)

    def test_it_is_right_to_left(self):
        assert 'dir="rtl"' in render_document(_document(net=500))

    def test_every_kind_of_document_renders(self):
        for kind in DocumentKind:
            html = render_document(_document(kind=kind, net=500))
            assert kind.hebrew in html


class TestFromTheLedger:
    def test_a_ledger_invoice_becomes_a_printable_document(self):
        from profileos.erp.sales import SalesInvoice, SalesLine

        invoice = SalesInvoice(
            invoice_id="INV-2001",
            customer="משה כהן",
            on=date(2026, 8, 21),
            lines=[SalesLine("חלון הזזה", 4, 240_000, unit="יח")],
        )
        document = from_invoice(invoice, _identity())
        assert document.number == "INV-2001"
        assert document.net == pytest.approx(9600.0)
        assert document.vat_rate == invoice.vat_rate
        assert document.lines[0]["unit_price"] == pytest.approx(2400.0)

    def test_the_shekels_match_the_ledger_to_the_agora(self):
        from profileos.erp.sales import SalesInvoice, SalesLine

        invoice = SalesInvoice(
            invoice_id="INV-2002",
            customer="לקוח",
            on=date(2026, 8, 21),
            lines=[SalesLine("עבודה", 3, 33_333, unit="יח")],
        )
        document = from_invoice(invoice, _identity())
        assert round(document.net * 100) == invoice.net
        assert round(document.gross * 100) == pytest.approx(invoice.gross, abs=1)
