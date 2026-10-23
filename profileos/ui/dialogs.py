"""The dialogs that create things: a job, a customer, a call, a cheque.

Both follow the same rule — the fewest fields that make the record useful, and
nothing that can be filled in later. A shop opening a job while the customer
is still on the phone should not be stopped by a form.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .theme import METRICS
from .widgets import FieldGrid, documents_dir


class _FormDialog(QDialog):
    """Shared shell: a titled form, an accept button and a cancel button."""

    accept_text = "אישור"

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(4))

        self.grid = FieldGrid()
        layout.addWidget(self.grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(self.accept_text)
        ok.setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = ok


class NewJobDialog(_FormDialog):
    """Open a job: a name, who it is for, and where it is.

    The customer box is searched as it is typed into — by name, city or any
    part of a phone number — and a customer who is not in the book yet is
    added from here without leaving the dialog. Choosing a customer fills in
    the site and suggests a name, so a job opened while they are still on the
    phone needs almost no typing.
    """

    accept_text = "פתח פרויקט"

    def __init__(self, customers: list[Any], systems: list[tuple[str, str]],
                 parent: QWidget | None = None, *,
                 add_customer: Callable[[dict[str, Any]], Any] | None = None) -> None:
        super().__init__("פרויקט חדש", parent)
        self._customers = list(customers)
        self._add_customer = add_customer
        self._name_was_suggested = False
        self._site_was_suggested = False

        self.name = QLineEdit()
        self.name.setPlaceholderText("וילה בשכונת הגבעה")
        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.customer.lineEdit().setPlaceholderText("הקלד שם, עיר או טלפון…")
        self._fill_customers()
        completer = self.customer.completer()
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        add = QPushButton("+ לקוח חדש")
        add.setObjectName("Ghost")
        add.setEnabled(add_customer is not None)
        add.clicked.connect(self.new_customer)
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(METRICS.space(2))
        line.addWidget(self.customer, 1)
        line.addWidget(add)

        self.reference = QLineEdit()
        self.reference.setPlaceholderText("מספר הזמנה של הלקוח")
        self.site = QLineEdit()
        self.site.setPlaceholderText("כתובת האתר")
        self.system = QComboBox()
        for entry_id, label in systems:
            self.system.addItem(label, entry_id)
        self.trade = QComboBox()
        self.trade.addItem("אלומיניום", "aluminium")
        self.trade.addItem("אינסטלציה", "plumbing")
        self.trade.addItem("אלומיניום ואינסטלציה", "both")

        from ..projects.model import LEAD_SOURCES

        self.source = QComboBox()
        self.source.setEditable(True)
        self.source.addItem("")
        self.source.addItems(LEAD_SOURCES)
        self.source.lineEdit().setPlaceholderText("איך הגיע אלינו?")

        self.grid.add("תחום", self.trade)
        self.grid.add("לקוח", row)
        self.grid.add("שם הפרויקט", self.name)
        self.grid.add("אתר", self.site)
        self.grid.add("אסמכתה", self.reference)
        self.grid.add("מערכת", self.system)
        self.grid.add("הגיע דרך", self.source)

        # A job without a name is a job nobody can find again.
        self.name.textChanged.connect(self._name_edited)
        self.site.textEdited.connect(lambda _text: setattr(self, "_site_was_suggested", False))
        self.customer.currentIndexChanged.connect(self._customer_chosen)
        self.ok_button.setEnabled(False)
        self.customer.setFocus()

    def _fill_customers(self, select: str = "") -> None:
        self.customer.blockSignals(True)
        self.customer.clear()
        self.customer.addItem("— ללא לקוח —", "")
        for customer in sorted(self._customers, key=lambda c: c.name):
            label = customer.name
            details = " · ".join(part for part in (customer.city, customer.phone) if part)
            if details:
                label += f"  ({details})"
            self.customer.addItem(label, customer.customer_id)
        # Nothing chosen yet shows the hint, not "no customer" highlighted.
        index = self.customer.findData(select) if select else -1
        self.customer.setCurrentIndex(index)
        self.customer.blockSignals(False)

    def _selected(self) -> Any:
        index = self.customer.currentIndex()
        typed = self.customer.currentText().strip()
        # The box's text is the truth: a customer picked earlier and then
        # typed over is not the customer meant any more.
        if index > 0 and typed == self.customer.itemText(index).strip():
            wanted = self.customer.itemData(index)
            return next((c for c in self._customers if c.customer_id == wanted), None)
        # A name typed and never picked from the list still counts when it
        # matches exactly one customer.
        matches = [c for c in self._customers if c.name == typed]
        return matches[0] if len(matches) == 1 else None

    def _name_edited(self, text: str) -> None:
        self.ok_button.setEnabled(bool(text.strip()))

    def _customer_chosen(self, _index: int) -> None:
        customer = self._selected()
        if customer is None:
            return
        where = ", ".join(part for part in (customer.address, customer.city) if part)
        if where and (not self.site.text().strip() or self._site_was_suggested):
            self.site.setText(where)
            self._site_was_suggested = True
        if not self.name.text().strip() or self._name_was_suggested:
            suggestion = customer.name + (f" — {customer.city}" if customer.city else "")
            self.name.setText(suggestion)
            self._name_was_suggested = True
            self.name.selectAll()
        self.name.setFocus()

    def new_customer(self) -> None:
        if self._add_customer is None:
            return
        dialog = NewCustomerDialog(self, customers=self._customers)
        typed = self.customer.currentText().strip()
        if typed and self._selected() is None:
            dialog.name.setText(typed)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            customer = self._add_customer(dialog.values())
        except Exception as exc:  # noqa: BLE001 - shown in the dialog's parent
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "הלקוח לא נוסף", str(exc))
            return
        self._customers.append(customer)
        self._fill_customers(select=customer.customer_id)
        self._customer_chosen(self.customer.currentIndex())

    def values(self) -> dict[str, Any]:
        customer = self._selected()
        return {
            "name": self.name.text().strip(),
            "customer_id": customer.customer_id if customer else "",
            "reference": self.reference.text().strip(),
            "site_address": self.site.text().strip(),
            "system_id": self.system.currentData() or "generic",
            "trade": self.trade.currentData() or "aluminium",
            "source": self.source.currentText().strip(),
        }


class NewCustomerDialog(_FormDialog):
    """Add a customer, or correct one. Only the name is required.

    Details usually arrive as a block — a shared WhatsApp contact, an email
    signature — so the first box takes the whole block and fills the fields
    from it. The phone is checked while it is typed and written back the one
    way the shop writes numbers, and a customer who is already in the book
    is pointed out before a second copy is made.
    """

    accept_text = "הוסף לקוח"

    def __init__(self, parent: QWidget | None = None, *,
                 customers: list[Any] | None = None, customer: Any = None) -> None:
        super().__init__("עריכת לקוח" if customer is not None else "לקוח חדש", parent)
        if customer is not None:
            self.ok_button.setText("שמור שינויים")
        self.setMinimumWidth(480)
        self._customers = list(customers or [])
        self._editing = customer

        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText(
            "הדבק כאן פרטים — איש קשר מוואטסאפ, חתימת מייל, הודעה — "
            "והשדות יתמלאו לבד"
        )
        self.paste.setFixedHeight(84)
        self.paste.setTabChangesFocus(True)
        self.paste.textChanged.connect(self._paste_changed)

        self.name = QLineEdit()
        self.name.setPlaceholderText("משה כהן / קבלן בע״מ")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("054-123-4567")
        self.phone.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.phone.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        self.contact = QLineEdit()
        self.contact.setPlaceholderText("אם יש מישהו אחר שמדברים איתו")
        self.email = QLineEdit()
        self.email.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.email.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        self.address = QLineEdit()
        self.address.setPlaceholderText("רחוב ומספר")
        self.city = QLineEdit()
        from ..projects.contacts import KNOWN_CITIES

        cities = QCompleter(sorted(KNOWN_CITIES), self)
        cities.setFilterMode(Qt.MatchFlag.MatchContains)
        self.city.setCompleter(cities)
        self.tax_id = QLineEdit()
        self.tax_id.setPlaceholderText("ח.פ. / ע.מ. — לחשבונית")
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("מה כדאי לזכור עליו — קומה, שעות נוחות, מי המליץ")
        self.notes.setFixedHeight(84)
        self.notes.setTabChangesFocus(True)

        self.problem = QLabel("")
        self.problem.setObjectName("FieldError")
        self.problem.setWordWrap(True)
        self.problem.hide()
        self.duplicate = QLabel("")
        self.duplicate.setObjectName("FieldWarning")
        self.duplicate.setWordWrap(True)
        self.duplicate.hide()

        if customer is None:
            self.grid.add_span(self.paste)
        self.grid.add("שם", self.name)
        self.grid.add("טלפון", self.phone)
        self.grid.add("עיר", self.city)
        self.grid.add("כתובת", self.address)
        self.grid.add("דוא\"ל", self.email)
        self.grid.add("איש קשר", self.contact)
        self.grid.add("מספר עוסק", self.tax_id)
        self.grid.add("הערות", self.notes)
        self.grid.add_span(self.problem)
        self.grid.add_span(self.duplicate)

        if customer is not None:
            for widget, value in (
                (self.name, customer.name), (self.phone, customer.phone),
                (self.contact, customer.contact), (self.email, customer.email),
                (self.address, customer.address), (self.city, customer.city),
                (self.tax_id, customer.tax_id),
            ):
                widget.setText(value)
            self.notes.setPlainText(customer.notes)

        for field_ in (self.name, self.phone, self.email):
            field_.textChanged.connect(self._check)
        for field_ in (self.name, self.phone, self.email, self.address, self.city, self.tax_id):
            # Typed by hand: the paste box leaves it alone from now on.
            field_.textEdited.connect(
                lambda _text, widget=field_: widget.setProperty("filled", False)
            )
            field_.textEdited.connect(
                lambda _text, widget=field_: widget.setProperty("typed", True)
            )
        self.phone.editingFinished.connect(self._tidy_phone)
        self._check()
        (self.name if customer is not None else self.paste).setFocus()

    # -- reading what was pasted -------------------------------------------- #
    def _paste_changed(self) -> None:
        from ..projects.contacts import parse_contact

        parsed = parse_contact(self.paste.toPlainText())
        targets = {
            "name": self.name, "phone": self.phone, "email": self.email,
            "address": self.address, "city": self.city, "tax_id": self.tax_id,
        }
        values = parsed.as_dict()
        for key, widget in targets.items():
            # Never over what somebody typed by hand.
            if widget.property("typed"):
                continue
            if key in values:
                if not widget.text().strip() or widget.property("filled"):
                    widget.setText(values[key])
                    widget.setProperty("filled", True)
            elif widget.property("filled"):
                # The paste no longer says it: what it filled in goes too.
                widget.clear()
                widget.setProperty("filled", False)
        if parsed.extra_phones and not self.notes.toPlainText().strip():
            self.notes.setPlainText("טלפון נוסף: " + ", ".join(parsed.extra_phones))

    def _tidy_phone(self) -> None:
        from ..projects.contacts import format_phone, phone_problem

        text = self.phone.text()
        if text.strip() and not phone_problem(text):
            tidy = format_phone(text)
            if tidy != text:
                self.phone.setText(tidy)

    # -- checking ------------------------------------------------------------ #
    def _check(self) -> None:
        from ..projects.contacts import email_problem, find_duplicates, phone_problem

        problems = [
            message for message in (
                phone_problem(self.phone.text()), email_problem(self.email.text())
            ) if message
        ]
        self._mark(self.phone, bool(phone_problem(self.phone.text())))
        self._mark(self.email, bool(email_problem(self.email.text())))
        self.problem.setText(" · ".join(problems))
        self.problem.setVisible(bool(problems))

        same = find_duplicates(
            self._customers, name=self.name.text(), phone=self.phone.text(),
            email=self.email.text(),
            ignore_id=self._editing.customer_id if self._editing is not None else "",
        )
        if same:
            first = same[0]
            details = " · ".join(part for part in (first.city, first.phone) if part)
            self.duplicate.setText(
                f"כבר יש בספר לקוח דומה: {first.name}"
                + (f" ({details})" if details else "")
                + " — אם זה אותו אדם, עדיף לבחור אותו ולא להוסיף שוב."
            )
        self.duplicate.setVisible(bool(same))
        self.duplicates = same
        # A number that is not Israeli, or carries an extension, is still the
        # customer's number: it is marked, not refused.
        self.ok_button.setEnabled(
            bool(self.name.text().strip()) and not email_problem(self.email.text())
        )

    @staticmethod
    def _mark(widget: QLineEdit, invalid: bool) -> None:
        if bool(widget.property("invalid")) == invalid:
            return
        widget.setProperty("invalid", invalid)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def values(self) -> dict[str, Any]:
        from ..projects.contacts import format_phone

        return {
            "name": self.name.text().strip(),
            "contact": self.contact.text().strip(),
            "phone": format_phone(self.phone.text()) if self.phone.text().strip() else "",
            "email": self.email.text().strip(),
            "address": self.address.text().strip(),
            "city": self.city.text().strip(),
            "tax_id": self.tax_id.text().strip(),
            "notes": self.notes.toPlainText().strip(),
        }


class NewServiceCallDialog(_FormDialog):
    """Log a call while the customer is still on the phone.

    The handover date is asked for because it is what warranty is counted
    from, and asking later means looking it up in a folder.
    """

    accept_text = "רישום קריאה"

    def __init__(self, parent: QWidget | None = None) -> None:
        from datetime import date

        super().__init__("קריאת שירות", parent)

        self.customer = QLineEdit()
        self.customer.setPlaceholderText("מי מתקשר")
        self.phone = QLineEdit()
        self.job_id = QLineEdit()
        self.job_id.setPlaceholderText("J-2026-0007")
        self.element = QLineEdit()
        self.element.setPlaceholderText("W-04, חלון הסלון")

        self.symptom = QComboBox()
        from ..service import Symptom

        # Grouped by trade, so the person on the phone finds the fault fast.
        for trade, heading in (("aluminium", "חלונות ודלתות"), ("plumbing", "אינסטלציה")):
            self.symptom.addItem(f"— {heading} —", "")
            self.symptom.model().item(self.symptom.count() - 1).setEnabled(False)
            for symptom in Symptom:
                if symptom.trade == trade:
                    self.symptom.addItem(symptom.hebrew, symptom.value)
        self.symptom.addItem(Symptom.OTHER.hebrew, Symptom.OTHER.value)
        self.symptom.setCurrentIndex(1)

        self.delivered = QDateEdit()
        self.delivered.setCalendarPopup(True)
        self.delivered.setDisplayFormat("dd/MM/yyyy")
        self.delivered.setDate(date.today())

        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("במילים של הלקוח")
        self.description.setMaximumHeight(90)

        for label, widget in (
            ("לקוח", self.customer), ("טלפון", self.phone),
            ("פרויקט", self.job_id), ("פתח", self.element),
            ("תקלה", self.symptom), ("תאריך מסירה", self.delivered),
            ("תיאור", self.description),
        ):
            self.grid.add(label, widget)

        self.customer.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(False)
        self.customer.setFocus()

    def values(self) -> dict[str, Any]:
        return {
            "customer": self.customer.text().strip(),
            "phone": self.phone.text().strip(),
            "job_id": self.job_id.text().strip(),
            "element": self.element.text().strip(),
            "symptom": self.symptom.currentData(),
            "delivered": self.delivered.date().toPython(),
            "description": self.description.toPlainText().strip(),
        }


class CloseServiceCallDialog(_FormDialog):
    """Shut a call, and say what it turned out to be.

    The cause is the required field. A register of calls with no causes in it
    answers no question anybody would open it to ask.
    """

    accept_text = "סגירת קריאה"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("סגירת קריאה", parent)

        self.cause = QComboBox()
        from ..service import Cause

        for cause in Cause:
            if cause is Cause.UNKNOWN:
                continue
            self.cause.addItem(cause.hebrew, cause.value)

        self.engineer = QLineEdit()
        self.engineer.setPlaceholderText("מי יצא")
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 3000)
        self.minutes.setValue(60)
        self.minutes.setSuffix(" דקות")
        self.charged = QDoubleSpinBox()
        self.charged.setRange(0, 1_000_000)
        self.charged.setSuffix(" ₪")
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("מה נעשה בפועל")
        self.note.setMaximumHeight(90)

        for label, widget in (
            ("סיבה", self.cause), ("טכנאי", self.engineer),
            ("זמן עבודה", self.minutes), ("חויב", self.charged),
            ("הערה", self.note),
        ):
            self.grid.add(label, widget)

    def values(self) -> dict[str, Any]:
        return {
            "cause": self.cause.currentData(),
            "engineer": self.engineer.text().strip(),
            "minutes": self.minutes.value(),
            "charged": self.charged.value(),
            "note": self.note.toPlainText().strip(),
        }


class PasteOpeningsDialog(QDialog):
    """A list of openings pasted from a table, a message or a schedule, read
    line by line, with what each became — before anything is added."""

    def __init__(self, parent: QWidget | None = None, *, existing: list[Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("הדבקת רשימת פתחים")
        self.setModal(True)
        self.setMinimumSize(720, 520)
        self._existing = list(existing or [])
        self.intake = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        hint = QLabel(
            "הדביקו מטבלה של אדריכל, מוואטסאפ או מכל מקום — שורה לכל פתח. "
            "למשל: ״W1 חלון ציר 120×140 2 יח׳ סלון״ או ״הזזה 2 כנפיים 240/210״. "
            "מידות במ״מ, בס״מ או במטרים."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("W1\tחלון ציר\t120\t140\t2\nW2 סלון הזזה 240/210")
        layout.addWidget(self.text, 1)
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setObjectName("FieldValue")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("הוספה לפרויקט")
        self.ok_button.setObjectName("Primary")
        self.ok_button.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        from PySide6.QtCore import QTimer

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.read)
        self.text.textChanged.connect(self._timer.start)

    def read(self) -> Any:
        from ..projects.intake import read_list

        self.intake = read_list(self.text.toPlainText(), existing=self._existing)
        # Each line opens with a right-to-left mark: a line that starts "W1"
        # would otherwise be laid out left to right.
        lines = ["\u200f" + ("✓ " if line.ok else "✗ ") + line.describe()
                 for line in self.intake.lines]
        self.preview.setText(
            (self.intake.describe() + "\n\n" + "\n".join(lines[:40])) if lines else "")
        self.ok_button.setEnabled(bool(self.intake.read))
        return self.intake


class RackDialog(QDialog):
    """The offcut rack: what is on it, entered by hand from the real one,
    and taken off when it is used or thrown away. Each change is written at
    once, onto the rack as it is on disk."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .widgets import DataTable

        self.setWindowTitle("מדף השאריות")
        self.setMinimumSize(720, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        hint = QLabel(
            "שאריות שבמדף נלקחות בחשבון בחיתוך ובהזמנת חומר לפני מוט חדש. "
            "שארית שנשמרה בצד לפרויקט מסומנת — היא לא תיספר לפרויקט אחר."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("מק״ט הפרופיל")
        self.length = QSpinBox()
        self.length.setRange(100, 7500)
        self.length.setValue(1500)
        self.length.setSuffix(" מ״מ")
        self.count = QSpinBox()
        self.count.setRange(1, 200)
        self.count.setSuffix(" יח׳")
        self.place = QLineEdit()
        self.place.setPlaceholderText("איפה במדף (לא חובה)")
        add = QPushButton("הוספה")
        add.setObjectName("Primary")
        add.clicked.connect(self.add)
        for widget in (self.code, self.length, self.count, self.place, add):
            row.addWidget(widget)
        layout.addLayout(row)
        self.table = DataTable(["פרופיל", "אורך", "כמות", "מיקום", "מאיפה", "שמורה ל"],
                               empty_text="המדף ריק — הוסיפו את השאריות שיש לכם")
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        remove = QPushButton("הוצאה מהמדף")
        remove.setToolTip("נחתכה, נזרקה, או לא באמת שם")
        remove.clicked.connect(self.remove)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        close = QPushButton("סגור")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.problem = ""
        self.show_rack()

    def show_rack(self) -> None:
        from ..nesting.rack import load_rack

        try:
            offcuts = sorted(load_rack().all(), key=lambda o: (o.profile_id, -o.length))
        except Exception as exc:  # noqa: BLE001
            self.problem = str(exc)
            self.table.set_rows([[str(exc), "", "", "", "", ""]])
            self._ids: list[str] = []
            return
        self._ids = [offcut.remnant_id for offcut in offcuts]
        self.table.set_rows([
            [f"\u2066{o.profile_id}\u2069", f"\u2066{o.length:,.0f}\u2069 מ״מ", o.quantity,
             o.location or "—", f"\u2066{o.source_project}\u2069" if o.source_project else "ביד",
             f"\u2066{o.metadata.get('job_id', '')}\u2069" if o.reserved else ""]
            for o in offcuts
        ], numeric_columns=(2,))

    def add(self) -> bool:
        from ..models.orders import RemnantBar
        from ..nesting.rack import change_rack

        code = self.code.text().strip().upper()
        if not code:
            self.problem = "חסר מק״ט הפרופיל"
            self.table.setToolTip(self.problem)
            return False
        offcut = RemnantBar(profile_id=code, length=float(self.length.value()),
                            quantity=self.count.value(),
                            location=self.place.text().strip() or None)
        try:
            change_rack(lambda rack: rack.add(offcut))
        except Exception as exc:  # noqa: BLE001
            self.problem = str(exc)
            return False
        self.show_rack()
        return True

    def remove(self) -> bool:
        from ..nesting.rack import change_rack

        model = self.table.selectionModel()
        if model is None or not model.selectedRows():
            return False
        remnant_id = self._ids[model.selectedRows()[0].row()]
        try:
            change_rack(lambda rack: rack.consume(remnant_id))
        except Exception as exc:  # noqa: BLE001
            self.problem = str(exc)
            return False
        self.show_rack()
        return True


class PickJobsDialog(QDialog):
    """Which jobs go into one order — ticked from a list, never typed."""

    def __init__(self, jobs: list[Any], parent: QWidget | None = None, *,
                 title: str = "בחירת פרויקטים", hint: str = "",
                 ticked: set[str] | None = None, at_least: int = 2) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(520, 420)
        self._at_least = at_least
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(3))
        if hint:
            label = QLabel(hint)
            label.setObjectName("Hint")
            label.setWordWrap(True)
            layout.addWidget(label)
        self.list = QListWidget()
        for job in jobs:
            text = " · ".join(part for part in (job.name, job.customer_name) if part)
            item = QListWidgetItem(f"{text} · \u2066{job.job_id}\u2069")
            item.setData(Qt.ItemDataRole.UserRole, job.job_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if job.job_id in (ticked or set())
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("המשך")
        self.ok_button.setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list.itemChanged.connect(lambda *_: self._enable())
        self._enable()

    def chosen(self) -> list[str]:
        return [self.list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(self.list.count())
                if self.list.item(row).checkState() == Qt.CheckState.Checked]

    def _enable(self) -> None:
        self.ok_button.setEnabled(len(self.chosen()) >= self._at_least)


class NewChequeDialog(_FormDialog):
    """Take cheques into the drawer — one, or the whole series a customer
    hands over at once (one a month, consecutive numbers from one book).

    The project is picked from the list, never typed: a mistyped job number
    left a cheque that never reached the job's balance, and the home page
    then chased money that was already in the drawer. Picking it fills in
    the customer.
    """

    accept_text = "רישום"

    def __init__(self, parent: QWidget | None = None, *, jobs: list[Any] | None = None,
                 job: Any = None, outgoing: bool = False) -> None:
        from datetime import date

        self.outgoing = outgoing
        super().__init__("צ׳קים לספק" if outgoing else "קבלת צ׳קים", parent)
        self.setMinimumWidth(480)

        self.job = QComboBox()
        self.job.addItem("— בלי פרויקט —", "")
        for item in jobs or ([job] if job is not None else []):
            label = f"{item.job_id} · {item.name}"
            if item.customer_name:
                label += f" · {item.customer_name}"
            self.job.addItem(label, item.job_id)
            self.job.setItemData(self.job.count() - 1, item.customer_name, Qt.ItemDataRole.UserRole + 1)
        self.customer = QLineEdit()
        self.count = QSpinBox()
        self.count.setRange(1, 60)
        self.count.setValue(1)
        self.count.setSuffix(" צ׳קים")
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, 10_000_000)
        self.amount.setDecimals(2)
        self.amount.setValue(1000.0)
        self.amount.setSuffix(" ₪")
        self.due = QDateEdit()
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("dd/MM/yyyy")
        self.due.setDate(date.today())
        self.bank = QLineEdit()
        self.bank.setPlaceholderText("למשל: 12 הפועלים")
        self.branch = QLineEdit()
        self.number = QLineEdit()
        self.number.setPlaceholderText("מספר הצ׳ק (הראשון בסדרה)")
        self.preview = QLabel("")
        self.preview.setObjectName("Hint")
        self.preview.setWordWrap(True)

        if outgoing:
            self.customer.setPlaceholderText("למי נכתב — ספק, קבלן משנה")
        for label, widget in (
            ("פרויקט", self.job), ("לספק" if outgoing else "לקוח", self.customer),
            ("כמה צ׳קים", self.count),
            ("סכום לכל צ׳ק", self.amount), ("פירעון (הראשון)", self.due),
            ("בנק", self.bank), ("סניף", self.branch), ("מספר", self.number),
        ):
            self.grid.add(label, widget)
        self.grid.add("", self.preview)

        self.job.currentIndexChanged.connect(self._job_chosen)
        for signal in (self.count.valueChanged, self.amount.valueChanged,
                       self.due.dateChanged, self.number.textChanged):
            signal.connect(lambda *_: self._show_preview())
        self.customer.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(False)
        if job is not None:
            self.job.setCurrentIndex(max(0, self.job.findData(job.job_id)))
            self._job_chosen()
        self._show_preview()
        self.customer.setFocus()

    def _job_chosen(self) -> None:
        customer = self.job.currentData(Qt.ItemDataRole.UserRole + 1)
        if customer and not self.outgoing and not self.customer.text().strip():
            self.customer.setText(str(customer))

    def series(self) -> list[Any]:
        """The cheques as they will be recorded (dates and numbers worked out)."""
        from ..erp.collection import cheque_series

        values = self.values()
        return cheque_series(
            count=values["count"], amount=values["amount"], first_due=values["due"],
            first_number=values["number"], customer=values["customer"] or "—",
            job_id=values["job_id"], bank=values["bank"], branch=values["branch"],
            outgoing=self.outgoing,
        )

    def _show_preview(self) -> None:
        count = self.count.value()
        if count == 1:
            self.preview.setText("")
            return
        try:
            cheques = self.series()
        except Exception as exc:  # noqa: BLE001 - said in the dialog
            self.preview.setText(str(exc))
            return
        total = sum(c.amount for c in cheques)
        last = cheques[-1]
        numbers = (f" · מספרים ⁦{cheques[0].number}⁩–⁦{last.number}⁩"
                   if cheques[0].number and last.number else
                   (" · מספרים: רק הראשון נרשם (המספר אינו ספרות בלבד)" if cheques[0].number else ""))
        self.preview.setText(
            f"⁦{count}⁩ צ׳קים של ⁦{self.amount.value():,.0f} ₪⁩ — סה״כ ⁦{total:,.0f} ₪⁩ · "
            f"פירעון כל חודש, מ־⁦{cheques[0].due:%d/%m/%Y}⁩ עד ⁦{last.due:%d/%m/%Y}⁩{numbers}"
        )

    def values(self) -> dict[str, Any]:
        return {
            "customer": self.customer.text().strip(),
            "amount": self.amount.value(),
            "due": self.due.date().toPython(),
            "bank": self.bank.text().strip(),
            "branch": self.branch.text().strip(),
            "number": self.number.text().strip(),
            "job_id": str(self.job.currentData() or ""),
            "count": self.count.value(),
        }


class ConfirmSystemDialog(_FormDialog):
    """The eleven numbers that turn a series into one the shop may cut to.

    Laid out in the order a catalogue prints them, each with the place to look
    for it, so somebody can work down the page with the book open beside them
    instead of hunting for each field. Every field starts empty: a number is
    in the record only because somebody read it off the page.
    """

    accept_text = "אשר את הסדרה"
    #: Shown in a field nothing has been typed into.
    EMPTY = "לא הוזן"

    def __init__(
        self,
        series: str,
        existing: Any = None,
        editions: Any = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"נתוני יצרן — {series}", parent)
        self.setMinimumWidth(560)

        from ..systems import FIGURES

        editions = list(editions)
        self.source = QLineEdit()
        self.source.setPlaceholderText("קטלוג קליל 7300, מהדורת 2026, עמ׳ 41")
        self.entered_by = QLineEdit()
        self.entered_by.setPlaceholderText("מי קרא את המספרים")
        self.edition = QComboBox()
        self.edition.addItem("לא מתוך מהדורה שנקלטה בתוכנה", "")
        for edition in editions:
            revision = getattr(edition, "revision", "")
            self.edition.addItem(
                edition.source + (f" · {revision}" if revision else ""), edition.edition_id
            )
        if editions:
            self.edition.setCurrentIndex(1)  # the newest accepted, listed first

        self.grid.add("מקור", self.source)
        self.grid.add("נקרא מתוך", self.edition)
        self.grid.add("הוזן על ידי", self.entered_by)

        self.figures: dict[str, QDoubleSpinBox] = {}
        self._unset: dict[str, float] = {}
        for figure in FIGURES:
            box = QDoubleSpinBox()
            # One step below the range means "not entered", shown in words.
            unset = round(figure.minimum - 0.1, 1)
            box.setRange(unset, figure.maximum)
            box.setDecimals(1)
            box.setSuffix(f" {figure.unit}")
            box.setSpecialValueText(self.EMPTY)
            box.setToolTip(figure.where)
            box.setValue(unset)
            self.figures[figure.key] = box
            self._unset[figure.key] = unset
            label = figure.hebrew if figure.required else f"{figure.hebrew} (לא חובה)"
            self.grid.add(label, box)

        self.profiles: dict[str, QLineEdit] = {}
        for role, label in (
            ("frame", "מק״ט משקוף"), ("sash", "מק״ט כנף"),
            ("mullion", "מק״ט זקף"), ("transom", "מק״ט קורה"), ("bead", "מק״ט סרגל"),
        ):
            field = QLineEdit()
            field.setPlaceholderText("מספר הפריט בקטלוג")
            self.profiles[role] = field
            self.grid.add(label, field)

        if existing is not None:
            self.source.setText(existing.source)
            self.entered_by.setText(existing.entered_by)
            for key, value in existing.values.items():
                if key in self.figures:
                    self.figures[key].setValue(value)
            for role, code in existing.profiles.items():
                if role in self.profiles:
                    self.profiles[role].setText(code)
            read_from = getattr(existing, "edition_id", "")
            index = self.edition.findData(read_from) if read_from else -1
            if index >= 0:
                self.edition.setCurrentIndex(index)

        # A source is the one field with no sensible default: a confirmed
        # figure with no record of where it came from is not confirmed.
        self.source.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(bool(self.source.text().strip()))
        self.source.setFocus()

    def values(self) -> dict[str, Any]:
        return {
            "source": self.source.text().strip(),
            "entered_by": self.entered_by.text().strip(),
            "edition_id": self.edition.currentData() or "",
            # Only what was typed: a field left at "not entered" is missing,
            # and a missing required figure is refused by name.
            "figures": {
                key: round(box.value(), 1) for key, box in self.figures.items()
                if box.value() > self._unset[key] + 1e-9
            },
            "profiles": {
                role: field.text().strip()
                for role, field in self.profiles.items()
                if field.text().strip()
            },
        }


class PriceListDialog(_FormDialog):
    """What a supplier's price list file was read as, before it is kept.

    Says how many rows were read, which were not and why, and which column
    was taken as which field — so a list read with the wrong column as the
    price is caught here, not on a quotation.
    """

    accept_text = "שמירה כמחירון ממתין"

    def __init__(self, file_name: str, count: int, skipped: list[str],
                 matched: dict[str, str], suppliers: list[str] = (),
                 terms: dict[str, tuple[float, float]] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__("קליטת מחירון ספק", parent)
        self.setMinimumWidth(540)
        self._terms = terms or {}

        self.supplier = QComboBox()
        self.supplier.setEditable(True)
        self.supplier.addItems(list(suppliers))
        self.supplier.setCurrentText("")
        self.supplier.lineEdit().setPlaceholderText("שם הספק: קליל, אקסטל, ספק הפרזול…")
        self.source = QLineEdit(file_name)
        self.has_date = QCheckBox("בתוקף מתאריך")
        self.valid_from = QDateEdit()
        self.valid_from.setCalendarPopup(True)
        self.valid_from.setDisplayFormat("dd/MM/yyyy")
        from PySide6.QtCore import QDate

        self.valid_from.setDate(QDate.currentDate())
        self.valid_from.setEnabled(False)
        self.has_date.toggled.connect(self.valid_from.setEnabled)
        date_row = QHBoxLayout()
        date_row.setContentsMargins(0, 0, 0, 0)
        date_row.addWidget(self.has_date)
        date_row.addWidget(self.valid_from, 1)
        date_holder = QWidget()
        date_holder.setLayout(date_row)
        self.currency = QComboBox()
        for code, label in (("ILS", "שקל"), ("USD", "דולר"), ("EUR", "אירו")):
            self.currency.addItem(label, code)
        self.recorded_by = QLineEdit()
        self.recorded_by.setPlaceholderText("מי קלט")
        # What the shop actually pays: its own discount off this supplier's
        # list, and any surcharge. Filled in from the last list from them.
        self.discount = QDoubleSpinBox()
        self.discount.setRange(0.0, 99.0)
        self.discount.setDecimals(1)
        self.discount.setSuffix(" %")
        self.surcharge = QDoubleSpinBox()
        self.surcharge.setRange(0.0, 100.0)
        self.surcharge.setDecimals(1)
        self.surcharge.setSuffix(" %")
        # The bar length profiles are sold in, when the file has no length
        # column: said once for the supplier, carried to its next lists.
        self.bar_length: QDoubleSpinBox | None = None
        if "length" not in matched:
            self.bar_length = QDoubleSpinBox()
            self.bar_length.setRange(0.0, 8.0)
            self.bar_length.setDecimals(2)
            self.bar_length.setSingleStep(0.5)
            self.bar_length.setSuffix(" מ׳")
            self.bar_length.setSpecialValueText("לא ידוע")
        self.unit: QComboBox | None = None
        if "unit" not in matched:
            # No unit column: every price is in one unit, and the person says
            # which — a per-kilogram list read as per-metre would be wrong.
            self.unit = QComboBox()
            self.unit.addItem("בחרו יחידה…", "")
            for code, label in (("m", "למטר"), ("kg", "לק״ג"), ("bar", "למוט"),
                                ("pc", "ליחידה"), ("m2", "למ״ר")):
                self.unit.addItem(label, code)

        self.grid.add("ספק", self.supplier)
        self.grid.add("מקור", self.source)
        self.grid.add("תחולה", date_holder)
        self.grid.add("מטבע", self.currency)
        if self.unit is not None:
            self.grid.add("המחירים הם", self.unit)
        if self.bar_length is not None:
            self.grid.add("אורך מוט", self.bar_length)
        self.grid.add("ההנחה שלכם", self.discount)
        self.grid.add("תוספת (מתכת, אנרגיה)", self.surcharge)
        self.grid.add("נקלט על ידי", self.recorded_by)

        names = {"code": "מק״ט", "description": "תיאור", "price": "מחיר", "unit": "יחידה",
                 "supplier": "ספק", "currency": "מטבע"}
        columns = " · ".join(
            f"{names.get(field, field)} ← ״{header}״" for field, header in matched.items()
        )
        text = f"⁦{count}⁩ שורות מחיר נקראו. עמודות: {columns}."
        if skipped:
            text += (f"\n⁦{len(skipped)}⁩ שורות לא נקראו או נקראו חלקית, למשל: "
                     + "; ".join(skipped[:3]))
        text += "\nשום מחיר לא משתנה עד שמאשרים את המחירון."
        hint = QLabel(text)
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().insertWidget(1, hint)

        self.supplier.currentTextChanged.connect(self._update_ok)
        self.supplier.currentTextChanged.connect(self._fill_terms)
        self.source.textChanged.connect(self._update_ok)
        if self.unit is not None:
            self.unit.currentIndexChanged.connect(self._update_ok)
        self._update_ok()
        self.supplier.setFocus()

    def _fill_terms(self, supplier: str) -> None:
        terms = self._terms.get(" ".join(supplier.split()).casefold())
        if terms is not None:
            self.discount.setValue(terms[0])
            self.surcharge.setValue(terms[1])
            if self.bar_length is not None and len(terms) > 2:
                self.bar_length.setValue(terms[2] / 1000.0)

    def _update_ok(self, *_: Any) -> None:
        self.ok_button.setEnabled(
            bool(self.supplier.currentText().strip()) and bool(self.source.text().strip())
            and (self.unit is None or bool(self.unit.currentData()))
        )

    def values(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier.currentText().strip(),
            "source": self.source.text().strip(),
            "valid_from": (
                self.valid_from.date().toString("yyyy-MM-dd") if self.has_date.isChecked() else ""
            ),
            "currency": self.currency.currentData() or "ILS",
            "recorded_by": self.recorded_by.text().strip(),
            "discount_pct": self.discount.value(),
            "surcharge_pct": self.surcharge.value(),
            "bar_length_mm": (
                round(self.bar_length.value() * 1000.0) if self.bar_length is not None
                and self.bar_length.value() > 0 else None
            ),
            "unit": self.unit.currentData() if self.unit is not None else "",
        }


class PriceLetterDialog(_FormDialog):
    """"+4% on everything from 1/12": the supplier's letter, as a new list."""

    accept_text = "שמירה כמחירון ממתין"

    def __init__(self, suppliers: list[str], parent: QWidget | None = None) -> None:
        super().__init__("שינוי מחירים באחוזים", parent)
        self.setMinimumWidth(480)
        from PySide6.QtCore import QDate

        self.supplier = QComboBox()
        self.supplier.addItems(list(suppliers))
        self.pct = QDoubleSpinBox()
        self.pct.setRange(-50.0, 100.0)
        self.pct.setDecimals(1)
        self.pct.setSuffix(" %")
        self.pct.setValue(0.0)
        self.source = QLineEdit()
        self.source.setPlaceholderText("מכתב מהספק מ־20/11, הודעת וואטסאפ מהסוכן…")
        self.valid_from = QDateEdit()
        self.valid_from.setCalendarPopup(True)
        self.valid_from.setDisplayFormat("dd/MM/yyyy")
        self.valid_from.setDate(QDate.currentDate())
        self.recorded_by = QLineEdit()
        self.recorded_by.setPlaceholderText("מי קלט")

        self.grid.add("ספק", self.supplier)
        self.grid.add("שינוי", self.pct)
        self.grid.add("בתוקף מ־", self.valid_from)
        self.grid.add("מקור", self.source)
        self.grid.add("נקלט על ידי", self.recorded_by)
        hint = QLabel(
            "כל המחירים במחירון האחרון שאושר מהספק יוזזו באחוז הזה. המחירון החדש "
            "ימתין לאישור, ותאריך עתידי ייתן זמן להזמין חומר במחיר הישן."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().insertWidget(1, hint)

        self.pct.valueChanged.connect(self._update_ok)
        self.source.textChanged.connect(self._update_ok)
        self._update_ok()

    def _update_ok(self, *_: Any) -> None:
        self.ok_button.setEnabled(abs(self.pct.value()) > 1e-9 and bool(self.source.text().strip()))

    def values(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier.currentText(),
            "pct": self.pct.value(),
            "source": self.source.text().strip(),
            "valid_from": self.valid_from.date().toString("yyyy-MM-dd"),
            "recorded_by": self.recorded_by.text().strip(),
        }


def _system_combo(selected: str = "") -> QComboBox:
    """Every series in the directory, plus "none" for a source not tied to one."""
    from ..systems import DIRECTORY

    combo = QComboBox()
    combo.addItem("לא משויך לסדרה מסוימת", "")
    for entry in sorted(DIRECTORY, key=lambda e: (e.manufacturer, e.series)):
        combo.addItem(entry.display, entry.id)
    index = combo.findData(selected)
    combo.setCurrentIndex(max(0, index))
    return combo


class RecordEditionDialog(_FormDialog):
    """Keep what was just read from a catalogue as a dated edition.

    Asks only what the comparison needs: where the catalogue came from (the
    provenance, required), which series, and whether it is the whole series
    or a partial pack — because only a whole catalogue can say a profile
    was dropped.
    """

    accept_text = "שמירה כמהדורה"

    def __init__(self, series: str = "", count: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__("שמירת מהדורת קטלוג", parent)
        self.setMinimumWidth(520)

        self.source = QLineEdit()
        self.source.setPlaceholderText("קטלוג קליל 4300, מהדורת מרץ 2026, מאתר הספק")
        self.system = _system_combo(self._guess(series))
        self.series = QLineEdit(series)
        self.series.setPlaceholderText("שם הסדרה, אם אינה ברשימה")
        self.revision = QLineEdit()
        self.revision.setPlaceholderText("כפי שמודפס בקטלוג: מהדורה 3, 03/2026")
        self.complete = QCheckBox("זה הקטלוג המלא של הסדרה, לא עדכון חלקי")
        self.recorded_by = QLineEdit()
        self.recorded_by.setPlaceholderText("מי קלט")

        self.grid.add("מקור", self.source)
        self.grid.add("סדרה", self.system)
        self.grid.add("שם הסדרה", self.series)
        self.grid.add("מהדורת הספק", self.revision)
        self.grid.add_span(self.complete)
        self.grid.add("נקלט על ידי", self.recorded_by)

        hint = QLabel(
            f"⁦{count}⁩ פרופילים יישמרו כמהדורה ממתינה ויושוו למה שאושר קודם. "
            "שום מספר לא מחליף את הספרייה עד שמאשרים את המהדורה."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().insertWidget(1, hint)

        self.source.textChanged.connect(self._update_ok)
        self.series.textChanged.connect(self._update_ok)
        self.system.currentIndexChanged.connect(self._update_ok)
        self._update_ok()
        self.source.setFocus()

    @staticmethod
    def _guess(series: str) -> str:
        from ..systems import DIRECTORY

        text = series.strip()
        if not text:
            return ""
        if DIRECTORY.get(text) is not None:
            return text
        exact = [e for e in DIRECTORY.search(text) if e.series.casefold() == text.casefold()]
        return exact[0].id if len(exact) == 1 else ""

    def _update_ok(self, *_: Any) -> None:
        self.ok_button.setEnabled(
            bool(self.source.text().strip())
            and bool(self.system.currentData() or self.series.text().strip())
        )

    def values(self) -> dict[str, Any]:
        return {
            "source": self.source.text().strip(),
            "system_id": self.system.currentData() or "",
            "series": self.series.text().strip(),
            "complete": self.complete.isChecked(),
            "recorded_by": self.recorded_by.text().strip(),
            "revision": self.revision.text().strip(),
        }


class WatchSourceDialog(_FormDialog):
    """A supplier folder or downloads page for the software to keep an eye on."""

    accept_text = "הוספה למעקב"

    def __init__(self, kind: str = "folder", parent: QWidget | None = None) -> None:
        title = "מעקב אחרי תיקייה" if kind == "folder" else "מעקב אחרי אתר ספק"
        super().__init__(title, parent)
        self.setMinimumWidth(520)
        self.kind = kind

        self.location = QLineEdit()
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(2))
        row.addWidget(self.location, 1)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        if kind == "folder":
            self.location.setPlaceholderText("התיקייה שבה נשמרים קבצי הספק")
            browse = QPushButton("בחירה…")
            browse.clicked.connect(self._browse)
            row.addWidget(browse)
        else:
            self.location.setPlaceholderText("כתובת דף ההורדות או הקטלוגים באתר הספק")
            self.location.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.supplier = QLineEdit()
        self.supplier.setPlaceholderText("קליל, אקסטל, אלומיל…")
        self.system = _system_combo()

        self.grid.add("מיקום", holder)
        self.grid.add("ספק", self.supplier)
        self.grid.add("סדרה", self.system)

        hint = QLabel(
            "התוכנה בודקת את המקור בעצמה ומודיעה כשמופיע בו קובץ קטלוג חדש "
            "או מעודכן. הבדיקה הראשונה רק רושמת את מה שכבר קיים."
            if kind == "folder" else
            "התוכנה בודקת את הדף פעם ביום ומודיעה כשמופיע בו קישור לקובץ קטלוג "
            "חדש, או כשקובץ קיים מתעדכן. היא לא מורידה ולא קולטת דבר בעצמה."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().insertWidget(1, hint)

        self.location.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(False)
        self.location.setFocus()

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(self, "תיקיית קבצי הספק", str(documents_dir()))
        if folder:
            self.location.setText(folder)

    def values(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "location": self.location.text().strip(),
            "supplier": self.supplier.text().strip(),
            "system_id": self.system.currentData() or "",
        }


__all__ = [
    "CloseServiceCallDialog",
    "ConfirmSystemDialog",
    "NewChequeDialog",
    "NewCustomerDialog",
    "NewJobDialog",
    "NewServiceCallDialog",
    "PriceLetterDialog",
    "PriceListDialog",
    "RecordEditionDialog",
    "WatchSourceDialog",
]


class QuickEstimateDialog(_FormDialog):
    """A price over the phone: type what the customer said, read the answer.

    Accepting the dialog means "design this properly" — the caller takes
    :attr:`estimate` and opens it on the element page as a real opening.
    """

    accept_text = "העבר לתכנון מלא"

    def __init__(self, parent: QWidget | None = None) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QLabel

        super().__init__("הערכת מחיר מהירה", parent)
        self.setMinimumWidth(560)

        self.query = QLineEdit()
        self.query.setPlaceholderText("מה הלקוח ביקש? למשל: הזזה 2 כנפיים 240/210")
        self.grid.add("מה מבקשים", self.query)

        self.result = QLabel("הקלד סוג פתח ומידה — המחיר יופיע כאן.")
        self.result.setObjectName("EstimateResult")
        self.result.setWordWrap(True)
        # Two lines of large type: the answer must never be the thing clipped.
        self.result.setMinimumHeight(METRICS.space(20))
        self.grid.add("הערכה", self.result)

        note = QLabel(
            "הערכה בלבד — לא הצעת מחיר מחייבת. מחושבת באותם כללים כמו הצעה "
            "מלאה, לפי המחירון שהוזן ובהעדרו לפי מחירי ברירת מחדל."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.layout().insertWidget(1, note)

        self.estimate: Any = None
        self.ok_button.setEnabled(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._update)
        self.query.textChanged.connect(lambda _text: self._timer.start())
        self.query.setFocus()

    def _update(self) -> None:
        from ..quoting.estimate import estimate

        text = self.query.text().strip()
        if not text:
            self.estimate = None
        else:
            try:
                self.estimate = estimate(text)
            except Exception:  # noqa: BLE001 - half a sentence is not an error
                self.estimate = None
        self.ok_button.setEnabled(self.estimate is not None)
        if self.estimate is None:
            self.result.setText(
                "לא זוהה פתח — נסה סוג ומידה, למשל ״חלון ציר 120/140״"
                if text else "הקלד סוג פתח ומידה — המחיר יופיע כאן."
            )
        else:
            self.result.setText(self.estimate.describe())


class AppointmentDialog(_FormDialog):
    """Put a visit in the diary: when, what, for whom, and who goes."""

    accept_text = "קבע ביקור"

    def __init__(self, parent: QWidget | None = None, *, jobs: list[Any] | None = None,
                 job: Any = None, kind: str = "", when: Any = None,
                 customer_lookup: Callable[[str], Any] | None = None) -> None:
        from datetime import date

        from ..projects.agenda import VisitKind, next_working_day

        super().__init__("ביקור ביומן", parent)
        self._jobs = list(jobs or [])
        self._lookup = customer_lookup

        self.on = QDateEdit()
        self.on.setCalendarPopup(True)
        self.on.setDisplayFormat("dd/MM/yyyy")
        self.on.setDate(when or next_working_day(date.today()))
        self.time = QComboBox()
        self.time.setEditable(True)
        self.time.addItems(["", "07:30", "08:00", "09:00", "10:00", "12:00", "14:00",
                            "08:00–12:00", "12:00–16:00"])
        self.time.lineEdit().setPlaceholderText("במהלך היום")
        self.kind = QComboBox()
        for option in VisitKind:
            self.kind.addItem(option.hebrew, option.value)
        if kind:
            self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.job = QComboBox()
        self.job.addItem("— בלי פרויקט —", "")
        for item in self._jobs:
            label = f"{item.job_id} · {item.name}"
            if item.customer_name:
                label += f" · {item.customer_name}"
            self.job.addItem(label, item.job_id)
        self.customer = QLineEdit()
        self.phone = QLineEdit()
        self.phone.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.phone.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        self.address = QLineEdit()
        self.crew = QComboBox()
        self.crew.setEditable(True)
        self.crew.addItems(["", "צוות א׳", "צוות ב׳", "טכנאי שירות", "אינסטלטור"])
        self.note = QLineEdit()
        self.note.setPlaceholderText("להביא סולם, קומה 3 בלי מעלית…")
        for widget in (self.customer, self.phone, self.address):
            widget.textEdited.connect(
                lambda _text, w=widget: w.setProperty("from_job", False)
            )

        for label, widget in (
            ("תאריך", self.on), ("שעה", self.time), ("סוג", self.kind),
            ("פרויקט", self.job), ("לקוח", self.customer), ("טלפון", self.phone),
            ("כתובת", self.address), ("מי יוצא", self.crew), ("הערה", self.note),
        ):
            self.grid.add(label, widget)

        self.job.currentIndexChanged.connect(self._job_chosen)
        if job is not None:
            index = self.job.findData(job.job_id)
            if index < 0:
                self._jobs.append(job)
                self.job.addItem(f"{job.job_id} · {job.name}", job.job_id)
                index = self.job.count() - 1
            self.job.setCurrentIndex(index)

    def _job_chosen(self, _index: int) -> None:
        wanted = self.job.currentData()
        job = next((item for item in self._jobs if item.job_id == wanted), None)
        if job is None:
            return
        # Choosing a job fills its customer in — and choosing another job
        # replaces what the first one filled, so a visit never goes out with
        # one customer's name and another's address. What was typed by hand
        # stays.
        phone = ""
        if self._lookup is not None:
            try:
                phone = self._lookup(job.job_id) or ""
            except Exception:  # noqa: BLE001 - typed by hand instead
                phone = ""
        for widget, value in ((self.customer, job.customer_name),
                              (self.address, job.site_address), (self.phone, phone)):
            if not widget.text().strip() or widget.property("from_job"):
                widget.setText(value or "")
                widget.setProperty("from_job", True)
        if job.trade.value == "plumbing" and self.kind.currentData() in ("other", "installation"):
            self.kind.setCurrentIndex(self.kind.findData("plumbing"))

    def accept(self) -> None:  # noqa: D401 - Qt override
        """A visit nobody can find is refused: it needs a job, a name or an address."""
        if not (self.job.currentData() or self.customer.text().strip()
                or self.address.text().strip()):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "חסרים פרטים", "לאן יוצאים? בחרו פרויקט או כתבו לקוח או כתובת.")
            return
        super().accept()

    def appointment(self) -> Any:
        from ..projects.agenda import Appointment, VisitKind

        return Appointment(
            on=self.on.date().toPython(),
            kind=VisitKind(self.kind.currentData()),
            time=self.time.currentText().strip(),
            customer_name=self.customer.text().strip(),
            phone=self.phone.text().strip(),
            address=self.address.text().strip(),
            job_id=self.job.currentData() or "",
            crew=self.crew.currentText().strip(),
            note=self.note.text().strip(),
        )
