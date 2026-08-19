"""The shop drawing package: every sheet an approval needs, assembled.

A package is not a folder of pictures. It is a numbered set with a cover, an
elevation sheet per elevation, a details sheet, one title block filled the same
way on every sheet, and a revision history that runs across all of them. That
consistency is the part a drawing office checks first and the part that is
tedious to maintain by hand, so it is generated.

The package refuses to call itself issued-for-construction when the systems
behind it are not confirmed. A not-for-construction stamp on every sheet is
cheap; a job cut to a stand-in deduction is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..elements.builder import ElementBuild
from .elevation import ElevationStyle, elevation, legend
from .model import Drawing
from .section import Detail, SectionStyle, STONE_CLAD_CONCRETE, WallBuildUp, wall_section
from .sheet import Revision, Sheet, SheetSize, TitleBlock, Viewport, grid_frames


@dataclass
class PackageInfo:
    """What every sheet in the set says about the job."""

    project: str = ""
    client: str = ""
    number_prefix: str = "A"
    company: str = ""
    company_line: str = ""
    drawn_by: str = ""
    checked_by: str = ""
    issued: date = field(default_factory=date.today)
    revisions: list[Revision] = field(default_factory=list)
    size: SheetSize = SheetSize.A3
    wall: WallBuildUp = field(default_factory=lambda: STONE_CLAD_CONCRETE)
    #: The language the sheets are labelled in, alongside English.
    language: Any = "he"

    @property
    def revision(self) -> str:
        return self.revisions[-1].mark if self.revisions else "-"


@dataclass
class DrawingPackage:
    """A numbered set of sheets, and what is provisional about it."""

    info: PackageInfo
    sheets: list[Sheet] = field(default_factory=list)
    #: Warnings that belong on every sheet, not in a covering email.
    stamps: list[str] = field(default_factory=list)

    def numbers(self) -> list[str]:
        return [sheet.title_block.number for sheet in self.sheets]

    def write(self, directory: str | Path, *, formats: Sequence[str] = ("pdf", "dxf", "svg")
              ) -> list[Path]:
        """Write every sheet in every requested format."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for sheet in self.sheets:
            stem = sheet.title_block.number or sheet.title_block.title or "sheet"
            if "pdf" in formats:
                written.append(sheet.to_pdf(target / f"{stem}.pdf"))
            if "dxf" in formats:
                written.append(sheet.to_dxf(target / f"{stem}.dxf"))
            if "svg" in formats:
                path = target / f"{stem}.svg"
                path.write_text(sheet.to_svg(), encoding="utf-8")
                written.append(path)
        return written


def _bilingual(key: str, language: Any, *, plural: bool = False) -> str:
    """The word in the sheet's language and in English, since both are read."""
    from ..i18n import translate

    local = translate(key, language)
    english = translate(key, "en")
    if plural:
        english = english + "s"
    return local if local == english else f"{local} / {english}"


def _title_block(info: PackageInfo, index: int, title: str, scale: str) -> TitleBlock:
    return TitleBlock(
        company=info.company,
        company_line=info.company_line,
        project=info.project,
        client=info.client,
        title=title,
        number=f"{info.number_prefix}{index:02d}",
        revision=info.revision,
        scale=scale,
        sheet_size=info.size.value,
        drawn_by=info.drawn_by,
        checked_by=info.checked_by,
        issued=info.issued,
        language=info.language,
    )


def elevation_sheets(
    builds: Sequence[ElementBuild],
    info: PackageInfo,
    *,
    scale: int = 20,
    per_sheet: int = 2,
    start: int = 1,
) -> list[Sheet]:
    """One sheet per group of elevations, at a stated scale."""
    sheets: list[Sheet] = []
    for offset in range(0, len(builds), per_sheet):
        group = builds[offset : offset + per_sheet]
        index = start + len(sheets)
        sheet = Sheet(
            size=info.size,
            title_block=_title_block(info, index, _bilingual("drawing.elevation", info.language, plural=True), f"1:{scale}"),
            revisions=list(info.revisions),
        )
        frames = grid_frames(_views_area(sheet), columns=len(group), rows=1)
        for build, frame in zip(group, frames):
            drawing = elevation(build, style=ElevationStyle(scale=scale))
            sheet.add(
                Viewport(
                    drawing=drawing,
                    scale=scale,
                    frame=frame,
                    label=build.opening.name or build.opening.element_id,
                )
            )
        sheets.append(sheet)
    return sheets


def detail_sheet(
    info: PackageInfo,
    *,
    details: Iterable[Detail] = (Detail.HEAD, Detail.JAMB, Detail.SILL, Detail.MULLION),
    scale: int = 5,
    profile: Any = None,
    index: int = 90,
) -> tuple[Sheet, list[str]]:
    """The wall sections, four to a sheet, with whatever they cannot promise."""
    details = list(details)
    sheet = Sheet(
        size=info.size,
        title_block=_title_block(info, index, _bilingual("drawing.section", info.language, plural=True), f"1:{scale}"),
        revisions=list(info.revisions),
    )
    columns = min(len(details), 2)
    rows = (len(details) + columns - 1) // columns
    frames = grid_frames(_views_area(sheet), columns=columns, rows=rows)
    notes: list[str] = []
    for detail, frame in zip(details, frames):
        result = wall_section(
            detail,
            build_up=info.wall,
            style=SectionStyle(scale=scale, language=info.language),
            profile=profile,
        )
        notes.extend(result.notes)
        sheet.add(
            Viewport(
                drawing=result.drawing,
                scale=scale,
                frame=frame,
                label=f"{detail.label(info.language)} / {detail.label('en').capitalize()}",
            )
        )
    return sheet, sorted(set(notes))


def _views_area(sheet: Sheet) -> tuple[float, float, float, float]:
    """The drawing area, less the strip the title block occupies."""
    x, y, width, height = sheet.drawing_area()
    return (x, y + 14.0, width - sheet.block_width - 8.0, height - 18.0)


def build_package(
    builds: Sequence[ElementBuild],
    info: PackageInfo,
    *,
    elevation_scale: int = 20,
    detail_scale: int = 5,
    profile: Any = None,
    details: Iterable[Detail] = (Detail.HEAD, Detail.JAMB, Detail.SILL, Detail.MULLION),
) -> DrawingPackage:
    """Assemble the whole set from the elements as they will be built."""
    sheets = elevation_sheets(builds, info, scale=elevation_scale)
    detail, notes = detail_sheet(
        info, details=details, scale=detail_scale, profile=profile,
        index=len(sheets) + 1,
    )
    sheets.append(detail)

    stamps = list(notes)
    provisional = [b for b in builds if not b.may_be_cut]
    if provisional:
        stamps.insert(0, _bilingual("drawing.not_for_construction", info.language))

    # The stamp goes on every sheet. A package where only the first page
    # carries the caveat is a package whose caveat gets detached from it.
    for sheet in sheets:
        sheet.title_block.notes = tuple(stamps)

    # The legend belongs with the elevations, where the symbols are — placed
    # bottom left, clear of the title block and the revision table, which grow
    # up from the bottom right corner.
    if sheets and builds:
        first = sheets[0]
        x, y, _, _ = first.drawing_area()
        first.add(
            Viewport(
                drawing=legend(scale=float(elevation_scale), language=info.language),
                scale=elevation_scale,
                frame=(x + 4.0, y + 4.0, 110.0, 30.0),
            )
        )

    return DrawingPackage(info=info, sheets=sheets, stamps=stamps)


__all__ = [
    "DrawingPackage",
    "PackageInfo",
    "build_package",
    "detail_sheet",
    "elevation_sheets",
]
