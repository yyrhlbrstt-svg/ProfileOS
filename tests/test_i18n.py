"""Language tests.

Two kinds of thing are checked. The first is completeness — every term has a
word in every language, and the test fails when somebody adds a key and forgets
five of them. The second is the part that is not words at all: direction,
digits and decimal marks, where getting it wrong puts a wrong number on a
cutting list rather than an odd-looking label on a screen.
"""

from __future__ import annotations

from datetime import date

import pytest

from profileos.i18n import (
    LOCALES,
    Language,
    MissingMessage,
    available,
    catalogue,
    get_locale,
    missing,
    negotiate,
    require,
    translate,
)


class TestCompleteness:
    def test_every_term_exists_in_every_language(self):
        assert missing() == {}

    def test_all_six_languages_are_on_offer(self):
        assert {locale.code for locale in available()} == {
            "he", "en", "ar", "ru", "it", "es"
        }

    def test_a_catalogue_covers_the_whole_vocabulary(self):
        from profileos.i18n.messages import MESSAGES

        for language in Language:
            assert set(catalogue(language)) == set(MESSAGES)

    def test_no_two_languages_share_a_translation_by_accident(self):
        """A key left untranslated shows up as Hebrew matching English."""
        hebrew = catalogue("he")
        english = catalogue("en")
        identical = [
            key for key, value in hebrew.items()
            if value == english[key] and not key.startswith(("unit.", "material.epdm"))
        ]
        assert identical == []


class TestFallback:
    def test_an_unknown_key_reads_as_a_word_not_a_token(self):
        """An operator seeing 'stage.machined' learns nothing."""
        assert translate("stage.no_such_thing", "he") == "no such thing"

    def test_require_raises_where_a_developer_will_see_it(self):
        with pytest.raises(MissingMessage):
            require("nothing.here")

    def test_an_unknown_language_falls_back_to_the_default(self):
        assert get_locale("zz").code == "he"
        assert translate("stage.cut", "zz") == translate("stage.cut", "he")


class TestDirection:
    @pytest.mark.parametrize("code,rtl", [("he", True), ("ar", True), ("ru", False),
                                          ("en", False), ("it", False), ("es", False)])
    def test_direction_is_a_property_of_the_language(self, code, rtl):
        assert get_locale(code).rtl is rtl


class TestNumbers:
    def test_each_language_writes_a_number_its_own_way(self):
        assert get_locale("en").format_number(1234567.5, 2) == "1,234,567.50"
        assert get_locale("it").format_number(1234567.5, 2) == "1.234.567,50"
        assert get_locale("ru").format_number(1234567.5, 2) == "1 234 567,50"

    def test_the_two_separators_cannot_swap_into_each_other(self):
        """A quotation with the marks crossed is out by a factor of a thousand."""
        italian = get_locale("it").format_number(1234.56, 2)
        assert italian == "1.234,56"
        assert italian.count(".") == 1 and italian.count(",") == 1

    def test_arabic_uses_european_digits(self):
        """Which is what an Israeli workshop reads, whatever the script."""
        assert get_locale("ar").format_number(1234, 0) == "1,234"

    def test_currency_sits_where_the_language_puts_it(self):
        assert get_locale("en").format_money(1200.0, "₪") == "₪1,200.00"
        assert get_locale("he").format_money(1200.0, "₪") == "1,200.00 ₪"

    def test_dates_follow_the_language(self):
        when = date(2026, 8, 19)
        assert get_locale("he").format_date(when) == "19/08/2026"
        assert get_locale("ru").format_date(when) == "19.08.2026"


class TestNegotiation:
    def test_the_highest_quality_language_wins(self):
        assert negotiate("ru-RU,ru;q=0.9,he;q=0.8").code == "ru"

    def test_a_language_we_do_not_speak_falls_through_to_the_next(self):
        assert negotiate("fr-FR,fr;q=0.9,it;q=0.5").code == "it"

    def test_nothing_recognisable_gives_the_shop_s_own_language(self):
        assert negotiate("fr,de,ja").code == "he"
        assert negotiate(None).code == "he"

    def test_a_region_tag_resolves_to_its_language(self):
        assert negotiate("es-MX").code == "es"


class TestDomainVocabulary:
    """The words that reach a drawing, a job card or a phone."""

    def test_a_stage_names_itself_in_the_operators_language(self):
        from profileos.mes.tracking import Stage

        assert Stage.GLAZED.label("ru") == "остеклено"
        assert Stage.GLAZED.label("he") == "זוגג"

    def test_an_opening_type_names_itself(self):
        from profileos.elements.model import OpeningType

        assert OpeningType.TILT_TURN.label("it") == "anta-ribalta"
        assert OpeningType.TILT_TURN.label("es") == "oscilobatiente"

    def test_a_severity_names_itself(self):
        from profileos.elements.feasibility import Severity

        assert Severity.BLOCKER.label("en") == "cannot be made"
        assert Severity.BLOCKER.hebrew == "לא ניתן לייצור"

    def test_a_detail_names_itself(self):
        from profileos.drawing.section import Detail

        assert Detail.SILL.label("ru") == "узел низа"
        assert Detail.SILL.english == "Sill detail"

    def test_provenance_names_itself(self):
        from profileos.systems.model import Provenance

        assert "fornitore" in Provenance.CONFIRMED.label("it")


class TestDrawingsCarryTheLanguage:
    def _package(self, language):
        from profileos.drawing import PackageInfo, Revision, build_package
        from profileos.elements.builder import ElementBuilder
        from profileos.elements.model import Opening

        build = ElementBuilder.for_system("klil-7300").build(
            Opening(element_id="W-01", name="W-01", width=1200.0, height=1400.0)
        )
        info = PackageInfo(
            project="Casa", client="Cliente", number_prefix="A", language=language,
            revisions=[Revision("A", date(2026, 8, 19), "issued", "DA")],
        )
        return build_package([build], info)

    def test_the_title_block_is_labelled_in_the_sheets_language(self):
        rows = dict(self._package("ru").sheets[0].title_block.rows())
        assert "Объект / Project" in rows

    def test_english_is_printed_alongside_since_both_read_the_sheet(self):
        rows = dict(self._package("it").sheets[0].title_block.rows())
        assert "Commessa / Project" in rows

    def test_the_english_sheet_does_not_repeat_itself(self):
        rows = dict(self._package("en").sheets[0].title_block.rows())
        assert "Project" in rows and "Project / Project" not in rows

    def test_the_date_follows_the_language(self):
        rows = dict(self._package("ru").sheets[0].title_block.rows())
        assert "19.08.2026" in rows.values()

    def test_the_not_for_construction_stamp_is_translated(self):
        stamp = self._package("es").stamps[0]
        assert "NO APTO PARA CONSTRUCCIÓN" in stamp
        assert "NOT FOR CONSTRUCTION" in stamp


class TestThePhoneSpeaksIt:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from profileos.api.server import app
        from profileos.mobile.state import configure

        configure(
            registry_path=tmp_path / "d.json",
            measurement_path=tmp_path / "m.json",
            station="office",
        )
        return TestClient(app)

    def test_an_explicit_choice_wins(self, client):
        page = client.get("/m?lang=ru").text
        assert 'lang="ru"' in page and 'dir="ltr"' in page
        assert "Замер" in page

    def test_the_phones_own_setting_decides_by_default(self, client):
        page = client.get("/m", headers={"Accept-Language": "it-IT,it;q=0.9"}).text
        assert 'lang="it"' in page
        assert "Rilievo" in page

    def test_an_rtl_language_flips_the_document(self, client):
        page = client.get("/m?lang=ar").text
        assert 'lang="ar"' in page and 'dir="rtl"' in page

    def test_the_vocabulary_is_inlined_not_fetched(self, client):
        """A screen whose labels need a second request sometimes has none."""
        page = client.get("/m?lang=es").text
        assert "oscilobatiente" in page
        assert "Medición" in page

    def test_a_refusal_comes_back_in_the_readers_language(self, client):
        response = client.get("/m/api/jobs", headers={"Accept-Language": "ru"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Устройство не подключено"
