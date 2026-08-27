"""Making another one of those.

A shop that fits fifty of the same window a year rebuilds it fifty times: the
same series, the same division, the same glass, the same handle at the same
height, keyed in again by whoever is quoting that morning. Half the errors in
a quotation are in the half that was typed rather than chosen.

A template is one of those configurations, saved with a name a person would
say — "חלון הזזה שתי כנפיים קליל ⁦7300⁩ סטנדרט" — and used again at a new size.
It carries what was made and not who it was for: the system, the divisions,
the glazing, the finish, the notes that matter on the floor. The customer, the
size and the price belong to the job.

The one thing this refuses to be is a price list. It records what the shop
charged per square metre the last time it made this and **when**, and it says
how long ago that was — because a template quoted at last year's aluminium
price is not a shortcut, it is a way to lose money quickly and repeatedly. Past
a few months the figure is shown struck through rather than offered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("projects.templates")

#: Past this many days, a remembered price is history rather than a price.
PRICE_STALE_DAYS = 120


@dataclass
class Template:
    """One configuration the shop has made before, ready to make again."""

    template_id: str = field(
        default_factory=lambda: f"TP-{uuid4().hex[:6].upper()}"
    )
    name: str = ""
    note: str = ""
    system_id: str = "generic"
    kind: str = "window"
    #: Division as fractions of the width and height, so it scales with size.
    #: A mullion at ⁦0.5⁩ stays in the middle whatever the window becomes.
    mullion_fractions: list[float] = field(default_factory=list)
    transom_fractions: list[float] = field(default_factory=list)
    #: What each cell is, in the builder's own vocabulary, row by row.
    cell_plan: list[dict[str, Any]] = field(default_factory=list)
    glass_spec_id: str | None = None
    finish: str = ""
    #: The size it is usually made at, as a starting point — never as a rule.
    typical_width: float | None = None
    typical_height: float | None = None
    #: What the shop charged per square metre, and when.
    last_price_per_m2: float | None = None
    priced_on: date | None = None
    #: Where the template came from, so its provenance survives.
    from_job: str = ""
    created: date = field(default_factory=date.today)
    last_used: date | None = None
    times_used: int = 0
    tags: list[str] = field(default_factory=list)

    # -- the price ------------------------------------------------------------- #
    @property
    def price_age_days(self) -> int | None:
        if self.priced_on is None:
            return None
        return (date.today() - self.priced_on).days

    @property
    def price_is_stale(self) -> bool:
        age = self.price_age_days
        return age is None or age > PRICE_STALE_DAYS

    def price_line(self) -> str:
        """What to show beside the template, and what not to offer."""
        if self.last_price_per_m2 is None:
            return "לא תומחר"
        age = self.price_age_days or 0
        figure = f"⁦{self.last_price_per_m2:,.0f}⁩ ₪/מ״ר"
        if self.price_is_stale:
            return f"{figure} · לפני ⁦{age}⁩ ימים — לתמחר מחדש"
        return f"{figure} · לפני ⁦{age}⁩ ימים"

    def describe(self) -> str:
        used = (
            f" · שומש ⁦{self.times_used}⁩ פעמים" if self.times_used else " · טרם שומש"
        )
        return f"{self.name or self.template_id}{used} · {self.price_line()}"

    # -- using it -------------------------------------------------------------- #
    def apply(self, width: float, height: float, *, name: str = "") -> Any:
        """Build an opening from this template at a new size.

        The divisions scale because they are stored as fractions: a template
        with a mullion in the middle stays in the middle at any width, which is
        what a person means by "another one of those, but wider".
        """
        from ..elements.model import Opening

        if width <= 0 or height <= 0:
            raise ProfileOSError("מידה חייבת להיות חיובית")

        values: dict[str, Any] = {
            "name": name or self.name or "פתח",
            "width": float(width),
            "height": float(height),
            "system_id": self.system_id,
            "mullion_positions": [
                round(fraction * width, 1)
                for fraction in self.mullion_fractions
                if 0.0 < fraction < 1.0
            ],
            "transom_positions": [
                round(fraction * height, 1)
                for fraction in self.transom_fractions
                if 0.0 < fraction < 1.0
            ],
        }
        if self.glass_spec_id:
            values["glass_spec_id"] = self.glass_spec_id
        if self.finish:
            values["finish"] = self.finish
        if self.cell_plan:
            values["cells"] = [dict(cell) for cell in self.cell_plan]
        return Opening(**values)

    def used(self, *, on: date | None = None) -> None:
        self.times_used += 1
        self.last_used = on or date.today()

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id, "name": self.name,
            "note": self.note, "system_id": self.system_id, "kind": self.kind,
            "mullion_fractions": list(self.mullion_fractions),
            "transom_fractions": list(self.transom_fractions),
            "cell_plan": [dict(cell) for cell in self.cell_plan],
            "glass_spec_id": self.glass_spec_id, "finish": self.finish,
            "typical_width": self.typical_width,
            "typical_height": self.typical_height,
            "last_price_per_m2": self.last_price_per_m2,
            "priced_on": self.priced_on.isoformat() if self.priced_on else None,
            "from_job": self.from_job,
            "created": self.created.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "times_used": self.times_used, "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Template":
        def day(key: str) -> date | None:
            value = raw.get(key)
            return date.fromisoformat(str(value)) if value else None

        def number(key: str) -> float | None:
            value = raw.get(key)
            return None if value is None else float(value)

        return cls(
            template_id=str(raw.get("template_id", "")),
            name=str(raw.get("name", "")), note=str(raw.get("note", "")),
            system_id=str(raw.get("system_id", "generic")),
            kind=str(raw.get("kind", "window")),
            mullion_fractions=[float(v) for v in raw.get("mullion_fractions", [])],
            transom_fractions=[float(v) for v in raw.get("transom_fractions", [])],
            cell_plan=[dict(cell) for cell in raw.get("cell_plan", [])],
            glass_spec_id=raw.get("glass_spec_id"),
            finish=str(raw.get("finish", "")),
            typical_width=number("typical_width"),
            typical_height=number("typical_height"),
            last_price_per_m2=number("last_price_per_m2"),
            priced_on=day("priced_on"),
            from_job=str(raw.get("from_job", "")),
            created=day("created") or date.today(),
            last_used=day("last_used"),
            times_used=int(raw.get("times_used", 0)),
            tags=[str(tag) for tag in raw.get("tags", [])],
        )


def template_from_opening(
    opening: Any,
    *,
    name: str = "",
    from_job: str = "",
    price_per_m2: float | None = None,
    priced_on: date | None = None,
    note: str = "",
) -> Template:
    """Save an opening as a template, with its divisions stored as fractions.

    Absolute mullion positions would make the template useless at any other
    size, which is the only size anybody wants it at.
    """
    width = float(getattr(opening, "width", 0.0) or 0.0)
    height = float(getattr(opening, "height", 0.0) or 0.0)
    if width <= 0 or height <= 0:
        raise ProfileOSError("אי אפשר לשמור תבנית מפתח בלי מידות")

    cells = []
    for cell in getattr(opening, "cells", []) or []:
        dumped = cell.model_dump() if hasattr(cell, "model_dump") else dict(cell)
        cells.append(dumped)

    template = Template(
        name=name or str(getattr(opening, "name", "") or "תבנית"),
        note=note,
        system_id=str(getattr(opening, "system_id", "generic") or "generic"),
        kind=str(getattr(getattr(opening, "kind", None), "value", "window")),
        # Six places, not four: a third of a three-metre window rounded to
        # four decimals lands a tenth of a millimetre out at the new size,
        # and a division that drifts is a division somebody has to correct.
        mullion_fractions=[
            round(position / width, 6)
            for position in getattr(opening, "mullion_positions", []) or []
        ],
        transom_fractions=[
            round(position / height, 6)
            for position in getattr(opening, "transom_positions", []) or []
        ],
        cell_plan=cells,
        glass_spec_id=getattr(opening, "glass_spec_id", None),
        finish=str(getattr(opening, "finish", "") or ""),
        typical_width=width,
        typical_height=height,
        last_price_per_m2=price_per_m2,
        priced_on=priced_on or (date.today() if price_per_m2 else None),
        from_job=from_job,
    )
    _log.info("Template %s saved from %s", template.template_id, template.name)
    return template


class TemplateBook:
    """Every configuration the shop has saved, kept on disk."""

    def __init__(self, path: Path | None = None) -> None:
        from ..core.config import get_settings

        self.path = (
            Path(path) if path else get_settings().data_dir / "templates.json"
        )
        self._templates: dict[str, Template] = {}

    def load(self) -> "TemplateBook":
        if not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return self
        for item in raw.get("templates", []):
            try:
                template = Template.from_dict(item)
            except (ValueError, KeyError) as exc:
                _log.warning("Skipped an unreadable template: %s", exc)
                continue
            self._templates[template.template_id] = template
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written": datetime.now().isoformat(timespec="seconds"),
            "templates": [t.as_dict() for t in self._templates.values()],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def add(self, template: Template) -> Template:
        self._templates[template.template_id] = template
        self.save()
        return template

    def get(self, template_id: str) -> Template:
        if template_id not in self._templates:
            raise ProfileOSError(f"אין תבנית {template_id}")
        return self._templates[template_id]

    def remove(self, template_id: str) -> None:
        if template_id in self._templates:
            del self._templates[template_id]
            self.save()

    def __len__(self) -> int:
        return len(self._templates)

    def __iter__(self):
        return iter(self._templates.values())

    def use(self, template_id: str, width: float, height: float, **kwargs: Any) -> Any:
        """Build an opening from a saved template and record that it was used."""
        template = self.get(template_id)
        opening = template.apply(width, height, **kwargs)
        template.used()
        self.save()
        return opening

    # -- finding one ------------------------------------------------------------ #
    def search(self, text: str) -> list[Template]:
        """Find a template the way somebody says its name, in any order."""
        words = [word for word in text.strip().casefold().split() if word]
        if not words:
            return self.popular()
        found = []
        for template in self:
            haystack = " ".join(
                [template.name, template.note, template.system_id]
                + template.tags
            ).casefold()
            if all(word in haystack for word in words):
                found.append(template)
        return sorted(found, key=lambda t: t.times_used, reverse=True)

    def popular(self, limit: int = 0) -> list[Template]:
        """Most used first — a shop's real catalogue is its habits."""
        ordered = sorted(
            self, key=lambda t: (t.times_used, t.last_used or date.min),
            reverse=True,
        )
        return ordered[:limit] if limit else ordered

    def needing_repricing(self) -> list[Template]:
        """Templates whose remembered price is old enough to mislead."""
        return [
            template for template in self
            if template.last_price_per_m2 is not None and template.price_is_stale
        ]


def default_templates() -> TemplateBook:
    return TemplateBook().load()


__all__ = [
    "PRICE_STALE_DAYS",
    "Template",
    "TemplateBook",
    "default_templates",
    "template_from_opening",
]
