"""The quotation as something you edit, not something you regenerate.

The existing pipeline prices a project in one direction: elements → bill of
materials → quotation. That is right for the first draft and wrong for every
minute after it, because a quotation is negotiated — the client asks what the
thermal series would cost, the estimator rounds a line down to win the job,
somebody adds a crane day. Regenerating from scratch loses those decisions;
editing the printed numbers by hand loses the arithmetic. This module holds
both at once.

The design is **overrides on top of a recomputation**:

* The draft owns the *inputs* — openings, system, glass, finish, policy. Change
  any of them and everything downstream is recomputed: cut lists, glass, BOM,
  labour, price. That is the one-button change propagation; there is no partial
  update to get stale.
* The operator's explicit edits are stored as *overrides on lines*, keyed by
  the element they price. A recompute reapplies them rather than losing them —
  and when the recomputed base price has moved more than a token amount since
  the override was made, the draft says so, because a hand-set price that was
  generous under one spec may be underwater under another.
* Every edit lands in a journal with what changed and both values, and
  ``undo()`` walks it backwards. A negotiation is an argument; the journal is
  the minutes.

Options — "option A: standard, option B: thermal" — are separate variants of
the same draft sharing the openings, each with its own system, glass, finish
and policy, compared side by side.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..elements.builder import ElementBuild, ElementBuilder
from ..elements.model import Opening
from ..glazing.glass import STANDARD_BUILDUPS, GlassBuildUp
from ..systems.model import Provenance
from .bom import BillOfMaterials, build_bom
from .pricing import LabourRates, PricingPolicy, Quotation, build_quotation
from .suppliers import Supplier

_log = get_logger("quoting.editor")


class QuoteEditError(ProfileOSError):
    """The edit cannot be applied, and the message says why."""


# --------------------------------------------------------------------------- #
# Finishes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Finish:
    """A surface finish, priced per kilogram of aluminium.

    Israeli coaters and anodisers charge by weight, so that is the unit here.
    The shipped rates are stand-ins with the same provenance discipline as
    everything else: fine to compare options with, and the quotation says
    they are estimates until the shop enters its coater's own list.
    """

    id: str
    name: str
    hebrew: str
    rate_per_kg: float
    provenance: Provenance = Provenance.TYPICAL

    def confirm(self, rate: float, source: str) -> "Finish":
        if not source.strip():
            raise QuoteEditError("A confirmed finish rate needs a source")
        return replace(self, rate_per_kg=rate, provenance=Provenance.CONFIRMED)


#: The finishes a quotation offers. Rates are stand-ins (see class docstring).
FINISHES: dict[str, Finish] = {
    finish.id: finish
    for finish in (
        Finish("mill", "Mill finish", "טבעי", 0.0),
        Finish("ral", "Polyester powder, RAL", "צבע RAL", 9.0),
        Finish("ral-premium", "Premium / metallic RAL", "צבע פרימיום", 13.0),
        Finish("anodized", "Anodised", "אלגון", 11.0),
        Finish("wood", "Wood-effect sublimation", "דמוי עץ", 18.0),
    )
}


# --------------------------------------------------------------------------- #
# What the customer sees
# --------------------------------------------------------------------------- #
@dataclass
class DisplayPolicy:
    """Which numbers the customer's copy shows.

    The internal sheet always shows everything. The customer copy is a choice:
    some clients get one line per opening and a total, others demand the
    breakdown. What is *never* shown outward is cost and margin — those are
    the shop's own, and a checkbox that could leak them is a checkbox someone
    will tick by accident, so they are not options here at all.
    """

    show_unit_prices: bool = True
    show_aluminium_weight: bool = False
    show_glass_area: bool = False
    show_labour_hours: bool = False
    show_options_comparison: bool = True
    show_vat: bool = True
    payment_terms: str = ""
    notes: str = ""


# --------------------------------------------------------------------------- #
# Line overrides
# --------------------------------------------------------------------------- #
class OverrideKind(StrEnum):
    UNIT_PRICE = "unit_price"
    DESCRIPTION = "description"
    DISCOUNT_PCT = "discount_pct"
    HIDDEN = "hidden"


@dataclass
class LineOverride:
    """One hand-made decision about one line, and what it was made against."""

    element_id: str
    kind: OverrideKind
    value: Any
    #: The computed unit price at the moment the override was made. When the
    #: recomputed base drifts from this, the override is flagged as stale.
    base_unit_price: float | None = None
    made_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    made_by: str = ""
    reason: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.element_id, self.kind.value)


@dataclass
class JournalEntry:
    """One edit, with both sides of it."""

    at: datetime
    what: str
    before: Any
    after: Any
    by: str = ""
    #: Reverses this edit. Held as a closure so undo needs no case analysis.
    revert: Callable[[], None] | None = None

    def describe(self) -> str:
        return f"{self.what}: {self.before!r} → {self.after!r}"


# --------------------------------------------------------------------------- #
# A variant
# --------------------------------------------------------------------------- #
@dataclass
class QuoteVariant:
    """One option in the quotation: a spec, and the price it works out to."""

    variant_id: str
    name: str
    system_id: str = "generic"
    glass_id: str = "dgu-6-16-4"
    finish_id: str = "ral"
    policy: PricingPolicy = field(default_factory=PricingPolicy)
    labour: LabourRates = field(default_factory=LabourRates)

    #: Filled by recompute().
    builds: list[ElementBuild] = field(default_factory=list)
    bom: BillOfMaterials | None = None
    quotation: Quotation | None = None
    finish_cost: float = 0.0
    aluminium_kg: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def finish(self) -> Finish:
        return FINISHES.get(self.finish_id, FINISHES["mill"])

    @property
    def net_price(self) -> float:
        return self.quotation.net_price if self.quotation else 0.0

    @property
    def gross_price(self) -> float:
        return self.quotation.gross_price if self.quotation else 0.0


def _aluminium_mass(builds: Iterable[ElementBuild], mass_lookup) -> tuple[float, bool]:
    """Total aluminium [kg], and whether every profile's mass was known."""
    total = 0.0
    complete = True
    for build in builds:
        for cut in build.cuts:
            per_metre = mass_lookup(cut.profile_id) if mass_lookup else None
            if per_metre is None:
                complete = False
                per_metre = 1.6  # a plausible window profile, used only for finish cost
            total += per_metre * (cut.length / 1000.0) * cut.quantity * build.opening.quantity
    return total, complete


def _default_mass_lookup(profile_id: str) -> float | None:
    from ..core.registry import PROFILE_SYSTEMS

    profile = PROFILE_SYSTEMS.get_or_none(profile_id)
    for attribute in ("mass_per_metre_declared", "mass_per_metre"):
        value = getattr(profile, attribute, None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


# --------------------------------------------------------------------------- #
# The draft
# --------------------------------------------------------------------------- #
@dataclass
class QuoteDraft:
    """A quotation being negotiated: inputs, options, overrides, journal."""

    project_name: str = ""
    customer: str = ""
    display: DisplayPolicy = field(default_factory=DisplayPolicy)
    openings: list[Opening] = field(default_factory=list)
    variants: list[QuoteVariant] = field(default_factory=list)
    #: Which variant the customer lines and totals are read from by default.
    active_variant_id: str = "A"
    overrides: dict[tuple[str, str], LineOverride] = field(default_factory=dict)
    journal: list[JournalEntry] = field(default_factory=list)
    supplier: Supplier | None = None
    fallback_rates: dict[str, float] | None = None
    mass_lookup: Any = None
    #: Overrides whose base price has drifted since they were made.
    stale_overrides: list[tuple[str, str]] = field(default_factory=list)

    # -- construction --------------------------------------------------------
    @classmethod
    def start(
        cls,
        openings: Iterable[Opening],
        *,
        project_name: str = "",
        customer: str = "",
        system_id: str = "generic",
        glass_id: str = "dgu-6-16-4",
        finish_id: str = "ral",
        policy: PricingPolicy | None = None,
        supplier: Supplier | None = None,
        fallback_rates: dict[str, float] | None = None,
    ) -> "QuoteDraft":
        draft = cls(
            project_name=project_name,
            customer=customer,
            openings=list(openings),
            supplier=supplier,
            fallback_rates=fallback_rates,
        )
        if policy is None:
            # VAT comes from the statute book, not from a default that was
            # right the year the software was written. The ERP holds the
            # Israeli history by date; a quotation issued today uses today's.
            from datetime import date as _date

            from ..erp.sales import vat_rate

            policy = PricingPolicy(tax_pct=vat_rate(_date.today()) * 100.0, currency="ILS")
        draft.variants.append(
            QuoteVariant(
                variant_id="A",
                name="A",
                system_id=system_id,
                glass_id=glass_id,
                finish_id=finish_id,
                policy=policy,
            )
        )
        draft.recompute()
        return draft

    # -- lookups --------------------------------------------------------------
    def variant(self, variant_id: str | None = None) -> QuoteVariant:
        wanted = variant_id or self.active_variant_id
        for candidate in self.variants:
            if candidate.variant_id == wanted:
                return candidate
        raise QuoteEditError(
            f"No option {wanted!r}. Have: {', '.join(v.variant_id for v in self.variants)}"
        )

    @property
    def quotation(self) -> Quotation:
        quote = self.variant().quotation
        if quote is None:  # pragma: no cover - recompute() always fills it
            raise QuoteEditError("The draft has not been priced yet")
        return quote

    # -- recomputation ---------------------------------------------------------
    def recompute(self) -> None:
        """Re-derive everything from the inputs, then reapply the edits.

        This is the whole trick: a change to one opening, the system, the
        glass or the margin goes through the same full chain as the first
        draft, so the cut list, the BOM and the price cannot disagree — and
        the operator's pinned lines are laid back on top, flagged when the
        ground has moved under them.
        """
        from ..systems import DIRECTORY

        lookup = self.mass_lookup or _default_mass_lookup
        for variant in self.variants:
            glass = STANDARD_BUILDUPS.get(variant.glass_id)
            if glass is None:
                raise QuoteEditError(
                    f"No glass build-up {variant.glass_id!r}. Have: "
                    + ", ".join(sorted(STANDARD_BUILDUPS))
                )
            builder = (
                ElementBuilder.for_system(
                    variant.system_id, glass_catalogue={glass.id: glass}
                )
                if DIRECTORY.get(variant.system_id) is not None
                else ElementBuilder(glass_catalogue={glass.id: glass})
            )
            variant.builds = [builder.build(opening) for opening in self.openings]
            variant.bom = build_bom(
                variant.builds,
                project_name=self.project_name,
                currency=variant.policy.currency,
            )

            kilograms, mass_complete = _aluminium_mass(variant.builds, lookup)
            variant.aluminium_kg = round(kilograms, 1)
            # Charged on the figure the sheet shows, so the client's arithmetic
            # and the shop's cannot disagree by a rounding step.
            variant.finish_cost = round(variant.aluminium_kg * variant.finish.rate_per_kg, 2)

            policy = replace(
                variant.policy, fixed_charges=variant.policy.fixed_charges + variant.finish_cost
            )
            variant.quotation = build_quotation(
                variant.builds,
                variant.bom,
                project_name=self.project_name,
                customer=self.customer or None,
                policy=policy,
                labour=variant.labour,
                default_supplier=self.supplier,
                fallback_rates=self.fallback_rates,
            )
            variant.warnings = list(variant.quotation.warnings)
            if variant.finish.rate_per_kg and not variant.finish.provenance.may_be_cut_to:
                variant.warnings.append(
                    f"Finish '{variant.finish.name}' is priced at a stand-in "
                    f"{variant.finish.rate_per_kg:.2f}/kg; enter the coater's own rate "
                    "to firm it up."
                )
            if not mass_complete:
                variant.warnings.append(
                    "Some profile masses are missing from the library; the finish "
                    "cost uses a typical 1.6 kg/m for those and is approximate."
                )

        self._apply_overrides()

    def _apply_overrides(self) -> None:
        """Lay the hand edits back over the recomputed lines.

        A pinned unit price is a negotiation outcome, and it has to mean the
        same thing on every option or the options table becomes an argument.
        The rule: the pin binds the active option exactly, and carries to the
        other options **plus the computed spec difference** — the price that
        was agreed, plus what the thermal glass or the anodising actually
        adds. Carrying the bare pin would quietly swallow the premium; not
        carrying it would compare a negotiated A against a list-price B.

        Discounts and rewritten descriptions are relative decisions and carry
        as they are.
        """
        self.stale_overrides = []
        computed: dict[str, dict[str, float]] = {
            variant.variant_id: {
                line.code: line.unit_price
                for line in (variant.quotation.lines if variant.quotation else [])
                if line.code
            }
            for variant in self.variants
        }
        active_computed = computed.get(self.active_variant_id, {})

        for variant in self.variants:
            quote = variant.quotation
            if quote is None:
                continue
            is_active = variant.variant_id == self.active_variant_id
            lines = {line.code: line for line in quote.lines if line.code}
            for override in self.overrides.values():
                line = lines.get(override.element_id)
                if line is None:
                    if is_active:
                        self.stale_overrides.append(override.key)
                    continue
                if override.kind is OverrideKind.UNIT_PRICE:
                    pinned = float(override.value)
                    if is_active:
                        base = active_computed.get(override.element_id)
                        if (
                            override.base_unit_price is not None
                            and base is not None
                            and abs(base - override.base_unit_price)
                            > max(0.01, 0.005 * override.base_unit_price)
                        ):
                            # The arithmetic moved under the hand price. The
                            # hand price still wins — it was a decision — but
                            # the drift is surfaced instead of buried.
                            self.stale_overrides.append(override.key)
                        line.unit_price = pinned
                    else:
                        premium = line.unit_price - active_computed.get(
                            override.element_id, line.unit_price
                        )
                        line.unit_price = round(pinned + premium, 2)
                elif override.kind is OverrideKind.DESCRIPTION:
                    line.description = str(override.value)
                elif override.kind is OverrideKind.DISCOUNT_PCT:
                    line.unit_price = round(
                        line.unit_price * (1.0 - float(override.value) / 100.0), 2
                    )

    # -- journal ----------------------------------------------------------------
    def _record(self, what: str, before: Any, after: Any, revert: Callable[[], None],
                by: str = "") -> None:
        self.journal.append(
            JournalEntry(
                at=datetime.now(timezone.utc), what=what,
                before=before, after=after, by=by, revert=revert,
            )
        )

    def undo(self) -> JournalEntry | None:
        """Take back the last edit. Returns it, or ``None`` when there is none."""
        if not self.journal:
            return None
        entry = self.journal.pop()
        if entry.revert is not None:
            entry.revert()
        self.recompute()
        return entry

    # -- global edits -------------------------------------------------------------
    def set_margin(self, margin_pct: float, *, variant_id: str | None = None, by: str = "") -> None:
        variant = self.variant(variant_id)
        before = variant.policy.margin_pct
        if margin_pct >= 100.0 or margin_pct < 0.0:
            raise QuoteEditError("Margin must be between 0 and 100% of selling price")

        def revert() -> None:
            variant.policy.margin_pct = before

        variant.policy.margin_pct = margin_pct
        self._record(f"margin ({variant.variant_id})", before, margin_pct, revert, by)
        self.recompute()

    def set_system(self, system_id: str, *, variant_id: str | None = None, by: str = "") -> None:
        """The what-if that sells thermal series: swap and see the number move."""
        variant = self.variant(variant_id)
        before = variant.system_id

        def revert() -> None:
            variant.system_id = before

        variant.system_id = system_id
        self._record(f"system ({variant.variant_id})", before, system_id, revert, by)
        self.recompute()

    def set_glass(self, glass_id: str, *, variant_id: str | None = None, by: str = "") -> None:
        if glass_id not in STANDARD_BUILDUPS:
            raise QuoteEditError(
                f"No glass build-up {glass_id!r}. Have: " + ", ".join(sorted(STANDARD_BUILDUPS))
            )
        variant = self.variant(variant_id)
        before = variant.glass_id

        def revert() -> None:
            variant.glass_id = before

        variant.glass_id = glass_id
        self._record(f"glass ({variant.variant_id})", before, glass_id, revert, by)
        self.recompute()

    def set_finish(self, finish_id: str, *, variant_id: str | None = None, by: str = "") -> None:
        if finish_id not in FINISHES:
            raise QuoteEditError(
                f"No finish {finish_id!r}. Have: " + ", ".join(sorted(FINISHES))
            )
        variant = self.variant(variant_id)
        before = variant.finish_id

        def revert() -> None:
            variant.finish_id = before

        variant.finish_id = finish_id
        self._record(f"finish ({variant.variant_id})", before, finish_id, revert, by)
        self.recompute()

    # -- opening edits ---------------------------------------------------------
    def resize_opening(
        self, element_id: str, *, width: float | None = None, height: float | None = None,
        quantity: int | None = None, by: str = "",
    ) -> None:
        """The last-minute size change, propagated everywhere at once."""
        for index, opening in enumerate(self.openings):
            if opening.element_id != element_id:
                continue
            before = (opening.width, opening.height, opening.quantity)
            updated = opening.model_copy(
                update={
                    key: value
                    for key, value in (
                        ("width", width), ("height", height), ("quantity", quantity)
                    )
                    if value is not None
                }
            )

            def revert(index: int = index, previous: Opening = opening) -> None:
                self.openings[index] = previous

            self.openings[index] = updated
            after = (updated.width, updated.height, updated.quantity)
            self._record(f"opening {element_id}", before, after, revert, by)
            self.recompute()
            return
        raise QuoteEditError(f"No opening {element_id!r} in this draft")

    # -- line edits --------------------------------------------------------------
    def _set_override(self, override: LineOverride) -> None:
        before = self.overrides.get(override.key)

        def revert() -> None:
            if before is None:
                self.overrides.pop(override.key, None)
            else:
                self.overrides[override.key] = before

        self.overrides[override.key] = override
        self._record(
            f"{override.kind.value} on {override.element_id}",
            before.value if before else None,
            override.value,
            revert,
            override.made_by,
        )
        self.recompute()

    def _computed_unit_price(self, element_id: str) -> float:
        for line in self.quotation.lines:
            if line.code == element_id:
                return line.unit_price
        raise QuoteEditError(f"No line prices {element_id!r}")

    def set_line_price(self, element_id: str, unit_price: float, *, by: str = "",
                       reason: str = "") -> None:
        """Pin a line's unit price by hand. Survives recomputes; flagged on drift."""
        if unit_price < 0:
            raise QuoteEditError("A price cannot be negative")
        # Capture the *computed* base, not the currently shown one, so drift is
        # measured against what the arithmetic said rather than a prior edit.
        previous = self.overrides.pop((element_id, OverrideKind.UNIT_PRICE.value), None)
        self.recompute()
        base = self._computed_unit_price(element_id)
        if previous is not None:
            self.overrides[previous.key] = previous
        self._set_override(
            LineOverride(
                element_id=element_id, kind=OverrideKind.UNIT_PRICE,
                value=float(unit_price), base_unit_price=base, made_by=by, reason=reason,
            )
        )

    def set_line_description(self, element_id: str, description: str, *, by: str = "") -> None:
        if not description.strip():
            raise QuoteEditError("A line needs a description")
        self._set_override(
            LineOverride(
                element_id=element_id, kind=OverrideKind.DESCRIPTION,
                value=description.strip(), made_by=by,
            )
        )

    def set_line_discount(self, element_id: str, discount_pct: float, *, by: str = "",
                          reason: str = "") -> None:
        if not 0.0 <= discount_pct < 100.0:
            raise QuoteEditError("A discount is between 0 and 100%")
        self._set_override(
            LineOverride(
                element_id=element_id, kind=OverrideKind.DISCOUNT_PCT,
                value=float(discount_pct), made_by=by, reason=reason,
            )
        )

    def clear_override(self, element_id: str, kind: OverrideKind, *, by: str = "") -> None:
        key = (element_id, kind.value)
        before = self.overrides.get(key)
        if before is None:
            return

        def revert() -> None:
            self.overrides[key] = before

        del self.overrides[key]
        self._record(f"cleared {kind.value} on {element_id}", before.value, None, revert, by)
        self.recompute()

    # -- options -------------------------------------------------------------------
    def add_variant(
        self, name: str, *, from_variant: str | None = None,
        system_id: str | None = None, glass_id: str | None = None,
        finish_id: str | None = None, by: str = "",
    ) -> QuoteVariant:
        """Add an option — same openings, different specification."""
        source = self.variant(from_variant)
        next_id = chr(ord("A") + len(self.variants))
        variant = QuoteVariant(
            variant_id=next_id,
            name=name or next_id,
            system_id=system_id or source.system_id,
            glass_id=glass_id or source.glass_id,
            finish_id=finish_id or source.finish_id,
            policy=copy.deepcopy(source.policy),
            labour=copy.deepcopy(source.labour),
        )

        def revert() -> None:
            self.variants.remove(variant)

        self.variants.append(variant)
        self._record("added option", None, f"{next_id}: {name}", revert, by)
        self.recompute()
        return variant

    def _printed_net(self, variant: QuoteVariant) -> float:
        """What this option's lines add up to, edits and hiding applied."""
        if variant.quotation is None:
            return 0.0
        hidden = {
            key[0] for key, override in self.overrides.items()
            if override.kind is OverrideKind.HIDDEN and override.value
        }
        return round(
            sum(line.total for line in variant.quotation.lines if line.code not in hidden), 2
        )

    def compare(self) -> list[dict[str, Any]]:
        """The options side by side, with the gap from the first.

        Prices are the sum of each option's *printed* lines rather than the
        policy's computed figure, so the row for the offered option always
        equals the total at the bottom of the same page. A comparison table
        that disagrees with its own document loses the argument for the shop.
        """
        if not self.variants:
            return []
        base = self._printed_net(self.variants[0])
        rows: list[dict[str, Any]] = []
        for variant in self.variants:
            glass = STANDARD_BUILDUPS.get(variant.glass_id)
            net = self._printed_net(variant)
            vat = variant.policy.tax_pct / 100.0
            rows.append(
                {
                    "id": variant.variant_id,
                    "name": variant.name,
                    "system": variant.system_id,
                    "glass": glass.describe() if glass else variant.glass_id,
                    "u_value": round(glass.u_value(), 2) if glass else None,
                    "finish": variant.finish.name,
                    "finish_hebrew": variant.finish.hebrew,
                    "aluminium_kg": variant.aluminium_kg,
                    "net": net,
                    "gross": round(net * (1.0 + vat), 2),
                    "difference": round(net - base, 2),
                }
            )
        return rows

    # -- output ----------------------------------------------------------------
    def customer_lines(self) -> list[dict[str, Any]]:
        """The lines the customer's copy shows, overrides applied, hidden gone."""
        hidden = {
            key[0] for key, override in self.overrides.items()
            if override.kind is OverrideKind.HIDDEN and override.value
        }
        rows = []
        for line in self.quotation.lines:
            if line.code in hidden:
                continue
            rows.append(
                {
                    "code": line.code,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "unit_price": round(line.unit_price, 2),
                    "total": round(line.total, 2),
                    "edited": (line.code, OverrideKind.UNIT_PRICE.value) in self.overrides
                    or (line.code, OverrideKind.DISCOUNT_PCT.value) in self.overrides,
                }
            )
        return rows

    def totals(self) -> dict[str, float]:
        """What the customer copy adds up to, from the visible lines.

        Deliberately summed from the lines rather than read off the policy:
        once the operator has pinned prices, the truthful total is the sum of
        what is printed, and a total computed any other way will one day
        disagree with its own lines in front of the client.
        """
        lines = self.customer_lines()
        net = sum(row["total"] for row in lines)
        vat = net * self.variant().policy.tax_pct / 100.0
        return {
            "net": round(net, 2),
            "vat": round(vat, 2),
            "gross": round(net + vat, 2),
        }

    def internal_sheet(self) -> dict[str, Any]:
        """The shop's own view: everything, including what the customer never sees."""
        quote = self.quotation
        variant = self.variant()
        printed = self.totals()
        return {
            "breakdown": quote.breakdown(),
            "labour_hours": quote.labour_hours,
            "finish_cost": variant.finish_cost,
            "aluminium_kg": variant.aluminium_kg,
            "computed_net": round(quote.net_price, 2),
            "printed_net": printed["net"],
            "margin_after_edits": round(printed["net"] - quote.total_cost, 2),
            "unpriced_codes": quote.unpriced_codes,
            "overrides": [
                {
                    "element": override.element_id,
                    "kind": override.kind.value,
                    "value": override.value,
                    "by": override.made_by,
                    "reason": override.reason,
                    "stale": override.key in self.stale_overrides,
                }
                for override in self.overrides.values()
            ],
            "warnings": variant.warnings,
        }


__all__ = [
    "DisplayPolicy",
    "FINISHES",
    "Finish",
    "JournalEntry",
    "LineOverride",
    "OverrideKind",
    "QuoteDraft",
    "QuoteEditError",
    "QuoteVariant",
]
