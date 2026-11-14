"""A new shop's first start: its own name, never somebody else's."""

from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A brand-new installation: empty data folder, default settings."""
    import profileos.branding as branding
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("PROFILEOS_SHOWROOM", raising=False)
    monkeypatch.setattr(branding, "_active_brand_id", None)
    reload_settings()
    yield tmp_path
    branding._active_brand_id = None
    # The environment back as it was (the run's own settings folder), then
    # re-read: removing the variable alone keeps the folder last read.
    monkeypatch.undo()
    reload_settings()


def test_a_fresh_installation_is_welcomed(fresh):
    from profileos.ui.welcome import needs_welcome

    assert needs_welcome()
    assert needs_welcome()          # until the shop says who it is


def test_a_shop_already_working_is_never_asked(fresh):
    from profileos.core.config import get_settings, reload_settings
    from profileos.projects import JobFile, default_store
    from profileos.ui.welcome import needs_welcome

    default_store().save(JobFile(job_id="J-1", name="מטבח", customer_name="לקוח"))
    assert not needs_welcome()
    reload_settings()
    assert get_settings().onboarded          # remembered: not checked again
    assert get_settings().brand_id == "dadi"  # and its name untouched


def test_the_showroom_window_is_never_asked(fresh, monkeypatch):
    from profileos.ui.welcome import needs_welcome

    monkeypatch.setenv("PROFILEOS_SHOWROOM", "1")
    assert not needs_welcome()


def test_the_shop_s_own_details_carry_through(fresh, qapp):
    from profileos.branding import active_brand
    from profileos.core.config import get_settings, reload_settings
    from profileos.ui.welcome import WelcomeDialog, needs_welcome

    dialog = WelcomeDialog()
    assert not dialog.start()                       # a name is needed
    assert "שם העסק" in dialog.problem.text()
    dialog.inputs["name"].setText('  אלומיניום הגליל בע"מ ')
    dialog.inputs["city"].setText("כרמיאל")
    dialog.inputs["phone"].setText("04-9999999")
    assert dialog.start()

    brand = active_brand()
    assert brand.name == 'אלומיניום הגליל בע"מ'
    assert brand.city == "כרמיאל" and brand.phone == "04-9999999"
    assert brand.email is None                      # nothing typed, nothing invented
    assert brand.window_title().startswith('אלומיניום הגליל בע"מ')
    reload_settings()
    assert get_settings().brand_id == "custom" and get_settings().onboarded
    assert not needs_welcome()


def test_the_open_window_shows_the_new_name_at_once(fresh, qapp):
    from profileos.branding import active_brand
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT
    from profileos.ui.welcome import present, save_company

    window = MainWindow(LIGHT)
    before = active_brand().window_title()
    assert "דאדי" in window.windowTitle()
    save_company({"name": "חלונות השרון", "tagline": "חלונות ודלתות"})
    present(window, before)
    assert window.windowTitle().startswith("חלונות השרון")
    assert "דאדי" not in window.windowTitle()
    assert window.sidebar.logo.text() == "חלונות השרון"
    home = window.pages[0]
    assert "חלונות השרון" in home.greeting.text()
    window.close()


def test_the_installer_puts_no_sample_jobs_in_the_shop_s_books():
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "install" / "התקנה.bat").read_text(
        encoding="utf-8")
    assert "seed" not in script
    assert "דאדי" not in script


def test_a_new_shop_that_says_later_still_quotes_under_no_other_name(fresh, qapp, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from profileos.branding import active_brand
    from profileos.ui import welcome as module
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    window = MainWindow(LIGHT)
    assert "דאדי" in window.windowTitle()
    monkeypatch.setattr(module.WelcomeDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not module.welcome(window)
    brand = active_brand()
    assert "דאדי" not in brand.name and brand.phone is None
    assert window.windowTitle() == "ProfileOS"
    assert "דאדי" not in window.sidebar.logo.text()
    window.close()
