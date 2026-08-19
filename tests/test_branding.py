"""Branding tests.

The branding layer must render only what the operator actually supplied. A
quotation that goes to a customer with invented contact details is worse than
one with a field missing.
"""

from __future__ import annotations

import pytest

from profileos.branding import (
    BUILTIN_BRANDS,
    DADI_BRAND,
    DEFAULT_BRAND,
    Brand,
    active_brand,
    get_brand,
    register_brand,
    set_active_brand,
)


@pytest.fixture(autouse=True)
def _restore_default():
    yield
    set_active_brand("profileos")


class TestBrand:
    def test_unset_fields_are_omitted_not_invented(self):
        brand = Brand(id="minimal", name="Minimal Co")
        assert brand.letterhead() == ["Minimal Co"]
        assert brand.address_lines() == []
        assert brand.contact_lines() == []

    def test_letterhead_renders_supplied_fields_in_order(self):
        brand = Brand(
            id="x", name="X Ltd", tagline="Aluminium", address_line="Street 1",
            city="Town", postcode="12345", country="Country", phone="01-234",
        )
        assert brand.letterhead() == [
            "X Ltd", "Aluminium", "Street 1", "Town 12345", "Country", "Tel 01-234",
        ]

    def test_document_name_prefers_the_legal_name(self):
        assert Brand(id="x", name="Trading", legal_name="Legal Ltd").document_name == "Legal Ltd"
        assert Brand(id="x", name="Trading").document_name == "Trading"

    def test_window_title_includes_the_operator(self):
        assert "X Ltd" in Brand(id="x", name="X Ltd").window_title()
        assert Brand(id="p", name="ProfileOS").window_title() == "ProfileOS"

    def test_nc_header_falls_back_to_the_name(self):
        assert "X Ltd" in Brand(id="x", name="X Ltd").nc_header()


class TestConfiguredBrand:
    def test_dadi_brand_is_available(self):
        assert "dadi" in BUILTIN_BRANDS
        assert get_brand("dadi") is DADI_BRAND

    def test_dadi_details_render(self):
        letterhead = DADI_BRAND.letterhead()
        assert 'דאדי בע"מ' in letterhead[0]
        assert any("סולם יעקב" in line for line in letterhead)
        assert any("02-9973510" in line for line in letterhead)

    def test_unconfirmed_fields_are_left_unset(self):
        """Email, website and registration number were not verified, so they must be empty."""
        assert DADI_BRAND.email is None
        assert DADI_BRAND.website is None
        assert DADI_BRAND.registration_number is None
        joined = " ".join(DADI_BRAND.letterhead())
        assert "@" not in joined
        assert "www." not in joined

    def test_activation_switches_the_brand(self):
        assert set_active_brand("dadi").id == "dadi"
        assert active_brand().id == "dadi"
        assert set_active_brand("profileos") is DEFAULT_BRAND


class TestBrandPropagation:
    def test_nc_headers_carry_the_operator(self):
        from profileos.cnc import MachiningJob, PieceProgram, expand_macros, get_driver
        from profileos.models.machines import MachineDefinition
        from profileos.models.profile import Face, MachiningMacro

        set_active_brand("dadi")
        machine = MachineDefinition(
            id="m", name="M", vendor="V", model="X", post_processor="elumatec.ncw",
            axis_count=5, machinable_faces=set(Face),
        )
        piece = PieceProgram(
            piece_id="P", profile_id="X", length=1000,
            operations=expand_macros(
                [MachiningMacro(macro_id="drill.simple", face=Face.TOP, position_x=100,
                                position_y=20, depth=10, tool_id=3)],
                bar_length=1000,
            ),
        )
        job = MachiningJob(machine=machine, name="J", pieces=[piece])
        content = get_driver("elumatec.ncw").post(job)[0].content
        assert "דאדי" in content

    def test_job_cards_carry_the_letterhead(self):
        from profileos.elements import Opening, build_elements
        from profileos.mes import render_job_card, work_order_from_builds

        set_active_brand("dadi")
        builds = build_elements([Opening(name="W", width=1500, height=1200)])
        order = work_order_from_builds(builds, project_id="P", name="T")
        card = render_job_card(order, builds)
        # The name contains a gershayim, which HTML-escapes to &quot; in the
        # output, so match on the part that survives escaping unchanged.
        assert "דאדי" in card
        assert "סולם יעקב" in card
        assert "letterhead" in card

    def test_quotations_carry_the_issuer(self):
        from profileos.elements import Opening, build_elements
        from profileos.quoting import build_bom, build_quotation

        set_active_brand("dadi")
        builds = build_elements([Opening(name="W", width=1500, height=1200)])
        quote = build_quotation(builds, build_bom(builds), project_name="T")
        assert quote.metadata["issued_by"] == 'דאדי בע"מ'
        assert quote.metadata["letterhead"]


class TestBrandPlugin:
    def test_a_registered_brand_overrides_a_builtin(self):
        register_brand(Brand(id="dadi", name="Overridden"))
        assert get_brand("dadi").name == "Overridden"

    def test_brand_is_a_hot_reloadable_kind(self):
        from profileos.core.hotreload import DATA_SCHEMAS, register_builtin_schemas

        register_builtin_schemas()
        assert "brand" in DATA_SCHEMAS.kinds()
