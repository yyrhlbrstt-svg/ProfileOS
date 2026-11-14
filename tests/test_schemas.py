"""Schema generation tests.

The schemas are only worth anything if they are (a) legal JSON Schema, (b)
accept what the software itself writes, and (c) reject what it would refuse.
So the documents fed to them here are produced by the models, not typed out
beside them — a schema that only validates hand-written examples proves nothing.
"""

from __future__ import annotations

import json

import pytest

from profileos.schemas import (
    SCHEMA_DIALECT,
    DocumentProblem,
    all_schemas,
    check_directory,
    document_models,
    export,
    json_schema,
    known_schemas,
    validate_document,
)

jsonschema = pytest.importorskip("jsonschema", reason="optional: validates the generated schemas")
from jsonschema import Draft202012Validator  # noqa: E402


@pytest.fixture(scope="module")
def schemas():
    return all_schemas()


class TestGeneration:
    def test_every_schema_is_legal_json_schema(self, schemas):
        for name, schema in schemas.items():
            Draft202012Validator.check_schema(schema)
            assert schema["$schema"] == SCHEMA_DIALECT, name

    def test_every_schema_has_its_own_identity(self, schemas):
        ids = [schema["$id"] for schema in schemas.values()]
        assert len(set(ids)) == len(ids)

    def test_the_document_kinds_are_all_covered(self, schemas):
        assert set(document_models()) <= set(schemas)

    def test_a_plugin_document_must_declare_its_kind(self, schemas):
        for name in document_models():
            schema = schemas[name]
            assert schema["properties"]["kind"]["const"] == name
            assert "kind" in schema["required"]

    def test_a_stored_model_does_not_get_a_kind_it_never_writes(self, schemas):
        assert "kind" not in schemas["section_properties"].get("properties", {})


class TestAgreementWithTheModels:
    def _document(self, model):
        """A document the software itself would write."""
        return json.loads(model.model_dump_json())

    def test_the_default_rules_validate_against_their_own_schema(self, schemas):
        from profileos.elements.rules import DEFAULT_SYSTEM_RULES

        document = self._document(DEFAULT_SYSTEM_RULES) | {"kind": "system_rules"}
        assert list(Draft202012Validator(schemas["system_rules"]).iter_errors(document)) == []

    def test_a_wrong_type_is_caught_by_the_schema(self, schemas):
        from profileos.elements.rules import DEFAULT_SYSTEM_RULES

        document = self._document(DEFAULT_SYSTEM_RULES) | {"kind": "system_rules"}
        document["glass"]["edge_clearance"] = "wide"
        errors = [e.message for e in Draft202012Validator(schemas["system_rules"]).iter_errors(document)]
        assert any("not of type" in message for message in errors)

    def test_a_missing_kind_is_caught_by_the_schema(self, schemas):
        from profileos.elements.rules import DEFAULT_SYSTEM_RULES

        document = self._document(DEFAULT_SYSTEM_RULES)
        del document["kind"]
        errors = [e.message for e in Draft202012Validator(schemas["system_rules"]).iter_errors(document)]
        assert any("kind" in message for message in errors)

    def test_the_schema_accepts_what_the_software_writes(self, schemas):
        """A model that computes fields still writes them into its JSON.

        With extra="forbid" the generated schema would reject the software's
        own output, so the computed fields are admitted as read-only.
        """
        from profileos.models.results import SectionProperties

        assert SectionProperties.model_config.get("extra") == "forbid"
        for name in SectionProperties.model_computed_fields:
            entry = schemas["section_properties"]["properties"][name]
            assert entry.get("readOnly") is True

    def test_an_analysed_section_validates_against_its_schema(self, schemas, mullion_dxf):
        """The properties the analyser writes must match what the schema says."""
        from profileos.structural import analyse_dxf

        properties, _ = analyse_dxf(str(mullion_dxf), profile_id="mullion")
        document = json.loads(properties.model_dump_json())
        assert list(Draft202012Validator(schemas["section_properties"]).iter_errors(document)) == []

    def test_a_brand_validates_against_its_schema(self, schemas):
        from profileos.branding import BUILTIN_BRANDS

        brand = next(iter(BUILTIN_BRANDS.values()))
        document = self._document(brand) | {"kind": "brand"}
        assert list(Draft202012Validator(schemas["brand"]).iter_errors(document)) == []


class TestValidatingDocuments:
    def test_a_good_document_comes_back_parsed(self):
        from profileos.elements.rules import DEFAULT_SYSTEM_RULES

        document = json.loads(DEFAULT_SYSTEM_RULES.model_dump_json()) | {"kind": "system_rules"}
        parsed = validate_document(document)
        assert parsed.id == DEFAULT_SYSTEM_RULES.id

    def test_a_document_without_a_kind_says_which_kinds_exist(self):
        with pytest.raises(DocumentProblem) as excinfo:
            validate_document({"id": "x"})
        assert "system_rules" in str(excinfo.value)

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(DocumentProblem, match="Unknown document kind"):
            validate_document({"kind": "spaceship", "id": "x"})

    def test_the_error_names_the_field_that_is_wrong(self):
        from profileos.elements.rules import DEFAULT_SYSTEM_RULES

        document = json.loads(DEFAULT_SYSTEM_RULES.model_dump_json()) | {"kind": "system_rules"}
        document["glass"]["edge_clearance"] = "wide"
        with pytest.raises(DocumentProblem) as excinfo:
            validate_document(document)
        assert "edge_clearance" in str(excinfo.value)


class TestCheckingAFolder:
    def test_good_and_bad_are_both_reported(self, tmp_path):
        from profileos.elements.rules import DEFAULT_SYSTEM_RULES

        good = json.loads(DEFAULT_SYSTEM_RULES.model_dump_json()) | {"kind": "system_rules"}
        (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
        bad = dict(good)
        bad["glass"] = {"edge_clearance": "wide"}
        (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        (tmp_path / "notadocument.json").write_text('{"hello": 1}', encoding="utf-8")
        (tmp_path / "broken.json").write_text("{oh dear", encoding="utf-8")

        results = dict(check_directory(tmp_path))
        assert results[tmp_path / "good.json"] is None
        assert "edge_clearance" in results[tmp_path / "bad.json"]
        assert "not readable JSON" in results[tmp_path / "broken.json"]
        # A JSON file that is not a plugin document is not our business.
        assert tmp_path / "notadocument.json" not in results


class TestExport:
    def test_every_schema_lands_on_disk_with_an_index(self, tmp_path):
        written = export(tmp_path)
        assert (tmp_path / "index.json").is_file()
        assert len(written) == len(known_schemas()) + 1
        index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
        for name, entry in index["schemas"].items():
            path = tmp_path / entry["file"]
            assert path.is_file()
            assert json.loads(path.read_text(encoding="utf-8"))["$id"] == entry["$id"]

    def test_exporting_twice_gives_the_same_bytes(self, tmp_path):
        """Regenerating must not churn a repository."""
        first = (tmp_path / "a")
        second = (tmp_path / "b")
        export(first)
        export(second)
        for path in first.glob("*.json"):
            assert path.read_bytes() == (second / path.name).read_bytes()
