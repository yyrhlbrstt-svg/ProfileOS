"""The dialogs that create things: a job, a customer, a call, a cheque.

Both follow the same rule — the fewest fields that make the record useful, and
nothing that can be filled in later. A shop opening a job while the customer
is still on the phone should not be stopped by a form.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from .widgets import FieldGrid


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

    accept_text = "פתיחת פרויקט"

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

        self.grid.add("לקוח", row)
        self.grid.add("שם הפרויקט", self.name)
        self.grid.add("אתר", self.site)
        self.grid.add("אסמכתה", self.reference)
        self.grid.add("מערכת", self.system)

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
        index = self.customer.findData(select) if select else 0
        self.customer.setCurrentIndex(max(index, 0))
        self.customer.blockSignals(False)

    def _selected(self) -> Any:
        wanted = self.customer.currentData()
        # A name typed and never picked from the list still counts when it
        # matches exactly one customer.
        if not wanted:
            typed = self.customer.currentText().strip()
            matches = [c for c in self._customers if c.name == typed]
            return matches[0] if len(matches) == 1 else None
        return next((c for c in self._customers if c.customer_id == wanted), None)

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
        if typed and not self.customer.currentData():
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
        }


class NewCustomerDialog(_FormDialog):
    """Add a customer, or correct one. Only the name is required.

    Details usually arrive as a block — a shared WhatsApp contact, an email
    signature — so the first box takes the whole block and fills the fields
    from it. The phone is checked while it is typed and written back the one
    way the shop writes numbers, and a customer who is already in the book
    is pointed out before a second copy is made.
    """

    accept_text = "הוספת לקוח"

    def __init__(self, parent: QWidget | None = None, *,
                 customers: list[Any] | None = None, customer: Any = None) -> None:
        super().__init__("עריכת לקוח" if customer is not None else "לקוח חדש", parent)
        if customer is not None:
            self.ok_button.setText("שמירת שינויים")
        self.setMinimumWidth(480)
        self._customers = list(customers or [])
        self._editing = customer

        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText(
            "הדבק כאן פרטים — איש קשר מוואטסאפ, חתימת מייל, הודעה — "
            "והשדות יתמלאו לבד"
        )
        self.paste.setMaximumHeight(64)
        self.paste.setTabChangesFocus(True)
        self.paste.textChanged.connect(self._paste_changed)

        self.name = QLineEdit()
        self.name.setPlaceholderText("משה כהן / קבלן בע״מ")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("054-123-4567")
        self.phone.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.contact = QLineEdit()
        self.contact.setPlaceholderText("אם יש מישהו אחר שמדברים איתו")
        self.email = QLineEdit()
        self.email.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
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
        self.notes.setMaximumHeight(70)
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
        for key, value in parsed.as_dict().items():
            widget = targets[key]
            # Never over what somebody typed by hand.
            if not widget.text().strip() or widget.property("filled"):
                widget.setText(value)
                widget.setProperty("filled", True)
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
        self.ok_button.setEnabled(bool(self.name.text().strip()) and not problems)

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

        for symptom in Symptom:
            self.symptom.addItem(symptom.hebrew, symptom.value)

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


class NewChequeDialog(_FormDialog):
    """Take a cheque into the drawer."""

    accept_text = "רישום צ׳ק"

    def __init__(self, parent: QWidget | None = None) -> None:
        from datetime import date

        super().__init__("צ׳ק חדש", parent)

        self.customer = QLineEdit()
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, 10_000_000)
        self.amount.setValue(1000.0)
        self.amount.setSuffix(" ₪")
        self.due = QDateEdit()
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("dd/MM/yyyy")
        self.due.setDate(date.today())
        self.bank = QLineEdit()
        self.number = QLineEdit()
        self.number.setPlaceholderText("מספר הצ׳ק")
        self.job_id = QLineEdit()

        for label, widget in (
            ("לקוח", self.customer), ("סכום", self.amount),
            ("תאריך פירעון", self.due), ("בנק", self.bank),
            ("מספר", self.number), ("פרויקט", self.job_id),
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
            "amount": self.amount.value(),
            "due": self.due.date().toPython(),
            "bank": self.bank.text().strip(),
            "number": self.number.text().strip(),
            "job_id": self.job_id.text().strip(),
        }


class ConfirmSystemDialog(_FormDialog):
    """The eleven numbers that turn a series into one the shop may cut to.

    Laid out in the order a catalogue prints them, each with the place to look
    for it, so somebody can work down the page with the book open beside them
    instead of hunting for each field.
    """

    accept_text = "אשר את הסדרה"

    def __init__(
        self,
        series: str,
        existing: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"נתוני יצרן — {series}", parent)
        self.setMinimumWidth(560)

        from ..systems import FIGURES

        self.source = QLineEdit()
        self.source.setPlaceholderText("קטלוג קליל 7300, מהדורת 2026, עמ׳ 41")
        self.entered_by = QLineEdit()
        self.entered_by.setPlaceholderText("מי קרא את המספרים")

        self.grid.add("מקור", self.source)
        self.grid.add("הוזן על ידי", self.entered_by)

        self.figures: dict[str, QDoubleSpinBox] = {}
        for figure in FIGURES:
            box = QDoubleSpinBox()
            box.setRange(figure.minimum, figure.maximum)
            box.setDecimals(1)
            box.setSuffix(f" {figure.unit}")
            box.setToolTip(figure.where)
            if figure.default is not None:
                box.setValue(figure.default)
            self.figures[figure.key] = box
            label = figure.hebrew if figure.required else f"{figure.hebrew} (לא חובה)"
            self.grid.add(label, box)

        self.profiles: dict[str, QLineEdit] = {}
        for role, label in (
            ("frame", "מק״ט משקוף"), ("sash", "מק״ט כנף"),
            ("mullion", "מק״ט זקף"), ("bead", "מק״ט סרגל"),
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
            "figures": {key: box.value() for key, box in self.figures.items()},
            "profiles": {
                role: field.text().strip()
                for role, field in self.profiles.items()
                if field.text().strip()
            },
        }


__all__ = [
    "CloseServiceCallDialog",
    "ConfirmSystemDialog",
    "NewChequeDialog",
    "NewCustomerDialog",
    "NewJobDialog",
    "NewServiceCallDialog",
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
