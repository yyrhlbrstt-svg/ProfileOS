"""Buying in euros and selling in shekels, without losing the difference.

An Israeli aluminium shop buys hardware from Italy and Germany, sometimes
profiles from Greece, and sells every one of those jobs in shekels. So the
cost side of a quotation is in one currency and the price side is in another,
and the number that joins them moves — five per cent in a quiet month, more
in a bad one.

Two things go wrong when that is handled by typing a rate into a spreadsheet.
A quotation priced in March and won in June is costed at March's rate, and
nobody notices until the invoice from the supplier arrives. And a rate typed
once gets used for a year.

So a rate here is a fact with a date on it and a note saying where it came
from. Converting a figure asks for the date it belongs to, and a rate older
than the shop's own tolerance is reported rather than used silently. Nothing
is fetched from the internet: the shop enters the rate they actually got, or
the one their bank published, and that is the honest number anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("erp.currency")

#: What the shop keeps its books in.
HOME = "ILS"

#: The currencies a fabricator here actually meets.
CURRENCIES: dict[str, str] = {
    "ILS": "שקל",
    "EUR": "אירו",
    "USD": "דולר",
    "GBP": "לירה שטרלינג",
}

#: Past this, a rate is history rather than a price. A quotation costed on a
#: rate older than this is quoting last season's euro.
STALE_DAYS = 30


@dataclass(frozen=True)
class Rate:
    """One exchange rate, on one day, from somewhere somebody can name."""

    currency: str
    #: How many shekels one unit of ``currency`` costs.
    per_unit: float
    on: date
    source: str = ""

    def __post_init__(self) -> None:
        if self.per_unit <= 0:
            raise ProfileOSError("שער חליפין חייב להיות חיובי")
        if self.currency == HOME:
            raise ProfileOSError("אין שער חליפין לשקל מול עצמו")

    @property
    def is_sourced(self) -> bool:
        return bool(self.source.strip())

    def age(self, on: date | None = None) -> int:
        return ((on or date.today()) - self.on).days

    def is_stale(self, on: date | None = None, *, days: int = STALE_DAYS) -> bool:
        return self.age(on) > days

    def describe(self) -> str:
        return (
            f"⁦1⁩ {CURRENCIES.get(self.currency, self.currency)} = "
            f"⁦{self.per_unit:.4f}⁩ ₪ · ⁦{self.on.strftime('%d/%m/%Y')}⁩"
        )


@dataclass
class Conversion:
    """A converted figure, with everything needed to defend it later."""

    amount: float
    currency: str
    home_amount: float
    rate: Rate | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        """Whether this number may be quoted to a customer as it stands."""
        return not self.warnings

    def describe(self) -> str:
        if self.currency == HOME or self.rate is None:
            return f"⁦{self.home_amount:,.2f}⁩ ₪"
        return (
            f"⁦{self.amount:,.2f}⁩ {self.currency} → ⁦{self.home_amount:,.2f}⁩ ₪ "
            f"(⁦{self.rate.per_unit:.4f}⁩, ⁦{self.rate.on.strftime('%d/%m/%Y')}⁩)"
        )


class RateBook:
    """Every rate the shop has recorded, so a figure can be re-checked."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._rates: dict[str, list[Rate]] = {}
        if self.path is not None:
            self.load()

    # -- persistence --------------------------------------------------------- #
    def load(self) -> "RateBook":
        if self.path is None or not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a bad file must not stop pricing
            _log.exception("Rate book at %s unreadable", self.path)
            return self
        for entry in raw.get("rates", []):
            try:
                self.record(
                    Rate(
                        currency=entry["currency"],
                        per_unit=float(entry["per_unit"]),
                        on=date.fromisoformat(entry["on"]),
                        source=entry.get("source", ""),
                    ),
                    save=False,
                )
            except Exception:  # noqa: BLE001 - one bad row, not the book
                _log.warning("Skipping unreadable rate: %s", entry)
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rates": [
                {
                    "currency": rate.currency, "per_unit": rate.per_unit,
                    "on": rate.on.isoformat(), "source": rate.source,
                }
                for rates in self._rates.values() for rate in rates
            ]
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    # -- contents ------------------------------------------------------------ #
    def __len__(self) -> int:
        return sum(len(rates) for rates in self._rates.values())

    def currencies(self) -> list[str]:
        return sorted(self._rates)

    def history(self, currency: str) -> list[Rate]:
        return sorted(self._rates.get(currency, ()), key=lambda rate: rate.on)

    def record(self, rate: Rate, *, save: bool = True) -> Rate:
        rates = self._rates.setdefault(rate.currency, [])
        # One rate per currency per day: entering it twice replaces it rather
        # than leaving two answers to the same question.
        rates[:] = [item for item in rates if item.on != rate.on]
        rates.append(rate)
        if save:
            self.save()
        return rate

    def latest(self, currency: str) -> Rate | None:
        rates = self.history(currency)
        return rates[-1] if rates else None

    def on(self, currency: str, when: date) -> Rate | None:
        """The rate in force on a day: the most recent one not after it.

        A quotation is costed at the rate that applied when it was priced,
        not at today's — otherwise re-opening an old quote silently reprices
        it, and the margin it was won on becomes unknowable.
        """
        applicable = [rate for rate in self.history(currency) if rate.on <= when]
        return applicable[-1] if applicable else None

    # -- converting ----------------------------------------------------------- #
    def convert(
        self, amount: float, currency: str, *, on: date | None = None,
        stale_days: int = STALE_DAYS,
    ) -> Conversion:
        """Turn a foreign figure into shekels, saying what it rests on."""
        currency = (currency or HOME).upper()
        when = on or date.today()
        if currency == HOME:
            return Conversion(amount=amount, currency=HOME, home_amount=amount)

        rate = self.on(currency, when) or self.latest(currency)
        if rate is None:
            return Conversion(
                amount=amount, currency=currency, home_amount=0.0,
                warnings=[
                    f"אין שער ל{CURRENCIES.get(currency, currency)} — "
                    "הזינו אותו לפני שמתמחרים בו"
                ],
            )

        conversion = Conversion(
            amount=amount, currency=currency,
            home_amount=round(amount * rate.per_unit, 2), rate=rate,
        )
        if rate.on > when:
            conversion.warnings.append(
                f"השער היחיד הידוע הוא מ-⁦{rate.on.strftime('%d/%m/%Y')}⁩, "
                "אחרי התאריך המבוקש"
            )
        elif rate.is_stale(when, days=stale_days):
            conversion.warnings.append(
                f"השער בן ⁦{rate.age(when)}⁩ ימים — עדכנו לפני שמוסרים הצעה"
            )
        if not rate.is_sourced:
            conversion.warnings.append("לשער לא נרשם מקור")
        return conversion

    def exposure(self, amounts: dict[str, float], *, on: date | None = None) -> dict[str, Any]:
        """How much of a job's cost is in somebody else's money.

        The number a shop needs before fixing a price for ninety days: what
        share of the cost moves if the euro does, and by how much a five per
        cent move would change it.
        """
        when = on or date.today()
        total = 0.0
        foreign = 0.0
        lines: list[dict[str, Any]] = []
        warnings: list[str] = []

        for currency, amount in amounts.items():
            conversion = self.convert(amount, currency, on=when)
            total += conversion.home_amount
            if currency.upper() != HOME:
                foreign += conversion.home_amount
            warnings.extend(conversion.warnings)
            lines.append({
                "currency": currency.upper(),
                "amount": amount,
                "home": conversion.home_amount,
                "rate": conversion.rate.per_unit if conversion.rate else None,
            })

        share = (foreign / total * 100.0) if total else 0.0
        return {
            "total": round(total, 2),
            "foreign": round(foreign, 2),
            "share_pct": round(share, 1),
            "if_rate_moves_5pct": round(foreign * 0.05, 2),
            "lines": lines,
            "warnings": sorted(set(warnings)),
        }


def default_rates() -> RateBook:
    from ..core.config import get_settings

    return RateBook(get_settings().data_dir / "exchange_rates.json")


__all__ = [
    "CURRENCIES",
    "Conversion",
    "HOME",
    "STALE_DAYS",
    "Rate",
    "RateBook",
    "default_rates",
]
