"""The gate, as the operator meets it.

Three things are asked for and all three must be right; the box says which
*layer* failed — no key file, wrong machine, wrong credentials — and never
which of the three credentials was wrong, because that is what turns guessing
into a search.

The dialog is deliberately plain. A login box that tries to be clever is a
login box somebody clicks past.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.logging_setup import get_logger
from .theme import METRICS, Palette
from .widgets import Card, FieldGrid

_log = get_logger("ui.login")


class LoginDialog(QDialog):
    """Ask for the USB key, the password and the security answers."""

    def __init__(
        self,
        gate: Any,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.gate = gate
        self.session: Any = None
        self._key_path: Path | None = None
        self._answers: list[QLineEdit] = []

        brand = self._brand_name()
        self.setWindowTitle(f"{brand} — sign in")
        self.setModal(True)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(METRICS.space(5),) * 4)
        layout.setSpacing(METRICS.space(4))

        heading = QLabel(brand)
        heading.setObjectName("SidebarLogo")
        layout.addWidget(heading)

        self.key_label = QLabel()
        self.key_label.setObjectName("Hint")
        self.key_label.setWordWrap(True)
        layout.addWidget(self.key_label)

        key_row = QHBoxLayout()
        find = QPushButton("Look for the key again")
        find.clicked.connect(self._find_key)
        choose = QPushButton("Choose the key file…")
        choose.clicked.connect(self._choose_key)
        key_row.addWidget(find)
        key_row.addWidget(choose)
        key_row.addStretch(1)
        layout.addLayout(key_row)

        self.card = Card("Credentials")
        self.grid = FieldGrid()
        self.username = QLineEdit()
        self.username.setPlaceholderText("username")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.grid.add("Username", self.username)
        self.grid.add("Password", self.password)
        self.card.add(self.grid)
        layout.addWidget(self.card)

        self.message = QLabel()
        self.message.setObjectName("Hint")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Sign in")
        self.ok.setObjectName("Primary")
        buttons.accepted.connect(self._attempt)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._find_key()

    # -- brand ---------------------------------------------------------------- #
    def _brand_name(self) -> str:
        try:
            from ..branding import active_brand

            return active_brand().display_name
        except Exception:  # noqa: BLE001 - the dialog must open regardless
            return "ProfileOS"

    # -- key ------------------------------------------------------------------ #
    def _find_key(self) -> None:
        found = self.gate.key_files()
        self._key_path = found[0] if found else None
        self._refresh_questions()

    def _choose_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "ProfileOS key file", "", "Key file (*.key);;All files (*)"
        )
        if path:
            self._key_path = Path(path)
            self._refresh_questions()

    def _refresh_questions(self) -> None:
        """Load the questions off the key, or explain why we cannot."""
        for widget in self._answers:
            widget.setParent(None)
        self._answers = []

        if self._key_path is None:
            self.key_label.setText(
                "No key file found. Insert the ProfileOS USB key, then press "
                "“Look for the key again”."
            )
            self.ok.setEnabled(False)
            return

        try:
            prompts = self.gate.prompts(key_path=self._key_path)
        except Exception as exc:  # noqa: BLE001 - shown, never raised at the user
            self.key_label.setText(f"That key cannot open this installation: {exc}")
            self.ok.setEnabled(False)
            return

        self.key_label.setText(f"Key found: {self._key_path}")
        for prompt in prompts:
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            self.grid.add(prompt, field)
            self._answers.append(field)
        self.ok.setEnabled(True)

    # -- attempt --------------------------------------------------------------- #
    def _attempt(self) -> None:
        from ..security.gate import AccessDenied

        try:
            self.session = self.gate.authenticate(
                self.username.text(),
                self.password.text(),
                [field.text() for field in self._answers],
                key_path=self._key_path,
            )
        except AccessDenied as exc:
            self.message.setText(str(exc))
            self.password.clear()
            for field in self._answers:
                field.clear()
            self.password.setFocus()
            return
        except Exception as exc:  # noqa: BLE001
            self.message.setText(str(exc))
            return
        self.accept()


def require_login(palette: Palette, parent: QWidget | None = None) -> Any:
    """Show the gate. Returns the session, or ``None`` if the operator gave up.

    An installation with no operator yet is let through: somebody has to be
    able to run ``access enrol``, and locking the software before it has an
    owner would lock everybody out including its owner.
    """
    from ..security.gate import Gate

    gate = Gate()
    if not gate.is_enrolled:
        _log.warning(
            "No operator is enrolled; running unlocked. Run "
            "`profileos access enrol` to lock this installation."
        )
        return None

    dialog = LoginDialog(gate, palette, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.session
    return None


__all__ = ["LoginDialog", "require_login"]
