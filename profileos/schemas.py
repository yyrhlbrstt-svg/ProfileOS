"""JSON Schemas, generated from the models rather than written beside them.

Every document this suite reads or writes — a profile, a project, a system's
rules, a price list — has a pydantic model that already states its shape,
its units and its constraints. A schema written by hand next to that model
starts out correct and drifts within a release: somebody adds a field, the
model accepts it, the schema does not, and the file that validates in the
editor is rejected by the software.

So the schemas here are derived, always, at the moment they are asked for.
They cannot describe a version of the model that is not the one running.

What they are for
-----------------
* A supplier or a systems house can be handed one file that says exactly what
  a profile library or a rule set has to contain, without reading Python.
* Editors that understand ``$schema`` validate those files as they are typed.
* ``profileos schema check`` validates a folder of documents before they are
  dropped into the plugin directory, so a typo is found on a desk rather than
  at the saw.

Document plugins additionally get a ``kind`` constant pinned into their schema,
because that field is what the loader dispatches on and a document without it
is not loadable however well-formed the rest of it is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from .core.errors import ProfileOSError
from .core.logging_setup import get_logger

_log = get_logger("schemas")

#: Namespace the generated ``$id`` values sit in. It is not fetched over the
#: network by anything here — it exists so two schemas from two installations
#: can be told apart, and so an editor can cache them by identity.
SCHEMA_NAMESPACE = "https://profileos.app/schema"
#: The JSON Schema dialect pydantic emits.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _core_models() -> dict[str, type[BaseModel]]:
    """The models that describe what the software stores, not what it loads."""
    from .elements.model import Opening
    from .models.machines import MachineDefinition, ToolLibrary
    from .models.materials import Material
    from .models.orders import CutItem, Project
    from .models.profile import ProfileDefinition
    from .models.results import GeometryReport, SectionProperties

    return {
        "profile": ProfileDefinition,
        "project": Project,
        "cut_item": CutItem,
        "opening": Opening,
        "material": Material,
        "machine": MachineDefinition,
        "tool_library": ToolLibrary,
        "section_properties": SectionProperties,
        "geometry_report": GeometryReport,
    }


def document_models() -> dict[str, type[BaseModel]]:
    """The models behind the ``kind:`` documents the plugin loader accepts."""
    from .core.hotreload import DATA_SCHEMAS, register_builtin_schemas

    register_builtin_schemas()
    models: dict[str, type[BaseModel]] = {}
    for kind in DATA_SCHEMAS.kinds():
        schema = DATA_SCHEMAS.get(kind)
        if schema is None or schema.document_model is None:
            continue
        if issubclass(schema.document_model, BaseModel):
            models[kind] = schema.document_model
    return models


def known_schemas() -> dict[str, type[BaseModel]]:
    """Every name a schema can be generated for."""
    return {**_core_models(), **document_models()}


def json_schema(model: type[BaseModel], *, name: str, kind: str | None = None) -> dict[str, Any]:
    """The JSON Schema for one model.

    ``kind`` pins the loader's discriminator into the document, so a file that
    validates is a file the loader will actually pick up.
    """
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = f"{SCHEMA_NAMESPACE}/{name}.schema.json"
    schema.setdefault("title", model.__name__)
    if model.__doc__:
        schema["description"] = " ".join(model.__doc__.split("\n")[0].split())

    _admit_computed_fields(model, schema)

    if kind is not None:
        properties = schema.setdefault("properties", {})
        existing = properties.get("kind", {})
        properties["kind"] = {
            **existing,
            "const": kind,
            "type": "string",
            "description": existing.get("description")
            or (
                "Document discriminator. The loader dispatches on this; a file "
                "without it is not loaded whatever else it contains."
            ),
        }
        required = schema.setdefault("required", [])
        if "kind" not in required:
            required.insert(0, "kind")
    return schema


def _admit_computed_fields(model: type[BaseModel], schema: dict[str, Any]) -> None:
    """Let a document carry the fields the model computes for itself.

    A model with ``extra="forbid"`` and computed fields writes JSON it would
    then refuse to read: the computed values are in the output but not in the
    validation schema. The models solve this by dropping those keys on the way
    back in; the schema has to say the same thing, or a file the software wrote
    fails validation in an editor that trusts the schema.

    They are marked read-only, because writing one changes nothing — the value
    is recomputed from the fields it is derived from.
    """
    computed = getattr(model, "model_computed_fields", None)
    if not computed or schema.get("additionalProperties") is not False:
        return
    properties = schema.setdefault("properties", {})
    for name, info in computed.items():
        if name in properties:
            continue
        entry: dict[str, Any] = {"readOnly": True}
        description = getattr(info, "description", None)
        if description:
            entry["description"] = description
        entry.setdefault(
            "description",
            "Derived by the software from the other fields; ignored when read back.",
        )
        properties[name] = entry


def all_schemas() -> dict[str, dict[str, Any]]:
    """Every schema, keyed by the name it is written under."""
    kinds = set(document_models())
    return {
        name: json_schema(model, name=name, kind=name if name in kinds else None)
        for name, model in known_schemas().items()
    }


def export(directory: str | Path, *, indent: int = 2) -> list[Path]:
    """Write every schema into ``directory`` and return the files written."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in all_schemas().items():
        path = target / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, indent=indent, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)

    index = target / "index.json"
    index.write_text(
        json.dumps(
            {
                "$comment": (
                    "Generated from the running models by profileos.schemas. "
                    "Do not edit: regenerate with `profileos schema export`."
                ),
                "schemas": {
                    name: {
                        "file": f"{name}.schema.json",
                        "$id": schema["$id"],
                        "title": schema.get("title", name),
                        "document_kind": name if name in document_models() else None,
                    }
                    for name, schema in all_schemas().items()
                },
            },
            indent=indent,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(index)
    _log.info("Wrote %d schema files to %s", len(written), target)
    return written


class DocumentProblem(ProfileOSError):
    """A document does not match the schema for its kind."""


def validate_document(document: dict[str, Any]) -> Any:
    """Validate one loaded document against the model for its ``kind``.

    Returns the parsed object. The error deliberately quotes pydantic's own
    message: it names the field and says what was wrong with it, which is more
    use to whoever is editing the file than anything paraphrased.
    """
    from .core.hotreload import DATA_SCHEMAS, register_builtin_schemas

    register_builtin_schemas()
    kind = document.get("kind")
    if not kind:
        raise DocumentProblem(
            "The document has no 'kind', so there is nothing to validate it "
            "against. Known kinds: " + ", ".join(sorted(document_models()))
        )
    schema = DATA_SCHEMAS.get(str(kind))
    if schema is None:
        raise DocumentProblem(
            f"Unknown document kind {kind!r}. Known kinds: "
            + ", ".join(sorted(document_models()))
        )
    try:
        return schema.model(document)
    except Exception as exc:  # noqa: BLE001 - re-raised with the file's own words
        raise DocumentProblem(f"{kind}: {exc}") from exc


def check_directory(directory: str | Path) -> list[tuple[Path, str | None]]:
    """Validate every JSON document in a folder.

    Returns one ``(path, problem)`` per file, with ``None`` where the file is
    good — so the caller can print the whole picture rather than stopping at
    the first bad one.
    """
    results: list[tuple[Path, str | None]] = []
    for path in sorted(Path(directory).rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append((path, f"not readable JSON: {exc}"))
            continue
        if not isinstance(document, dict):
            results.append((path, "the top level of a document must be an object"))
            continue
        if "kind" not in document:
            continue  # not a plugin document; nothing to check it against
        try:
            validate_document(document)
        except DocumentProblem as exc:
            results.append((path, str(exc)))
        else:
            results.append((path, None))
    return results


__all__ = [
    "SCHEMA_DIALECT",
    "SCHEMA_NAMESPACE",
    "DocumentProblem",
    "all_schemas",
    "check_directory",
    "document_models",
    "export",
    "json_schema",
    "known_schemas",
    "validate_document",
]
