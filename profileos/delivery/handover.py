"""The folder handed over with the building, and the warranty that starts there.

A fabricator's obligation does not end when the lorry leaves. It ends —
if it ever does — with a customer who can prove what was installed, an
occupier who knows not to clean anodised aluminium with an abrasive, and a shop
that can tell, three years later, whether the call about a leaking window is
inside the warranty or outside it.

Most shops hand over nothing. The occupier finds out how to adjust a hinge by
ringing the fabricator, the warranty period is whatever anybody remembers, and
a claim in year four is settled by whoever sounds more certain on the phone.

This is that folder: what was installed and where, which glass with which
certificate, the hardware and how it is adjusted, how to clean each finish, and
a warranty whose clock starts on a stated date with stated exclusions. It is
signed by somebody at handover, and the signature is what the shop keeps.

The discipline here is a narrow one and it matters: a warranty period is a
commercial promise, not a fact this software knows. It is entered per job or it
stays empty, and a handover pack with no period on it says so in the place the
period would have been rather than quietly printing a common figure.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Iterable
from uuid import uuid4

from ..core.logging_setup import get_logger

_log = get_logger("delivery.handover")


class Cover(StrEnum):
    """The parts of the work a warranty is normally given on separately."""

    PROFILE = "profile"
    FINISH = "finish"
    GLASS = "glass"
    HARDWARE = "hardware"
    SEALING = "sealing"
    INSTALLATION = "installation"

    @property
    def hebrew(self) -> str:
        return {
            "profile": "פרופילי אלומיניום",
            "finish": "צביעה / אנודייז",
            "glass": "זכוכית ובידוד",
            "hardware": "פרזול ומנגנונים",
            "sealing": "אטימה וסיליקון",
            "installation": "עבודת ההתקנה",
        }[self.value]


@dataclass
class Warranty:
    """One line of the promise, with its clock and its exclusions."""

    cover: Cover = Cover.PROFILE
    #: Months. ``None`` means nobody has stated one, which is printed as such.
    months: int | None = None
    starts: date | None = None
    excludes: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def expires(self) -> date | None:
        if self.months is None or self.starts is None:
            return None
        # Month arithmetic without a calendar library: the day of the month is
        # kept where it exists, and clamped where it does not (a warranty that
        # starts on the 31st ends on the 30th, not on the 1st of the month
        # after — which is the direction that favours the customer).
        total = self.starts.month - 1 + self.months
        year = self.starts.year + total // 12
        month = total % 12 + 1
        day = self.starts.day
        while day > 1:
            try:
                return date(year, month, day)
            except ValueError:
                day -= 1
        return date(year, month, 1)

    @property
    def is_stated(self) -> bool:
        return self.months is not None

    def is_live(self, on: date | None = None) -> bool:
        end = self.expires
        return end is not None and (on or date.today()) <= end

    def describe(self) -> str:
        if not self.is_stated:
            return f"{self.cover.hebrew}: לא נקבעה תקופת אחריות"
        end = self.expires
        body = f"{self.cover.hebrew}: ⁦{self.months}⁩ חודשים"
        if end:
            body += f" · עד ⁦{end.strftime('%d/%m/%Y')}⁩"
        if self.excludes:
            body += " · למעט " + ", ".join(self.excludes)
        return body


#: What a warranty on aluminium work does not normally cover, in the words a
#: shop would use rather than in legal language. These are defaults offered to
#: a shop to edit, not terms this software imposes.
COMMON_EXCLUSIONS: dict[Cover, tuple[str, ...]] = {
    Cover.FINISH: (
        "שריטות שנגרמו אחרי המסירה",
        "ניקוי בחומרים שוחקים או ממסים",
    ),
    Cover.GLASS: ("שבר", "שריטות"),
    Cover.HARDWARE: ("שימוש בכוח", "העדר כיוונון תקופתי"),
    Cover.SEALING: ("תזוזות מבנה", "פגיעה מכנית"),
    Cover.INSTALLATION: ("רטיבות שמקורה במבנה ולא בפתח",),
}

#: How each finish is looked after. Written for the occupier, not the fitter.
CARE: dict[str, tuple[str, ...]] = {
    "אנודייז": (
        "שטיפה במים פושרים וסבון ניטרלי, פעמיים בשנה, וליד הים ארבע.",
        "לעולם לא בחומר שוחק, לא בממס ולא ביתדת מתכת — השכבה האנודית דקה "
        "ממאית המילימטר ואינה נבנית מחדש.",
    ),
    "צבע": (
        "שטיפה במים פושרים וסבון ניטרלי, פעמיים בשנה.",
        "לא בחומרי ניקוי חומציים או בסיסיים ולא בסקוטש.",
    ),
    "זכוכית": (
        "מים וסבון או נוזל לניקוי חלונות, במגב גומי.",
        "לא בסכין גילוח ולא בצמר פלדה גם על כתם עקשן.",
    ),
    "פרזול": (
        "שמן קל על נקודות הסיבוב פעם בשנה.",
        "כיוונון על ידי בעל מקצוע כל שנתיים; דלת שנגררת אינה מתקנת את עצמה.",
    ),
    "ניקוז": (
        "פתחי הניקוז בסף מתנקים פעמיים בשנה — סתימה שלהם היא הסיבה הנפוצה "
        "ביותר למים על אדן החלון.",
    ),
}


@dataclass
class InstalledUnit:
    """One thing that was fitted, as it will be referred to later."""

    mark: str = ""
    room: str = ""
    description: str = ""
    width: float | None = None
    height: float | None = None
    system: str = ""
    finish: str = ""
    glass: str = ""
    hardware: str = ""
    installed_on: date | None = None
    note: str = ""

    def describe(self) -> str:
        size = (
            f"⁦{self.width:g}×{self.height:g}⁩"
            if self.width and self.height else ""
        )
        parts = [self.mark or "—", self.room, size, self.description]
        return " · ".join(part for part in parts if part)


@dataclass
class HandoverPack:
    """What the customer is given, and what the shop keeps a copy of."""

    pack_id: str = field(default_factory=lambda: f"HO-{uuid4().hex[:6].upper()}")
    job_id: str = ""
    job_name: str = ""
    customer_name: str = ""
    site_address: str = ""
    handed_over_on: date | None = None
    handed_over_by: str = ""
    received_by: str = ""
    units: list[InstalledUnit] = field(default_factory=list)
    warranties: list[Warranty] = field(default_factory=list)
    #: Certificates and test reports that belong with the work.
    documents: list[str] = field(default_factory=list)
    service_contact: str = ""
    note: str = ""

    @property
    def is_signed(self) -> bool:
        return bool(self.received_by.strip()) and self.handed_over_on is not None

    def warranty(self, cover: Cover) -> Warranty | None:
        for entry in self.warranties:
            if entry.cover is cover:
                return entry
        return None

    def live_warranties(self, on: date | None = None) -> list[Warranty]:
        return [entry for entry in self.warranties if entry.is_live(on)]

    def covers(self, cover: Cover, *, on: date | None = None) -> bool:
        """Whether a call about this part is inside the warranty today.

        The question the shop asks three years later, answered from what was
        written at handover rather than from what anybody remembers.
        """
        entry = self.warranty(cover)
        return entry is not None and entry.is_live(on)

    def care_notes(self) -> list[tuple[str, tuple[str, ...]]]:
        """Care instructions for the finishes actually on this job.

        Printing the anodising instructions on a job that was painted teaches
        the occupier to ignore the page.
        """
        wanted: list[str] = []
        finishes = " ".join(unit.finish for unit in self.units)
        if "אנודייז" in finishes:
            wanted.append("אנודייז")
        if "צבע" in finishes or "ראל" in finishes or "RAL" in finishes.upper():
            wanted.append("צבע")
        if not wanted:
            wanted.append("אנודייז")
        wanted += ["זכוכית", "פרזול", "ניקוז"]
        return [(key, CARE[key]) for key in wanted if key in CARE]

    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.units:
            found.append("אין פריטים בתיק המסירה")
        if not self.customer_name.strip():
            found.append("חסר שם הלקוח")
        unstated = [
            entry.cover.hebrew for entry in self.warranties
            if not entry.is_stated
        ]
        if unstated:
            found.append(
                "לא נקבעה תקופת אחריות ל: " + ", ".join(unstated)
                + " — התקופה היא התחייבות מסחרית ואינה נקבעת על ידי התוכנה"
            )
        if not self.warranties:
            found.append("לא נרשמה אף אחריות")
        if not self.service_contact.strip():
            found.append("לא נרשם למי פונים בקריאת שירות")
        if not self.is_signed:
            found.append("התיק טרם נחתם על ידי מקבל העבודה")
        return found

    def describe(self) -> str:
        state = (
            f"נמסר ⁦{self.handed_over_on.strftime('%d/%m/%Y')}⁩"
            if self.handed_over_on else "טרם נמסר"
        )
        return (
            f"⁦{self.pack_id}⁩ · {self.customer_name or self.job_id} · "
            f"⁦{len(self.units)}⁩ פריטים · {state}"
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("תיק מסירה", self.pack_id),
            ("תיק עבודה", f"{self.job_id} · {self.job_name}"),
            ("לקוח", self.customer_name or "—"),
            ("כתובת", self.site_address or "—"),
            (
                "תאריך מסירה",
                f"⁦{self.handed_over_on.strftime('%d/%m/%Y')}⁩"
                if self.handed_over_on else "—",
            ),
            ("נמסר על ידי", self.handed_over_by or "—"),
            ("התקבל על ידי", self.received_by or "—"),
            ("פריטים", f"⁦{len(self.units)}⁩"),
            ("שירות", self.service_contact or "—"),
        ]


def standard_warranties(
    *, starts: date | None = None, months: dict[Cover, int] | None = None
) -> list[Warranty]:
    """A warranty line per part of the work, with the periods left to the shop.

    ``months`` is where the shop's own promise goes. Anything not named there
    comes back with no period and prints as "not stated" — which is the honest
    output for a figure this software has no way to know.
    """
    stated = months or {}
    return [
        Warranty(
            cover=cover,
            months=stated.get(cover),
            starts=starts,
            excludes=list(COMMON_EXCLUSIONS.get(cover, ())),
        )
        for cover in Cover
    ]


def pack_from_job(
    job: Any,
    *,
    builds: Iterable[Any] = (),
    handed_over_on: date | None = None,
    warranty_months: dict[Cover, int] | None = None,
    service_contact: str = "",
) -> HandoverPack:
    """Build the handover pack from what the job actually contains."""
    pack = HandoverPack(
        job_id=str(getattr(job, "job_id", "")),
        job_name=str(getattr(job, "name", "")),
        customer_name=str(getattr(job, "customer_name", "")),
        site_address=str(getattr(job, "site_address", "")),
        handed_over_on=handed_over_on,
        service_contact=service_contact,
    )

    for build in builds:
        opening = getattr(build, "opening", None)
        if opening is None:
            continue
        panes = getattr(build, "glass", []) or []
        build_up = getattr(panes[0], "build_up", None) if panes else None
        for _copy in range(max(1, int(getattr(opening, "quantity", 1) or 1))):
            pack.units.append(InstalledUnit(
                mark=str(getattr(opening, "name", "") or ""),
                room=str(getattr(opening, "reference", "") or ""),
                description=str(getattr(opening, "notes", "") or ""),
                width=float(getattr(opening, "width", 0.0) or 0.0) or None,
                height=float(getattr(opening, "height", 0.0) or 0.0) or None,
                system=str(getattr(opening, "system_id", "") or ""),
                finish=str(getattr(opening, "finish", "") or ""),
                glass=build_up.describe() if build_up is not None else "",
                installed_on=handed_over_on,
            ))

    pack.warranties = standard_warranties(
        starts=handed_over_on, months=warranty_months
    )
    _log.info(
        "Handover pack %s built for %s with %d units",
        pack.pack_id, pack.job_id, len(pack.units),
    )
    return pack


# --------------------------------------------------------------------------- #
# The printed pack
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_handover(pack: HandoverPack, *, company: str = "") -> str:
    """The folder, in Hebrew, ready to print and to sign."""
    from ..branding import active_brand

    brand = active_brand()
    name = company or brand.display_name

    meta = "".join(
        f"<div><dt>{_esc(label)}</dt><dd>{_esc(value)}</dd></div>"
        for label, value in pack.summary_rows()
    )

    units = "".join(
        "<tr>"
        f"<td>{_esc(unit.mark)}</td><td>{_esc(unit.room)}</td>"
        # A width and a height either side of a × are two runs of Latin
        # digits inside a right-to-left paragraph, and without an explicit
        # direction the renderer puts the height first. A size read backwards
        # on a handover document is the kind of error nobody catches for years.
        f"<td class='num' dir='ltr'>"
        f"{_esc(f'{unit.width:g}×{unit.height:g}') if unit.width and unit.height else '—'}"
        "</td>"
        f"<td>{_esc(unit.system)}</td><td>{_esc(unit.finish)}</td>"
        f"<td>{_esc(unit.glass)}</td><td>{_esc(unit.note)}</td>"
        "</tr>"
        for unit in pack.units
    )

    warranty_rows = "".join(
        "<tr>"
        f"<td>{_esc(entry.cover.hebrew)}</td>"
        f"<td class='num'>"
        + (f"{entry.months}" if entry.is_stated else
           "<span class='unstated'>לא נקבעה</span>")
        + "</td>"
        f"<td class='num'>"
        + (
            _esc(entry.expires.strftime("%d/%m/%Y"))
            if entry.expires else "—"
        )
        + "</td>"
        f"<td>{_esc(', '.join(entry.excludes) or '—')}</td>"
        "</tr>"
        for entry in pack.warranties
    )

    care = "".join(
        f"<div class='care'><h3>{_esc(title)}</h3><ul>"
        + "".join(f"<li>{_esc(line)}</li>" for line in lines)
        + "</ul></div>"
        for title, lines in pack.care_notes()
    )

    documents = (
        "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in pack.documents)
        + "</ul>"
        if pack.documents
        else "<p class='muted'>לא צורפו תעודות.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>תיק מסירה {_esc(pack.pack_id)}</title>
<style>{_HANDOVER_CSS}</style>
</head><body><div class="wrap">
<header>
  <div><h1>תיק מסירה ואחריות</h1>
  <div class="sub">{_esc(name)} · {_esc(pack.site_address)}</div></div>
  <div class="id">{_esc(pack.pack_id)}</div>
</header>

<dl class="meta">{meta}</dl>

<h2>מה הותקן</h2>
<table><thead><tr>
  <th>סימון</th><th>מיקום</th><th>מידה</th><th>מערכת</th>
  <th>גימור</th><th>זיגוג</th><th>הערות</th>
</tr></thead><tbody>{units}</tbody></table>

<h2>אחריות</h2>
<table><thead><tr>
  <th>על מה</th><th>חודשים</th><th>עד</th><th>אינו כולל</th>
</tr></thead><tbody>{warranty_rows}</tbody></table>
<p class="muted">
  תקופת האחריות נמנית מיום המסירה הרשום למעלה. אחריות אינה חלה על נזק
  שנגרם לאחר המסירה, על שינויים שבוצעו בידי אחר, ועל תחזוקה שלא בוצעה
  כמפורט בעמוד הבא.
</p>

<h2>איך מטפלים בזה</h2>
{care}

<h2>תעודות מצורפות</h2>
{documents}

<h2>קריאת שירות</h2>
<p>{_esc(pack.service_contact or 'לא נרשם')}</p>

<div class="sign">
  <div><span>נמסר על ידי</span><div class="line">{_esc(pack.handed_over_by)}</div></div>
  <div><span>התקבל על ידי</span><div class="line">{_esc(pack.received_by)}</div></div>
  <div><span>תאריך</span><div class="line">{
      _esc(pack.handed_over_on.strftime('%d/%m/%Y'))
      if pack.handed_over_on else ''
  }</div></div>
</div>
<footer>{_esc(pack.note)}</footer>
</div></body></html>"""


_HANDOVER_CSS = """
:root { --ink:#101828; --muted:#5b6472; --line:#e4e7ec; --panel:#f7f9fc;
        --warn:#8a5a00; }
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:var(--ink);
       font-family:"Heebo","Segoe UI",system-ui,sans-serif; font-size:15px;
       line-height:1.55; }
.wrap { max-width:1000px; margin:0 auto; padding:24px; }
header { display:flex; justify-content:space-between; align-items:flex-start;
         border-bottom:3px solid var(--ink); padding-bottom:12px; }
h1 { margin:0; font-size:26px; letter-spacing:-0.02em; }
h2 { font-size:17px; margin:26px 0 8px; padding-bottom:5px;
     border-bottom:2px solid var(--line); }
h3 { font-size:14px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:14px; margin-top:4px; }
.id { font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }
.meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:8px; margin:16px 0 0; padding:0; }
.meta div { background:var(--panel); border-radius:8px; padding:8px 10px; }
.meta dt { margin:0; font-size:11px; letter-spacing:.05em; color:var(--muted); }
.meta dd { margin:2px 0 0; font-size:16px; font-weight:600; }
table { width:100%; border-collapse:collapse; }
th,td { text-align:right; padding:8px 6px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { font-size:11px; letter-spacing:.05em; color:var(--muted);
     background:var(--panel); }
td.num { text-align:left; font-variant-numeric:tabular-nums; }
.unstated { color:var(--warn); font-weight:700; }
.muted { color:var(--muted); font-size:13px; }
.care { break-inside:avoid; margin-bottom:12px; }
.care ul { margin:2px 0 0; padding-inline-start:18px; }
.sign { display:grid; grid-template-columns:repeat(3,1fr); gap:20px;
        margin-top:32px; }
.sign span { font-size:11px; letter-spacing:.05em; color:var(--muted); }
.sign .line { border-bottom:1px solid var(--ink); min-height:34px;
              padding-top:12px; font-weight:600; }
footer { margin-top:26px; padding-top:12px; border-top:1px solid var(--line);
         color:var(--muted); font-size:13px; }
@media print { .wrap { max-width:none; padding:0; }
               tr,.care,.sign { break-inside:avoid; } h2 { break-after:avoid; } }
"""


def write_handover(pack: HandoverPack, path: Any, **kwargs: Any) -> Any:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_handover(pack, **kwargs), encoding="utf-8")
    return target


__all__ = [
    "CARE",
    "COMMON_EXCLUSIONS",
    "Cover",
    "HandoverPack",
    "InstalledUnit",
    "Warranty",
    "pack_from_job",
    "render_handover",
    "standard_warranties",
    "write_handover",
]
