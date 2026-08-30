"""Reading DWG, which cannot be read directly and should not pretend to be.

DXF is a documented interchange format. DWG is AutoCAD's own binary format: its
layout is not published, it changes between releases, and no open library reads
every version of it correctly. Anything that claims to "support DWG" is really
doing one of two things — converting it to DXF first, or reading a subset and
hoping. This module does the first, out loud.

The conversion is done by a converter installed on the machine:

**ODA File Converter** — free from the Open Design Alliance, and what most
fabricators already have. It reads every DWG version AutoCAD has shipped. On
Linux it is a Qt application and wants a display even in batch mode, so it is
run with an offscreen platform plugin.

**dwg2dxf** — from LibreDWG, packaged on most Linux distributions. Open source,
lighter to install, and less complete on recent DWG versions; it is tried
second for that reason.

If neither is installed the import fails with a message naming both, rather
than with a parse error thirty frames deep. That is the honest outcome: the
file is readable, just not by anything on this computer yet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import DxfReadError
from ..core.logging_setup import get_logger

_log = get_logger("geometry.dwg")

#: Point this at a converter binary to override the search.
CONVERTER_ENV = "PROFILEOS_DWG_CONVERTER"
#: How long a single conversion may take before it is abandoned [s].
CONVERSION_TIMEOUT = 180


@dataclass(frozen=True)
class Converter:
    """One external program that can turn a DWG into a DXF."""

    name: str
    #: Executable names to look for on PATH, in order of preference.
    executables: tuple[str, ...]
    #: Where to get it, printed when nothing is installed.
    source: str

    def find(self) -> str | None:
        override = os.environ.get(CONVERTER_ENV)
        if override and Path(override).name.casefold() in {
            name.casefold() for name in self.executables
        }:
            return override if Path(override).is_file() else None
        for executable in self.executables:
            found = shutil.which(executable)
            if found:
                return found
        return None

    def command(self, binary: str, source: Path, out_dir: Path) -> list[str]:
        raise NotImplementedError

    def output_for(self, source: Path, out_dir: Path) -> Path:
        return out_dir / f"{source.stem}.dxf"

    @property
    def environment(self) -> dict[str, str]:
        return {}


class OdaFileConverter(Converter):
    """The Open Design Alliance's batch converter.

    Its arguments are positional and undocumented in ``--help``: input folder,
    output folder, output version, output type, recurse, audit, then an
    optional filename filter. It converts a *directory*, never a single file,
    which is why the source is staged into a temporary folder of its own.
    """

    def command(self, binary: str, source: Path, out_dir: Path) -> list[str]:
        return [
            binary,
            str(source.parent),
            str(out_dir),
            "ACAD2018",
            "DXF",
            "0",  # do not recurse
            "1",  # audit and repair
            source.name,
        ]

    @property
    def environment(self) -> dict[str, str]:
        # A Qt application with no window to show. Without this it aborts on a
        # headless machine, which is exactly where a batch import runs.
        return {"QT_QPA_PLATFORM": "offscreen"}


class Dwg2Dxf(Converter):
    """LibreDWG's converter, which does take a single file."""

    def command(self, binary: str, source: Path, out_dir: Path) -> list[str]:
        return [binary, "-o", str(self.output_for(source, out_dir)), str(source)]


#: Tried in order. ODA first: it is the one that reads every DWG version.
CONVERTERS: tuple[Converter, ...] = (
    OdaFileConverter(
        name="ODA File Converter",
        executables=("ODAFileConverter", "ODAFileConverter.exe"),
        source="https://www.opendesign.com/guestfiles/oda_file_converter",
    ),
    Dwg2Dxf(
        name="LibreDWG dwg2dxf",
        executables=("dwg2dxf",),
        source="https://www.gnu.org/software/libredwg/ (package: libredwg-tools)",
    ),
)


def available_converters() -> list[tuple[Converter, str]]:
    """Every converter installed on this machine, with its path."""
    found = []
    for converter in CONVERTERS:
        binary = converter.find()
        if binary:
            found.append((converter, binary))
    return found


def converter_status() -> dict[str, str]:
    """What the operator sees on the system page: installed, or where to get it."""
    return {
        converter.name: (converter.find() or f"not installed — {converter.source}")
        for converter in CONVERTERS
    }


def is_dwg(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.casefold() == ".dwg"


def convert_dwg(
    source: str | os.PathLike[str], *, out_dir: str | os.PathLike[str] | None = None
) -> Path:
    """Convert a DWG to DXF and return the path to the DXF.

    ``out_dir`` defaults to a temporary directory that the caller owns and is
    expected to clean up; :func:`read_dwg` does that for you.
    """
    path = Path(source)
    if not path.is_file():
        raise DxfReadError(f"DWG not found: {path}")

    installed = available_converters()
    if not installed:
        raise DxfReadError(
            "This is a DWG, and DWG needs a converter that is not installed on "
            "this computer. Install one of: "
            + "; ".join(f"{c.name} — {c.source}" for c in CONVERTERS)
            + f". Alternatively set {CONVERTER_ENV} to its path, or open the "
            "drawing in CAD and save it as DXF."
        )

    destination = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="profileos-dwg-"))
    destination.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    for converter, binary in installed:
        # ODA converts whole folders, so the file is staged alone in one.
        with tempfile.TemporaryDirectory(prefix="profileos-dwg-in-") as staging:
            staged = Path(staging) / path.name
            shutil.copy2(path, staged)
            command = converter.command(binary, staged, destination)
            environment = {**os.environ, **converter.environment}
            _log.info("Converting %s with %s", path.name, converter.name)
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=CONVERSION_TIMEOUT,
                    env=environment,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                problems.append(f"{converter.name}: {exc}")
                continue

            produced = converter.output_for(staged, destination)
            if produced.is_file() and produced.stat().st_size > 0:
                _log.info("Converted %s -> %s", path.name, produced.name)
                return produced
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            problems.append(
                f"{converter.name} exited {completed.returncode}"
                + (f": {detail[-1]}" if detail else " and produced no DXF")
            )

    raise DxfReadError(
        "The DWG could not be converted. " + "; ".join(problems)
    )


def read_dwg(source: str | os.PathLike[str], **kwargs):
    """Convert a DWG and run the normal section pipeline on the result."""
    from . import load_section

    with tempfile.TemporaryDirectory(prefix="profileos-dwg-out-") as out_dir:
        dxf = convert_dwg(source, out_dir=out_dir)
        section = load_section(str(dxf), **kwargs)
        section.source = str(Path(source))
        return section


__all__ = [
    "CONVERTERS",
    "CONVERTER_ENV",
    "CONVERSION_TIMEOUT",
    "Converter",
    "Dwg2Dxf",
    "OdaFileConverter",
    "available_converters",
    "convert_dwg",
    "converter_status",
    "is_dwg",
    "read_dwg",
]
