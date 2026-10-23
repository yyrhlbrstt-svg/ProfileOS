"""IFC export: the openings, where they sit, in a file an architect can open."""

from __future__ import annotations

import re

import pytest

from profileos.elements import Opening, build_elements
from profileos.elements.model import ElementKind
from profileos.exchange.ifc import (
    LIMITATIONS_HE,
    SCHEMA,
    IfcOptions,
    compress_guid,
    render_ifc,
    write_ifc,
)


@pytest.fixture
def builds():
    return build_elements([
        Opening(name="W1", width=1500, height=1400, quantity=2,
                system_id="klil-7300"),
        Opening(name="D1", width=1000, height=2200, kind=ElementKind.DOOR),
    ])


@pytest.fixture
def model(builds) -> str:
    return render_ifc(builds, IfcOptions(project_name="וילה בבית אל"))


def _entities(text: str) -> dict[int, str]:
    found = {}
    for line in text.splitlines():
        match = re.match(r"#(\d+)=(.*);$", line)
        if match:
            found[int(match.group(1))] = match.group(2)
    return found


class TestTheFileItself:
    def test_it_is_a_step_physical_file(self, model):
        assert model.startswith("ISO-10303-21;")
        assert model.rstrip().endswith("END-ISO-10303-21;")

    def test_it_declares_the_schema_it_is_written_in(self, model):
        assert f"FILE_SCHEMA(('{SCHEMA}'))" in model

    def test_every_reference_resolves(self, model):
        """A dangling reference is the failure a reader reports as corruption."""
        defined = set(_entities(model))
        referenced = {
            int(number)
            for body in _entities(model).values()
            for number in re.findall(r"#(\d+)", body)
        }
        assert not referenced - defined

    def test_entity_numbers_are_unique_and_ordered(self, model):
        numbers = [
            int(match.group(1))
            for match in re.finditer(r"^#(\d+)=", model, re.MULTILINE)
        ]
        assert numbers == sorted(numbers)
        assert len(numbers) == len(set(numbers))

    def test_lengths_are_in_metres_not_millimetres(self, model):
        """The commonest reason an imported model turns up a thousand times too big."""
        assert "IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)" in model
        assert ".MILLI." not in model


class TestGuids:
    def test_a_guid_is_twenty_two_characters(self):
        assert len(compress_guid()) == 22

    def test_it_uses_ifc_s_own_alphabet_not_standard_base64(self):
        """Plain base64 is rejected by strict readers and mis-keyed by lenient ones."""
        seen = "".join(compress_guid() for _ in range(200))
        assert "+" not in seen and "/" not in seen and "=" not in seen

    def test_two_guids_differ(self):
        assert compress_guid() != compress_guid()

    def test_every_rooted_entity_carries_one(self, model):
        for body in _entities(model).values():
            if body.startswith(("IFCPROJECT", "IFCSITE", "IFCBUILDING(",
                                "IFCWINDOW", "IFCDOOR")):
                guid = body.split("(", 1)[1].split(",", 1)[0].strip("'")
                assert len(guid) == 22


class TestHebrew:
    def test_hebrew_survives_as_the_extended_encoding_step_defines(self, model):
        """A file that drops the marks arrives with every window called nothing."""
        assert "\\X2\\" in model
        assert "\\X0\\" in model

    def test_a_quote_in_a_name_is_doubled_not_dropped(self):
        text = render_ifc([], IfcOptions(project_name="בית ד'אבו"))
        assert "''" in text


class TestWhatIsExported:
    def test_a_window_is_a_window_and_a_door_is_a_door(self, model):
        assert "IFCWINDOW(" in model
        assert "IFCDOOR(" in model

    def test_an_element_fitted_twice_appears_twice(self, model):
        assert model.count("IFCWINDOW(") == 2

    def test_the_overall_size_is_the_real_size_in_metres(self, model):
        window = next(
            body for body in _entities(model).values()
            if body.startswith("IFCWINDOW(")
        )
        assert "1.4" in window and "1.5" in window

    def test_a_window_sits_at_its_sill_not_half_way_up_it(self, builds):
        """Centre the placement in Z and every window floats."""
        model = render_ifc(builds)
        entities = _entities(model)
        window = next(
            (number, body) for number, body in entities.items()
            if body.startswith("IFCWINDOW(")
        )
        placement = int(re.findall(r"#(\d+)", window[1])[1])
        axis = int(re.findall(r"#(\d+)", entities[placement])[-1])
        point = int(re.findall(r"#(\d+)", entities[axis])[0])
        z = float(entities[point].split("((")[1].rstrip("))").split(",")[2])
        assert z == pytest.approx(0.9, abs=0.001)

    def test_a_door_sits_on_the_floor(self, builds):
        entities = _entities(render_ifc(builds))
        door = next(
            (number, body) for number, body in entities.items()
            if body.startswith("IFCDOOR(")
        )
        placement = int(re.findall(r"#(\d+)", door[1])[1])
        axis = int(re.findall(r"#(\d+)", entities[placement])[-1])
        point = int(re.findall(r"#(\d+)", entities[axis])[0])
        z = float(entities[point].split("((")[1].rstrip("))").split(",")[2])
        assert z == pytest.approx(0.0, abs=0.001)

    def test_the_spatial_tree_is_complete(self, model):
        for entity in (
            "IFCPROJECT(", "IFCSITE(", "IFCBUILDING(", "IFCBUILDINGSTOREY("
        ):
            assert entity in model
        assert model.count("IFCRELAGGREGATES(") == 3
        assert "IFCRELCONTAINEDINSPATIALSTRUCTURE(" in model

    def test_the_properties_answer_what_an_architect_asks(self, model):
        assert "Pset_ProfileOS" in model
        assert "System" in model
        assert "Glazing" in model

    def test_properties_may_be_left_out(self, builds):
        text = render_ifc(builds, IfcOptions(include_properties=False))
        assert "IFCPROPERTYSET(" not in text

    def test_an_empty_schedule_still_produces_a_readable_file(self):
        text = render_ifc([])
        assert "IFCPROJECT(" in text
        assert "IFCWINDOW(" not in text
        assert "IFCRELCONTAINEDINSPATIALSTRUCTURE(" not in text

    def test_an_opening_with_no_size_is_skipped_rather_than_written_as_zero(self):
        class Sizeless:
            width = 0
            height = 0
            name = "X"

        assert "IFCWINDOW(" not in render_ifc([Sizeless()])


class TestHonesty:
    def test_the_limits_are_stated_where_the_export_lives(self):
        joined = " ".join(LIMITATIONS_HE)
        assert "לא גיאומטריית הפרופיל" in joined
        assert "אין למדוד" in joined


class TestWriting:
    def test_it_is_written_where_it_was_asked_for(self, builds, tmp_path):
        target = write_ifc(builds, tmp_path / "out" / "model.ifc")
        assert target.exists()
        assert target.read_text(encoding="utf-8").startswith("ISO-10303-21;")
