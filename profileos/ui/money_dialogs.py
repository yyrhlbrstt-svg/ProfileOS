"""The windows that handle a job's money: the price book, and the job's account.

Each change is written at once, onto the copy of the job that is on disk now,
so a payment recorded here is never undone by another screen saving an older
copy of the same job.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .theme import METRICS
from .widgets import DataTable, FieldGrid

TRADES = (("both", "הכול"), ("aluminium", "אלומיניום"), ("plumbing", "אינסטלציה"))


def _money(value: float) -> str:
    return f"⁦{value:,.0f} ₪⁩" if value else "—"


def _warning_colour(widget: QWidget) -> str:
    """The palette's warning colour, from the window the dialog belongs to."""
    window = widget.parent().window() if widget.parent() is not None else None
    colours = getattr(window, "colours", None)
    return getattr(colours, "warning", "#B25000")


def _warn(parent: QWidget, title: str, exc: Exception) -> None:
    box = QMessageBox(parent)
    box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(str(exc))
    box.exec()


# --------------------------------------------------------------------------- #
# Picking from the price book
# --------------------------------------------------------------------------- #
class PriceItemPicker(QDialog):
    """Search the price book and choose one item and a quantity."""

    def __init__(self, book: Any, parent: QWidget | None = None, *, trade: str = "both") -> None:
        super().__init__(parent)
        self.setWindowTitle("הוספה מהמחירון")
        self.setMinimumSize(620, 480)
        self._book = book
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש: סתימה, דוד, מנוף, פירוק…")
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search, 1)
        self.trade = QComboBox()
        for key, label in TRADES:
            self.trade.addItem(label, key)
        self.trade.setCurrentIndex(max(0, self.trade.findData(trade)))
        row.addWidget(self.trade)
        layout.addLayout(row)

        self.table = DataTable(["קוד", "עבודה", "יחידה", "מחיר", "קטגוריה"],
                               empty_text="אין פריט כזה — אפשר לכתוב שורה חופשית")
        self.table.stretch(1)
        self.table.doubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self.table, 1)

        grid = FieldGrid()
        self.quantity = QDoubleSpinBox()
        self.quantity.setRange(0.01, 100000)
        self.quantity.setDecimals(2)
        self.quantity.setValue(1)
        grid.add("כמות", self.quantity)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 10_000_000)
        self.price.setDecimals(2)
        self.price.setSuffix(" ₪")
        grid.add("מחיר ליחידה", self.price)
        self.update_book = QCheckBox("לשמור את המחיר הזה גם במחירון")
        grid.add("", self.update_book)
        layout.addWidget(grid)
        self.note = QLabel("")
        self.note.setObjectName("FieldWarning")
        self.note.setWordWrap(True)
        self.note.hide()
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("הוסף")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._shown: list[Any] = []
        self.search.textChanged.connect(lambda _t: self._fill())
        self.trade.currentIndexChanged.connect(lambda _i: self._fill())
        self.table.itemSelectionChanged.connect(self._picked)
        self._fill()
        self.search.setFocus()

    def _fill(self) -> None:
        self._shown = self._book.search(self.search.text(), self.trade.currentData() or "")
        self.table.set_rows([
            [item.code, item.name, item.unit,
             _money(item.price) if item.priced else "לא הוזן", item.category]
            for item in self._shown
        ], numeric_columns=(3,))
        if self._shown:
            self.table.setCurrentCell(0, 0)

    def _picked(self) -> None:
        item = self.item()
        if item is None:
            return
        self.price.setValue(item.price)
        # A first price goes into the book by default; changing an existing
        # one for a single customer does not, unless asked.
        self.update_book.setChecked(not item.priced)
        self.note.setVisible(not item.priced)
        self.note.setText(
            "לפריט הזה עוד לא הוזן מחיר במחירון. הקלידו מחיר — הוא יישמר גם במחירון."
        )

    def item(self) -> Any:
        row = self.table.currentRow()
        return self._shown[row] if 0 <= row < len(self._shown) else None


# --------------------------------------------------------------------------- #
# The price book itself
# --------------------------------------------------------------------------- #
class PriceBookDialog(QDialog):
    """Every kind of work the shop charges for, and its price."""

    def __init__(self, book: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("המחירון של העסק")
        self.setMinimumSize(820, 560)
        self._book = book
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))

        intro = QLabel(
            "המחירים שלכם לכל סוג עבודה — לא הצעת מחיר של ספק. פריט בלי מחיר "
            "מסומן ״לא הוזן״ בכל מקום שמשתמשים בו, עד שמזינים לו מחיר. "
            "לחיצה כפולה על שורה מעדכנת את המחיר."
        )
        intro.setObjectName("Hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש…")
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search, 1)
        self.trade = QComboBox()
        for key, label in TRADES:
            self.trade.addItem(label, key)
        row.addWidget(self.trade)
        add = QPushButton("פריט חדש")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_item)
        row.addWidget(add)
        layout.addLayout(row)

        self.table = DataTable(
            ["קוד", "עבודה", "יחידה", "מחיר", "מקור המחיר", "קטגוריה"],
            empty_text="המחירון ריק",
        )
        self.table.stretch(1)
        self.table.doubleClicked.connect(lambda _i: self.edit_price())
        layout.addWidget(self.table, 1)
        self.summary = QLabel("")
        self.summary.setObjectName("Hint")
        layout.addWidget(self.summary)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("סגור")
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self._shown: list[Any] = []
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.trade.currentIndexChanged.connect(lambda _i: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        self._shown = self._book.search(self.search.text(), self.trade.currentData() or "")
        colours = {}
        rows = []
        for index, item in enumerate(self._shown):
            rows.append([
                item.code, item.name, item.unit,
                _money(item.price) if item.priced else "לא הוזן",
                item.source or "—", item.category,
            ])
            if not item.priced:
                colours[(index, 3)] = _warning_colour(self)
        self.table.set_rows(rows, numeric_columns=(3,), colours=colours)
        if getattr(self._book, "damaged", None) is not None:
            self.summary.setText("המחירון לא נקרא — מוצג ריק לקריאה בלבד, ושום דבר לא יישמר")
            return
        missing = len(self._book.unpriced())
        self.summary.setText(
            f"⁦{len(self._book)}⁩ פריטים במחירון"
            + (f" · ⁦{missing}⁩ עוד בלי מחיר" if missing else " · לכולם יש מחיר")
        )

    def edit_price(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self._shown):
            return
        item = self._shown[row]
        price, ok = QInputDialog.getDouble(
            self, "מחיר", f"{item.name} — מחיר ל{item.unit} לפני מע״מ:",
            item.price, 0, 10_000_000, 2,
        )
        if not ok:
            return
        try:
            self._book.set_price(item.code, price)
        except Exception as exc:  # noqa: BLE001
            _warn(self, "המחיר לא נשמר", exc)
        self.refresh()

    def add_item(self) -> None:
        from ..quoting.pricebook import PriceItem

        dialog = QDialog(self)
        dialog.setWindowTitle("פריט חדש במחירון")
        layout = QVBoxLayout(dialog)
        grid = FieldGrid()
        name = QLineEdit()
        name.setPlaceholderText("למשל: החלפת מצוף בניאגרה")
        unit = QComboBox()
        unit.setEditable(True)
        unit.addItems(["יח׳", "מ״א", "מ״ר", "שעה", "ביקור", "קומפלט"])
        trade = QComboBox()
        for key, label in TRADES[1:]:
            trade.addItem(label, key)
        trade.addItem("שניהם", "both")
        price = QDoubleSpinBox()
        price.setRange(0, 10_000_000)
        price.setSuffix(" ₪")
        category = QLineEdit()
        grid.add("עבודה", name)
        grid.add("יחידה", unit)
        grid.add("תחום", trade)
        grid.add("מחיר", price)
        grid.add("קטגוריה", category)
        layout.addWidget(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("הוסף")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not name.text().strip():
            return
        try:
            self._book.add(PriceItem(
                code=self._book.next_code(trade.currentData()), name=name.text().strip(),
                unit=unit.currentText().strip() or "יח׳", price=price.value(),
                trade=trade.currentData(), category=category.text().strip(),
                source="הוזן במחירון" if price.value() else "",
            ))
        except Exception as exc:  # noqa: BLE001
            _warn(self, "הפריט לא נוסף", exc)
        self.refresh()


# --------------------------------------------------------------------------- #
# One job's account
# --------------------------------------------------------------------------- #
class JobAccountDialog(QDialog):
    """Work, extras, payments and invoices of one job, and what is still owed."""

    def __init__(self, job_id: str, store: Any, parent: QWidget | None = None, *,
                 cheques: Callable[[], Any] | None = None,
                 pricebook: Callable[[], Any] | None = None) -> None:
        super().__init__(parent)
        self._job_id = job_id
        self._store = store
        self._cheques = cheques or (lambda: [])
        self._pricebook = pricebook
        self.setMinimumSize(860, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))

        self.headline = QLabel("")
        self.headline.setObjectName("HomeNext")
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)
        self.breakdown = QLabel("")
        self.breakdown.setObjectName("Hint")
        self.breakdown.setWordWrap(True)
        layout.addWidget(self.breakdown)

        tabs = QTabWidget()
        tabs.addTab(self._lines_tab(), "עבודות ותוספות")
        tabs.addTab(self._payments_tab(), "תשלומים")
        tabs.addTab(self._invoices_tab(), "חשבוניות")
        tabs.addTab(self._bills_tab(), "חשבונות חלקיים")
        layout.addWidget(tabs, 1)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("סגור")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    # -- the job, always the copy on disk ------------------------------------- #
    def job(self) -> Any:
        return self._store.load(self._job_id)

    def _change(self, change: Callable[[Any], None], what: str) -> bool:
        try:
            job = self.job()
            change(job)
            job.touch()
            self._store.save(job)
        except Exception as exc:  # noqa: BLE001
            _warn(self, f"{what} — לא נשמר", exc)
            return False
        self.refresh()
        return True

    # -- tabs ------------------------------------------------------------------ #
    def _lines_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        for label, handler, primary in (
            ("הוסף מהמחירון", self.add_from_book, True),
            ("שורה חופשית", self.add_free_line, False),
            ("סמן תוספת כמאושרת", self.approve_line, False),
            ("הסר שורה", self.remove_line, False),
        ):
            button = QPushButton(label)
            if primary:
                button.setObjectName("Primary")
            button.clicked.connect(handler)
            row.addWidget(button)
        self.as_extra = QCheckBox("תוספת שהתבקשה תוך כדי עבודה")
        self.as_extra.setChecked(True)
        row.addWidget(self.as_extra)
        row.addStretch(1)
        layout.addLayout(row)
        self.lines_table = DataTable(
            ["סוג", "עבודה", "כמות", "מחיר", "סה״כ", "אישור", "נוסף"],
            empty_text="אין עבודות מעבר לפתחים — הוסיפו מהמחירון",
        )
        self.lines_table.stretch(1)
        layout.addWidget(self.lines_table, 1)
        return page

    def _payments_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        add = QPushButton("רישום תשלום")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_payment)
        row.addWidget(add)
        hint = QLabel("צ׳קים נרשמים בעמוד ״גבייה״ עם מספר הפרויקט, ומחושבים כאן לבד.")
        hint.setObjectName("Hint")
        row.addWidget(hint, 1)
        layout.addLayout(row)
        self.payments_table = DataTable(
            ["תאריך", "סכום", "אמצעי", "ניכוי במקור", "אסמכתה"],
            empty_text="עוד לא נרשם תשלום",
        )
        layout.addWidget(self.payments_table, 1)
        return page

    def _invoices_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        add = QPushButton("רישום חשבונית")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_invoice)
        row.addWidget(add)
        hint = QLabel(
            "החשבונית עצמה מופקת בתוכנת החשבוניות (Morning, iCount). כאן נרשמים "
            "המספר ומספר ההקצאה, כדי לדעת מה הוצא ומה חסר."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        layout.addLayout(row)
        self.invoices_table = DataTable(
            ["מספר", "תאריך", "סכום לפני מע״מ", "לקוח עסקי", "מספר הקצאה"],
            empty_text="עוד לא נרשמה חשבונית",
        )
        layout.addWidget(self.invoices_table, 1)
        return page

    def _bills_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        add = QPushButton("חשבון חלקי חדש")
        add.setObjectName("Primary")
        add.clicked.connect(self.add_bill)
        row.addWidget(add)
        copy = QPushButton("העתק חשבון נבחר")
        copy.setToolTip("לשליחה למשרד הקבלן בוואטסאפ או במייל")
        copy.clicked.connect(self.copy_bill)
        row.addWidget(copy)
        self.retention_label = QLabel("")
        self.retention_label.setObjectName("Hint")
        row.addWidget(self.retention_label, 1)
        layout.addLayout(row)
        self.bills_table = DataTable(
            ["מס׳", "תאריך", "מצטבר", "חשבון זה", "מע״מ", "עיכבון", "ניכוי במקור", "לתשלום"],
            empty_text="לעבודה מול קבלן ראשי: כל חודש — האחוז המצטבר שאושר",
        )
        layout.addWidget(self.bills_table, 1)
        return page

    # -- showing --------------------------------------------------------------- #
    def refresh(self) -> None:
        from ..projects.account import invoices_missing_allocation, job_account

        try:
            job = self.job()
        except Exception as exc:  # noqa: BLE001
            self.headline.setText(f"הפרויקט לא נקרא: {exc}")
            return
        self.setWindowTitle(f"חשבון הפרויקט — {job.job_id} · {job.name}")
        account = job_account(job, self._cheques())
        self.headline.setText(account.describe())
        parts = [
            f"הצעה ⁦{account.contract_net:,.0f} ₪⁩",
            f"תוספות מאושרות ⁦{account.extras_net:,.0f} ₪⁩",
            f"מע״מ ⁦{account.vat:,.0f} ₪⁩",
            f"התקבל ⁦{account.received:,.0f} ₪⁩",
        ]
        if account.in_cheques:
            parts.append(f"בצ׳קים שטרם נפרעו ⁦{account.in_cheques:,.0f} ₪⁩")
        if account.bounced:
            parts.append(f"צ׳קים שחזרו ⁦{account.bounced:,.0f} ₪⁩")
        if account.pending_extras_net:
            parts.append(f"תוספות שלא אושרו ⁦{account.pending_extras_net:,.0f} ₪⁩")
        if account.not_invoiced_net:
            parts.append(f"טרם הוצאה חשבונית על ⁦{account.not_invoiced_net:,.0f} ₪⁩")
        self.breakdown.setText(" · ".join(parts))

        self._lines = list(job.lines)
        colours = {}
        for index, line in enumerate(self._lines):
            if line.kind.value == "extra" and not line.approved:
                colours[(index, 5)] = _warning_colour(self)
        self.lines_table.set_rows([
            [line.kind.hebrew, line.description, f"⁦{line.quantity:g}⁩ {line.unit}",
             _money(line.unit_price), _money(line.total),
             line.approved or ("לא אושרה" if line.kind.value == "extra" else "—"),
             line.added_on]
            for line in self._lines
        ], numeric_columns=(3, 4), colours=colours)

        self.payments_table.set_rows([
            [payment.on, _money(payment.amount), payment.method.hebrew,
             _money(payment.withheld), payment.reference or "—"]
            for payment in job.payments
        ], numeric_columns=(1, 3))

        from ..projects.progress import bill_figures, retention_held

        self._bills = bill_figures(job)
        self.bills_table.set_rows([
            [str(f.number), f.on, f"⁦{f.cumulative_pct:g}%⁩", _money(f.this_net),
             _money(f.vat), _money(f.retention), _money(f.withholding), _money(f.to_pay)]
            for f in self._bills
        ], numeric_columns=(3, 4, 5, 6, 7))
        held = retention_held(job)
        self.retention_label.setText(
            f"עיכבון שמוחזק אצל הקבלן: ⁦{held:,.0f} ₪⁩ — לגבות במסירה" if held else ""
        )

        missing = {id(invoice) for invoice in invoices_missing_allocation(job)}
        self.invoices_table.set_rows([
            [invoice.number, invoice.issued_on, _money(invoice.net),
             "כן" if invoice.business_customer else "לא",
             invoice.allocation_number or ("חסר!" if id(invoice) in missing else "—")]
            for invoice in job.invoices
        ], numeric_columns=(2,))

    # -- changes --------------------------------------------------------------- #
    def _line_kind(self) -> Any:
        from ..projects import LineKind

        return LineKind.EXTRA if self.as_extra.isChecked() else LineKind.QUOTED

    def add_from_book(self) -> None:
        from ..projects import JobLine

        if self._pricebook is None:
            return
        book = self._pricebook()
        trade = getattr(self.job(), "trade", None)
        picker = PriceItemPicker(book, self, trade=getattr(trade, "value", "both"))
        if picker.exec() != QDialog.DialogCode.Accepted or picker.item() is None:
            return
        item = picker.item()
        price = picker.price.value()
        if picker.update_book.isChecked() and price > 0 and price != item.price:
            try:
                book.set_price(item.code, price)
            except Exception:  # noqa: BLE001 - the line still carries the price
                pass
        line = JobLine(
            kind=self._line_kind(), description=item.name,
            quantity=picker.quantity.value(), unit=item.unit, unit_price=price,
            item_code=item.code,
        )
        self._change(lambda job: job.lines.append(line), "השורה")

    def add_free_line(self) -> None:
        from ..projects import JobLine

        text, ok = QInputDialog.getText(self, "שורה חופשית", "מה העבודה?")
        if not ok or not text.strip():
            return
        price, ok = QInputDialog.getDouble(
            self, "מחיר", "מחיר לפני מע״מ:", 0, 0, 10_000_000, 2
        )
        if not ok:
            return
        line = JobLine(kind=self._line_kind(), description=text.strip(), unit_price=price)
        self._change(lambda job: job.lines.append(line), "השורה")

    def _selected_line(self) -> Any:
        row = self.lines_table.currentRow()
        return self._lines[row] if 0 <= row < len(getattr(self, "_lines", [])) else None

    def approve_line(self) -> None:
        line = self._selected_line()
        if line is None:
            return
        how, ok = QInputDialog.getText(
            self, "אישור תוספת", "איך אושרה?", text="אושר בוואטסאפ"
        )
        if not ok:
            return
        from datetime import date

        stamp = f"{how.strip() or 'אושר'} ⁦{date.today():%d/%m}⁩"

        def change(job: Any) -> None:
            for candidate in job.lines:
                if candidate.line_id == line.line_id:
                    candidate.approved = stamp

        self._change(change, "האישור")

    def remove_line(self) -> None:
        line = self._selected_line()
        if line is None:
            return
        self._change(
            lambda job: setattr(job, "lines",
                                [c for c in job.lines if c.line_id != line.line_id]),
            "ההסרה",
        )

    def add_payment(self) -> None:
        from ..projects import Payment, PaymentMethod

        dialog = QDialog(self)
        dialog.setWindowTitle("רישום תשלום")
        layout = QVBoxLayout(dialog)
        grid = FieldGrid()
        amount = QDoubleSpinBox()
        amount.setRange(0.01, 100_000_000)
        amount.setSuffix(" ₪")
        method = QComboBox()
        for option in PaymentMethod:
            method.addItem(option.hebrew, option.value)
        withheld = QDoubleSpinBox()
        withheld.setRange(0, 100_000_000)
        withheld.setSuffix(" ₪")
        withheld.setToolTip("מס שהלקוח ניכה במקור — נחשב כשולם")
        reference = QLineEdit()
        reference.setPlaceholderText("מספר אסמכתה / העברה")
        grid.add("סכום שהתקבל", amount)
        grid.add("אמצעי", method)
        grid.add("ניכוי במקור", withheld)
        grid.add("אסמכתה", reference)
        layout.addWidget(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("רשום")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payment = Payment(
            amount=round(amount.value(), 2), method=PaymentMethod(method.currentData()),
            withheld=round(withheld.value(), 2), reference=reference.text().strip(),
        )
        self._change(lambda job: job.payments.append(payment), "התשלום")

    def add_bill(self) -> None:
        from ..projects.progress import next_bill

        job = self.job()
        last = job.progress_bills[-1] if job.progress_bills else None
        pct, ok = QInputDialog.getDouble(
            self, "חשבון חלקי", "אחוז מצטבר שאושר עד עכשיו:",
            min(100.0, (last.cumulative_pct if last else 0) + 10), 0.1, 100, 1,
        )
        if not ok:
            return
        retention, ok = QInputDialog.getDouble(
            self, "עיכבון", "אחוז עיכבון:", last.retention_pct if last else 5.0, 0, 20, 1
        )
        if not ok:
            return
        withholding, ok = QInputDialog.getDouble(
            self, "ניכוי במקור", "אחוז ניכוי מס במקור (לפי האישור שלכם):",
            last.withholding_pct if last else 0.0, 0, 50, 1,
        )
        if not ok:
            return
        try:
            bill = next_bill(job, pct, retention_pct=retention, withholding_pct=withholding)
        except Exception as exc:  # noqa: BLE001
            _warn(self, "החשבון לא נוסף", exc)
            return
        self._change(lambda fresh: fresh.progress_bills.append(bill), "החשבון החלקי")

    def copy_bill(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ..branding import active_brand
        from ..projects.progress import bill_text

        row = self.bills_table.currentRow()
        bills = getattr(self, "_bills", [])
        if not 0 <= row < len(bills):
            return
        QApplication.clipboard().setText(
            bill_text(self.job(), bills[row], brand_name=active_brand().display_name)
        )

    def add_invoice(self) -> None:
        from ..projects import InvoiceRecord

        dialog = QDialog(self)
        dialog.setWindowTitle("רישום חשבונית")
        layout = QVBoxLayout(dialog)
        grid = FieldGrid()
        number = QLineEdit()
        net = QDoubleSpinBox()
        net.setRange(0, 100_000_000)
        net.setSuffix(" ₪")
        business = QCheckBox("הלקוח עוסק (יקזז מע״מ)")
        allocation = QLineEdit()
        allocation.setPlaceholderText("מספר הקצאה מרשות המסים, אם נדרש")
        grid.add("מספר חשבונית", number)
        grid.add("סכום לפני מע״מ", net)
        grid.add("", business)
        grid.add("מספר הקצאה", allocation)
        layout.addWidget(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("רשום")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not number.text().strip():
            _warn(self, "החשבונית לא נרשמה", ValueError("חסר מספר חשבונית"))
            return
        if any(existing.number == number.text().strip() for existing in self.job().invoices):
            _warn(self, "החשבונית לא נרשמה",
                  ValueError(f"חשבונית מספר {number.text().strip()} כבר רשומה בפרויקט"))
            return
        invoice = InvoiceRecord(
            number=number.text().strip(), net=round(net.value(), 2),
            business_customer=business.isChecked(),
            allocation_number=allocation.text().strip(),
        )
        self._change(lambda job: job.invoices.append(invoice), "החשבונית")


# --------------------------------------------------------------------------- #
# Ordering the job's material
# --------------------------------------------------------------------------- #

def _supplier_named(name: str) -> Any:
    """The price list in force by its supplier's name, or None."""
    if not name:
        return None
    from ..quoting.suppliers import all_suppliers

    return next((s for s in all_suppliers() if s.name == name), None)


class MaterialOrderDialog(QDialog):
    """What to order for the job, from whom, by when — checked before it goes.

    The quantities are the cutting optimiser's whole bars and the bill of
    materials; each can be changed (the rack may already hold three bars of
    the frame) or left off. Prices are the list in force after the shop's
    discount, and a line no list prices says so instead of showing a number.
    """

    #: The person chose to send it now (WhatsApp) or only to record it.
    SEND, RECORD = "send", "record"

    def __init__(self, job: Any, proposal: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from dataclasses import replace

        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QDateEdit, QTableWidget, QTableWidgetItem

        self.setWindowTitle(f"הזמנת חומר — {job.name}")
        self.setMinimumSize(820, 560)
        self._proposal = proposal
        self.choice = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))

        grid = FieldGrid()
        self.supplier = QComboBox()
        self.supplier.setEditable(True)
        self.supplier.addItems(proposal.suppliers())
        self.supplier.lineEdit().setPlaceholderText("שם הספק")
        self.needed_by = QDateEdit()
        self.needed_by.setCalendarPopup(True)
        self.needed_by.setDisplayFormat("dd/MM/yyyy")
        self.needed_by.setDate(QDate.currentDate().addDays(7))
        self.note = QLineEdit()
        self.note.setPlaceholderText("הערה לספק: צבע, אספקה לאתר, איש קשר…")
        # A mis-cut or a scratched painted bar stops a job for a week: the
        # shop decides whether one spare bar per profile is worth it.
        self.spare = QCheckBox("להוסיף מוט רזרבי לכל פרופיל")
        grid.add("ספק", self.supplier)
        grid.add("נדרש עד", self.needed_by)
        grid.add("הערה", self.note)
        grid.add_span(self.spare)
        layout.addWidget(grid)

        self.table = QTableWidget(len(proposal.lines), 6)
        self.table.setHorizontalHeaderLabels(["להזמין", "מק״ט", "פריט", "כמות", "מחיר ליחידה", "ספק"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, line in enumerate(proposal.lines):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, tick)
            for column, text in (
                (1, line.code),
                (2, " · ".join(part for part in (
                    line.description if line.description != line.code else "",
                    f"מוטות של ⁦{line.length_mm / 1000:g}⁩ מ׳" if line.unit == "bar" and line.length_mm
                    else f"גלילים של ⁦{line.length_mm / 1000:g}⁩ מ׳" if line.unit == "roll" and line.length_mm
                    else f"ב{line.unit_hebrew}",
                ) if part)),
                (4, f"⁦{line.unit_price:,.2f} ₪⁩" if line.unit_price > 0 else "אין במחירון"),
                (5, line.supplier or "—"),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, column, item)
            quantity = QTableWidgetItem(f"{line.quantity:g}")
            quantity.setToolTip(f"ב{line.unit_hebrew} — אפשר לשנות; המחיר יחושב מחדש")
            self.table.setItem(row, 3, quantity)
            if line.unit == "bar":
                self.table.item(row, 2).setToolTip(
                    f"אורך המוט לפי {line.length_source}" if line.length_source
                    else "אורך המוט לפי הגדרת המפעל — המחירון לא אומר אותו"
                )
        self.table.resizeColumnsToContents()
        self._lines = [replace(line) for line in proposal.lines]
        self.table.itemChanged.connect(self._changed)
        layout.addWidget(self.table, 1)
        # One order is one supplier: lines another supplier's list prices are
        # left for that supplier's order, not sent to this one.
        self.supplier.currentTextChanged.connect(self._match_supplier)
        self.spare.toggled.connect(self._spare)

        self.summary = QLabel("")
        self.summary.setObjectName("Hint")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        warnings = list(proposal.warnings)
        earlier = [o for o in getattr(job, "material_orders", []) if o.state.value != "cancelled"]
        if earlier:
            warnings.insert(0, "כבר הוזמן לפרויקט: " + "; ".join(
                f"{o.supplier} — ⁦{len(o.lines)}⁩ שורות ({o.state.hebrew})" for o in earlier
            ) + ". הכמויות כאן הן לכל הפרויקט — הורידו את מה שכבר הוזמן.")
        warnings.append("הזכוכית מוזמנת לפי מידה בנפרד, ממסך ״זכוכית״.")
        hint = QLabel("\n".join(warnings))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        send = QPushButton("שליחה לספק בוואטסאפ")
        send.setObjectName("WhatsApp")
        send.clicked.connect(lambda: self._finish(self.SEND))
        record = QPushButton("רישום בלי שליחה")
        record.setToolTip("כבר הוזמן בטלפון: רק לרשום בפרויקט")
        record.clicked.connect(lambda: self._finish(self.RECORD))
        cancel = QPushButton("ביטול")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(send)
        buttons.addWidget(record)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self._match_supplier(self.supplier.currentText())
        self._update_total()

    def _spare(self, on: bool) -> None:
        """One more bar of each profile — on its longest-bar line — or one less."""
        seen: set[str] = set()
        for row, line in sorted(enumerate(self._lines), key=lambda pair: -pair[1].length_mm):
            if line.unit != "bar" or line.code in seen:
                continue
            seen.add(line.code)
            quantity = self._quantity(row) or 0.0
            self.table.item(row, 3).setText(f"{max(0.0, quantity + (1 if on else -1)):g}")

    def _changed(self, item: Any) -> None:
        """A quantity typed: price it again at that quantity (breaks, minimums)."""
        if item.column() == 3:
            quantity = self._quantity(item.row())
            line = self._lines[item.row()]
            if quantity is not None and quantity != line.quantity:
                from ..projects.materials import price_line

                line.quantity = quantity
                price_line(line, mass_per_metre=self._weights(),
                           supplier=_supplier_named(line.supplier))
                cell = self.table.item(item.row(), 4)
                self.table.blockSignals(True)
                cell.setText(f"⁦{line.unit_price:,.2f} ₪⁩" if line.unit_price > 0 else "אין במחירון")
                self.table.blockSignals(False)
        self._update_total()

    def _weights(self) -> Any:
        if getattr(self, "_weights_cache", None) is None:
            from ..quoting.pricelists import catalogue_weights

            self._weights_cache = catalogue_weights()
        return self._weights_cache

    def _match_supplier(self, supplier: str) -> None:
        wanted = " ".join(supplier.split()).casefold()
        for row, line in enumerate(self._proposal.lines):
            tick = self.table.item(row, 0)
            if tick is None or not line.supplier:
                continue  # unpriced lines go with whichever supplier is chosen
            mine = " ".join(line.supplier.split()).casefold() == wanted
            tick.setCheckState(Qt.CheckState.Checked if mine else Qt.CheckState.Unchecked)

    def invalid(self) -> list[str]:
        """Codes of ticked lines whose quantity is not a number."""
        bad = []
        for row in range(self.table.rowCount()):
            tick = self.table.item(row, 0)
            if tick is not None and tick.checkState() == Qt.CheckState.Checked \
                    and self._quantity(row) is None:
                bad.append(self.table.item(row, 1).text())
        return bad

    def _quantity(self, row: int) -> float | None:
        from ..migration.reader import to_number

        item = self.table.item(row, 3)
        value = to_number(item.text() if item is not None else "")
        return value if value is not None and value >= 0 else None

    def chosen(self) -> list[Any]:
        """The lines ticked, with the quantities as typed."""
        from dataclasses import replace

        lines = []
        for row, line in enumerate(getattr(self, "_lines", self._proposal.lines)):
            tick = self.table.item(row, 0)
            quantity = self._quantity(row)
            if tick is None or tick.checkState() != Qt.CheckState.Checked or not quantity:
                continue
            lines.append(replace(line, quantity=quantity))
        return lines

    def _update_total(self) -> None:
        lines = self.chosen()
        total = sum(line.total for line in lines)
        unpriced = sum(1 for line in lines if line.unit_price <= 0)
        bad = [self.table.item(r, 1).text() for r in range(self.table.rowCount())
               if self._quantity(r) is None]
        count = "שורה אחת" if len(lines) == 1 else f"⁦{len(lines)}⁩ שורות"
        text = f"{count} · לפי המחירונים: ⁦{total:,.0f} ₪⁩ לפני מע״מ, אחרי ההנחה"
        if unpriced:
            text += (" · שורה אחת בלי מחיר במחירון" if unpriced == 1
                     else f" · ⁦{unpriced}⁩ שורות בלי מחיר במחירון") + " — הספק יתמחר"
        if bad:
            text += " · כמות לא תקינה: " + ", ".join(f"⁦{code}⁩" for code in bad[:3])
        self.summary.setText(text)

    def _finish(self, choice: str) -> None:
        if not self.supplier.currentText().strip():
            _warn(self, "חסר ספק", ValueError("כתבו למי ההזמנה"))
            return
        bad = self.invalid()
        if bad:
            # A ticked line with no quantity would silently leave the order:
            # the supplier would get the order without the frame bars.
            _warn(self, "כמות חסרה", ValueError(
                "בשורות האלה הכמות ריקה או לא מספר: " + ", ".join(f"⁦{c}⁩" for c in bad)
                + ". תקנו את הכמות או הורידו את הסימון."
            ))
            return
        if not self.chosen():
            _warn(self, "אין מה להזמין", ValueError("סמנו לפחות שורה אחת עם כמות"))
            return
        self.choice = choice
        self.accept()

    def values(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier.currentText().strip(),
            "needed_by": self.needed_by.date().toString("yyyy-MM-dd"),
            "note": self.note.text().strip(),
            "lines": self.chosen(),
        }


#: Where an order may go from where it is. An order that arrived does not
#: become cancelled, and a cancelled one does not come back.
NEXT_STATES = {
    "sent": ("confirmed", "received", "cancelled"),
    "confirmed": ("received", "cancelled"),
    "received": (),
    "cancelled": (),
}


class MaterialOrdersDialog(QDialog):
    """The job's orders, and what came of each: confirmed, received, cancelled."""

    def __init__(self, job: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"הזמנות חומר — {job.name}")
        self.setMinimumSize(720, 380)
        # Copies: a state chosen here is only real once the page has saved it.
        self._orders = [order.model_copy(deep=True) for order in job.material_orders]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        self.table = DataTable(
            ["הזמנה", "ספק", "נשלחה", "נדרש עד", "סכום", "מצב"],
            empty_text="עוד לא הוזמן חומר לפרויקט",
        )
        layout.addWidget(self.table, 1)
        self._fill()
        buttons = QHBoxLayout()
        self.actions: dict[str, QPushButton] = {}
        for state, label in (("confirmed", "הספק אישר"), ("received", "החומר הגיע"),
                             ("cancelled", "בוטלה")):
            button = QPushButton(label)
            button.clicked.connect(lambda _c=False, s=state: self._set(s))
            buttons.addWidget(button)
            self.actions[state] = button
        buttons.addStretch(1)
        close = QPushButton("סגירה")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        #: (order_id, new state) chosen here, applied by the page to the job on disk.
        self.changes: list[tuple[str, str]] = []

    def _fill(self) -> None:
        from datetime import date

        def day(text: str) -> str:
            try:
                return f"{date.fromisoformat(text[:10]):%d/%m/%Y}"
            except ValueError:
                return text or "—"

        self.table.set_rows([
            [order.order_id, order.supplier, day(order.ordered_on), day(order.needed_by),
             _money(order.total), order.state.hebrew]
            for order in self._orders
        ])

    def _set(self, state: str) -> None:
        from ..projects import MaterialOrderState

        rows = self.table.selectionModel()
        if rows is None or not rows.selectedRows():
            _warn(self, "לא נבחרה הזמנה", ValueError("בחרו הזמנה מהרשימה"))
            return
        order = self._orders[rows.selectedRows()[0].row()]
        allowed = NEXT_STATES.get(order.state.value, ())
        if state not in allowed:
            _warn(self, "לא אפשרי", ValueError(
                f"ההזמנה {order.state.hebrew} — אי אפשר לסמן אותה כ״{MaterialOrderState(state).hebrew}״"
            ))
            return
        order.state = MaterialOrderState(state)
        self.changes.append((order.order_id, state))
        self._fill()


class NeedsDialog(QDialog):
    """What the shop is waiting for from the customer before it can make the
    job — ticked off as it arrives. Works on copies; the page saves them."""

    def __init__(self, job: Any, *, deposit_missing: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from ..erp.order_confirmation import Need

        self.setWindowTitle(f"ממתינים מהלקוח — {job.name}")
        self.setMinimumSize(720, 420)
        self.needs = [need.model_copy(deep=True) for need in job.waiting_for]
        self.send_requested = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        hint = QLabel(
            "מה צריך מהלקוח לפני שמתחילים לייצר. כל עוד משהו כאן פתוח, תאריך המסירה אינו סופי."
            + (" גם מקדמה עוד לא התקבלה — היא נבדקת לבד מהתשלומים שנרשמו." if deposit_missing
               else "")
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.kind = QComboBox()
        for need in Need:
            if need is Need.DEPOSIT:
                continue  # read from the payments, never ticked by hand
            self.kind.addItem(need.hebrew, need.value)
        row.addWidget(self.kind, 1)
        self.detail = QLineEdit()
        self.detail.setPlaceholderText("פירוט (לא חובה) — למשל: גוון לפרופילים ולתריסים")
        self.detail.returnPressed.connect(self.add)
        row.addWidget(self.detail, 2)
        add = QPushButton("הוספה")
        add.setObjectName("Primary")
        add.clicked.connect(self.add)
        row.addWidget(add)
        layout.addLayout(row)
        self.table = DataTable(["מה", "התבקש", "התקבל"], empty_text="לא מחכים לשום דבר מהלקוח")
        self.table.stretch(0)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        got = QPushButton("התקבל")
        got.clicked.connect(self.mark_received)
        buttons.addWidget(got)
        reopen = QPushButton("עוד לא התקבל")
        reopen.clicked.connect(self.reopen)
        buttons.addWidget(reopen)
        delete = QPushButton("מחיקה")
        delete.setToolTip("למשהו שלא צריך מהלקוח בעבודה הזאת")
        delete.clicked.connect(self.delete)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        send = QPushButton("שליחה ללקוח בוואטסאפ")
        send.setObjectName("WhatsApp")
        send.clicked.connect(self._send)
        buttons.addWidget(send)
        close = QPushButton("סגירה")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self) -> None:
        from datetime import date

        def day(text: str) -> str:
            try:
                return f"⁦{date.fromisoformat(text[:10]):%d/%m}⁩"
            except ValueError:
                return text or ""

        self.needs.sort(key=lambda need: (not need.is_open, need.requested_on))
        self.table.set_rows([
            [need.label, day(need.requested_on), day(need.received_on) if need.received_on else "—"]
            for need in self.needs
        ])

    def add(self) -> None:
        from ..projects import CustomerNeed

        self.needs.append(CustomerNeed(need=str(self.kind.currentData()),
                                       detail=self.detail.text().strip()))
        self.detail.clear()
        self._fill()

    def _chosen(self) -> Any:
        rows = self.table.selectionModel()
        if rows is None or not rows.selectedRows():
            _warn(self, "לא נבחרה שורה", ValueError("בחרו שורה מהרשימה"))
            return None
        return self.needs[rows.selectedRows()[0].row()]

    def mark_received(self) -> None:
        from datetime import date

        need = self._chosen()
        if need is not None and need.is_open:
            need.received_on = date.today().isoformat()
            self._fill()

    def reopen(self) -> None:
        need = self._chosen()
        if need is not None and not need.is_open:
            need.received_on = ""
            self._fill()

    def delete(self) -> None:
        need = self._chosen()
        if need is None:
            return
        answer = QMessageBox.question(self, "מחיקה", f"להוריד את ״{need.label}״ מהרשימה?")
        if answer == QMessageBox.StandardButton.Yes:
            self.needs.remove(need)
            self._fill()

    def _send(self) -> None:
        self.send_requested = True
        self.accept()


class SnagListDialog(QDialog):
    """What is left to put right after installation, and what was.

    Works on copies: nothing is real until the page saves it on the job.
    """

    def __init__(self, job: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"רשימת תיקונים — {job.name}")
        self.setMinimumSize(720, 420)
        self.snags = [snag.model_copy(deep=True) for snag in job.snags]
        #: Whether the person asked to send the list to the customer.
        self.send_requested = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        hint = QLabel("מה שנשאר לתקן אחרי ההתקנה. התשלום האחרון בדרך כלל מחכה לרשימה הזאת — "
                      "כשהיא ריקה, שולחים ללקוח ״הכול תוקן״ ואת היתרה.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.description = QLineEdit()
        self.description.setPlaceholderText("מה לתקן — למשל: ידית תקועה, שריטה במסגרת")
        self.description.returnPressed.connect(self.add)
        row.addWidget(self.description, 3)
        self.where = QLineEdit()
        self.where.setPlaceholderText("איפה — פתח או חדר")
        self.where.returnPressed.connect(self.add)
        row.addWidget(self.where, 1)
        add = QPushButton("הוספה")
        add.setObjectName("Primary")
        add.clicked.connect(self.add)
        row.addWidget(add)
        layout.addLayout(row)
        self.table = DataTable(["מה", "איפה", "נרשם", "תוקן"],
                               empty_text="אין תיקונים — כל הכבוד")
        self.table.stretch(0)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        fixed = QPushButton("תוקן")
        fixed.clicked.connect(self.mark_fixed)
        buttons.addWidget(fixed)
        reopen = QPushButton("עוד לא תוקן")
        reopen.clicked.connect(self.reopen)
        buttons.addWidget(reopen)
        delete = QPushButton("מחיקה")
        delete.setToolTip("לשורה שנרשמה בטעות — לא לתיקון שבוצע (עליו מסמנים ״תוקן״)")
        delete.clicked.connect(self.delete)
        buttons.addWidget(delete)
        buttons.addStretch(1)
        send = QPushButton("שליחה ללקוח בוואטסאפ")
        send.setObjectName("WhatsApp")
        send.clicked.connect(self._send)
        buttons.addWidget(send)
        close = QPushButton("סגירה")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self) -> None:
        from datetime import date

        def day(text: str) -> str:
            try:
                return f"⁦{date.fromisoformat(text[:10]):%d/%m}⁩"
            except ValueError:
                return text or ""

        # Open first, oldest first: the order they should be done in.
        self.snags.sort(key=lambda snag: (not snag.is_open, snag.raised_on))
        self.table.set_rows([
            [snag.description, f"\u2068{snag.where}\u2069" if snag.where else "", day(snag.raised_on),
             day(snag.fixed_on) if snag.fixed_on else "—"]
            for snag in self.snags
        ])

    def add(self) -> None:
        from ..projects import Snag

        text = self.description.text().strip()
        if not text:
            _warn(self, "חסר תיאור", ValueError("כתבו מה צריך לתקן"))
            return
        self.snags.append(Snag(description=text, where=self.where.text().strip()))
        self.description.clear()
        self.where.clear()
        self.description.setFocus()
        self._fill()

    def _chosen(self) -> Any:
        rows = self.table.selectionModel()
        if rows is None or not rows.selectedRows():
            _warn(self, "לא נבחר תיקון", ValueError("בחרו שורה מהרשימה"))
            return None
        return self.snags[rows.selectedRows()[0].row()]

    def mark_fixed(self) -> None:
        from datetime import date

        snag = self._chosen()
        if snag is not None and snag.is_open:
            snag.fixed_on = date.today().isoformat()
            self._fill()

    def reopen(self) -> None:
        snag = self._chosen()
        if snag is not None and not snag.is_open:
            snag.fixed_on = ""
            self._fill()

    def delete(self) -> None:
        snag = self._chosen()
        if snag is None:
            return
        answer = QMessageBox.question(self, "מחיקה", f"למחוק את ״{snag.description}״ מהרשימה?")
        if answer == QMessageBox.StandardButton.Yes:
            self.snags.remove(snag)
            self._fill()

    def _send(self) -> None:
        if not self.snags:
            _warn(self, "אין מה לשלוח", ValueError("הרשימה ריקה"))
            return
        self.send_requested = True
        self.accept()


class RevisionsDialog(QDialog):
    """Every version of a job's quotation: when, how much, why — and, for
    the one chosen, exactly which lines moved and by how much."""

    def __init__(self, history: Any, *, accepted: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("גרסאות ההצעה")
        self.setMinimumSize(820, 520)
        self._history = history
        self._accepted = accepted
        #: Notes written here, by version number; the page saves them.
        self.notes: dict[int, str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        self.table = DataTable(["גרסה", "תאריך", "לפני מע״מ", "שינוי", "למה", "הערה"],
                               empty_text="אין גרסאות")
        self.table.stretch(4)
        layout.addWidget(self.table, 1)
        self.changes = QLabel()
        self.changes.setWordWrap(True)
        self.changes.setObjectName("Hint")
        self.changes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.changes)
        buttons = QHBoxLayout()
        note = QPushButton("הערה לגרסה…")
        note.clicked.connect(self.add_note)
        buttons.addWidget(note)
        buttons.addStretch(1)
        close = QPushButton("סגירה")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._fill()
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._show_changes())
        if len(self._history.revisions):
            self.table.selectRow(len(self._history.revisions) - 1)
        self._show_changes()

    def _fill(self) -> None:
        rows = []
        previous = None
        for revision in self._history.revisions:
            label = f"⁦{revision.number}⁩"
            if revision.number == self._accepted:
                label += " · אושרה"
            move = ""
            if previous is not None:
                delta = revision.net_price - previous.net_price
                move = f"⁦{'+' if delta > 0 else ''}{delta:,.0f} ₪⁩" if abs(delta) >= 0.5 else "—"
            rows.append([
                label, f"⁦{revision.on:%d/%m/%Y}⁩", _money(revision.net_price), move,
                revision.reason or ("ההצעה הראשונה" if previous is None else ""),
                self.notes.get(revision.number, revision.note),
            ])
            previous = revision
        self.table.set_rows(rows)

    def _chosen(self) -> int:
        model = self.table.selectionModel()
        if model is None or not model.selectedRows():
            return -1
        return model.selectedRows()[0].row()

    def _show_changes(self) -> None:
        from ..quoting.revisions import compare

        row = self._chosen()
        revisions = self._history.revisions
        if row < 0:
            self.changes.setText("בחרו גרסה כדי לראות מה השתנה בה.")
            return
        if row == 0:
            self.changes.setText(
                f"ההצעה הראשונה: ⁦{len(revisions[0].lines)}⁩ שורות, {_money(revisions[0].net_price)} לפני מע״מ."
            )
            return
        comparison = compare(revisions[row - 1], revisions[row])
        lines = [comparison.describe()]
        lines += [f"• {change.describe()}" for change in comparison.biggest[:12]]
        if len(comparison.changes) > 12:
            lines.append(f"ועוד ⁦{len(comparison.changes) - 12}⁩ שינויים קטנים יותר.")
        self.changes.setText("\n".join(lines))

    def add_note(self) -> None:
        row = self._chosen()
        if row < 0:
            _warn(self, "לא נבחרה גרסה", ValueError("בחרו גרסה מהרשימה"))
            return
        revision = self._history.revisions[row]
        text, ok = QInputDialog.getText(
            self, f"הערה לגרסה \u2066{revision.number}\u2069",
            "למשל: הלקוח ביקש זכוכית מחוסמת בחדר הילדים",
            text=self.notes.get(revision.number, revision.note),
        )
        if ok:
            self.notes[revision.number] = text.strip()
            self._fill()
            self.table.selectRow(row)


__all__ = [
    "JobAccountDialog", "MaterialOrderDialog", "MaterialOrdersDialog", "PriceBookDialog",
    "NeedsDialog", "PriceItemPicker", "RevisionsDialog", "SnagListDialog",
]
