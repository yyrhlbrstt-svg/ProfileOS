"""The dialogs that create things: a job, a customer, a call, a cheque.

Both follow the same rule — the fewest fields that make the record useful, and
nothing that can be filled in later. A shop opening a job while the customer
is still on the phone should not be stopped by a form.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLineEdit,
    QPlainTextEdit,
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
    """Open a job: a name, who it is for, and where it is."""

    accept_text = "פתיחת פרויקט"

    def __init__(self, customers: list[Any], systems: list[tuple[str, str]],
                 parent: QWidget | None = None) -> None:
        super().__init__("פרויקט חדש", parent)

        self.name = QLineEdit()
        self.name.setPlaceholderText("וילה בשכונת הגבעה")
        self.customer = QComboBox()
        self.customer.addItem("— ללא לקוח —", "")
        for customer in customers:
            self.customer.addItem(customer.name, customer.customer_id)
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("מספר הזמנה של הלקוח")
        self.site = QLineEdit()
        self.site.setPlaceholderText("כתובת האתר")
        self.system = QComboBox()
        for entry_id, label in systems:
            self.system.addItem(label, entry_id)

        self.grid.add("שם הפרויקט", self.name)
        self.grid.add("לקוח", self.customer)
        self.grid.add("אסמכתה", self.reference)
        self.grid.add("אתר", self.site)
        self.grid.add("מערכת", self.system)

        # A job without a name is a job nobody can find again.
        self.name.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(False)
        self.name.setFocus()

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "customer_id": self.customer.currentData() or "",
            "reference": self.reference.text().strip(),
            "site_address": self.site.text().strip(),
            "system_id": self.system.currentData() or "generic",
        }


class NewCustomerDialog(_FormDialog):
    """Add a customer. Only the name is required."""

    accept_text = "הוספת לקוח"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("לקוח חדש", parent)

        self.name = QLineEdit()
        self.contact = QLineEdit()
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("02-9973510")
        self.email = QLineEdit()
        self.city = QLineEdit()
        self.tax_id = QLineEdit()
        self.tax_id.setPlaceholderText("ח.פ. / ע.מ.")

        self.grid.add("שם", self.name)
        self.grid.add("איש קשר", self.contact)
        self.grid.add("טלפון", self.phone)
        self.grid.add("דוא\"ל", self.email)
        self.grid.add("עיר", self.city)
        self.grid.add("מספר עוסק", self.tax_id)

        self.name.textChanged.connect(
            lambda text: self.ok_button.setEnabled(bool(text.strip()))
        )
        self.ok_button.setEnabled(False)
        self.name.setFocus()

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "contact": self.contact.text().strip(),
            "phone": self.phone.text().strip(),
            "email": self.email.text().strip(),
            "city": self.city.text().strip(),
            "tax_id": self.tax_id.text().strip(),
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


__all__ = [
    "CloseServiceCallDialog",
    "NewChequeDialog",
    "NewCustomerDialog",
    "NewJobDialog",
    "NewServiceCallDialog",
]
