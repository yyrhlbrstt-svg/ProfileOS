"""Shared model behaviour.

Every model here sets ``extra="forbid"``, which is the right default: a typo in
a hand-edited plugin file should be an error, not a silently ignored key. It
collides, though, with :func:`pydantic.computed_field`. A computed field is
*written* by ``model_dump``, so a model that has one cannot read back the file
it just wrote — the derived keys come back as unexpected extras. That breaks
saving a job and reopening it, posting a payload the API itself produced, and
committing a data plugin exported from the UI.

:class:`RoundTrips` closes the loop by dropping the derived keys on the way in.
They carry no information — they are recomputed from the stored fields — so
discarding them loses nothing, and a real typo is still rejected because it is
not the name of a computed field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


class RoundTrips(BaseModel):
    """Mixin: a model that can read back exactly what it wrote."""

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_fields(cls, data: Any) -> Any:
        computed = getattr(cls, "model_computed_fields", None)
        if not computed or not isinstance(data, dict):
            return data
        if not any(key in data for key in computed):
            return data
        return {key: value for key, value in data.items() if key not in computed}


__all__ = ["RoundTrips"]
