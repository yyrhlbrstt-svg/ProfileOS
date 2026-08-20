"""The two dialogs that create things: a job, and a customer.

Both follow the same rule — the fewest fields that make the record useful, and
nothing that can be filled in later. A shop opening a job while the customer
is still on the phone should not be stopped by a form.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
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


__all__ = ["NewCustomerDialog", "NewJobDialog"]
