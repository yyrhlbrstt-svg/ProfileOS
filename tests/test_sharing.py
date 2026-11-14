"""Tests for the WhatsApp sharing helpers."""

from __future__ import annotations

from urllib.parse import unquote

import pytest

from profileos.sharing import quote_message, status_message, whatsapp_link, whatsapp_number


class TestNumbers:
    @pytest.mark.parametrize("raw", [
        "054-1234567", "0541234567", "+972 54 123 4567", "972-54-1234567",
        "00972541234567", "(054) 123-4567",
    ])
    def test_israeli_mobile_in_every_common_spelling(self, raw):
        assert whatsapp_number(raw) == "972541234567"

    def test_landline(self):
        assert whatsapp_number("02-9973510") == "97229973510"

    @pytest.mark.parametrize("raw", ["", None, "12", "054-12", "+1 555 123 4567", "abc"])
    def test_unreadable_numbers_are_not_guessed(self, raw):
        assert whatsapp_number(raw) is None


class TestLinks:
    def test_link_carries_the_number_and_the_text(self):
        link = whatsapp_link("054-1234567", "שלום עולם")
        assert link.startswith("https://wa.me/972541234567?text=")
        assert unquote(link.split("text=", 1)[1]) == "שלום עולם"

    def test_without_a_number_whatsapp_asks_for_the_chat(self):
        assert whatsapp_link("", "x").startswith("https://wa.me/?text=")


class TestMessages:
    def test_quote_message_names_the_customer_job_and_total(self):
        text = quote_message(
            brand_name='דאדי בע"מ', customer_name="דוד כהן", job_name="וילה כהן",
            quote_id="Q-1", total=2121.9, valid_days=30,
        )
        assert "שלום דוד כהן," in text
        assert "וילה כהן" in text and "Q-1" in text
        assert "2,122" in text and "₪" in text
        assert text.rstrip().endswith('דאדי בע"מ')

    def test_quote_message_without_a_total_does_not_invent_one(self):
        text = quote_message(brand_name="X", total=None)
        assert "סה״כ" not in text

    @pytest.mark.parametrize("status", [
        "enquiry", "quoted", "won", "in_production", "installed", "lost",
    ])
    def test_every_status_has_its_own_line(self, status):
        text = status_message(brand_name="X", status=status, job_name="J")
        assert "לגבי J:" in text
        assert "רצינו לעדכן אותך על מצב ההזמנה" not in text

    def test_unknown_status_falls_back_politely(self):
        assert "מצב ההזמנה" in status_message(brand_name="X", status="??")


class TestPhoneEstimate:
    def test_a_spoken_sentence_becomes_a_priced_opening(self):
        from profileos.quoting.estimate import estimate

        result = estimate("הזזה 2 כנפיים 240/210")
        assert result is not None
        assert (result.width, result.height) == (2400, 2100)
        assert result.gross > result.net > 0
        assert "כולל מע״מ" in result.describe()

    def test_nothing_recognisable_is_not_priced(self):
        from profileos.quoting.estimate import estimate

        assert estimate("xyzzy") is None

    def test_the_estimate_is_the_same_opening_the_form_would_build(self):
        from profileos.library import search_openings
        from profileos.quoting.estimate import opening_from_preset

        preset = search_openings("חלון ציר 120/140", limit=1)[0]
        opening = opening_from_preset(preset)
        assert (opening.width, opening.height) == (preset.width, preset.height)
        assert opening.column_count == preset.columns


class TestChequeReminders:
    def test_a_bounced_cheque_asks_for_another_payment(self):
        from datetime import date

        from profileos.sharing import cheque_message

        text = cheque_message(
            brand_name="X", customer_name="כהן", amount=2500,
            due=date(2026, 11, 1), bounced=True,
        )
        assert "חזר מהבנק" in text and "2,500" in text and "01/11/2026" in text

    def test_a_coming_cheque_is_a_friendly_reminder(self):
        from datetime import date

        from profileos.sharing import cheque_message

        text = cheque_message(
            brand_name="X", amount=900, due=date(2026, 11, 1), bounced=False,
        )
        assert "תזכורת" in text and "חזר" not in text
