"""This quotation's price per square metre against the jobs the shop won.

A quotation 40% below everything the shop has sold is almost never a bargain
on purpose: it is a missing line, a price at zero, a wrong series. One like
that, signed, is a job done at a loss. The comparison is with the shop's own
won jobs of the same trade, so it needs no outside figure — and with fewer
than a handful of them it says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

#: Fewer won jobs than this and nothing is compared.
ENOUGH = 5
#: Outside these shares of the median, the quotation is pointed out.
LOW, HIGH = 0.7, 1.5


@dataclass(frozen=True)
class Benchmark:
    median: float
    count: int

    def judge(self, net: float, area_m2: float) -> str:
        """A sentence when this price stands out, else ``""``."""
        if area_m2 <= 0 or net <= 0 or self.median <= 0:
            return ""
        per_m2 = net / area_m2
        ratio = per_m2 / self.median
        said = (f"המחיר למ״ר ⁦{per_m2:,.0f} ₪⁩ מול ⁦{self.median:,.0f} ₪⁩ בחציון של "
                f"⁦{self.count}⁩ עבודות שזכיתם בהן")
        if ratio < LOW:
            return (f"{said} — נמוך ב־⁦{(1 - ratio) * 100:.0f}%⁩. שווה לבדוק שלא חסרה שורה, "
                    "מחיר באפס או סדרה לא נכונה.")
        if ratio > HIGH:
            return (f"{said} — גבוה ב־⁦{(ratio - 1) * 100:.0f}%⁩. אם אין לזה סיבה (פתחים "
                    "מיוחדים, קומה גבוהה), הלקוח עלול ללכת למתחרה.")
        return ""


def benchmark(jobs: Iterable[Any], *, trade: str = "aluminium") -> Benchmark | None:
    """The median price per m² of the shop's won jobs of this trade."""
    prices = []
    for job in jobs:
        if getattr(job.status, "value", job.status) not in ("won", "in_production", "installed"):
            continue
        if getattr(job.trade, "value", job.trade) != trade:
            continue
        area = float(getattr(job, "total_area", 0.0) or 0.0)
        if area <= 0 or job.quote_total <= 0:
            continue
        prices.append(job.quote_total / area)
    if len(prices) < ENOUGH:
        return None
    return Benchmark(median=round(median(prices), 2), count=len(prices))


__all__ = ["Benchmark", "ENOUGH", "benchmark"]
