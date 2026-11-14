"""ERP tests.

Accounting has one property worth testing above all others: the books balance.
Everything here either checks that invariant directly, or checks an answer
worked out by hand — a FIFO issue, a VAT figure, a net requirement — because a
number the system produced cannot be used to verify itself.
"""

from __future__ import annotations

from datetime import date

import pytest

from profileos.erp import (
    Account,
    AccountType,
    Calendar,
    Company,
    CompanyError,
    GoodsReceipt,
    InvoiceLine,
    JobDemand,
    JournalEntry,
    Ledger,
    LedgerError,
    MatchResult,
    OrderLine,
    Posting,
    PurchaseInvoice,
    PurchaseOrder,
    PurchasingError,
    ReceiptLine,
    SalesInvoice,
    SalesLine,
    Scheduler,
    StandardTimes,
    StockError,
    StockItem,
    StockLedger,
    Valuation,
    credit_note,
    format_money,
    money,
    place,
    receive,
    requirements,
    three_way_match,
    vat_rate,
)

JAN = date(2026, 1, 15)


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #
class TestLedger:
    def test_an_unbalanced_entry_is_refused_with_the_difference(self):
        with pytest.raises(LedgerError) as excinfo:
            JournalEntry(
                "J1", JAN, "wrong",
                (Posting("1100", 50_000), Posting("3100", -40_000)),
            )
        assert "out_by_minor_units=10000" in str(excinfo.value)

    def test_a_zero_posting_is_refused(self):
        with pytest.raises(LedgerError):
            JournalEntry("J1", JAN, "nothing", (Posting("1100", 0),))

    def test_the_trial_balance_balances(self):
        ledger = Ledger()
        ledger.post_simple("J1", JAN, "Capital", "1100", "3100", money(100_000))
        ledger.post_simple("J2", JAN, "Rent", "6200", "1100", money(4_500))
        ledger.post_simple("J3", JAN, "Sale", "1200", "4100", money(12_000))
        ledger.check()
        rows = ledger.trial_balance()
        assert sum(r.debits for r in rows) == sum(r.credits for r in rows)

    def test_the_balance_sheet_closes(self):
        """Assets must equal liabilities plus equity plus the period's result."""
        ledger = Ledger()
        ledger.post_simple("J1", JAN, "Capital", "1100", "3100", money(100_000))
        ledger.post_simple("J2", JAN, "Stock bought", "1300", "2100", money(30_000))
        ledger.post_simple("J3", JAN, "Sale", "1200", "4100", money(45_000))
        ledger.post_simple("J4", JAN, "Materials used", "5100", "1300", money(18_000))
        assert ledger.balance_sheet()["difference"] == 0

    def test_natural_balances_read_the_right_way_round(self):
        """An asset and a liability of the same size both read as positive."""
        ledger = Ledger()
        ledger.post_simple("J1", JAN, "x", "1100", "2100", money(500))
        assert ledger.balance("1100") == money(500)   # asset, debit balance
        assert ledger.balance("2100") == money(500)   # liability, credit balance

    def test_posting_the_same_entry_twice_is_refused(self):
        ledger = Ledger()
        entry = JournalEntry("J1", JAN, "x",
                             (Posting("1100", 100), Posting("3100", -100)))
        ledger.post(entry)
        with pytest.raises(LedgerError):
            ledger.post(entry)

    def test_a_mistake_is_reversed_not_edited(self):
        ledger = Ledger()
        ledger.post_simple("J1", JAN, "Wrong amount", "6200", "1100", money(4_500))
        ledger.reverse("J1", "J1R")
        ledger.check()
        assert ledger.balance("6200") == 0
        # Both entries survive: an auditor sees what happened.
        assert len(ledger.entries) == 2

    def test_an_unknown_account_is_refused_before_anything_is_written(self):
        ledger = Ledger()
        entry = JournalEntry(
            "J1", JAN, "x",
            (Posting("1100", 100), Posting("9999", -100)),
        )
        with pytest.raises(LedgerError):
            ledger.post(entry)
        assert ledger.entries == []

    def test_money_rounds_commercially_not_to_even(self):
        """Banker's rounding turns 0.125 into 0.12, which no customer expects."""
        assert money(0.125) == 13
        assert money(0.135) == 14
        assert money(-0.125) == -13

    def test_money_is_exact_over_many_small_amounts(self):
        """A hundred invoices at 33.33 must not drift a single agora."""
        ledger = Ledger()
        for index in range(100):
            ledger.post_simple(
                f"J{index}", JAN, "small sale", "1200", "4100", money(33.33)
            )
        ledger.check()
        assert ledger.balance("4100") == 333_300


# --------------------------------------------------------------------------- #
# Stock
# --------------------------------------------------------------------------- #
class TestStock:
    def _two_layers(self, valuation=Valuation.FIFO) -> StockLedger:
        stock = StockLedger([StockItem("4301", "Frame", valuation=valuation)])
        stock.receive("4301", 300.0, 4150.0, on=date(2026, 1, 5))
        stock.receive("4301", 300.0, 4390.0, on=date(2026, 2, 3))
        return stock

    def test_fifo_consumes_the_oldest_price_first(self):
        """400 m out of layers of 300 at 41.50 and 300 at 43.90.

        300 x 41.50 + 100 x 43.90 = 12,450 + 4,390 = 16,840.
        """
        stock = self._two_layers()
        movement = stock.issue("4301", 400.0)
        assert -movement.value == money(16_840)
        assert stock.state("4301").on_hand == pytest.approx(200.0)
        assert stock.state("4301").value == money(8_780)   # 200 x 43.90

    def test_average_costing_smooths_the_layers(self):
        """300 at 41.50 plus 300 at 43.90 is 25,620 over 600 m: 42.70 a metre.

        400 m out is therefore 17,080, and the 200 m left is 8,540 — both at
        the blended rate rather than at either delivery's own price.
        """
        stock = self._two_layers(Valuation.AVERAGE)
        movement = stock.issue("4301", 400.0)
        assert -movement.value == money(17_080)
        assert stock.state("4301").value == money(8_540)

    def test_the_two_methods_disagree_which_is_the_point(self):
        assert (
            self._two_layers(Valuation.FIFO).issue("4301", 400.0).value
            != self._two_layers(Valuation.AVERAGE).issue("4301", 400.0).value
        )

    def test_issuing_more_than_is_held_is_refused(self):
        stock = self._two_layers()
        with pytest.raises(StockError) as excinfo:
            stock.issue("4301", 700.0)
        assert "on_hand=600" in str(excinfo.value)

    def test_the_movement_history_reproduces_the_current_value(self):
        stock = self._two_layers()
        stock.issue("4301", 250.0)
        stock.receive("4301", 120.0, 4500.0)
        stock.issue("4301", 90.0)
        stock.check()

    def test_a_stocktake_that_finds_nothing_records_nothing(self):
        stock = self._two_layers()
        assert stock.adjust("4301", 600.0) is None
        assert len(stock.movements) == 2

    def test_a_stocktake_shortfall_is_valued_and_recorded(self):
        stock = self._two_layers()
        movement = stock.adjust("4301", 580.0)
        assert movement is not None
        assert movement.quantity == pytest.approx(-20.0)
        assert movement.value == -money(830)   # 20 x 41.50, oldest layer
        stock.check()

    def test_allocation_stops_the_same_bar_being_promised_twice(self):
        stock = self._two_layers()
        stock.allocate("4301", 500.0)
        assert stock.state("4301").available == pytest.approx(100.0)
        with pytest.raises(StockError):
            stock.allocate("4301", 200.0)

    def test_projected_balance_accounts_for_orders_and_promises(self):
        stock = self._two_layers()
        stock.allocate("4301", 400.0)
        stock.order("4301", 300.0)
        state = stock.state("4301")
        assert state.on_hand == pytest.approx(600.0)
        assert state.projected == pytest.approx(500.0)   # 600 + 300 - 400


# --------------------------------------------------------------------------- #
# Purchasing
# --------------------------------------------------------------------------- #
class TestPurchasing:
    def _stock(self) -> StockLedger:
        stock = StockLedger([
            StockItem("4301", "Frame", supplier_id="extal", reorder_quantity=6.0),
            StockItem("4302", "Sash", supplier_id="extal", reorder_quantity=6.0),
        ])
        stock.receive("4301", 120.0, 4090.0)
        return stock

    def test_net_requirement_deducts_free_stock_and_open_orders(self):
        stock = self._stock()
        stock.allocate("4301", 40.0)
        stock.order("4301", 60.0)
        row = requirements({"4301": 486.0}, stock)[0]
        # 486 needed, 120 on hand less 40 promised = 80 free, 60 on order.
        assert row.free == pytest.approx(80.0)
        assert row.net == pytest.approx(346.0)

    def test_nothing_is_ordered_when_the_rack_covers_it(self):
        stock = self._stock()
        assert requirements({"4301": 100.0}, stock)[0].must_order is False

    def test_an_unknown_item_is_bought_in_full(self):
        row = requirements({"NEW-1": 25.0}, self._stock())[0]
        assert row.net == pytest.approx(25.0)

    def test_one_order_per_supplier_not_per_line(self):
        from profileos.erp import orders_from_requirements

        stock = self._stock()
        rows = requirements({"4301": 486.0, "4302": 312.0}, stock)
        orders = orders_from_requirements(
            rows, stock, {"4301": 4150.0, "4302": 4790.0}, raised=JAN
        )
        assert len(orders) == 1
        assert {line.item for line in orders[0].lines} == {"4301", "4302"}

    def test_quantities_round_up_to_the_stock_length(self):
        from profileos.erp import orders_from_requirements

        stock = self._stock()
        rows = requirements({"4301": 130.0}, stock)      # net 10 m
        orders = orders_from_requirements(rows, stock, {"4301": 4150.0}, raised=JAN)
        # Sold in 6 m bars, so 10 m becomes 12 m rather than a part bar.
        assert orders[0].lines[0].quantity == pytest.approx(12.0)

    def test_an_item_with_no_price_stops_the_order(self):
        from profileos.erp import orders_from_requirements

        stock = self._stock()
        rows = requirements({"4302": 100.0}, stock)
        with pytest.raises(PurchasingError):
            orders_from_requirements(rows, stock, {}, raised=JAN)

    def test_the_same_item_twice_on_one_order_is_refused(self):
        with pytest.raises(PurchasingError):
            PurchaseOrder("PO-1", "extal", JAN, [
                OrderLine("4301", 100.0, 4150.0),
                OrderLine("4301", 50.0, 4150.0),
            ])


class TestThreeWayMatch:
    def _setup(self):
        stock = StockLedger([StockItem("4301", "Frame", supplier_id="extal")])
        order = PurchaseOrder("PO-1", "extal", JAN, [OrderLine("4301", 372.0, 4150.0)])
        place(order, stock)
        receipt = GoodsReceipt("GRN-1", "PO-1", JAN, [ReceiptLine("4301", 372.0)])
        receive(order, receipt, stock)
        return stock, order, receipt

    def test_a_clean_invoice_matches(self):
        _, order, receipt = self._setup()
        invoice = PurchaseInvoice("INV-1", "extal", "PO-1", JAN,
                                  [InvoiceLine("4301", 372.0, 4150.0)])
        assert three_way_match(invoice, order, [receipt]).ok

    def test_being_invoiced_for_more_than_arrived_is_caught(self):
        _, order, receipt = self._setup()
        invoice = PurchaseInvoice("INV-1", "extal", "PO-1", JAN,
                                  [InvoiceLine("4301", 402.0, 4150.0)])
        match = three_way_match(invoice, order, [receipt])
        assert not match.ok
        assert match.failures[0].result is MatchResult.QUANTITY_OVER
        assert "402" in match.explain() and "372" in match.explain()

    def test_an_unagreed_price_rise_is_caught(self):
        _, order, receipt = self._setup()
        invoice = PurchaseInvoice("INV-1", "extal", "PO-1", JAN,
                                  [InvoiceLine("4301", 372.0, 4650.0)])
        match = three_way_match(invoice, order, [receipt])
        assert match.failures[0].result is MatchResult.PRICE_OVER

    def test_a_stock_length_overdelivery_is_within_tolerance(self):
        """An order for 372 m arriving as 378 m is normal, not an exception."""
        stock = StockLedger([StockItem("4301", "Frame")])
        order = PurchaseOrder("PO-1", "extal", JAN, [OrderLine("4301", 372.0, 4150.0)])
        place(order, stock)
        receipt = GoodsReceipt("GRN-1", "PO-1", JAN, [ReceiptLine("4301", 378.0)])
        receive(order, receipt, stock)
        invoice = PurchaseInvoice("INV-1", "extal", "PO-1", JAN,
                                  [InvoiceLine("4301", 378.0, 4150.0)])
        assert three_way_match(invoice, order, [receipt]).ok

    def test_an_invoice_for_goods_never_received_is_caught(self):
        _, order, _ = self._setup()
        invoice = PurchaseInvoice("INV-1", "extal", "PO-1", JAN,
                                  [InvoiceLine("4301", 100.0, 4150.0)])
        assert three_way_match(invoice, order, []).failures[0].result is MatchResult.NO_RECEIPT

    def test_an_item_never_ordered_is_caught(self):
        _, order, receipt = self._setup()
        invoice = PurchaseInvoice("INV-1", "extal", "PO-1", JAN,
                                  [InvoiceLine("9999", 10.0, 100.0)])
        assert three_way_match(invoice, order, [receipt]).failures[0].result is MatchResult.NO_ORDER


# --------------------------------------------------------------------------- #
# Sales and VAT
# --------------------------------------------------------------------------- #
class TestSales:
    @pytest.mark.parametrize(
        "on,expected",
        [
            (date(2014, 1, 1), 0.18),
            (date(2015, 10, 1), 0.17),
            (date(2024, 6, 1), 0.17),
            (date(2025, 1, 1), 0.18),
            (date(2026, 3, 1), 0.18),
        ],
    )
    def test_the_rate_is_the_one_in_force_on_the_day(self, on, expected):
        assert vat_rate(on) == expected

    def test_vat_is_computed_on_the_total_not_line_by_line(self):
        """Rounding each line and adding drifts from the tax authority's figure."""
        invoice = SalesInvoice("I-1", "Cust", date(2026, 3, 1), [
            SalesLine(f"line {n}", 1, money(33.33)) for n in range(7)
        ])
        assert invoice.net == money(233.31)
        assert invoice.vat == money(41.9958)      # 233.31 x 0.18 = 41.9958 -> 42.00
        assert invoice.vat == 4200

    def test_a_known_invoice_totals_correctly(self):
        invoice = SalesInvoice("I-1", "Cust", date(2026, 3, 1), [
            SalesLine("W1", 2, money(8_450)), SalesLine("D1", 1, money(12_900)),
        ])
        assert invoice.net == money(29_800)
        assert invoice.vat == money(5_364)        # 29,800 x 18%
        assert invoice.gross == money(35_164)

    def test_a_line_discount_reduces_the_net(self):
        invoice = SalesInvoice("I-1", "Cust", date(2026, 3, 1), [
            SalesLine("W1", 1, money(10_000), discount=0.1),
        ])
        assert invoice.net == money(9_000)

    def test_a_credit_note_keeps_the_original_rate(self):
        """Crediting a 17% invoice must undo 17%, not today's 18%."""
        original = SalesInvoice("I-1", "Cust", date(2024, 6, 1),
                                [SalesLine("W1", 1, money(10_000))])
        assert original.vat_rate == 0.17
        note = credit_note(original, "C-1", date(2026, 3, 1))
        assert note.vat_rate == 0.17
        assert note.gross == -original.gross

    def test_a_credit_note_returns_the_ledger_to_where_it_was(self):
        from profileos.erp import post_sales_invoice

        ledger = Ledger()
        invoice = SalesInvoice("I-1", "Cust", date(2026, 3, 1),
                               [SalesLine("W1", 1, money(10_000))])
        post_sales_invoice(invoice, ledger)
        post_sales_invoice(credit_note(invoice, "C-1", date(2026, 4, 1)), ledger)
        ledger.check()
        assert ledger.balance("1200") == 0
        assert ledger.balance("4100") == 0
        assert ledger.balance("2400") == 0

    def test_aged_debtors_bucket_by_how_late_they_are(self):
        from profileos.erp import aged_debtors

        invoices = [
            SalesInvoice("I-1", "A", date(2026, 1, 1), [SalesLine("x", 1, money(1_000))]),
            SalesInvoice("I-2", "A", date(2026, 3, 1), [SalesLine("x", 1, money(2_000))]),
        ]
        rows = aged_debtors(invoices, {}, date(2026, 4, 1))
        assert len(rows) == 1
        # I-1 fell due 31 Jan: 60 days late. I-2 fell due 31 Mar, so on 1 April
        # it is one day late — overdue, not current.
        assert rows[0].days_60 == money(1_180)
        assert rows[0].days_30 == money(2_360)
        assert rows[0].current == 0

    def test_a_payment_on_account_clears_the_oldest_first(self):
        from profileos.erp import aged_debtors

        invoices = [
            SalesInvoice("I-1", "A", date(2026, 1, 1), [SalesLine("x", 1, money(1_000))]),
            SalesInvoice("I-2", "A", date(2026, 3, 1), [SalesLine("x", 1, money(2_000))]),
        ]
        rows = aged_debtors(invoices, {"A": money(1_180)}, date(2026, 4, 1))
        assert rows[0].days_60 == 0        # the oldest is cleared first
        assert rows[0].total == money(2_360)


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
class TestScheduling:
    def test_the_working_week_is_sunday_to_thursday(self):
        calendar = Calendar()
        assert calendar.is_working(date(2026, 3, 15))       # Sunday
        assert calendar.is_working(date(2026, 3, 19))       # Thursday
        assert calendar.hours_on(date(2026, 3, 20)) == 4.0  # Friday, half day
        assert not calendar.is_working(date(2026, 3, 21))   # Saturday

    def test_a_holiday_closes_the_shop(self):
        holiday = date(2026, 4, 2)
        calendar = Calendar(holidays=frozenset({holiday}))
        assert not calendar.is_working(holiday)
        assert calendar.next_working_day(holiday) > holiday

    def test_glass_lead_time_runs_beside_the_shop_work_not_after_it(self):
        """Ordering glass is waiting, and a shop does not wait to start cutting.

        The same job without glass must finish far sooner; the same job with
        glass must not have the lead time added on top of the frame work.
        """
        glazed = JobDemand("J1", elements=10, cuts=120, machining_operations=200, panes=14)
        solid = JobDemand("J2", elements=10, cuts=120, machining_operations=200, panes=0)
        start = date(2026, 3, 16)
        with_glass = Scheduler().schedule([glazed], start=start).completion["J1"]
        without = Scheduler().schedule([solid], start=start).completion["J2"]
        assert without < with_glass
        # Lead time is 10 calendar days; the finish must sit just past it, not
        # a fortnight past it.
        assert (with_glass - start).days <= 14

    def test_capacity_is_finite_so_a_second_job_waits(self):
        """The same job scheduled alone and behind another must not finish together."""
        big = JobDemand("A", elements=60, cuts=900, machining_operations=1500, panes=0)
        second = JobDemand("B", elements=60, cuts=900, machining_operations=1500, panes=0)
        start = date(2026, 3, 16)
        alone = Scheduler().schedule([second], start=start).completion["B"]
        queued = Scheduler().schedule([big, second], start=start).completion["B"]
        assert queued > alone

    def test_earliest_due_date_ordering(self):
        jobs = [
            JobDemand("late", elements=20, cuts=240, machining_operations=400,
                      panes=0, due=date(2026, 5, 1)),
            JobDemand("soon", elements=20, cuts=240, machining_operations=400,
                      panes=0, due=date(2026, 3, 20)),
        ]
        plan = Scheduler().schedule(jobs, start=date(2026, 3, 16))
        assert plan.completion["soon"] <= plan.completion["late"]

    def test_lateness_is_reported_rather_than_hidden(self):
        job = JobDemand("J", elements=200, cuts=3000, machining_operations=5000,
                        panes=260, due=date(2026, 3, 20), name="Big tower")
        plan = Scheduler().schedule([job], start=date(2026, 3, 16))
        assert plan.late["J"] > 0
        assert plan.warnings and "after the promised" in plan.warnings[0]

    def test_utilisation_never_exceeds_capacity(self):
        jobs = [
            JobDemand(f"J{n}", elements=15, cuts=180, machining_operations=300, panes=20)
            for n in range(6)
        ]
        from profileos.erp import DEFAULT_WORK_CENTRES

        plan = Scheduler().schedule(jobs, start=date(2026, 3, 16))
        for row in plan.utilisation(DEFAULT_WORK_CENTRES):
            assert row["utilisation_pct"] <= 100.0 + 1e-6, row

    def test_a_bottleneck_is_identified(self):
        from profileos.erp import DEFAULT_WORK_CENTRES

        jobs = [
            JobDemand(f"J{n}", elements=15, cuts=400, machining_operations=60, panes=6)
            for n in range(4)
        ]
        plan = Scheduler().schedule(jobs, start=date(2026, 3, 16))
        assert plan.bottleneck(DEFAULT_WORK_CENTRES)["code"] == "SAW"

    def test_latest_start_leaves_room_for_the_whole_job(self):
        job = JobDemand("J", elements=10, cuts=120, machining_operations=200, panes=14)
        due = date(2026, 4, 10)
        start = Scheduler().latest_start(job, due)
        assert start < due
        finish = Scheduler().schedule([job], start=start).completion["J"]
        assert finish <= due

    def test_work_content_scales_with_the_job(self):
        times = StandardTimes()
        small = JobDemand("S", elements=1, cuts=4, machining_operations=8, panes=1)
        large = JobDemand("L", elements=40, cuts=160, machining_operations=320, panes=40)
        from profileos.erp import Operation

        assert (
            large.hours(times)[Operation.ASSEMBLY]
            > small.hours(times)[Operation.ASSEMBLY] * 10
        )


# --------------------------------------------------------------------------- #
# The whole company
# --------------------------------------------------------------------------- #
class TestCompany:
    def _shop(self) -> Company:
        shop = Company(name="Test Fabricators")
        shop.add_item(StockItem("4301", "Frame", supplier_id="extal",
                                reorder_quantity=6.0, lead_time_days=12))
        shop.add_item(StockItem("4302", "Sash", supplier_id="extal",
                                reorder_quantity=6.0, lead_time_days=12))
        return shop

    def test_order_to_cash_leaves_the_books_consistent(self):
        shop = self._shop()
        shop.receive_stock("4301", 120.0, 4090.0, on=date(2026, 2, 1))
        _, orders = shop.plan_purchases(
            {"4301": 486.0, "4302": 312.0},
            {"4301": 4150.0, "4302": 4790.0},
            on=date(2026, 3, 2),
        )
        order = orders[0]
        shop.place_order(order)
        shop.receive_delivery(GoodsReceipt("GRN-1", order.order_id, date(2026, 3, 16), [
            ReceiptLine("4301", 372.0), ReceiptLine("4302", 312.0),
        ]))
        invoice = PurchaseInvoice("EXT-1", "extal", order.order_id, date(2026, 3, 18), [
            InvoiceLine("4301", 372.0, 4150.0), InvoiceLine("4302", 312.0, 4790.0),
        ])
        shop.book_purchase_invoice(invoice)
        shop.issue_to_job("4301", 400.0, "P-1", on=date(2026, 3, 20))
        sale = SalesInvoice("INV-1", "Ariel", date(2026, 4, 1),
                            [SalesLine("Windows", 1, money(96_400))])
        shop.invoice(sale)
        shop.collect("Ariel", sale.gross, date(2026, 4, 25))
        shop.pay("extal", invoice.gross, date(2026, 4, 20))

        report = shop.audit()
        assert report["ledger_balanced"] and report["stock_reconciled"]
        assert report["stock_accounts_agree"]

    def test_an_over_invoice_is_refused_before_it_reaches_the_ledger(self):
        shop = self._shop()
        order = PurchaseOrder("PO-1", "extal", JAN, [OrderLine("4301", 372.0, 4150.0)])
        shop.place_order(order)
        shop.receive_delivery(GoodsReceipt("GRN-1", "PO-1", JAN,
                                           [ReceiptLine("4301", 372.0)]))
        before = len(shop.ledger.entries)
        with pytest.raises(PurchasingError):
            shop.book_purchase_invoice(
                PurchaseInvoice("EXT-1", "extal", "PO-1", JAN,
                                [InvoiceLine("4301", 500.0, 4150.0)])
            )
        assert len(shop.ledger.entries) == before

    def test_a_mismatch_can_be_accepted_but_only_deliberately(self):
        shop = self._shop()
        order = PurchaseOrder("PO-1", "extal", JAN, [OrderLine("4301", 372.0, 4150.0)])
        shop.place_order(order)
        shop.receive_delivery(GoodsReceipt("GRN-1", "PO-1", JAN,
                                           [ReceiptLine("4301", 372.0)]))
        entry, match = shop.book_purchase_invoice(
            PurchaseInvoice("EXT-1", "extal", "PO-1", JAN,
                            [InvoiceLine("4301", 372.0, 4650.0)]),
            force=True,
        )
        assert entry is not None
        assert not match.ok      # it is still recorded as a mismatch
        shop.ledger.check()

    def test_a_delivery_without_an_order_is_refused(self):
        shop = self._shop()
        with pytest.raises(CompanyError):
            shop.receive_delivery(GoodsReceipt("GRN-1", "PO-NOPE", JAN,
                                               [ReceiptLine("4301", 10.0)]))

    def test_document_numbers_are_sequential_within_a_year(self):
        shop = self._shop()
        numbers = [shop.next_number("INV", year=2026) for _ in range(3)]
        assert numbers == ["INV-2026-0001", "INV-2026-0002", "INV-2026-0003"]
        assert shop.next_number("INV", year=2027) == "INV-2027-0001"

    def test_the_audit_notices_when_the_accounts_drift_from_the_racks(self):
        """Post to the stock account behind the stock book's back."""
        shop = self._shop()
        shop.receive_stock("4301", 100.0, 4000.0, on=JAN)
        shop.audit()
        shop.ledger.post_simple("ODD", JAN, "unexplained", "1300", "2100", money(500))
        with pytest.raises(CompanyError) as excinfo:
            shop.audit()
        assert "out by" in str(excinfo.value)

    def test_the_vat_return_comes_from_the_accounts(self):
        shop = self._shop()
        sale = SalesInvoice("INV-1", "Ariel", date(2026, 2, 10),
                            [SalesLine("Windows", 1, money(50_000))])
        shop.invoice(sale)
        result = shop.vat_return(date(2026, 1, 1), date(2026, 3, 31))
        assert result["output_vat"] == money(9_000)      # 50,000 x 18%
        assert result["payable"] == money(9_000)

    def test_a_company_can_be_built_for_the_operator(self):
        from profileos.branding import set_active_brand
        from profileos.erp import company_for_brand

        try:
            set_active_brand("dadi")
            shop = company_for_brand()
            assert "דאדי" in shop.name
            assert shop.currency == "ILS"
        finally:
            set_active_brand("profileos")

    def test_scheduling_from_built_elements(self):
        from profileos.elements import Opening, build_elements
        from profileos.erp import demand_from_builds

        builds = build_elements([
            Opening(element_id="W1", name="W", width=1600.0, height=1400.0, quantity=6),
        ])
        demand = demand_from_builds(builds, "JOB-1", due=date(2026, 5, 1))
        assert demand.elements == 6
        assert demand.cuts > 0 and demand.panes > 0
        plan = Scheduler().schedule([demand], start=date(2026, 3, 16))
        assert plan.completion["JOB-1"] <= date(2026, 5, 1)
