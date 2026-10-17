"""Entering a customer takes a paste, not a form."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from profileos.projects.model import Customer  # noqa: E402
from profileos.ui.dialogs import NewCustomerDialog, NewJobDialog  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


def test_a_pasted_block_fills_the_form(qt_app):
    dialog = NewCustomerDialog()
    dialog.paste.setPlainText("דוד ישראלי\n0501112233\nרחוב האלה 3, עפרה")
    values = dialog.values()
    assert values["name"] == "דוד ישראלי"
    assert values["phone"] == "050-111-2233"
    assert values["city"] == "עפרה"
    assert dialog.ok_button.isEnabled()


def test_a_doubtful_number_is_marked_not_refused(qt_app):
    dialog = NewCustomerDialog()
    dialog.name.setText("דוד")
    dialog.phone.setText("+1 212 555 0100")
    assert dialog.phone.property("invalid")
    assert not dialog.problem.isHidden()
    assert dialog.ok_button.isEnabled()
    dialog.phone.setText("0501112233")
    assert not dialog.phone.property("invalid")


def test_a_bad_email_holds_the_form(qt_app):
    dialog = NewCustomerDialog()
    dialog.name.setText("דוד")
    dialog.email.setText("david@")
    assert not dialog.ok_button.isEnabled()


def test_what_was_typed_by_hand_survives_the_paste_box(qt_app):
    dialog = NewCustomerDialog()
    dialog.paste.setPlainText("דוד ישראלי\n0501112233")
    # What a keystroke does: the text changes and textEdited is sent.
    dialog.name.setText("דוד ישראלי-כהן")
    dialog.name.textEdited.emit("דוד ישראלי-כהן")
    dialog.paste.setPlainText("דוד ישראלי\n0501112233\nעפרה")
    assert dialog.name.text() == "דוד ישראלי-כהן"
    assert dialog.city.text() == "עפרה"
    dialog.paste.setPlainText("")
    assert dialog.city.text() == ""
    assert dialog.name.text() == "דוד ישראלי-כהן"


def test_typing_over_a_chosen_customer_unchooses_them(qt_app):
    book = [Customer(customer_id="C-0004", name="John Smith"),
            Customer(customer_id="C-0005", name="משה כהן")]
    dialog = NewJobDialog(book, [("generic", "כללי")])
    dialog.customer.setCurrentIndex(dialog.customer.findData("C-0004"))
    dialog.customer.setEditText("משה כהן")
    assert dialog.values()["customer_id"] == "C-0005"
    dialog.customer.setEditText("מישהו חדש")
    assert dialog.values()["customer_id"] == ""


def test_a_customer_already_in_the_book_is_pointed_out(qt_app):
    book = [Customer(customer_id="C-0001", name="משה כהן", phone="054-123-4567")]
    dialog = NewCustomerDialog(customers=book)
    dialog.name.setText("מוישה")
    dialog.phone.setText("0541234567")
    assert dialog.duplicates and dialog.duplicates[0].customer_id == "C-0001"
    assert not dialog.duplicate.isHidden()


def test_editing_shows_what_is_there(qt_app):
    customer = Customer(customer_id="C-0001", name="משה כהן", city="בית אל",
                        notes="קומה 3 בלי מעלית")
    dialog = NewCustomerDialog(customer=customer, customers=[customer])
    assert dialog.name.text() == "משה כהן"
    assert dialog.values()["notes"] == "קומה 3 בלי מעלית"
    assert dialog.duplicate.isHidden()


def test_choosing_a_customer_fills_the_site_and_a_name(qt_app):
    customer = Customer(customer_id="C-0001", name="משה כהן", city="בית אל",
                        address="הזית 5")
    dialog = NewJobDialog([customer], [("generic", "כללי")])
    dialog.customer.setCurrentIndex(dialog.customer.findData("C-0001"))
    values = dialog.values()
    assert values["customer_id"] == "C-0001"
    assert values["site_address"] == "הזית 5, בית אל"
    assert values["name"] == "משה כהן — בית אל"
    assert dialog.ok_button.isEnabled()


def test_a_new_customer_can_be_added_from_the_job_dialog(qt_app, monkeypatch):
    added = []

    def add(values):
        customer = Customer(customer_id="C-0009", **values)
        added.append(customer)
        return customer

    dialog = NewJobDialog([], [("generic", "כללי")], add_customer=add)
    monkeypatch.setattr(NewCustomerDialog, "exec", lambda self: (
        self.name.setText("רחל לוי"), self.city.setText("פסגות"),
        NewCustomerDialog.DialogCode.Accepted,
    )[-1])
    dialog.new_customer()
    assert added and dialog.values()["customer_id"] == "C-0009"
    assert dialog.site.text() == "פסגות"
