"""The first time the program opens on a new computer: who the shop is.

A program handed to a new shop must not greet it with somebody else's name.
On a fresh installation — no jobs, never welcomed — one calm screen asks for
the business's own details, and from then on every page, quotation and
document carries them. Nothing is filled in for the person: an empty field
stays empty and is simply not printed.

A shop already working in the program (it has jobs) is never shown this; it
is marked as welcomed the first time the check runs.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import METRICS

#: (key, label, placeholder) — in the order a shop thinks of them.
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("name", "שם העסק", 'למשל: אלומיניום הגליל בע"מ'),
    ("tagline", "במה עוסקים", "למשל: חלונות, דלתות וחזיתות"),
    ("city", "עיר", ""),
    ("phone", "טלפון", ""),
    ("email", "אימייל", ""),
    ("registration_number", "ח.פ. / ע.מ.", "מודפס על הצעות מחיר"),
)


def needs_welcome() -> bool:
    """Whether this installation should be welcomed now. A shop that already
    has jobs is marked as welcomed instead — it never sees the screen."""
    from ..core.config import get_settings, save_settings
    from ..showroom import is_showroom

    if is_showroom():
        return False
    settings = get_settings()
    if getattr(settings, "onboarded", False):
        return False
    try:
        from ..projects import default_store

        store = default_store()
        has_jobs = bool(store.all()) or bool(getattr(store, "unreadable", []))
    except Exception:  # noqa: BLE001 - unknown is treated as "already working"
        has_jobs = True
    if has_jobs:
        settings.onboarded = True
        try:
            save_settings(settings)
        except Exception:  # noqa: BLE001 - asked again next time, no harm
            pass
        return False
    # A new installation presents nobody else's name, even before the
    # welcome is answered: the neutral brand until the shop types its own.
    if settings.brand_id != "custom":
        from ..branding import set_active_brand

        settings.brand_id = "custom"
        try:
            save_settings(settings)
        except Exception:  # noqa: BLE001 - still neutral for this run
            pass
        set_active_brand("custom")
    return True


def save_company(values: dict[str, str]) -> None:
    """The shop's details kept, and the program presenting them from now on."""
    from ..branding import set_active_brand
    from ..core.config import get_settings, save_settings

    settings = get_settings()
    settings.company = {key: value.strip() for key, value in values.items() if value.strip()}
    settings.company.setdefault("legal_name", settings.company.get("name", ""))
    settings.brand_id = "custom"
    settings.onboarded = True
    save_settings(settings)
    set_active_brand("custom")


class WelcomeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ברוכים הבאים ל־ProfileOS")
        self.setMinimumWidth(640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*(METRICS.space(8),) * 4)
        outer.setSpacing(METRICS.space(3))

        title = QLabel("ברוכים הבאים ל־ProfileOS")
        title.setObjectName("WelcomeTitle")
        lead = QLabel("תוכנה אחת למפעל האלומיניום: תכנון ותלת־ממד, הצעות מחיר, חיתוך וייצור, "
                      "התקנה, גבייה וצ׳קים. נתחיל בפרטי העסק — הם יופיעו בכל מסך ובכל מסמך.")
        lead.setObjectName("WelcomeLead")
        lead.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(lead)

        card = QFrame()
        card.setObjectName("Card")
        form = QVBoxLayout(card)
        form.setContentsMargins(*(METRICS.space(5),) * 4)
        form.setSpacing(METRICS.space(2))
        self.inputs: dict[str, QLineEdit] = {}
        for key, label, placeholder in FIELDS:
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            caption.setMinimumWidth(110)
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            self.inputs[key] = field
            row.addWidget(caption)
            row.addWidget(field, 1)
            form.addLayout(row)
        outer.addWidget(card)

        self.showroom = QCheckBox("לפתוח גם חלון הדגמה — מפעל לדוגמה עם פרויקטים, הצעות וצ׳קים")
        self.showroom.setChecked(True)
        outer.addWidget(self.showroom)
        self.problem = QLabel("")
        self.problem.setObjectName("FieldError")
        outer.addWidget(self.problem)

        buttons = QHBoxLayout()
        later = QPushButton("אחר כך")
        later.clicked.connect(self.reject)
        start = QPushButton("להתחיל")
        start.setObjectName("Primary")
        start.setDefault(True)
        start.clicked.connect(self.start)
        buttons.addWidget(start)
        buttons.addWidget(later)
        buttons.addStretch(1)
        outer.addLayout(buttons)
        self.inputs["name"].setFocus(Qt.FocusReason.OtherFocusReason)

    def values(self) -> dict[str, str]:
        return {key: field.text() for key, field in self.inputs.items()}

    def start(self) -> bool:
        if not self.inputs["name"].text().strip():
            self.problem.setText("מה שם העסק? הוא יופיע בכותרת, בהצעות ובמסמכים")
            return False
        try:
            save_company(self.values())
        except Exception as exc:  # noqa: BLE001 - said in the window
            self.problem.setText(f"הפרטים לא נשמרו: {exc}")
            return False
        self.accept()
        return True


def present(window: Any, before: str) -> None:
    """The open window shows the shop's own name at once — title, sidebar
    and the home page's greeting — keeping anything after the name (the
    signed-in person, the showroom note)."""
    from ..branding import active_brand

    brand = active_brand()
    title = window.windowTitle()
    window.setWindowTitle(brand.window_title() + title[len(before):]
                          if title.startswith(before) else brand.window_title())
    sidebar = getattr(window, "sidebar", None)
    if sidebar is not None:
        sidebar.refresh_brand()
    for page in getattr(window, "pages", []):
        if hasattr(page, "_brand_name"):
            page._brand_name = brand.display_name
            page.refresh()


def welcome(window: Any) -> bool:
    """Show the welcome if this installation needs it. Returns whether the
    shop's details were saved."""
    from ..branding import active_brand

    before = active_brand().window_title()      # the name shown until now
    if not needs_welcome():
        return False
    present(window, before)            # neutral at once, answered or not
    before = active_brand().window_title()
    dialog = WelcomeDialog(window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    present(window, before)
    if dialog.showroom.isChecked() and hasattr(window, "open_showroom"):
        window.open_showroom()
    return True


__all__ = ["FIELDS", "WelcomeDialog", "needs_welcome", "present", "save_company", "welcome"]
