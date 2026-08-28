"""The systems sold in Israel, as a directory of names.

This is a directory, not a data sheet. It answers "which series exist, from
whom, and what kind of system is each" — the questions a fabricator asks when
starting a job. It deliberately does **not** answer "what is the cut deduction
on a Klil 7000 frame", because that number belongs to Klil, it changes between
catalogue editions, and a wrong one turns into a container of scrap.

The numbers arrive the other way round: the shop loads the supplier's own
catalogue or DXF through :mod:`profileos.catalogue`, and the figures extracted
from it are attached to the series here with
:data:`~profileos.systems.model.Provenance.CONFIRMED` and a reference to the
file they came from. Until that happens a series carries family-typical values
and every document produced from it says so.

Where this list came from
-------------------------
The manufacturers are the system houses whose profiles are sold in the Israeli
market. The series and their groupings are as the operator listed them for
their own market; each entry records that in ``source``. Adding a series does
not need a code change — a JSON document with ``kind: "system_catalogue"``
registers more, which is how a shop keeps its own list.
"""

from __future__ import annotations

from .model import Manufacturer, Provenance, SystemEntry, SystemFamily

#: What ``source`` says for an entry that came from this file. Shown verbatim
#: in the catalogue table's "מקור" column, so it has to be the Hebrew a shop
#: reads, not the English name of the constant.
OPERATOR_LIST = "רשימת סדרות שהוזנה ע״י המפעיל"


MANUFACTURERS: tuple[Manufacturer, ...] = (
    Manufacturer(
        id="klil",
        name="Klil Industries",
        hebrew="קליל",
        country="IL",
        local_stock=True,
        website="https://www.klil.co.il",
    ),
    Manufacturer(
        id="alubin",
        name="Alubin",
        hebrew="אלובין",
        country="IL",
        local_stock=True,
        website="https://www.alubin.com",
    ),
    Manufacturer(
        id="extal",
        name="Extal",
        hebrew="אקסטל",
        country="IL",
        local_stock=True,
        website="https://www.extal.co.il",
    ),
    Manufacturer(
        id="alumgold", name="Alum Gold", hebrew="אלום גולד", country="IL", local_stock=True
    ),
    Manufacturer(id="apex", name="Apex", hebrew="אפקס", country="IL", local_stock=True),
    Manufacturer(
        id="alumgraph", name="Alum Graph", hebrew="אלום גרף", country="IL", local_stock=True
    ),
    Manufacturer(
        id="schuco",
        name="Schüco",
        hebrew="שוקו",
        country="DE",
        local_stock=True,
        website="https://www.schueco.com",
    ),
    Manufacturer(
        id="reynaers",
        name="Reynaers Aluminium",
        hebrew="ריינרס",
        country="BE",
        local_stock=False,
        website="https://www.reynaers.com",
    ),
    Manufacturer(
        id="wicona",
        name="Wicona",
        hebrew="ויקונה",
        country="DE",
        local_stock=False,
        website="https://www.wicona.com",
    ),
    Manufacturer(
        id="cortizo",
        name="Cortizo",
        hebrew="קורטיזו",
        country="ES",
        local_stock=False,
        website="https://www.cortizo.com",
    ),
    Manufacturer(
        id="alumil",
        name="Alumil",
        hebrew="אלומיל",
        country="GR",
        local_stock=False,
        website="https://www.alumil.com",
    ),
    Manufacturer(
        id="hula", name="Hula Aluminium", hebrew="אלומיניום החולה",
        country="IL", local_stock=True,
    ),
    Manufacturer(
        id="metalpress", name="Metalpress", hebrew="מטלפרס",
        country="IL", local_stock=True,
    ),
    Manufacturer(
        id="technal", name="Technal", hebrew="טכנל",
        country="FR", local_stock=False,
    ),
    # PVC systems. A growing share of the Israeli market — and a different
    # trade inside the same workshop: welded corners rather than mitred and
    # screwed, steel reinforcement inside the chambers, and a coefficient of
    # thermal expansion that makes a long dark frame move enough to matter.
    Manufacturer(
        id="rehau", name="REHAU", hebrew="רהאו",
        country="DE", local_stock=False, website="https://www.rehau.com",
    ),
    Manufacturer(
        id="veka", name="VEKA", hebrew="וקה",
        country="DE", local_stock=False, website="https://www.veka.com",
    ),
    Manufacturer(
        id="deceuninck", name="Deceuninck", hebrew="דקווניק",
        country="BE", local_stock=False,
    ),
    Manufacturer(
        id="salamander", name="Salamander", hebrew="סלמנדר",
        country="DE", local_stock=False,
    ),
)


def _entry(
    manufacturer: str,
    series: str,
    hebrew: str,
    family: SystemFamily | None = None,
    *,
    thermally_broken: bool = False,
    aliases: tuple[str, ...] = (),
    notes: str = "",
) -> SystemEntry:
    """One directory entry. Every figure starts unknown, on purpose."""
    return SystemEntry(
        manufacturer=manufacturer,
        series=series,
        hebrew=hebrew,
        family=family,
        thermally_broken=thermally_broken,
        provenance=Provenance.UNKNOWN,
        source=OPERATOR_LIST,
        aliases=aliases,
        notes=notes,
    )


#: The series directory. ``family`` is filled in only where the grouping is
#: established; the rest are named but not yet classified, which is an honest
#: state and one the software handles rather than papering over.
SERIES: tuple[SystemEntry, ...] = (
    # -- קליל ------------------------------------------------------------- #
    _entry("klil", "7300", "קליל בלגי", SystemFamily.CASEMENT, aliases=("בלגי", "belgian")),
    _entry("klil", "4300", "קליל בלגי", SystemFamily.CASEMENT, aliases=("בלגי", "belgian")),
    _entry("klil", "2200", "קליל אופיס", SystemFamily.PARTITION, aliases=("אופיס", "office")),
    _entry("klil", "5500", "קליל אופיס", SystemFamily.PARTITION, aliases=("אופיס", "office")),
    _entry("klil", "7000", "קליל"),
    _entry("klil", "9000", "קליל"),
    _entry("klil", "8300", "קליל"),
    _entry("klil", "9200", "קליל"),
    _entry("klil", "1700", "קליל"),
    # -- אלובין ----------------------------------------------------------- #
    _entry("alubin", "4500", "אלובין"),
    _entry("alubin", "5000", "אלובין"),
    _entry("alubin", "8000", "אלובין"),
    _entry("alubin", "9000", "אלובין"),
    _entry("alubin", "Alumeal", "אלובין אלומייל", aliases=("אלומייל", "alumeal")),
    # -- אקסטל ------------------------------------------------------------ #
    _entry("extal", "E19", "אקסטל"),
    _entry("extal", "E36", "אקסטל"),
    _entry("extal", "E45", "אקסטל"),
    _entry("extal", "E50", "אקסטל"),
    _entry("extal", "E98", "אקסטל"),
)


#: Hardware makers whose gear is fitted to these systems. Named so a bill of
#: materials can say whose hinge it is; no load ratings are claimed here,
#: because a hinge rating is the thing a sash weight check must not guess.
HARDWARE_MAKERS: tuple[Manufacturer, ...] = (
    Manufacturer(id="fapim", name="Fapim", hebrew="פאפים", country="IT", local_stock=True),
    Manufacturer(id="savio", name="Savio", hebrew="סביו", country="IT", local_stock=True),
    Manufacturer(id="giesse", name="Giesse", hebrew="ג'יסה", country="IT", local_stock=True),
    Manufacturer(id="roto", name="Roto Frank", hebrew="רוטו", country="DE", local_stock=True),
    Manufacturer(id="stublina", name="Stublina", hebrew="סטובלינה", country="RS", local_stock=True),
)


__all__ = ["HARDWARE_MAKERS", "MANUFACTURERS", "OPERATOR_LIST", "SERIES"]
