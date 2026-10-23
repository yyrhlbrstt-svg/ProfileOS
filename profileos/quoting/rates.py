"""The shop's own estimating rates — the only stand-in for a supplier price.

A line no price list covers is either priced at a rate the shop itself set
(and the quotation says so), or it stays unpriced. A rate written into the
program would be a made-up price on a real quotation.
"""

from __future__ import annotations

#: (key, what it prices, unit) — in the order the System page shows them.
RATE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("profile", "פרופיל אלומיניום", "₪ לק״ג"),
    ("glass_m2", "זכוכית", "₪ למ״ר"),
    ("hardware", "פרזול", "₪ ליחידה"),
    ("gasket_m", "אטמים", "₪ למטר"),
    ("labour_hour", "שעת עבודה במפעל", "₪ לשעה"),
)
#: Rates that price material lines (the rest price something else).
MATERIAL_KEYS = ("profile", "glass_m2", "hardware", "gasket_m")


def _entered() -> dict[str, float]:
    try:
        from ..core.config import get_settings

        entered = dict(getattr(get_settings(), "estimate_rates", {}) or {})
    except Exception:  # noqa: BLE001 - unknown means none, never a guess
        return {}
    known = {key for key, _label, _unit in RATE_FIELDS}
    return {key: float(value) for key, value in entered.items()
            if key in known and isinstance(value, (int, float)) and value > 0}


def shop_rates() -> dict[str, float]:
    """The material rates the shop entered (System page); empty when none were."""
    return {key: value for key, value in _entered().items() if key in MATERIAL_KEYS}


def shop_labour() -> "LabourRates":
    """The workshop hour at the shop's own rate — or at zero, which the
    quotation then says ("labour not included"), never at a rate nobody set.
    The hours per task are the general norms times the shop's own factor,
    once the owner applied one learned from its booked hours."""
    from .pricing import LabourRates

    labour = LabourRates(hourly_rate=_entered().get("labour_hour", 0.0), currency="ILS")
    factor = shop_labour_factor()
    if factor != 1.0:
        for name in ("hours_per_cut", "hours_per_element", "hours_per_sash",
                     "hours_per_glass_m2", "hours_per_element_qc"):
            setattr(labour, name, round(getattr(labour, name) * factor, 4))
    return labour


def shop_labour_factor() -> float:
    try:
        from ..core.config import get_settings

        factor = float(getattr(get_settings(), "labour_factor", 1.0) or 1.0)
    except Exception:  # noqa: BLE001 - unknown means the norms as they are
        return 1.0
    return factor if 0.3 <= factor <= 3.0 else 1.0


__all__ = ["MATERIAL_KEYS", "RATE_FIELDS", "shop_labour", "shop_labour_factor", "shop_rates"]
