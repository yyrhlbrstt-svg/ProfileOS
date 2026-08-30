"""Command-line interface.

Every engine is reachable from one binary, so the suite is usable headless —
in a build pipeline, over SSH on a shop-floor server, or from a scheduler —
without the desktop application.

Run ``profileos --help`` for the command list, or ``profileos demo`` to see the
whole chain (DXF -> section analysis -> elements -> nesting -> CNC -> quote)
run end to end on the bundled samples.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .core.config import get_settings, load_settings, save_settings
from .core.errors import ProfileOSError
from .core.logging_setup import configure_logging
from .core.profiling import REGISTRY as PROFILER

app = typer.Typer(
    name="profileos",
    help="Integrated CAD/CAM, structural, nesting and CNC suite for aluminium profiles.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Sub-command groups.
geometry_app = typer.Typer(help="DXF import and section analysis.")
nest_app = typer.Typer(help="1D cutting-stock optimisation.")
cnc_app = typer.Typer(help="Machine code generation.")
element_app = typer.Typer(help="Windows, doors and curtain walls.")
pipe_app = typer.Typer(help="Pipework sizing and network analysis.")
glass_app = typer.Typer(help="Glass and panel sheet nesting.")
catalogue_app = typer.Typer(help="Build an owned profile library from supplier catalogues.")
erp_app = typer.Typer(help="Stock, purchasing, sales, ledger and capacity planning.")
access_app = typer.Typer(help="Who may open this installation at all.")
view_app = typer.Typer(help="3D presentation and technical views.")
plugin_app = typer.Typer(help="Plugin registries and hot reload.")
update_app = typer.Typer(help="Signed content updates.")
licence_app = typer.Typer(help="Hardware authentication and licensing.")
schema_app = typer.Typer(help="JSON Schemas for every document the suite reads.")
systems_app = typer.Typer(help="Which profile systems exist, and how far each is trusted.")
draw_app = typer.Typer(help="Shop drawings: elevations, wall sections and sheets.")
mobile_app = typer.Typer(help="Phones and tablets paired to this machine.")
quote_app = typer.Typer(help="Quotations: price, negotiate, issue.")
jobs_app = typer.Typer(help="Job files: the shop's own record of the work it has taken on.")
library_app = typer.Typer(help="The type library: every opening the shop can make.")
fit_app = typer.Typer(help="Shutters, screens, sills — what is fitted to an opening.")
spec_app = typer.Typer(help="Performance and the standards a window is judged against.")
service_app = typer.Typer(help="Service calls: what comes back, and why.")
money_app = typer.Typer(help="Cheques, collection and what a job actually earned.")
calendar_app = typer.Typer(help="The Israeli working year.")
deliver_app = typer.Typer(help="Loading and fitting: getting the work into the wall.")
finish_app = typer.Typer(help="Anodising and paint, on the area a bath reaches.")
files_app = typer.Typer(help="The photographs and papers kept with a job.")
hw_app = typer.Typer(help="Hardware chosen by what the sash weighs.")
import_app = typer.Typer(help="Bring across what the shop already has.")
backup_app = typer.Typer(help="A copy of the shop, in one file.")
time_app = typer.Typer(help="Hours actually worked, against the job.")
fx_app = typer.Typer(help="Buying in euros, selling in shekels.")
count_app = typer.Typer(help="Counting the racks, and posting the difference.")
report_app = typer.Typer(help="The numbers the owner asks for on a Sunday.")
audit_app = typer.Typer(help="Who changed what, in a chain that cannot lose a line.")
measure_app = typer.Typer(help="Measuring the hole in the wall, properly.")
template_app = typer.Typer(help="Making another one of those.")
task_app = typer.Typer(help="Chasing the quotation, and everything else owed.")

app.add_typer(geometry_app, name="section")
app.add_typer(nest_app, name="nest")
app.add_typer(cnc_app, name="cnc")
app.add_typer(element_app, name="element")
app.add_typer(pipe_app, name="pipe")
app.add_typer(glass_app, name="glass")
app.add_typer(catalogue_app, name="catalogue")
app.add_typer(erp_app, name="erp")
app.add_typer(access_app, name="access")
app.add_typer(view_app, name="view")
app.add_typer(plugin_app, name="plugin")
app.add_typer(update_app, name="update")
app.add_typer(licence_app, name="licence")
app.add_typer(schema_app, name="schema")
app.add_typer(systems_app, name="systems")
app.add_typer(draw_app, name="draw")
app.add_typer(mobile_app, name="mobile")
app.add_typer(quote_app, name="quote")
app.add_typer(jobs_app, name="jobs")
app.add_typer(library_app, name="library")
app.add_typer(fit_app, name="fit")
app.add_typer(spec_app, name="spec")
app.add_typer(service_app, name="service")
app.add_typer(money_app, name="money")
app.add_typer(calendar_app, name="calendar")
app.add_typer(deliver_app, name="deliver")
app.add_typer(finish_app, name="finish")
app.add_typer(files_app, name="files")
app.add_typer(hw_app, name="hardware")
app.add_typer(import_app, name="import")
app.add_typer(backup_app, name="backup")
app.add_typer(time_app, name="time")
app.add_typer(fx_app, name="fx")
app.add_typer(count_app, name="stocktake")
app.add_typer(report_app, name="report")
app.add_typer(audit_app, name="audit")
app.add_typer(measure_app, name="measure")
app.add_typer(template_app, name="template")
app.add_typer(task_app, name="task")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fail(message: str, detail: Any = None) -> None:
    """Print an error and exit non-zero."""
    console.print(f"[bold red]Error:[/] {message}")
    if detail:
        console.print(f"[dim]{detail}[/]")
    raise typer.Exit(code=1)


def _kv_table(title: str, rows: list[tuple[str, Any]], *, unit_column: bool = False) -> Table:
    table = Table(title=title, title_style="bold", header_style="dim", box=None, pad_edge=False)
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right", style="white")
    if unit_column:
        table.add_column("Unit", style="dim")
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    return table


def _banner() -> Panel:
    return Panel(
        Text.assemble(
            ("ProfileOS ", "bold cyan"),
            (f"v{__version__}", "dim"),
            ("\nCAD/CAM, structural analysis, nesting and CNC for aluminium.", "white"),
        ),
        border_style="cyan",
    )


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Errors only."),
    config_dir: Optional[Path] = typer.Option(None, "--config-dir", help="Configuration directory."),
) -> None:
    """Configure logging and settings before any command runs."""
    level = "DEBUG" if verbose else ("ERROR" if quiet else "INFO")
    configure_logging(level, use_rich=True, force=True)
    if config_dir is not None:
        load_settings(config_dir=config_dir)

    # The shop's own decisions about its systems — which family a series is,
    # and the supplier figures that make it cuttable — are restored before any
    # command runs. A cut sheet produced by the command line has to carry the
    # same provenance as one produced by the window.
    from .systems import load_confirmations, load_decisions

    load_decisions()
    load_confirmations()


@app.command()
def version() -> None:
    """Show the version and the optional components that are available."""
    console.print(_banner())

    from .cnc.drivers.base import available_drivers
    from .nesting.milp import ortools_available
    from .structural.torsion import sectionproperties_available
    from .mes.barcode import qr_available

    checks = [
        ("Exact torsion (sectionproperties)", sectionproperties_available()),
        ("MILP nesting (OR-Tools)", ortools_available()),
        ("QR labels (segno)", qr_available()),
        ("Desktop UI (PySide6)", _module_available("PySide6")),
        ("Service API (FastAPI)", _module_available("fastapi")),
    ]
    table = Table(box=None, header_style="dim")
    table.add_column("Component")
    table.add_column("Status")
    for name, ok in checks:
        table.add_row(name, "[green]available[/]" if ok else "[yellow]not installed[/]")
    console.print(table)

    from .geometry.dwg import converter_status

    dwg = Table(title="DWG converters", box=None, header_style="dim")
    dwg.add_column("Converter")
    dwg.add_column("Status")
    for name, state in converter_status().items():
        found = not state.startswith("not installed")
        dwg.add_row(name, f"[green]{state}[/]" if found else f"[yellow]{state}[/]")
    console.print(dwg)
    console.print(f"\n[dim]{len(available_drivers())} machine drivers registered.[/]")


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


# --------------------------------------------------------------------------- #
# Section analysis
# --------------------------------------------------------------------------- #

@geometry_app.command("analyse")
def section_analyse(
    dxf: Path = typer.Argument(..., help="DXF file holding the profile cross-section."),
    material: str = typer.Option("en-aw-6060-t66", "--material", "-m"),
    profile_id: Optional[str] = typer.Option(None, "--id"),
    no_torsion: bool = typer.Option(False, "--no-torsion", help="Skip the FEA torsion solve."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write results as JSON."),
) -> None:
    """Import a DXF cross-section and compute its full property set."""
    from .structural import analyse_dxf

    if not dxf.is_file():
        _fail(f"DXF not found: {dxf}")

    try:
        properties, section = analyse_dxf(
            str(dxf),
            profile_id=profile_id or dxf.stem,
            material=material,
            compute_torsion_constants=not no_torsion,
        )
    except ProfileOSError as exc:
        _fail(str(exc))

    console.print(
        Panel(
            f"[bold]{properties.profile_id}[/]  "
            f"{section.width:.1f} x {section.height:.1f} mm  "
            f"{section.topology.chamber_count} chamber(s), "
            f"{len(section.topology.regions)} region(s)",
            border_style="cyan",
        )
    )
    console.print(_kv_table("Section properties", properties.summary_rows(), unit_column=True))

    if properties.warnings:
        console.print("\n[yellow]Warnings[/]")
        for warning in properties.warnings:
            console.print(f"  [yellow]-[/] {warning}")
    for issue in section.validation.issues:
        style = {"error": "red", "warning": "yellow"}.get(issue.severity, "dim")
        console.print(f"  [{style}]{issue.severity}[/]: {issue.message}")

    if json_out:
        json_out.write_text(properties.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"\n[green]Wrote[/] {json_out}")


@geometry_app.command("info")
def section_info(dxf: Path = typer.Argument(..., help="DXF file to inspect.")) -> None:
    """Report what a DXF contains, without analysing it."""
    from .geometry import load_section

    if not dxf.is_file():
        _fail(f"DXF not found: {dxf}")
    try:
        section = load_section(str(dxf))
    except ProfileOSError as exc:
        _fail(str(exc))

    report = section.report
    console.print(
        _kv_table(
            f"{dxf.name}",
            [
                ("Area", f"{section.area:,.2f} mm2"),
                ("Envelope", f"{section.width:.1f} x {section.height:.1f} mm"),
                ("Regions", len(section.topology.regions)),
                ("Chambers", section.topology.chamber_count),
                ("Closed contours", report.closed_contours),
                ("Open chains", report.open_chains),
                ("Repaired gaps", report.repaired_gaps),
                ("Unit scale", f"{report.scale_to_mm} mm/unit"),
            ],
        )
    )
    if report.entity_counts:
        table = Table(title="Entities read", box=None, header_style="dim")
        table.add_column("Type", style="cyan")
        table.add_column("Count", justify="right")
        for name, count in sorted(report.entity_counts.items()):
            table.add_row(name, str(count))
        console.print(table)


@geometry_app.command("features")
def section_features(
    dxf: Path = typer.Argument(..., help="DXF file to read features from."),
    material: Optional[str] = typer.Option(None, "--material", "-m", help="Alloy id."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write the report as JSON."),
) -> None:
    """Read the grooves, rebates and channels straight off a section.

    Everything printed is measured from the drawing: the mouth and depth of
    every pocket, the glass the rebate takes, the polyamide strip width, the
    linear mass and the coated area per metre.
    """
    from .geometry import load_section
    from .geometry.features import features_for_section

    if not dxf.is_file():
        _fail(f"DXF not found: {dxf}")
    try:
        section = load_section(str(dxf))
        report = features_for_section(section, material=material)
    except ProfileOSError as exc:
        _fail(str(exc))

    console.print(
        _kv_table(
            dxf.name,
            [
                ("Envelope", f"{report.width:.1f} x {report.height:.1f} mm"),
                ("Mass", f"{report.mass_per_metre:.3f} kg/m"),
                ("Paint area", f"{report.paint_area_per_metre:.4f} m2/m"),
                (
                    "Glass capacity",
                    f"{report.glass_capacity:.1f} mm" if report.glass_capacity else "-",
                ),
                ("Euro groove", "yes" if report.takes_euro_hardware else "no"),
                (
                    "Thermal break",
                    f"{report.thermal_break_width:.1f} mm"
                    if report.thermal_break_width
                    else "none found",
                ),
                ("Screw ports", len(report.screw_ports)),
            ],
        )
    )

    if report.features:
        table = Table(title="Features", box=None, header_style="dim")
        table.add_column("Feature", style="cyan")
        table.add_column("Mouth", justify="right")
        table.add_column("Depth", justify="right")
        table.add_column("Undercut", justify="right")
        table.add_column("At", justify="right")
        table.add_column("Note", style="dim")
        for feature in report.features:
            pocket = feature.pocket
            table.add_row(
                f"{feature.kind.value}  {feature.kind.hebrew}",
                f"{pocket.mouth:.2f}",
                f"{pocket.depth:.2f}",
                f"{pocket.undercut:.2f}" if pocket.undercut > 0.4 else "-",
                f"{pocket.centre[0]:.1f}, {pocket.centre[1]:.1f}",
                feature.note,
            )
        console.print(table)
    else:
        console.print("[yellow]No features found on this section.[/yellow]")

    for strip in report.strips:
        console.print(
            f"[green]Polyamide strip {strip.width:.1f} mm[/green] "
            f"({', '.join(strip.evidence)})"
        )
    for warning in report.warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    if json_out:
        payload = {
            "source": str(dxf),
            "envelope_mm": [report.width, report.height],
            "bounds_mm": list(report.bounds),
            "mass_per_metre_kg": report.mass_per_metre,
            "paint_area_per_metre_m2": report.paint_area_per_metre,
            "glass_capacity_mm": report.glass_capacity,
            "thermal_break_width_mm": report.thermal_break_width,
            "screw_ports": [
                {"centre": list(centre), "diameter_mm": diameter}
                for centre, diameter in report.screw_ports
            ],
            "polyamide_strips": [
                {
                    "width_mm": strip.width,
                    "area_mm2": strip.area,
                    "centre": list(strip.centre),
                    "evidence": list(strip.evidence),
                }
                for strip in report.strips
            ],
            "features": [
                {
                    "kind": feature.kind.value,
                    "kind_he": feature.kind.hebrew,
                    "mouth_mm": feature.pocket.mouth,
                    "depth_mm": feature.pocket.depth,
                    "undercut_mm": feature.pocket.undercut,
                    "centre": list(feature.pocket.centre),
                    "direction": list(feature.pocket.direction),
                    "glass_capacity_mm": feature.glass_capacity,
                    "bite_mm": feature.bite,
                    "strip_width_mm": feature.strip_width,
                    "steps": [
                        {
                            "span_mm": step.span,
                            "depth_mm": step.height,
                            "openings": step.parts,
                        }
                        for step in feature.pocket.steps
                    ],
                }
                for feature in report.features
            ],
            "warnings": report.warnings,
        }
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Wrote[/green] {json_out}")


# --------------------------------------------------------------------------- #
# Nesting
# --------------------------------------------------------------------------- #

@nest_app.command("run")
def nest_run(
    project: Path = typer.Argument(..., help="Project JSON with the cut list."),
    strategy: str = typer.Option("auto", "--strategy", "-s", help="auto | milp | ffd | bfd"),
    kerf: Optional[float] = typer.Option(None, "--kerf", help="Blade kerf [mm]."),
    stock: Optional[str] = typer.Option(None, "--stock", help="Comma-separated bar lengths [mm]."),
    inventory: Optional[Path] = typer.Option(None, "--inventory", help="Remnant inventory JSON."),
    json_out: Optional[Path] = typer.Option(None, "--json"),
) -> None:
    """Optimise a cutting list onto stock bars."""
    from .models.orders import Project
    from .nesting import RemnantInventory, nest_project

    if not project.is_file():
        _fail(f"Project not found: {project}")
    try:
        parsed = Project.model_validate_json(project.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - user-supplied file
        _fail("Could not parse the project file", exc)

    store = RemnantInventory(inventory) if inventory else None
    if stock:
        # Applied through the defaults, which build_problem() reads for any
        # profile the project does not list explicit stock for.
        get_settings().nesting.stock_lengths_mm = [float(v) for v in stock.split(",")]
    if kerf is not None:
        get_settings().nesting.kerf_mm = kerf

    try:
        report = nest_project(parsed, inventory=store, strategy=strategy)  # type: ignore[arg-type]
    except ProfileOSError as exc:
        _fail(str(exc))

    table = Table(title=f"Nesting - {parsed.name}", header_style="dim")
    table.add_column("Profile", style="cyan")
    table.add_column("Bars", justify="right")
    table.add_column("Pieces", justify="right")
    table.add_column("Yield", justify="right")
    table.add_column("Waste", justify="right")
    table.add_column("Strategy", style="dim")
    for profile_id, result in sorted(report.results.items()):
        yield_style = "green" if result.yield_pct >= 95 else "yellow" if result.yield_pct >= 90 else "red"
        table.add_row(
            profile_id,
            str(result.bar_count),
            str(result.total_pieces),
            f"[{yield_style}]{result.yield_pct:.2f}%[/]",
            f"{result.waste_pct:.2f}%",
            result.strategy,
        )
    console.print(table)

    overall = report.overall_yield_pct
    style = "green" if overall >= 97.5 else "yellow"
    console.print(
        f"\n[bold]Total:[/] {report.total_bars} bars, "
        f"[{style}]{overall:.2f}% yield[/], "
        f"{report.total_stock_length / 1000:.1f} m consumed "
        f"in {report.solve_time_s:.2f} s"
    )

    for profile_id, result in report.results.items():
        for warning in result.warnings:
            console.print(f"  [yellow]{profile_id}:[/] {warning}")
    for profile_id, reason in report.failures.items():
        console.print(f"  [red]{profile_id}:[/] {reason}")

    if store is not None and inventory is not None:
        store.extend(report.all_remnants())
        store.save(inventory)
        console.print(f"[green]Updated[/] {inventory} ({len(store)} remnants)")

    if json_out:
        payload = {
            "summary": report.summary(),
            "profiles": {p: r.summary() for p, r in report.results.items()},
        }
        json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[green]Wrote[/] {json_out}")


@nest_app.command("inventory")
def nest_inventory(
    path: Path = typer.Argument(..., help="Remnant inventory JSON."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
) -> None:
    """Show the remnant stock on hand."""
    from .nesting import RemnantInventory

    store = RemnantInventory(path)
    profiles = [profile] if profile else store.profiles()
    if not profiles:
        console.print("[dim]Inventory is empty.[/]")
        return

    table = Table(title=f"Remnants - {path.name}", header_style="dim")
    table.add_column("Profile", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Longest", justify="right")
    table.add_column("Shortest", justify="right")
    for profile_id in profiles:
        stats = store.stats(profile_id)
        table.add_row(
            profile_id,
            str(stats.count),
            f"{stats.total_length / 1000:.2f} m",
            f"{stats.longest:.0f} mm",
            f"{stats.shortest:.0f} mm",
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# CNC
# --------------------------------------------------------------------------- #

@cnc_app.command("drivers")
def cnc_drivers() -> None:
    """List the machine drivers available for posting."""
    from .cnc import available_drivers

    table = Table(title="Machine drivers", header_style="dim")
    table.add_column("Key", style="cyan")
    table.add_column("Machine")
    table.add_column("Vendor", style="dim")
    table.add_column("Ext", style="dim")
    table.add_column("Version", justify="right", style="dim")
    for driver in available_drivers():
        table.add_row(
            driver["key"],
            driver.get("display_name", ""),
            driver.get("vendor", ""),
            driver.get("extension", ""),
            driver.get("version", ""),
        )
    console.print(table)


@cnc_app.command("post")
def cnc_post(
    job: Path = typer.Argument(..., help="Machining job JSON."),
    driver: Optional[str] = typer.Option(None, "--driver", "-d", help="Override the machine's driver."),
    output: Path = typer.Option(Path("output"), "--output", "-o"),
    no_clamps: bool = typer.Option(False, "--no-clamps", help="Skip clamp planning."),
) -> None:
    """Post a machining job to native machine code."""
    _fail(
        "Job files are produced by the desktop application or the API. "
        "Use 'profileos demo' to see posting run end to end."
    )


# --------------------------------------------------------------------------- #
# Elements
# --------------------------------------------------------------------------- #

@element_app.command("build")
def element_build(
    width: float = typer.Argument(..., help="Outer frame width [mm]."),
    height: float = typer.Argument(..., help="Outer frame height [mm]."),
    name: str = typer.Option("Element", "--name", "-n"),
    system: str = typer.Option("generic", "--system"),
    quantity: int = typer.Option(1, "--quantity", "-q"),
    mullions: Optional[str] = typer.Option(None, "--mullions", help="Comma-separated positions [mm]."),
    transoms: Optional[str] = typer.Option(None, "--transoms", help="Comma-separated positions [mm]."),
    sash: Optional[str] = typer.Option(None, "--sash", help="Cell to make operable, e.g. '1,0'."),
    sash_type: str = typer.Option("tilt_turn", "--sash-type"),
    sill: float = typer.Option(0.0, "--sill", help="Sill height above floor [mm]."),
) -> None:
    """Build one element and show its cut list, glass and hardware."""
    from .elements import Cell, ElementBuilder, Opening, OpeningType, Sash

    def _positions(raw: Optional[str]) -> list[float]:
        return [float(v) for v in raw.split(",")] if raw else []

    try:
        opening = Opening(
            name=name, width=width, height=height, quantity=quantity, system_id=system,
            mullion_positions=_positions(mullions), transom_positions=_positions(transoms),
        )
        if sash:
            column, row = (int(v) for v in sash.split(","))
            opening.set_cell(
                Cell(column=column, row=row, sash=Sash(opening_type=OpeningType(sash_type)))
            )
        # Building through the directory carries the system's provenance with
        # it, so the sheet can say whether it may be worked to.
        from .systems import DIRECTORY

        if DIRECTORY.get(system) is not None:
            builder = ElementBuilder.for_system(system)
        else:
            builder = ElementBuilder()
        build = builder.build(opening, sill_height=sill)
    except (ProfileOSError, ValueError) as exc:
        _fail(str(exc))

    console.print(Panel(opening.describe(), border_style="cyan"))
    if build.production_banner:
        console.print(Panel(build.production_banner, border_style="red"))

    cuts = Table(title="Cut list", header_style="dim")
    for column, justify in [("Role", "left"), ("Profile", "left"), ("Length", "right"),
                            ("Qty", "right"), ("Angles", "right")]:
        cuts.add_column(column, justify=justify)
    for cut in sorted(build.cuts, key=lambda c: (c.role, -c.length)):
        cuts.add_row(
            cut.role, cut.profile_id, f"{cut.length:.1f}", str(cut.quantity),
            f"{cut.angle_left:g}/{cut.angle_right:g}",
        )
    console.print(cuts)

    glass = Table(title="Glass", header_style="dim")
    for column in ("Mark", "Size", "Specification", "Area", "Mass", "U", "Safety"):
        glass.add_column(column)
    for panel in build.glass:
        safety = (
            "[green]ok[/]" if panel.compliant
            else "[red]required[/]" if panel.safety_required
            else "-"
        )
        glass.add_row(
            panel.mark or "", f"{panel.width:.0f} x {panel.height:.0f}",
            panel.build_up.describe(), f"{panel.area:.3f} m2",
            f"{panel.mass:.1f} kg", f"{panel.build_up.u_value():.2f}", safety,
        )
    console.print(glass)

    if build.hardware:
        hardware = Table(title="Hardware", header_style="dim")
        hardware.add_column("Code", style="cyan")
        hardware.add_column("Item")
        hardware.add_column("Qty", justify="right")
        for item in build.hardware:
            hardware.add_row(item.code, item.name, f"{item.quantity} {item.unit}")
        console.print(hardware)

    console.print(_kv_table("Summary", list(build.summary().items())))

    from .elements.feasibility import Severity, check_element

    report = check_element(build, sill_height=sill)
    if report.findings:
        table = Table(title="Feasibility", header_style="dim")
        table.add_column("", justify="left")
        table.add_column("Where", style="cyan")
        table.add_column("What")
        table.add_column("Measured", justify="right")
        table.add_column("Limit", justify="right")
        tone = {Severity.BLOCKER: "red", Severity.WARNING: "yellow", Severity.NOTE: "dim"}
        for finding in report.sorted():
            table.add_row(
                f"[{tone[finding.severity]}]{finding.severity.name}[/]",
                finding.subject,
                finding.english,
                "" if finding.measured is None else f"{finding.measured:.1f}",
                ""
                if finding.limit is None
                else f"{finding.limit.value:.1f} {finding.limit.unit}",
            )
        console.print(table)

    if report.can_be_made:
        console.print("[green]ניתן לייצור — buildable as drawn.[/green]")
    else:
        console.print(
            f"[red]לא ניתן לייצור — cannot be made as drawn: "
            f"{report.blockers[0].english}[/red]"
        )
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# Pipes
# --------------------------------------------------------------------------- #

@pipe_app.command("size")
def pipe_size(
    flow: float = typer.Argument(..., help="Design flow [L/s]."),
    length: float = typer.Argument(..., help="Run length [m]."),
    catalogue: str = typer.Option("copper-en1057", "--catalogue", "-c"),
    service: str = typer.Option("cold_water", "--service", "-s"),
    lift: float = typer.Option(0.0, "--lift", help="Static lift [m]."),
    available: Optional[float] = typer.Option(None, "--available", help="Available pressure [kPa]."),
    elbows: int = typer.Option(0, "--elbows"),
    valves: int = typer.Option(0, "--valves"),
) -> None:
    """Size a pipe run against velocity, loss and pressure limits."""
    from .plumbing import ServiceType, get_catalogue, size_pipe as _size

    try:
        selected = get_catalogue(catalogue)
        fittings: dict[str, int] = {}
        if elbows:
            fittings["elbow_90_long"] = elbows
        if valves:
            fittings["gate_valve_open"] = valves
        result = _size(
            flow, length, selected, service=ServiceType(service), fittings=fittings,
            height_gain_m=lift,
            available_pressure=available * 1000.0 if available is not None else None,
        )
    except (ProfileOSError, ValueError) as exc:
        _fail(str(exc))

    if not result.ok:
        console.print(f"[red]No suitable size in {selected.name}.[/]")
        for reason in result.reasons:
            console.print(f"  [dim]{reason}[/]")
        raise typer.Exit(code=1)

    console.print(
        _kv_table(
            f"{selected.name} - {flow} L/s over {length} m",
            [
                ("Selected", result.size.designation),
                ("Bore", f"{result.size.internal_diameter:.1f} mm"),
                ("Velocity", f"{result.velocity:.2f} m/s"),
                ("Reynolds", f"{result.reynolds:,.0f}"),
                ("Friction factor", f"{result.friction_factor:.5f}"),
                ("Friction loss", f"{result.friction_loss / 1000:.2f} kPa"),
                ("Fitting loss", f"{result.fitting_loss / 1000:.2f} kPa"),
                ("Static loss", f"{result.static_loss / 1000:.2f} kPa"),
                ("Total loss", f"{result.total_loss / 1000:.2f} kPa"),
                ("Loss per metre", f"{result.loss_per_metre:.0f} Pa/m"),
                ("Water content", f"{result.size.water_content():.3f} L/m"),
            ],
        )
    )
    for reason in result.reasons:
        console.print(f"  [yellow]-[/] {reason}")
    if result.rejected:
        console.print("\n[dim]Sizes rejected:[/]")
        for designation, why in result.rejected:
            console.print(f"  [dim]{designation}: {why}[/]")


@pipe_app.command("catalogues")
def pipe_catalogues() -> None:
    """List the available pipe catalogues."""
    from .plumbing import BUILTIN_CATALOGUES

    table = Table(title="Pipe catalogues", header_style="dim")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Material", style="dim")
    table.add_column("Sizes", justify="right")
    table.add_column("Roughness", justify="right", style="dim")
    for catalogue in BUILTIN_CATALOGUES.values():
        table.add_row(
            catalogue.id, catalogue.name, catalogue.material,
            str(len(catalogue.sizes)), f"{catalogue.effective_roughness:g} mm",
        )
    console.print(table)


def _load_schedule(path: Path) -> "Any":
    """Read an element schedule file, with a pointed message for the wrong kind.

    A cutting Project and an element schedule look alike from a shell prompt,
    and handing the saw's file to the drawing engine deserves a better answer
    than a validation traceback.
    """
    from .elements import ElementSchedule

    if not path.is_file():
        _fail(f"Schedule not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        return ElementSchedule.model_validate_json(text)
    except Exception as exc:  # noqa: BLE001 - inspected below
        if '"items"' in text and '"openings"' not in text:
            _fail(
                f"{path} looks like a cutting project (profile demand for the "
                "saw), not an element schedule. Drawings and quotations are "
                "produced from a schedule of openings."
            )
        _fail("Could not parse the schedule file", exc)


@quote_app.command("build")
def quote_build(
    project: Path = typer.Argument(..., help="Element schedule JSON."),
    output: Path = typer.Option(Path("quotation"), "--output", "-o", help="Output stem."),
    system: str = typer.Option("", "--system", help="Overrides the schedule's system."),
    glass: str = typer.Option("dgu-6-16-4", "--glass"),
    finish: str = typer.Option("ral", "--finish"),
    margin: float = typer.Option(25.0, "--margin", help="Gross margin, % of selling price."),
    thermal_option: bool = typer.Option(
        True, "--thermal-option/--no-thermal-option",
        help="Add a thermal alternative alongside the base specification.",
    ),
    language: str = typer.Option("he", "--lang", help="he | en | ar | ru | it | es."),
    terms: str = typer.Option("", "--terms", help="Payment terms printed on the document."),
) -> None:
    """Price a project and write both quotation documents.

    Two files come out: the customer copy, which never carries cost or margin,
    and the internal sheet, which carries all of it. Prices with no supplier
    list behind them use declared estimating rates and the document says so.
    """
    from .quoting.document import render_quotation
    from .quoting.editor import QuoteDraft, QuoteEditError

    parsed = _load_schedule(project)
    openings = parsed.openings
    if not openings:
        _fail("The schedule has no elements to quote.")

    try:
        draft = QuoteDraft.start(
            openings,
            project_name=parsed.name,
            customer=parsed.client,
            system_id=system or parsed.system_id,
            glass_id=glass,
            finish_id=finish,
            fallback_rates={"profile": 42.0, "glass_m2": 260.0, "hardware": 60.0,
                            "gasket_m": 4.0},
        )
        draft.set_margin(margin)
        if thermal_option:
            draft.add_variant("Thermal", glass_id="dgu-6-16-6", finish_id="anodized")
    except QuoteEditError as exc:
        _fail(str(exc))
    if terms:
        draft.display.payment_terms = terms

    customer_path = output.with_suffix(".customer.html")
    internal_path = output.with_suffix(".internal.html")
    customer_path.parent.mkdir(parents=True, exist_ok=True)
    customer_path.write_text(render_quotation(draft, language=language), encoding="utf-8")
    internal_path.write_text(
        render_quotation(draft, language=language, internal=True), encoding="utf-8"
    )

    totals = draft.totals()
    variant = draft.variant()
    console.print(
        _kv_table(
            f"Quotation {draft.quotation.quote_id}",
            [
                ("Elements", len(openings)),
                ("Net", f"{totals['net']:,.2f} {variant.policy.currency}"),
                (f"VAT ({variant.policy.tax_pct:g}%)", f"{totals['vat']:,.2f}"),
                ("Total due", f"{totals['gross']:,.2f}"),
                ("Aluminium", f"{variant.aluminium_kg:,.1f} kg, {variant.finish.name}"),
                ("Options", ", ".join(v.name for v in draft.variants)),
            ],
        )
    )
    for warning in variant.warnings:
        console.print(f"  [yellow]-[/] {warning}")
    console.print(f"[green]Wrote[/green] {customer_path} and {internal_path}")


def _lan_addresses() -> list[str]:
    """The addresses a phone on the shop's own network can actually reach.

    ``localhost`` is useless to a phone, and the hostname often is too, so the
    address is worked out rather than guessed — by asking the routing table
    which interface would be used to reach the outside, without sending
    anything.
    """
    import socket

    found: list[str] = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # a documentation address; nothing is sent
        address = probe.getsockname()[0]
        # A container that routes the probe address to itself hands back an
        # address from the documentation block, which no phone can reach.
        if not address.startswith(("127.", "192.0.2.")):
            found.append(address)
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith(("127.", "192.0.2.")) and address not in found:
                found.append(address)
    except OSError:
        pass
    return found


@mobile_app.command("pair")
def mobile_pair(
    name: str = typer.Argument(..., help="Whose device this is, e.g. 'הטלפון של דאדי'."),
    scopes: str = typer.Option(
        "jobs,measure,drawings", "--scopes",
        help="What the device may do: jobs | measure | drawings.",
    ),
    port: int = typer.Option(8000, "--port", help="Port the service is served on."),
) -> None:
    """Issue a one-time pairing code for a phone or tablet.

    Run this at the computer that already has the USB key in it. The code lasts
    five minutes and works once, so a phone can only be paired by somebody
    standing at an unlocked machine.
    """
    from .mes.barcode import qr_available, qr_svg
    from .mobile import DeviceRegistry, PairingError, default_registry_path

    registry = DeviceRegistry.load(default_registry_path())
    try:
        code = registry.issue_code(name, scopes=[s.strip() for s in scopes.split(",") if s.strip()])
    except PairingError as exc:
        _fail(str(exc))

    addresses = _lan_addresses()
    url = f"http://{addresses[0]}:{port}/m" if addresses else f"http://<this-computer>:{port}/m"

    console.print(
        Panel(
            f"[bold]{code.code}[/bold]",
            title=f"קוד חיבור עבור {name}",
            subtitle=f"תקף {(code.seconds_left + 59) // 60} דקות · חד-פעמי",
            border_style="cyan",
        )
    )
    console.print(f"On the phone, open: [bold]{url}[/bold]")
    for extra in addresses[1:]:
        console.print(f"  [dim]or http://{extra}:{port}/m[/dim]")
    console.print(
        "[dim]The phone must be on the same network as this computer, or "
        "reach it through your own VPN. Nothing is published to the internet.[/dim]"
    )
    if qr_available():
        console.print("[dim]QR for the address is written to mobile-pair.svg[/dim]")
        Path("mobile-pair.svg").write_text(qr_svg(url), encoding="utf-8")


@mobile_app.command("devices")
def mobile_devices(
    all_devices: bool = typer.Option(False, "--all", help="Include revoked and expired."),
) -> None:
    """List the phones and tablets paired to this machine."""
    from .mobile import DeviceRegistry, default_registry_path

    registry = DeviceRegistry.load(default_registry_path())
    devices = list(registry.devices.values()) if all_devices else registry.active_devices()
    if not devices:
        console.print("[dim]No devices are paired.[/dim]")
        return

    table = Table(title="Paired devices", header_style="dim")
    table.add_column("Id", style="cyan")
    table.add_column("Name")
    table.add_column("Allowed")
    table.add_column("Paired")
    table.add_column("Last seen")
    table.add_column("State")
    for device in sorted(devices, key=lambda d: d.last_seen, reverse=True):
        state = (
            "[red]revoked[/]" if device.revoked
            else "[yellow]expired[/]" if device.expired
            else "[green]active[/]"
        )
        table.add_row(
            device.device_id,
            device.name,
            ", ".join(device.scopes),
            device.paired_at.strftime("%Y-%m-%d"),
            device.last_seen.strftime("%Y-%m-%d %H:%M"),
            state,
        )
    console.print(table)


@mobile_app.command("revoke")
def mobile_revoke(
    device_id: Optional[str] = typer.Argument(None, help="Device id, or omit with --all."),
    revoke_all: bool = typer.Option(False, "--all", help="Revoke every device."),
) -> None:
    """Cut a lost phone off. Takes effect on its next request."""
    from .mobile import DeviceRegistry, PairingError, default_registry_path

    registry = DeviceRegistry.load(default_registry_path())
    if revoke_all:
        count = registry.revoke_all()
        console.print(f"[green]Revoked[/green] {count} device(s).")
        return
    if not device_id:
        _fail("Name a device id, or pass --all.")
    try:
        device = registry.revoke(device_id)
    except PairingError as exc:
        _fail(str(exc))
    console.print(f"[green]Revoked[/green] {device.name} ({device.device_id}).")


@mobile_app.command("measurements")
def mobile_measurements(
    reference: Optional[str] = typer.Option(None, "--ref", help="One opening's history."),
) -> None:
    """Show what has been measured on site."""
    from .mobile import MeasurementStore, default_store_path

    store = MeasurementStore.load(default_store_path())
    records = store.history(reference) if reference else store.records
    if not records:
        console.print("[dim]No measurements have been taken.[/dim]")
        return

    table = Table(title="Site measurements", header_style="dim")
    table.add_column("Opening", style="cyan")
    table.add_column("Width", justify="right")
    table.add_column("Height", justify="right")
    table.add_column("Out of square", justify="right")
    table.add_column("By")
    table.add_column("When", style="dim")
    for record in records[:60]:
        problems = record.problems()
        out = max(record.width_range, record.height_range, record.diagonal_difference or 0.0)
        table.add_row(
            record.reference,
            f"{record.width:.0f}",
            f"{record.height:.0f}",
            f"[yellow]{out:.0f}[/]" if problems else f"{out:.0f}",
            record.measured_by or "—",
            record.measured_at.strftime("%d/%m %H:%M"),
        )
    console.print(table)

    changed = store.changed()
    for opening, width_change, height_change in changed:
        console.print(
            f"  [yellow]{opening}[/yellow] was re-measured: "
            f"{width_change:+.0f} × {height_change:+.0f} mm against the previous figure"
        )


@draw_app.command("package")
def draw_package(
    project: Path = typer.Argument(..., help="Element schedule JSON."),
    output: Path = typer.Option(Path("drawings"), "--output", "-o", help="Where to write."),
    system: str = typer.Option("", "--system", help="Overrides the schedule's system."),
    size: str = typer.Option("A3", "--size", help="A0 | A1 | A2 | A3 | A4."),
    elevation_scale: int = typer.Option(20, "--elevation-scale"),
    detail_scale: int = typer.Option(5, "--detail-scale"),
    profile_dxf: Optional[Path] = typer.Option(
        None, "--profile", help="DXF of the frame section, for real wall details."
    ),
    formats: str = typer.Option("pdf,dxf,svg", "--formats"),
    language: str = typer.Option("he", "--lang", help="he | en | ar | ru | it | es."),
) -> None:
    """Produce the whole shop drawing package for a project.

    Elevations at the stated scale, wall sections at theirs, one title block
    filled the same way on every sheet, and a not-for-construction stamp on all
    of them when the systems behind the drawing are not confirmed.
    """
    from datetime import date

    from .branding import active_brand
    from .drawing import PackageInfo, Revision, SheetSize, build_package
    from .elements import ElementBuilder
    from .systems import DIRECTORY

    parsed = _load_schedule(project)
    openings = parsed.openings
    if not openings:
        _fail("The schedule has no elements to draw.")

    chosen = system or parsed.system_id
    builder = (
        ElementBuilder.for_system(chosen)
        if DIRECTORY.get(chosen) is not None
        else ElementBuilder()
    )
    builds = [builder.build(opening, sill_height=parsed.sill_height) for opening in openings]

    profile = None
    if profile_dxf:
        from .geometry import profile_from_dxf

        if not profile_dxf.is_file():
            _fail(f"Profile DXF not found: {profile_dxf}")
        profile, _ = profile_from_dxf(str(profile_dxf), profile_dxf.stem, system)

    brand = active_brand()
    info = PackageInfo(
        project=parsed.name,
        client=parsed.client,
        company=getattr(brand, "name", "") or "",
        company_line=getattr(brand, "tagline", "") or "",
        number_prefix=f"{parsed.name[:3].upper()}-A",
        drawn_by=getattr(brand, "name", "") or "",
        size=SheetSize(size.upper()),
        language=language,
        revisions=[Revision("A", date.today(), "Issued for approval", "")],
    )
    package = build_package(
        builds, info,
        elevation_scale=elevation_scale, detail_scale=detail_scale, profile=profile,
    )
    written = package.write(output, formats=tuple(f.strip() for f in formats.split(",")))

    console.print(
        _kv_table(
            f"Drawing package - {parsed.name}",
            [
                ("Sheets", len(package.sheets)),
                ("Numbers", ", ".join(package.numbers())),
                ("Elevation scale", f"1:{elevation_scale}"),
                ("Detail scale", f"1:{detail_scale}"),
                ("Files", len(written)),
            ],
        )
    )
    for stamp in package.stamps:
        console.print(f"[yellow]{stamp}[/yellow]")
    console.print(f"[green]Wrote[/green] {output}")


@draw_app.command("detail")
def draw_detail(
    detail: str = typer.Argument(..., help="head | sill | jamb | mullion | transom."),
    output: Path = typer.Option(Path("detail.pdf"), "--output", "-o"),
    wall: str = typer.Option("stone", "--wall", help="stone | block."),
    scale: int = typer.Option(5, "--scale"),
    profile_dxf: Optional[Path] = typer.Option(None, "--profile"),
    language: str = typer.Option("he", "--lang", help="he | en | ar | ru | it | es."),
) -> None:
    """Draw one wall section on its own sheet."""
    from .drawing import RENDERED_BLOCK, STONE_CLAD_CONCRETE, SectionStyle, wall_section
    from .drawing.section import Detail
    from .drawing.sheet import Sheet, SheetSize, TitleBlock, Viewport

    try:
        which = Detail(detail.lower())
    except ValueError:
        _fail(f"Unknown detail {detail!r}. Known: {', '.join(d.value for d in Detail)}")

    build_up = RENDERED_BLOCK if wall.lower().startswith("b") else STONE_CLAD_CONCRETE
    profile = None
    if profile_dxf:
        from .geometry import profile_from_dxf

        profile, _ = profile_from_dxf(str(profile_dxf), profile_dxf.stem, "detail")

    result = wall_section(
        which,
        build_up=build_up,
        style=SectionStyle(scale=scale, language=language),
        profile=profile,
    )
    sheet = Sheet(
        size=SheetSize.A3,
        title_block=TitleBlock(
            title=f"{which.label(language)} / {which.label('en').capitalize()}",
            number=which.value.upper(),
            scale=f"1:{scale}",
            language=language,
            notes=tuple(result.notes),
        ),
    )
    area = sheet.drawing_area()
    sheet.add(
        Viewport(
            drawing=result.drawing, scale=scale,
            frame=(area[0], area[1] + 10.0, area[2] - sheet.block_width - 10.0, area[3] - 20.0),
        )
    )
    suffix = output.suffix.lower()
    if suffix == ".dxf":
        sheet.to_dxf(output)
    elif suffix == ".svg":
        output.write_text(sheet.to_svg(), encoding="utf-8")
    else:
        sheet.to_pdf(output)
    for note in result.notes:
        console.print(f"[yellow]{note}[/yellow]")
    console.print(f"[green]Wrote[/green] {output}")


@app.command("languages")
def languages(
    key: Optional[str] = typer.Option(None, "--key", help="Show one term in every language."),
) -> None:
    """The languages the software speaks, and how complete each one is."""
    from .i18n import MESSAGES, available, catalogue, translate

    if key:
        if key not in MESSAGES:
            _fail(f"No term {key!r}.")
        table = Table(title=key, header_style="dim")
        table.add_column("Language", style="cyan")
        table.add_column("Term")
        for locale in available():
            table.add_row(f"{locale.native} ({locale.code})", translate(key, locale.language))
        console.print(table)
        return

    table = Table(title="Languages", header_style="dim")
    table.add_column("Code", style="cyan")
    table.add_column("Language")
    table.add_column("Direction")
    table.add_column("Terms", justify="right")
    table.add_column("Numbers", justify="right")
    for locale in available():
        translated = sum(
            1 for entries in MESSAGES.values() if entries.get(locale.code)
        )
        table.add_row(
            locale.code,
            f"{locale.native} — {locale.english}",
            "right to left" if locale.rtl else "left to right",
            f"{translated}/{len(MESSAGES)}",
            locale.format_number(1234.5, 2),
        )
    console.print(table)


@systems_app.command("list")
def systems_list(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Series, maker or Hebrew name."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", "-m"),
    family: Optional[str] = typer.Option(None, "--family", "-f"),
    cuttable: bool = typer.Option(False, "--cuttable", help="Only series that may be cut."),
) -> None:
    """The system directory, with what may be done with each series."""
    from .systems import DIRECTORY, SystemFamily

    entries = DIRECTORY.search(search) if search else list(DIRECTORY)
    if manufacturer:
        entries = [e for e in entries if e.manufacturer == manufacturer.lower()]
    if family:
        try:
            wanted = SystemFamily(family.lower())
        except ValueError:
            _fail(f"Unknown family {family!r}. Known: {', '.join(f.value for f in SystemFamily)}")
        entries = [e for e in entries if e.family is wanted]
    if cuttable:
        entries = [e for e in entries if DIRECTORY.readiness(e.id).may_cut]

    if not entries:
        console.print("[yellow]Nothing matched.[/yellow]")
        return

    table = Table(title="Profile systems", header_style="dim")
    table.add_column("Series", style="cyan")
    table.add_column("Maker")
    table.add_column("Type")
    table.add_column("Quote", justify="center")
    table.add_column("Cut", justify="center")
    table.add_column("Figures from", style="dim")
    for entry in sorted(entries, key=lambda e: (e.manufacturer, e.series)):
        readiness = DIRECTORY.readiness(entry.id)
        maker = DIRECTORY.manufacturer(entry.manufacturer)
        table.add_row(
            entry.display,
            maker.hebrew if maker else entry.manufacturer,
            entry.family.hebrew if entry.family else "[yellow]לא סווג[/]",
            "[green]yes[/]" if readiness.may_quote else "[red]no[/]",
            "[green]yes[/]" if readiness.may_cut else "[red]no[/]",
            entry.source or "—",
        )
    console.print(table)


@systems_app.command("show")
def systems_show(entry_id: str = typer.Argument(..., help="System id, e.g. 'klil-7300'.")) -> None:
    """Everything known about one series, including what is not known."""
    from .systems import DIRECTORY, UnclassifiedSystem

    entry = DIRECTORY.get(entry_id)
    if entry is None:
        matches = DIRECTORY.search(entry_id)
        hint = f" Did you mean: {', '.join(m.id for m in matches[:5])}?" if matches else ""
        _fail(f"No system {entry_id!r}.{hint}")

    readiness = DIRECTORY.readiness(entry.id)
    maker = DIRECTORY.manufacturer(entry.manufacturer)
    console.print(
        _kv_table(
            entry.display,
            [
                ("Maker", f"{maker.hebrew} ({maker.name})" if maker else entry.manufacturer),
                ("Type", entry.family.hebrew if entry.family else "not classified"),
                ("Thermally broken", "yes" if entry.thermally_broken else "not recorded"),
                ("Figures", DIRECTORY.provenance_for(entry.id).hebrew),
                ("Source", entry.source or "—"),
                ("May quote", "yes" if readiness.may_quote else "no"),
                ("May cut", "yes" if readiness.may_cut else "no"),
            ],
        )
    )
    for reason in readiness.reasons:
        console.print(f"  [yellow]{reason}[/yellow]")
    if readiness.banner:
        console.print(f"\n[red]{readiness.banner}[/red]")

    try:
        rules, provenance = DIRECTORY.rules_for(entry.id)
    except UnclassifiedSystem:
        return
    console.print(
        _kv_table(
            f"Deductions in use ({provenance.value})",
            [
                ("Frame face", f"{rules.frame.face_width:.1f} mm"),
                ("Mitred corners", "yes" if rules.frame.mitred_corners else "no"),
                ("Sash overlap", f"{rules.sash.frame_overlap:.1f} mm"),
                ("Rebate clearance", f"{rules.sash.rebate_clearance:.1f} mm"),
                ("Glass edge cover", f"{rules.glass.edge_cover:.1f} mm"),
                ("Glass clearance", f"{rules.glass.edge_clearance:.1f} mm"),
                ("Max glass", f"{rules.glass.max_glass_thickness:.1f} mm"),
                ("Mullion face", f"{rules.mullion.face_width:.1f} mm"),
            ],
        )
    )


@systems_app.command("classify")
def systems_classify(
    entry_id: str = typer.Argument(..., help="System id."),
    family: str = typer.Argument(..., help="casement | sliding | tilt_turn | ..."),
    source: str = typer.Option(..., "--source", help="Where the classification comes from."),
) -> None:
    """Say what kind of system a named series is. Does not make it cuttable."""
    from .systems import DIRECTORY, SystemFamily

    try:
        wanted = SystemFamily(family.lower())
    except ValueError:
        _fail(f"Unknown family {family!r}. Known: {', '.join(f.value for f in SystemFamily)}")
    try:
        entry = DIRECTORY.classify(entry_id, wanted, source=source)
    except KeyError as exc:
        _fail(str(exc))
    console.print(
        f"[green]{entry.display}[/green] is a {wanted.hebrew} system. "
        "It can now be quoted; loading the supplier's catalogue is what allows cutting."
    )


@systems_app.command("coverage")
def systems_coverage() -> None:
    """How much of the directory is actually usable."""
    from .systems import DIRECTORY

    counts = DIRECTORY.coverage()
    console.print(
        _kv_table(
            "System directory",
            [
                ("Series listed", counts["total"]),
                ("Ready to cut", counts["confirmed"]),
                ("Quotable on stand-ins", counts["typical"]),
                ("Not classified", counts["unclassified"]),
            ],
        )
    )
    if counts["confirmed"] == 0:
        console.print(
            "[yellow]No series has its supplier's own figures yet. Load a "
            "catalogue with `profileos catalogue ingest` to cut from one.[/yellow]"
        )


@schema_app.command("list")
def schema_list() -> None:
    """Show every schema that can be generated, and from which model."""
    from .schemas import document_models, known_schemas

    kinds = set(document_models())
    table = Table(title="Document schemas", header_style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Model")
    table.add_column("Loadable as a plugin", justify="center")
    for name, model in sorted(known_schemas().items()):
        table.add_row(
            name,
            f"{model.__module__}.{model.__name__}",
            f"kind: {name}" if name in kinds else "—",
        )
    console.print(table)
    console.print(
        "[dim]Generated from the running models, so they cannot describe a "
        "version of the software that is not this one.[/dim]"
    )


@schema_app.command("export")
def schema_export(
    output: Path = typer.Option(Path("schemas"), "--output", "-o", help="Directory."),
) -> None:
    """Write every schema to disk for editors and for suppliers."""
    from .schemas import export

    written = export(output)
    console.print(f"[green]Wrote[/green] {len(written)} files to {output}")
    for path in written:
        console.print(f"  [dim]{path.name}[/dim]")


@schema_app.command("show")
def schema_show(name: str = typer.Argument(..., help="Schema name, e.g. 'profile'.")) -> None:
    """Print one schema."""
    from .schemas import all_schemas

    schemas = all_schemas()
    if name not in schemas:
        _fail(f"No schema named {name!r}. Known: {', '.join(sorted(schemas))}")
    console.print_json(json.dumps(schemas[name], ensure_ascii=False))


@schema_app.command("check")
def schema_check(
    directory: Path = typer.Argument(..., help="Folder of JSON documents to validate."),
) -> None:
    """Validate a folder of documents before they go into the plugin directory.

    A typo is much cheaper to find on a desk than at the saw.
    """
    from .schemas import check_directory

    if not directory.is_dir():
        _fail(f"Not a directory: {directory}")
    results = check_directory(directory)
    if not results:
        console.print("[yellow]No documents with a 'kind' were found.[/yellow]")
        return

    bad = 0
    for path, problem in results:
        if problem is None:
            console.print(f"[green]ok[/green]   {path}")
        else:
            bad += 1
            console.print(f"[red]bad[/red]  {path}\n      [dim]{problem}[/dim]")
    console.print(f"\n{len(results) - bad} good, {bad} to fix.")
    if bad:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# Plugins
# --------------------------------------------------------------------------- #

@plugin_app.command("list")
def plugin_list() -> None:
    """Show every registry and what is loaded into it."""
    from .core.registry import registry_report

    for name, entries in registry_report().items():
        if not entries:
            continue
        table = Table(title=name, header_style="dim", box=None)
        table.add_column("Key", style="cyan")
        table.add_column("Version", justify="right", style="dim")
        table.add_column("Source", style="dim", overflow="fold")
        for entry in entries:
            table.add_row(entry["key"], entry.get("version", ""), str(entry.get("source") or ""))
        console.print(table)
        console.print()


@plugin_app.command("validate")
def plugin_validate(path: Path = typer.Argument(..., help="Plugin .py file to check.")) -> None:
    """Statically validate a Python plugin without executing it."""
    from .core.hotreload import validate_plugin_source

    if not path.is_file():
        _fail(f"Not found: {path}")
    report = validate_plugin_source(path)

    if report.ok:
        console.print(f"[green]OK[/] {path.name} passed static validation.")
    else:
        console.print(f"[red]REJECTED[/] {path.name}")
    for error in report.errors:
        console.print(f"  [red]-[/] {error}")
    for warning in report.warnings:
        console.print(f"  [yellow]-[/] {warning}")
    if report.entrypoints:
        console.print(f"  [dim]functions: {', '.join(report.entrypoints)}[/]")
    raise typer.Exit(code=0 if report.ok else 1)


@plugin_app.command("watch")
def plugin_watch(
    interval: float = typer.Option(2.0, "--interval", help="Poll interval [s]."),
) -> None:
    """Watch the plugin directories and hot-reload changes until interrupted."""
    from .core.hotreload import HotReloadManager

    settings = get_settings()
    settings.ensure_directories()
    settings.watch_interval_s = interval

    manager = HotReloadManager(settings=settings)
    loaded = manager.initial_load()
    console.print(f"[green]Loaded[/] {loaded} plugin(s). Watching for changes (Ctrl-C to stop).")
    for directory in settings.effective_plugin_dirs():
        console.print(f"  [dim]{directory}[/]")

    try:
        manager.start()
        while True:
            import time

            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")
    finally:
        manager.stop()


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Start the HTTP service API, including the phone terminal at /m."""
    try:
        import uvicorn
    except ImportError:
        _fail("The API needs FastAPI and uvicorn (pip install 'profileos[api]').")

    console.print(_banner())
    console.print(f"[green]Serving[/] on http://{host}:{port}  (docs at /docs)")
    uvicorn.run("profileos.api.server:app", host=host, port=port, reload=reload)


@app.command()
def ui() -> None:
    """Launch the desktop application."""
    try:
        from .ui.app import run
    except ImportError as exc:
        _fail("The desktop UI needs PySide6 (pip install 'profileos[ui]').", exc)
    raise typer.Exit(code=run())


@app.command()
def config(
    show: bool = typer.Option(True, "--show/--no-show"),
    init: bool = typer.Option(False, "--init", help="Write a default settings file."),
) -> None:
    """Show or initialise the configuration."""
    settings = get_settings()
    if init:
        settings.ensure_directories()
        path = save_settings(settings)
        console.print(f"[green]Wrote[/] {path}")
    if show:
        console.print(
            _kv_table(
                "Configuration",
                [
                    ("Config dir", settings.config_dir),
                    ("Data dir", settings.data_dir),
                    ("Log level", settings.log_level),
                    ("Hot reload", settings.enable_hot_reload),
                    ("Stock lengths", settings.nesting.stock_lengths_mm),
                    ("Kerf", f"{settings.nesting.kerf_mm} mm"),
                    ("Min remnant", f"{settings.nesting.min_reusable_remnant_mm} mm"),
                    ("Mesh size", f"{settings.analysis.mesh_size_mm2} mm2"),
                    ("Clamp clearance", f"{settings.cnc.clamp_clearance_mm} mm"),
                    ("Hardware key enforced", settings.security.enforce_hardware_key),
                ],
            )
        )


@app.command()
def demo(
    output: Path = typer.Option(Path("demo-output"), "--output", "-o"),
    profiling: bool = typer.Option(False, "--profile", help="Print timing report."),
) -> None:
    """Run the whole chain end to end on the bundled samples."""
    from .demo import run_demo

    console.print(_banner())
    try:
        run_demo(output, console=console)
    except ProfileOSError as exc:
        _fail(str(exc))

    if profiling:
        console.print("\n[bold]Performance[/]")
        console.print(PROFILER.report())


# --------------------------------------------------------------------------- #
# Updates
# --------------------------------------------------------------------------- #

def _update_engine(source_url: str, key_path: Path, channel: str):
    """Build an engine from a source that may be a URL or a directory."""
    from .core.hotreload import PluginLoader
    from .security.keys import VerifyKey
    from .updates import DirectorySource, HttpSource, UpdateChannel, UpdateEngine

    if not key_path.is_file():
        _fail(
            f"Issuer public key not found: {key_path}\n"
            "Updates are only accepted from a signed source, so the publisher's "
            "key is required. Generate a pair with 'profileos update keygen'."
        )
    verify_key = VerifyKey.from_pem(key_path.read_bytes())

    if source_url.startswith(("http://", "https://")):
        source = HttpSource(source_url, allow_insecure=source_url.startswith("http://"))
    else:
        source = DirectorySource(source_url)

    settings = get_settings()
    settings.ensure_directories()
    return UpdateEngine(
        source, verify_key, settings,
        channel=UpdateChannel(channel), loader=PluginLoader(settings, strict=False),
    )


@update_app.command("check")
def update_check(
    source: str = typer.Argument(..., help="Update URL or directory."),
    key: Path = typer.Option(..., "--key", "-k", help="Issuer public key (PEM)."),
    channel: str = typer.Option("stable", "--channel", "-c", help="stable | beta | canary"),
) -> None:
    """Check for updates without installing anything."""
    engine = _update_engine(source, key, channel)
    try:
        plan = engine.check()
    except ProfileOSError as exc:
        _fail(str(exc))

    console.print(Panel(plan.describe(), title="Available updates", border_style="cyan"))
    if plan.skipped:
        table = Table(title="Skipped", box=None, header_style="dim")
        table.add_column("Package", style="cyan")
        table.add_column("Reason", style="dim")
        for package_id, reason in plan.skipped:
            table.add_row(package_id, reason)
        console.print(table)


@update_app.command("apply")
def update_apply(
    source: str = typer.Argument(..., help="Update URL or directory."),
    key: Path = typer.Option(..., "--key", "-k", help="Issuer public key (PEM)."),
    channel: str = typer.Option("stable", "--channel", "-c"),
    no_reload: bool = typer.Option(False, "--no-reload", help="Install without hot reloading."),
) -> None:
    """Download, verify and install updates."""
    engine = _update_engine(source, key, channel)
    try:
        plan = engine.check()
        if not plan.has_updates:
            console.print("[green]Already up to date.[/]")
            return
        console.print(plan.describe())
        result = engine.apply(plan, reload=not no_reload)
    except ProfileOSError as exc:
        _fail(str(exc))

    if result.ok:
        console.print(
            f"\n[green]Applied {len(result.applied)} update(s)[/] "
            f"in {result.duration_s:.2f} s, {result.reloaded} reloaded live."
        )
    else:
        console.print(f"\n[red]Update failed.[/] Rolled back: {result.rolled_back}")
        for package_id, reason in result.failed:
            console.print(f"  [red]{package_id}:[/] {reason}")
    for warning in result.warnings:
        console.print(f"  [yellow]-[/] {warning}")


@update_app.command("status")
def update_status(
    source: str = typer.Argument("", help="Optional update source to probe."),
    key: Optional[Path] = typer.Option(None, "--key", "-k"),
) -> None:
    """Show installed packages and update history."""
    from .core.hotreload import PluginLoader
    from .security.keys import SigningKey, VerifyKey
    from .updates import DirectorySource, UpdateEngine

    settings = get_settings()
    settings.ensure_directories()
    verify_key = (
        VerifyKey.from_pem(key.read_bytes())
        if key and key.is_file()
        else SigningKey.generate().public_key()
    )
    engine = UpdateEngine(
        DirectorySource(source or settings.data_dir), verify_key, settings,
        loader=PluginLoader(settings, strict=False),
    )

    installed = engine.installed()
    if installed:
        table = Table(title="Installed content", header_style="dim")
        for column in ("Package", "Version", "Kind", "Installed"):
            table.add_column(column, style="cyan" if column == "Package" else None)
        for package in installed:
            table.add_row(
                package.package_id, package.version, package.kind, package.installed_at[:19]
            )
        console.print(table)
    else:
        console.print("[dim]No content packages installed.[/]")

    history = engine.history(10)
    if history:
        table = Table(title="Recent updates", box=None, header_style="dim")
        table.add_column("When", style="dim")
        table.add_column("Applied", justify="right")
        table.add_column("Failed", justify="right")
        for entry in history:
            table.add_row(
                entry["at"][:19], str(len(entry.get("applied", []))),
                str(len(entry.get("failed", []))),
            )
        console.print(table)


@update_app.command("keygen")
def update_keygen(
    out: Path = typer.Option(Path("update-key"), "--out", "-o", help="Output basename."),
    algorithm: str = typer.Option("eddsa", "--algorithm", help="eddsa | es256"),
) -> None:
    """Generate an update signing key pair.

    Keep the private key offline. Anyone holding it can publish content that
    every installation carrying the matching public key will accept.
    """
    from .security.keys import CoseAlgorithm, SigningKey

    algorithms = {"eddsa": CoseAlgorithm.EDDSA, "es256": CoseAlgorithm.ES256}
    if algorithm.lower() not in algorithms:
        _fail(f"Unknown algorithm: {algorithm}")

    key = SigningKey.generate(algorithm=algorithms[algorithm.lower()])
    private = key.save(out.with_suffix(".key"))
    public = out.with_suffix(".pub")
    public.write_bytes(key.public_key().to_pem())

    console.print(f"[green]Private key[/] {private}  [red](keep offline)[/]")
    console.print(f"[green]Public key [/] {public}")
    console.print(f"[dim]key id {key.key_id}[/]")


@update_app.command("publish")
def update_publish(
    directory: Path = typer.Argument(..., help="Directory of content files to publish."),
    key: Path = typer.Option(..., "--key", "-k", help="Private signing key (PEM)."),
    out: Path = typer.Option(Path("feed"), "--out", "-o", help="Output feed directory."),
    channel: str = typer.Option("stable", "--channel", "-c"),
    version: str = typer.Option("1.0.0", "--version"),
) -> None:
    """Sign a directory of content files into a publishable update feed."""
    from .core.hotreload import DATA_SCHEMAS, load_data_document, register_builtin_schemas
    from .security.keys import SigningKey
    from .updates import PackageKind, build_manifest, build_package, publish_directory

    if not directory.is_dir():
        _fail(f"Not a directory: {directory}")
    if not key.is_file():
        _fail(f"Signing key not found: {key}")

    register_builtin_schemas()
    signing_key = SigningKey.from_pem(key.read_bytes())

    kind_for_document = {
        "system_rules": PackageKind.SYSTEM_RULES,
        "price_list": PackageKind.PRICE_LIST,
        "pipe_catalogue": PackageKind.PIPE_CATALOGUE,
        "brand": PackageKind.SYSTEM_RULES,
    }

    packages = []
    contents: dict[str, bytes] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".json", ".xml", ".py") or not path.is_file():
            continue
        data = path.read_bytes()

        if path.suffix.lower() == ".py":
            kind = PackageKind.MACRO_LIBRARY
        else:
            try:
                document = load_data_document(path)
            except ProfileOSError as exc:
                console.print(f"  [yellow]skipped[/] {path.name}: {exc}")
                continue
            declared = str(document.get("kind") or "")
            kind = kind_for_document.get(declared)
            if kind is None:
                console.print(f"  [yellow]skipped[/] {path.name}: unknown kind {declared!r}")
                continue

        packages.append(
            build_package(
                path.stem.replace("_", "."), kind, data, path.name, signing_key,
                version=version, channel=channel, description=path.stem.replace("_", " "),
            )
        )
        contents[path.name] = data

    if not packages:
        _fail("Nothing publishable found in that directory.")

    manifest = build_manifest(packages, signing_key)
    target = publish_directory(contents, manifest, out)
    console.print(f"[green]Published[/] {len(packages)} package(s) to {target}")
    for package in packages:
        console.print(f"  {package.package_id} {package.version} [{package.kind.value}]")


# --------------------------------------------------------------------------- #
# Licensing
# --------------------------------------------------------------------------- #

@licence_app.command("fingerprint")
def licence_fingerprint() -> None:
    """Show this machine's hardware fingerprint."""
    from .security.hwid import current_fingerprint

    fingerprint = current_fingerprint()
    console.print(
        _kv_table(
            "Hardware fingerprint",
            [("Fingerprint", fingerprint.short), ("Traits", len(fingerprint.traits))],
        )
    )
    table = Table(box=None, header_style="dim")
    table.add_column("Trait", style="cyan")
    table.add_column("Weight", justify="right")
    table.add_column("Digest", style="dim")
    for trait in fingerprint.traits:
        table.add_row(trait.name, f"{trait.weight:g}", trait.digest)
    console.print(table)


@licence_app.command("issue")
def licence_issue(
    licensee: str = typer.Argument(..., help="Who the licence is for."),
    key: Path = typer.Option(..., "--key", "-k", help="Issuer private key (PEM)."),
    out: Path = typer.Option(Path("licence.p7"), "--out", "-o"),
    days: int = typer.Option(365, "--days", help="Validity in days."),
    seats: Optional[int] = typer.Option(None, "--seats"),
    features: str = typer.Option("", "--features", help="Comma-separated feature keys."),
) -> None:
    """Issue a licence sealed to this machine."""
    from datetime import date, timedelta

    from .security.hwid import current_fingerprint
    from .security.keys import SigningKey
    from .security.license import LicenseTerms, issue_license, save_license

    if not key.is_file():
        _fail(f"Signing key not found: {key}")

    terms = LicenseTerms(
        licensee=licensee,
        expires_on=date.today() + timedelta(days=days),
        features={f.strip() for f in features.split(",") if f.strip()},
        seats=seats,
    )
    blob = issue_license(terms, SigningKey.from_pem(key.read_bytes()), current_fingerprint())
    path = save_license(blob, out)
    console.print(f"[green]Issued[/] {terms.licence_id} for {licensee} -> {path}")
    console.print(f"[dim]Valid until {terms.expires_on}, sealed to this machine.[/]")


@licence_app.command("check")
def licence_check(
    licence: Path = typer.Argument(..., help="Licence file."),
    key: Path = typer.Option(..., "--key", "-k", help="Issuer public key (PEM)."),
) -> None:
    """Validate a licence on this machine."""
    from .security.keys import VerifyKey
    from .security.license import load_license_file

    if not key.is_file():
        _fail(f"Public key not found: {key}")
    try:
        status = load_license_file(licence, VerifyKey.from_pem(key.read_bytes()))
    except ProfileOSError as exc:
        _fail(str(exc))

    if status.valid:
        colour = "yellow" if status.read_only else "green"
        console.print(f"[{colour}]Licence is valid.[/]")
    else:
        console.print("[red]Licence rejected.[/]")
    if status.reason:
        console.print(f"  {status.reason}")
    if status.terms:
        console.print(
            _kv_table(
                "Terms",
                [
                    ("Licence", status.terms.licence_id),
                    ("Licensee", status.terms.licensee),
                    ("Expires", status.terms.expires_on or "never"),
                    ("Days left", status.terms.days_remaining() if status.terms.expires_on else "-"),
                    ("Seats", status.terms.seats or "unlimited"),
                    ("Features", ", ".join(sorted(status.terms.features)) or "all"),
                    ("Hardware match", f"{status.hardware_score * 100:.0f}%"),
                ],
            )
        )


@app.command()
def brand(
    select: Optional[str] = typer.Option(None, "--set", help="Activate a brand by id."),
) -> None:
    """Show or select the operator branding."""
    from .branding import BUILTIN_BRANDS, active_brand, set_active_brand

    if select:
        set_active_brand(select)
    current = active_brand()
    console.print(Panel("\n".join(current.letterhead()), title=f"Brand: {current.id}",
                        border_style="cyan"))
    console.print(f"[dim]Available: {', '.join(sorted(BUILTIN_BRANDS))}[/]")


# --------------------------------------------------------------------------- #
# Glass and panel nesting
# --------------------------------------------------------------------------- #
@glass_app.command("nest")
def glass_nest(
    elevations: Path = typer.Argument(..., help="Elevation-set JSON with the openings."),
    stock: Optional[str] = typer.Option(
        None,
        "--stock",
        help="Sheet sizes as WxH, comma separated, e.g. 3210x2250,6000x3210. "
        "Omitted, the standard float plate sizes are all offered.",
    ),
    kerf: float = typer.Option(0.0, "--kerf", help="Blade kerf [mm]; 0 for a scoring wheel."),
    trim: float = typer.Option(0.0, "--trim", help="Edge trim taken off all four sides [mm]."),
    stages: Optional[int] = typer.Option(
        None, "--stages", help="Cutting stages the table allows: 2, 3, or unlimited."
    ),
    exact: Optional[bool] = typer.Option(
        None, "--exact/--no-exact", help="Force the CP-SAT model on or off."
    ),
    svg_dir: Optional[Path] = typer.Option(None, "--svg", help="Write cutting maps here."),
    json_out: Optional[Path] = typer.Option(None, "--json"),
) -> None:
    """Nest a project's glass onto stock sheets and verify every cutting plan."""
    from .elements import ElevationSet, build_elements
    from .nesting import (
        SheetSpec,
        SheetStock,
        nest_project_glass,
        render_layout_svg,
        sheet_parts_from_builds,
    )
    from .nesting.sheet import STANDARD_GLASS_STOCK

    if not elevations.is_file():
        _fail(f"Elevation set not found: {elevations}")
    try:
        parsed = ElevationSet.model_validate_json(elevations.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - user-supplied file
        _fail("Could not parse the elevation set", exc)

    if stock:
        sheets: list[SheetStock] = []
        for token in stock.split(","):
            try:
                width, height = (float(v) for v in token.lower().split("x", 1))
            except ValueError:
                _fail(f"Could not read the sheet size {token!r}; expected WIDTHxHEIGHT")
            sheets.append(SheetStock(width, height, label=token.strip()))
    else:
        sheets = list(STANDARD_GLASS_STOCK)

    spec = SheetSpec(kerf=kerf, edge_trim=trim, stages=stages)
    builds = build_elements(parsed.openings)
    parts = sheet_parts_from_builds(builds)
    if not parts:
        _fail("This project has no glass to nest")

    try:
        report = nest_project_glass(parts, stock=sheets, spec=spec, exact=exact)
    except ProfileOSError as exc:
        _fail(str(exc))

    table = Table(title=f"Glass nesting - {parsed.name}", header_style="dim")
    table.add_column("Build-up", style="cyan")
    table.add_column("Panes", justify="right")
    table.add_column("Sheets", justify="right")
    table.add_column("Yield", justify="right")
    table.add_column("Off-cuts", justify="right")
    table.add_column("Stages", justify="right")
    table.add_column("Proof", style="dim")
    for material, result in sorted(report.results.items()):
        style = "green" if result.yield_pct >= 80 else "yellow" if result.yield_pct >= 65 else "red"
        if result.optimal:
            proof = "optimal"
        elif result.metadata.get("optimal_within_stage_limit"):
            proof = f"optimal <= 3 stages (bound {result.lower_bound})"
        else:
            proof = f"bound {result.lower_bound}"
        table.add_row(
            material,
            str(result.total_pieces),
            str(result.sheet_count),
            f"[{style}]{result.yield_pct:.2f}%[/]",
            str(len(result.reusable_offcuts())),
            str(result.stages_used or "-"),
            proof,
        )
    console.print(table)
    console.print(
        f"\n[bold]Total:[/] {report.sheet_count} sheets, "
        f"{report.yield_pct:.2f}% yield, "
        f"{report.total_stock_area / 1e6:.1f} m² consumed for "
        f"{report.total_placed_area / 1e6:.1f} m² of glass"
    )
    for warning in report.warnings:
        console.print(f"  [yellow]{warning}[/]")

    if svg_dir:
        svg_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for material, result in report.results.items():
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in material)
            for layout in result.layouts:
                target = svg_dir / f"{safe}-sheet{layout.sheet_index + 1:02d}.svg"
                target.write_text(render_layout_svg(layout), encoding="utf-8")
                written += 1
        console.print(f"[green]Wrote[/] {written} cutting maps to {svg_dir}")

    if json_out:
        payload = {
            "summary": report.summary(),
            "materials": {m: r.summary() for m, r in report.results.items()},
        }
        json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[green]Wrote[/] {json_out}")


@glass_app.command("stock")
def glass_stock() -> None:
    """List the standard float plate sizes the nester offers by default."""
    from .nesting.sheet import STANDARD_GLASS_STOCK

    table = Table(title="Standard glass stock", header_style="dim")
    table.add_column("Label", style="cyan")
    table.add_column("Width", justify="right")
    table.add_column("Height", justify="right")
    table.add_column("Area", justify="right")
    for sheet in STANDARD_GLASS_STOCK:
        table.add_row(
            sheet.label or "-",
            f"{sheet.width:.0f}",
            f"{sheet.height:.0f}",
            f"{sheet.area / 1e6:.2f} m²",
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# Catalogue ingestion
# --------------------------------------------------------------------------- #
@catalogue_app.command("ingest")
def catalogue_ingest(
    drawings: Optional[Path] = typer.Option(
        None, "--drawings", "-d", help="Folder of supplier DXF drawings."
    ),
    table: Optional[Path] = typer.Option(
        None, "--table", "-t", help="Supplier catalogue: PDF, CSV or TSV."
    ),
    series: str = typer.Option("unknown", "--series", help="Profile system family."),
    material: Optional[str] = typer.Option(None, "--material", help="Alloy id."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Stop after N drawings."),
    no_torsion: bool = typer.Option(False, "--no-torsion", help="Skip the warping FEA."),
    plugin_out: Optional[Path] = typer.Option(
        None, "--plugin", help="Write the ingested profiles as a data plugin."
    ),
    include_conflicts: bool = typer.Option(
        False,
        "--include-conflicts",
        help="Ship profiles whose published and measured figures disagree.",
    ),
    json_out: Optional[Path] = typer.Option(None, "--json"),
) -> None:
    """Read a supplier catalogue and drawing pack into an owned profile library.

    Every drawing is measured by the geometry and structural engines and set
    against the supplier's published table. Agreement is evidence; disagreement
    is reported rather than resolved.
    """
    from .catalogue import CatalogueError, ingest, to_plugin

    if drawings is None and table is None:
        _fail("Give at least one of --drawings or --table")

    try:
        report = ingest(
            table=table,
            drawings=drawings,
            system_series=series,
            material_id=material,
            torsion=not no_torsion,
            limit=limit,
        )
    except (CatalogueError, ProfileOSError) as exc:
        _fail(str(exc))

    table_view = Table(title="Catalogue ingestion", header_style="dim")
    table_view.add_column("Article", style="cyan")
    table_view.add_column("Name")
    table_view.add_column("Status")
    table_view.add_column("Checked", justify="right")
    table_view.add_column("Conflicts", justify="right")
    styles = {
        "verified": "green",
        "conflict": "red",
        "unverified": "yellow",
        "table only": "dim",
    }
    for entry in report.entries:
        summary = entry.summary()
        style = styles.get(entry.status, "")
        table_view.add_row(
            entry.profile_id,
            (entry.name or "")[:38],
            f"[{style}]{entry.status}[/]" if style else entry.status,
            str(summary["checked"]),
            str(summary["conflicts"]) if summary["conflicts"] else "-",
        )
    console.print(table_view)

    for entry in report.conflicts:
        console.print(f"\n[red]Conflict[/] on [cyan]{entry.profile_id}[/]:")
        for check in entry.disagreements:
            console.print(f"  {check.describe()}")
        console.print(
            "  [dim]The library stores the measured value; the published one is "
            "kept beside it.[/]"
        )

    stats = report.summary()
    console.print(
        f"\n[bold]{stats['entries']}[/] articles, "
        f"{stats['with_geometry']} with geometry, "
        f"[green]{stats['verified']} verified[/], "
        f"[red]{stats['conflicts']} in conflict[/]"
    )
    if report.unmatched_drawings:
        console.print(
            f"  [yellow]{len(report.unmatched_drawings)} drawing(s) matched no "
            f"catalogue row[/]"
        )
    if report.unmatched_rows:
        console.print(
            f"  [yellow]{len(report.unmatched_rows)} catalogue row(s) had no drawing[/]"
        )
    for message in report.errors:
        console.print(f"  [red]{message}[/]")

    if plugin_out:
        payload = to_plugin(
            report,
            plugin_id=plugin_out.stem,
            name=f"{series} profile library",
            include_conflicts=include_conflicts,
        )
        plugin_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(
            f"[green]Wrote[/] {plugin_out} "
            f"({len(payload['profiles'])} profiles, "
            f"{len(payload['excluded_for_conflict'])} excluded)"
        )

    if json_out:
        json_out.write_text(
            json.dumps(
                {
                    "summary": report.summary(),
                    "entries": [entry.summary() for entry in report.entries],
                    "conflicts": {
                        entry.profile_id: [c.describe() for c in entry.disagreements]
                        for entry in report.conflicts
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        console.print(f"[green]Wrote[/] {json_out}")


@catalogue_app.command("table")
def catalogue_table(
    source: Path = typer.Argument(..., help="Supplier catalogue: PDF, CSV or TSV."),
    limit: int = typer.Option(30, "--limit", "-n", help="Rows to show."),
) -> None:
    """Show what the parser reads out of a supplier table, before any matching.

    Run this first on a new supplier. If the columns come out in the wrong
    places here, nothing downstream can put them right.
    """
    from .catalogue import CatalogueError, read_table

    try:
        rows = read_table(source)
    except (CatalogueError, ProfileOSError) as exc:
        _fail(str(exc))

    if not rows:
        console.print("[yellow]No data rows recognised in this file.[/]")
        return

    properties: list[str] = []
    for row in rows:
        for name in row.values:
            if name not in properties:
                properties.append(name)

    view = Table(title=f"{source.name} - {len(rows)} rows", header_style="dim")
    view.add_column("Code", style="cyan")
    view.add_column("Description")
    for name in properties:
        view.add_column(name, justify="right")
    for row in rows[:limit]:
        cells = [f"{row.values[n]:,.4g}" if n in row.values else "-" for n in properties]
        code = f"[yellow]{row.code}[/]" if row.partial else row.code
        view.add_row(code, (row.description or "")[:32], *cells)
    console.print(view)
    if len(rows) > limit:
        console.print(f"[dim]... {len(rows) - limit} more rows[/]")
    partial = sum(1 for row in rows if row.partial)
    if partial:
        console.print(
            f"[yellow]{partial} row(s) were short of columns[/] and may have "
            "figures attributed to the wrong property."
        )


# --------------------------------------------------------------------------- #
# Capability comparison
# --------------------------------------------------------------------------- #
@app.command("compare")
def compare_command(
    area: Optional[str] = typer.Option(None, "--area", "-a", help="Filter to one area."),
    package: Optional[str] = typer.Option(
        None, "--package", "-p", help="Show one package's column only."
    ),
    gaps: bool = typer.Option(False, "--gaps", help="Show only what ProfileOS lacks."),
    verify: bool = typer.Option(
        False, "--verify", help="Check every ProfileOS claim against the code and exit."
    ),
    hebrew: bool = typer.Option(False, "--he", help="Hebrew capability names."),
    json_out: Optional[Path] = typer.Option(None, "--json"),
) -> None:
    """Compare ProfileOS against the established fabrication packages.

    Every ProfileOS claim is bound to a symbol in this codebase and checked
    before the table is drawn. Competitor columns record public documentation
    only; "no" in this table means "not documented", never "absent".
    """
    from . import compare as cmp

    failures = cmp.verify_claims()
    if verify:
        if failures:
            for capability_id, reason in sorted(failures.items()):
                console.print(f"[red]{capability_id}[/]: {reason}")
            _fail(f"{len(failures)} capability claim(s) have no code behind them")
        console.print(
            f"[green]All {sum(1 for c in cmp.CAPABILITIES if c.implemented)} "
            "capability claims resolve to real code.[/]"
        )
        return
    if failures:
        console.print(
            f"[red]Warning:[/] {len(failures)} claim(s) do not resolve; "
            "run `profileos compare --verify`."
        )

    packages = list(cmp.PACKAGES)
    if package:
        packages = [p for p in packages if p.id == package or p.name.lower() == package.lower()]
        if not packages:
            _fail(
                f"Unknown package {package!r}. Known: "
                + ", ".join(p.id for p in cmp.PACKAGES)
            )

    capabilities = list(cmp.CAPABILITIES)
    if area:
        capabilities = [c for c in capabilities if str(c.area) == area.lower()]
        if not capabilities:
            _fail(
                f"Unknown area {area!r}. Known: "
                + ", ".join(sorted({str(c.area) for c in cmp.CAPABILITIES}))
            )
    if gaps:
        capabilities = [c for c in capabilities if not c.implemented]

    marks = {
        cmp.Support.FULL: "[green]yes[/]",
        cmp.Support.PARTIAL: "[yellow]part[/]",
        cmp.Support.NOT_DOCUMENTED: "[dim]no[/]",
        cmp.Support.UNKNOWN: "[dim]?[/]",
    }

    view = Table(title="Capability comparison", header_style="dim")
    view.add_column("Capability", style="cyan", no_wrap=True)
    view.add_column("ProfileOS", justify="center")
    for entry in packages:
        view.add_column(entry.heading, justify="center")

    last_area = None
    for capability in capabilities:
        if capability.area != last_area:
            view.add_section()
            last_area = capability.area
        name = capability.name_he if hebrew else capability.name_en
        if capability.differentiator:
            name = f"{name} *"
        view.add_row(
            name,
            marks[cmp.profileos_support(capability)],
            *[marks[entry.level(capability.id)] for entry in packages],
        )
    console.print(view)
    console.print(
        "[dim]* engineering the compared packages do not document.  "
        "yes = documented, part = partial or a paid module, "
        "no = not found in public material, ? = not looked into.[/]"
    )

    stats = cmp.summary()
    console.print(
        f"\n[bold]ProfileOS:[/] {stats['profileos_implemented']} of "
        f"{stats['capabilities']} capabilities, "
        f"{stats['not_documented_elsewhere']} that no compared package documents, "
        f"[yellow]{stats['profileos_gaps']} it does not have[/]."
    )
    for capability in cmp.missing_from_profileos():
        console.print(f"  [yellow]missing:[/] {capability.name_en} — {capability.detail}")

    console.print("\n[bold]Standing limitations[/]")
    for limitation in cmp.STANDING_LIMITATIONS:
        console.print(f"  [dim]-[/] {limitation}")

    if json_out:
        json_out.write_text(
            json.dumps(
                {
                    "summary": cmp.summary(),
                    "capabilities": cmp.matrix(),
                    "packages": [
                        {
                            "id": entry.id,
                            "name": entry.name,
                            "vendor": entry.vendor,
                            "origin": entry.origin,
                            "note": entry.note,
                            "source": entry.source,
                            "coverage": cmp.coverage(entry),
                        }
                        for entry in cmp.PACKAGES
                    ],
                    "profileos_coverage": cmp.coverage(),
                    "gaps": [c.id for c in cmp.missing_from_profileos()],
                    "distinctive": [c.id for c in cmp.not_documented_elsewhere()],
                    "limitations": list(cmp.STANDING_LIMITATIONS),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        console.print(f"[green]Wrote[/] {json_out}")


# --------------------------------------------------------------------------- #
# 3D views
# --------------------------------------------------------------------------- #
@view_app.command("render")
def view_render(
    elevations: Path = typer.Argument(..., help="Elevation-set JSON with the openings."),
    element: Optional[str] = typer.Option(
        None, "--element", "-e", help="One element id; omitted, the whole elevation."
    ),
    out: Path = typer.Option(Path("views"), "--out", "-o", help="Output directory."),
    finish: str = typer.Option(
        "natural", "--finish", help="natural | bronze — the anodised look."
    ),
    no_glass: bool = typer.Option(False, "--no-glass", help="Frames only."),
    gltf: bool = typer.Option(False, "--gltf", help="Also export glTF and GLB."),
    viewer: bool = typer.Option(True, "--viewer/--no-viewer", help="Interactive HTML."),
) -> None:
    """Render elements in 3D: printable SVG, glTF, and an interactive viewer."""
    from .elements import ElevationSet, build_elements
    from .viz3d import (
        BRONZE_MATERIALS,
        DEFAULT_MATERIALS,
        RenderOptions,
        ViewStyle,
        build_element_scene,
        build_elevation_scene,
        render_viewer,
        render_views,
        write_gltf,
    )

    if not elevations.is_file():
        _fail(f"Elevation set not found: {elevations}")
    try:
        parsed = ElevationSet.model_validate_json(elevations.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - user-supplied file
        _fail("Could not parse the elevation set", exc)

    openings = parsed.openings
    if element:
        openings = [o for o in openings if o.element_id == element]
        if not openings:
            _fail(
                f"No element {element!r}. Known: "
                + ", ".join(o.element_id for o in parsed.openings)
            )

    style = ViewStyle(show_glass=not no_glass)
    options = RenderOptions(
        materials=dict(BRONZE_MATERIALS if finish == "bronze" else DEFAULT_MATERIALS),
        background=None,
    )
    builds = build_elements(openings)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def emit(scene, stem: str) -> None:
        for name, svg in render_views(scene, options).items():
            path = out / f"{stem}-{name}.svg"
            path.write_text(svg, encoding="utf-8")
            written.append(path)
        if viewer:
            path = out / f"{stem}.html"
            path.write_text(render_viewer(scene), encoding="utf-8")
            written.append(path)
        if gltf:
            written.append(write_gltf(scene, out / f"{stem}.gltf"))
            written.append(write_gltf(scene, out / f"{stem}.glb"))

    table = Table(title=f"3D views - {parsed.name}", header_style="dim")
    table.add_column("Element", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Parts", justify="right")
    table.add_column("Triangles", justify="right")
    table.add_column("Metal", justify="right")

    for build in builds:
        scene = build_element_scene(build, style=style)
        stem = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in build.opening.element_id
        )
        emit(scene, stem)
        size = scene.size
        table.add_row(
            build.opening.element_id,
            f"{size[0]:.0f} x {size[1]:.0f}",
            str(len(scene.meshes)),
            f"{scene.triangle_count:,}",
            f"{scene.aluminium_volume() * 2.70e-6:.1f} kg",
        )

    if len(builds) > 1 and not element:
        emit(build_elevation_scene(builds, style=style), "elevation")

    console.print(table)
    console.print(f"[green]Wrote[/] {len(written)} file(s) to {out}")


# --------------------------------------------------------------------------- #
# ERP
# --------------------------------------------------------------------------- #
@erp_app.command("plan")
def erp_plan(
    elevations: Path = typer.Argument(..., help="Elevation-set JSON with the openings."),
    start: Optional[str] = typer.Option(None, "--start", help="Release date, YYYY-MM-DD."),
    due: Optional[str] = typer.Option(None, "--due", help="Promised date, YYYY-MM-DD."),
) -> None:
    """Schedule a job against the shop's finite capacity and say when it lands."""
    from datetime import date as _date

    from .elements import ElevationSet, build_elements
    from .erp import DEFAULT_WORK_CENTRES, Scheduler, demand_from_builds

    if not elevations.is_file():
        _fail(f"Elevation set not found: {elevations}")
    try:
        parsed = ElevationSet.model_validate_json(elevations.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _fail("Could not parse the elevation set", exc)

    def parse(value: Optional[str]) -> Optional[_date]:
        if not value:
            return None
        try:
            return _date.fromisoformat(value)
        except ValueError:
            _fail(f"Not a date: {value!r}; use YYYY-MM-DD")

    builds = build_elements(parsed.openings)
    demand = demand_from_builds(
        builds, parsed.project_id, due=parse(due), name=parsed.name
    )
    scheduler = Scheduler()
    plan = scheduler.schedule([demand], start=parse(start))

    console.print(
        _kv_table(
            "Work content",
            [
                ("Elements", str(demand.elements)),
                ("Cuts", str(demand.cuts)),
                ("Machining operations", str(demand.machining_operations)),
                ("Panes", str(demand.panes)),
            ],
        )
    )

    steps = Table(title="Schedule", header_style="dim")
    for column in ("Operation", "Work centre", "Start", "Finish", "Hours"):
        steps.add_column(column, justify="right" if column == "Hours" else "left")
    for operation in sorted(plan.operations, key=lambda o: (o.start, o.operation)):
        steps.add_row(
            str(operation.operation), operation.work_centre,
            operation.start.isoformat(), operation.finish.isoformat(),
            f"{operation.hours:.2f}" if operation.hours else "-",
        )
    console.print(steps)

    load = Table(title="Work-centre load", header_style="dim")
    for column in ("Code", "Name", "Hours", "Available", "Utilisation"):
        load.add_column(column, justify="right" if column != "Name" else "left")
    for row in plan.utilisation(DEFAULT_WORK_CENTRES):
        style = "red" if row["utilisation_pct"] > 90 else "yellow" if row["utilisation_pct"] > 70 else "green"
        load.add_row(
            row["code"], row["name"], f"{row['hours']:.1f}",
            f"{row['available']:.1f}", f"[{style}]{row['utilisation_pct']:.0f}%[/]",
        )
    console.print(load)

    finish = plan.completion[demand.job_id]
    console.print(f"\n[bold]Complete:[/] {finish.isoformat()} ({finish.strftime('%A')})")
    bottleneck = plan.bottleneck(DEFAULT_WORK_CENTRES)
    if bottleneck:
        console.print(
            f"[dim]Bottleneck:[/] {bottleneck['name']} at "
            f"{bottleneck['utilisation_pct']:.0f}% of its capacity"
        )
    for warning in plan.warnings:
        console.print(f"  [yellow]{warning}[/]")


@erp_app.command("accounts")
def erp_accounts(
    ledger_file: Optional[Path] = typer.Option(
        None, "--ledger", help="Ledger JSON; omitted, a worked example is shown."
    ),
) -> None:
    """Show the trial balance, profit and loss, and balance sheet."""
    from datetime import date as _date

    from .erp import Company, StockItem, format_money, money
    from .erp.sales import SalesInvoice, SalesLine

    shop = Company(name="Example")
    if ledger_file is not None and ledger_file.is_file():
        _fail("Loading a saved ledger is not implemented; run without --ledger")

    # A worked example, so the command shows the shape of the report.
    shop.add_item(StockItem("4301", "Outer frame", supplier_id="extal"))
    shop.receive_stock("4301", 300.0, 4150.0, on=_date(2026, 1, 5))
    shop.issue_to_job("4301", 180.0, "P-1", on=_date(2026, 2, 1))
    shop.invoice(
        SalesInvoice("INV-1", "Ariel Bros", _date(2026, 2, 10),
                     [SalesLine("Aluminium windows", 1, money(48_000))])
    )
    shop.collect("Ariel Bros", money(56_640), _date(2026, 3, 1))

    trial = Table(title="Trial balance", header_style="dim")
    for column in ("Code", "Account", "Debit", "Credit"):
        trial.add_column(column, justify="right" if column in ("Debit", "Credit") else "left")
    for row in shop.ledger.trial_balance():
        trial.add_row(
            row.account.code, row.account.name,
            format_money(row.debits) if row.debits else "",
            format_money(row.credits) if row.credits else "",
        )
    console.print(trial)

    profit = shop.ledger.profit_and_loss()
    sheet = shop.ledger.balance_sheet()
    console.print(
        _kv_table(
            "Position",
            [
                ("Income", format_money(profit["income"])),
                ("Expense", format_money(profit["expense"])),
                ("Result", format_money(profit["result"])),
                ("Assets", format_money(sheet["assets"])),
                ("Liabilities", format_money(sheet["liabilities"])),
                ("Equity", format_money(sheet["equity"])),
                ("Balance sheet difference", format_money(sheet["difference"])),
            ],
        )
    )
    report = shop.audit()
    console.print(
        f"[green]Audit:[/] ledger balanced, stock reconciled against "
        f"{report['movements']} movement(s), stock accounts agree."
    )


@erp_app.command("vat")
def erp_vat(
    on: str = typer.Argument(..., help="Date to look the rate up for, YYYY-MM-DD."),
) -> None:
    """Show the statutory VAT rate in force on a date."""
    from datetime import date as _date

    from .erp import ISRAELI_VAT_HISTORY, vat_rate

    try:
        when = _date.fromisoformat(on)
    except ValueError:
        _fail(f"Not a date: {on!r}; use YYYY-MM-DD")

    console.print(f"[bold]{vat_rate(when):.0%}[/] on {when.isoformat()}")
    table = Table(title="Rate history", header_style="dim", box=None)
    table.add_column("From", style="cyan")
    table.add_column("Rate", justify="right")
    for effective, rate in ISRAELI_VAT_HISTORY:
        table.add_row(effective.isoformat(), f"{rate:.0%}")
    console.print(table)


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #
@access_app.command("status")
def access_status() -> None:
    """Say whether this installation is locked, and whether a key is present."""
    from .security.gate import Gate

    gate = Gate()
    state = gate.status()
    console.print(
        _kv_table(
            "Access",
            [
                ("Enrolled", "yes" if state["enrolled"] else "no"),
                ("Credential store", state["store"]),
                ("This machine", state["machine"]),
                (
                    "Key files found",
                    "\n".join(state["key_files_found"]) or "none — insert the USB key",
                ),
            ],
        )
    )
    if not state["enrolled"]:
        console.print(
            "[yellow]This installation has no operator yet.[/] Run "
            "[cyan]profileos access enrol[/] on the machine that will use it."
        )


@access_app.command("enrol")
def access_enrol(
    key_target: Path = typer.Argument(
        ..., help="The USB drive (or folder) the key file is written to."
    ),
    username: str = typer.Option(..., "--username", "-u", prompt=True),
) -> None:
    """Create the one operator and write the USB key that opens it.

    Asks for the password twice and for two security questions. Nothing typed
    here is stored in the clear: the password and each answer are hashed under
    their own salt, and the store is then sealed to this machine and to the
    key file together.
    """
    from .security.gate import AccessDenied, Gate, password_problems

    gate = Gate()
    if gate.is_enrolled:
        _fail(
            "This installation already has its operator. Use "
            "`profileos access rotate` and prove the current password."
        )

    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    problems = password_problems(password)
    if problems:
        _fail("; ".join(problems))

    console.print(
        "\n[dim]Two security questions. Choose things nobody outside the firm "
        "could answer, and that you will still answer the same way in five "
        "years. Spelling, spacing and punctuation are ignored.[/]\n"
    )
    questions: list[tuple[str, str]] = []
    for index in (1, 2):
        prompt = typer.prompt(f"Question {index}")
        answer = typer.prompt(f"Answer {index}", hide_input=True, confirmation_prompt=True)
        questions.append((prompt, answer))

    try:
        path = gate.enrol(username, password, questions, key_target=key_target)
    except AccessDenied as exc:
        _fail(str(exc))

    console.print(f"\n[green]Enrolled.[/] Key file written to [cyan]{path}[/]")
    console.print(
        "[red]Keep that file on the USB key and nowhere else.[/] Without it "
        "the software will not start, and it cannot be reissued from here."
    )


@access_app.command("login")
def access_login(
    key: Optional[Path] = typer.Option(None, "--key", help="Path to the key file."),
) -> None:
    """Check the three factors and report whether they open the gate."""
    from .security.gate import AccessDenied, Gate, NotEnrolled

    gate = Gate()
    try:
        prompts = gate.prompts(key_path=key)
    except (AccessDenied, NotEnrolled) as exc:
        _fail(str(exc))

    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)
    answers = [typer.prompt(prompt, hide_input=True) for prompt in prompts]

    try:
        session = gate.authenticate(username, password, answers, key_path=key)
    except AccessDenied as exc:
        _fail(str(exc))
    console.print(f"[green]Access granted[/] to {session.describe()}")


@access_app.command("authorise-machine")
def access_authorise_machine(
    fingerprint: str = typer.Argument(..., help="The other machine's fingerprint."),
    key: Optional[Path] = typer.Option(None, "--key", help="Path to the key file."),
) -> None:
    """Let the operator use another computer.

    Run this on a machine that already works, holding the key — which is what
    stops somebody who copies the key file adding their own laptop.
    """
    from .security.gate import AccessDenied, Gate

    gate = Gate()
    try:
        prompts = gate.prompts(key_path=key)
        username = typer.prompt("Username")
        password = typer.prompt("Password", hide_input=True)
        answers = [typer.prompt(prompt, hide_input=True) for prompt in prompts]
        gate.authorise_machine(
            username, password, answers, fingerprint=fingerprint, key_path=key
        )
    except AccessDenied as exc:
        _fail(str(exc))
    console.print("[green]Authorised.[/] That machine may now open the installation.")


@access_app.command("rotate")
def access_rotate(
    key: Optional[Path] = typer.Option(None, "--key", help="Path to the key file."),
    questions: bool = typer.Option(
        False, "--questions", help="Also replace the two security questions."
    ),
) -> None:
    """Change the password, having proved the current one."""
    from .security.gate import AccessDenied, Gate

    gate = Gate()
    try:
        prompts = gate.prompts(key_path=key)
    except AccessDenied as exc:
        _fail(str(exc))

    username = typer.prompt("Username")
    password = typer.prompt("Current password", hide_input=True)
    answers = [typer.prompt(prompt, hide_input=True) for prompt in prompts]
    new_password = typer.prompt(
        "New password", hide_input=True, confirmation_prompt=True
    )

    replacements: list[tuple[str, str]] | None = None
    if questions:
        replacements = []
        for index in (1, 2):
            prompt = typer.prompt(f"New question {index}")
            answer = typer.prompt(
                f"New answer {index}", hide_input=True, confirmation_prompt=True
            )
            replacements.append((prompt, answer))

    try:
        gate.rotate(
            username, password, answers,
            new_password=new_password, new_questions=replacements, key_path=key,
        )
    except AccessDenied as exc:
        _fail(str(exc))
    console.print("[green]Changed.[/]")


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
@finish_app.command("area")
def finish_area(
    drawing: Path = typer.Argument(..., help="The profile's DXF."),
    length: float = typer.Option(6000.0, "--length", "-l", help="Bar length [mm]."),
    pieces: int = typer.Option(1, "--pieces", "-n"),
) -> None:
    """Coated area of a profile, from the outside of the section only."""
    from .finishing import coating_area_per_metre
    from .structural import analyse_dxf

    try:
        properties, _section = analyse_dxf(str(drawing), profile_id=drawing.stem)
    except Exception as exc:  # noqa: BLE001
        _fail(f"Could not analyse {drawing}: {exc}")

    per_metre = coating_area_per_metre(properties)
    area = per_metre * length / 1000.0 * pieces
    table = Table(title=drawing.stem, header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value", justify="right")
    for label, value in (
        ("Wetted perimeter", f"{properties.perimeter:,.1f} mm"),
        ("Outer perimeter", f"{properties.outer_perimeter:,.1f} mm"),
        ("Coated area per metre", f"{per_metre:.4f} m2/m"),
        (f"Coated area, {pieces} x {length:,.0f} mm", f"{area:.3f} m2"),
    ):
        table.add_row(label, value)
    console.print(table)
    saved = (properties.perimeter - properties.outer_perimeter) / max(
        properties.perimeter, 1e-9
    )
    console.print(
        f"[dim]The chambers are {saved:.0%} of the wetted perimeter and no bath "
        f"reaches them; charging on the wetted figure would overstate this "
        f"order by that much.[/dim]"
    )


@finish_app.command("kinds")
def finish_kinds() -> None:
    """The finishes that can be ordered, and how many passes each takes."""
    from .finishing import FinishKind

    table = Table(title="Finishes", header_style="dim")
    table.add_column("Kind", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Passes", justify="right")
    for kind in FinishKind:
        table.add_row(kind.value, kind.hebrew, str(kind.passes))
    console.print(table)


@deliver_app.command("pack")
def deliver_pack(
    job_id: str = typer.Argument(..., help="The job to load."),
    vehicle: str = typer.Option("truck_7t", "--vehicle", "-v"),
) -> None:
    """The loading list, in the order the units come off the lorry."""
    from .delivery import pack, units_from_builds
    from .projects import default_store

    job = default_store().get(job_id)
    if job is None:
        _fail(f"No job {job_id}.")
    if job.schedule is None or not job.schedule.openings:
        _fail(f"{job_id} has no elements saved to it yet.")

    from .elements import ElementBuilder

    builds = [
        ElementBuilder().build(opening, sill_height=job.schedule.sill_height)
        for opening in job.schedule.openings
    ]
    try:
        packing = pack(
            units_from_builds(builds), vehicle_name=vehicle,
            job_id=job.job_id, job_name=job.name, site=job.site_address,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    for load in packing.loads:
        console.print(f"[cyan]{load.describe()}[/cyan]")
        table = Table(header_style="dim")
        table.add_column("Mark", style="cyan")
        table.add_column("Where")
        table.add_column("Floor", justify="right")
        table.add_column("Size")
        table.add_column("Qty", justify="right")
        table.add_column("kg", justify="right")
        table.add_column("Carry")
        for unit in load.units:
            table.add_row(
                unit.mark, unit.location or "-", str(unit.floor),
                f"{unit.width:,.0f} x {unit.height:,.0f}", str(unit.quantity),
                f"{unit.total_mass:,.1f}", unit.handling.hebrew,
            )
        console.print(table)
    for warning in packing.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@deliver_app.command("plan")
def deliver_plan(
    job_id: str = typer.Argument(..., help="The job to fit."),
    people: int = typer.Option(2, "--people", "-p"),
    condition: str = typer.Option("new_build", "--condition", "-c"),
    access: str = typer.Option("ground", "--access", "-a"),
    start: str = typer.Option("", "--start", help="First fitting day YYYY-MM-DD."),
) -> None:
    """Lay the fitting out on real working days, festivals included."""
    from datetime import date as _date

    from .delivery import (
        Access, Crew, SiteCondition, plan_installation, units_from_builds,
    )
    from .elements import ElementBuilder
    from .projects import default_store

    job = default_store().get(job_id)
    if job is None:
        _fail(f"No job {job_id}.")
    if job.schedule is None or not job.schedule.openings:
        _fail(f"{job_id} has no elements saved to it yet.")

    builds = [
        ElementBuilder().build(opening, sill_height=job.schedule.sill_height)
        for opening in job.schedule.openings
    ]
    plan = plan_installation(
        units_from_builds(builds),
        crew=Crew(name="crew", people=people),
        condition=SiteCondition(condition),
        access=Access(access),
        start=_date.fromisoformat(start) if start else None,
        job_id=job.job_id, site=job.site_address,
    )

    table = Table(title=f"{job.job_id} — fitting", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value")
    for label, value in plan.summary_rows():
        table.add_row(label, value)
    console.print(table)

    days = Table(header_style="dim")
    days.add_column("Day", style="cyan")
    days.add_column("Date")
    days.add_column("Units", justify="right")
    days.add_column("Hours", justify="right")
    for number, (day, tasks) in enumerate(plan.days, start=1):
        days.add_row(
            str(number), day.strftime("%d/%m/%Y %a"), str(len(tasks)),
            f"{sum(task.minutes for task in tasks) / 60.0:.1f}",
        )
    console.print(days)
    for warning in plan.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@files_app.command("list")
def files_list(
    job_id: str = typer.Argument(..., help="The job whose files to list."),
) -> None:
    """Everything kept with a job, and whether it is still what was filed."""
    from .projects.attachments import attachments_for

    store = attachments_for(job_id)
    if not len(store):
        console.print(f"[dim]No files attached to {job_id} yet.[/dim]")
        return

    table = Table(title=f"{job_id} files", header_style="dim")
    table.add_column("Kind", style="cyan")
    table.add_column("Caption")
    table.add_column("Element")
    table.add_column("Added")
    table.add_column("Size", justify="right")
    for item in store:
        table.add_row(
            item.kind.hebrew, item.caption or item.name, item.element or "-",
            item.added_at[:10], f"{item.size / 1024:,.0f} KB",
        )
    console.print(table)

    for item in store.changed():
        console.print(f"[yellow]{item.name} has changed since it was filed[/yellow]")
    for item in store.missing():
        console.print(f"[red]{item.name} is listed but missing from the folder[/red]")


@files_app.command("add")
def files_add(
    job_id: str = typer.Argument(..., help="The job to file it under."),
    path: Path = typer.Argument(..., help="The file to attach."),
    kind: str = typer.Option("other", "--kind", "-k"),
    caption: str = typer.Option("", "--caption", "-c"),
    element: str = typer.Option("", "--element", "-e"),
    by: str = typer.Option("", "--by"),
) -> None:
    """Copy a photograph or a document into the job's own folder."""
    from .projects.attachments import AttachmentKind, attachments_for

    try:
        attachment = attachments_for(job_id).add(
            path, kind=AttachmentKind(kind), caption=caption,
            added_by=by, element=element,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    console.print(f"[green]{attachment.name}[/green] {attachment.describe()}")


def _show_plan(plan: Any) -> None:
    """Show what an import would do, before it does any of it."""
    console.print(f"[cyan]{plan.summary()}[/cyan]")
    if plan.table.skipped_preamble:
        console.print(
            f"[dim]Skipped {plan.table.skipped_preamble} line(s) above the "
            f"header row.[/dim]"
        )

    columns = Table(title="Columns matched", header_style="dim")
    columns.add_column("Field", style="cyan")
    columns.add_column("Column in the file")
    for field_name, header in plan.describe_columns():
        columns.add_row(field_name, header)
    console.print(columns)

    if plan.unmatched_fields:
        console.print(
            "[yellow]No column for: " + ", ".join(plan.unmatched_fields) + "[/yellow]"
        )
    if plan.ignored_columns:
        console.print(
            "[dim]Nothing read from: " + ", ".join(plan.ignored_columns[:6]) + "[/dim]"
        )

    skipped = plan.of_action("skip")
    if skipped:
        console.print(f"[yellow]{len(skipped)} row(s) will be skipped:[/yellow]")
        for row in skipped[:10]:
            console.print(f"  line {row.number}: {row.label} — {row.reason}")
        if len(skipped) > 10:
            console.print(f"  [dim]…and {len(skipped) - 10} more[/dim]")

    for problem in plan.problems:
        console.print(f"[red]{problem}[/red]")


@cnc_app.command("prove")
def cnc_prove(
    driver: str = typer.Argument(..., help="Post-processor, e.g. elumatec.ncx."),
    machine: str = typer.Argument(..., help="The machine it was cut on."),
    by: str = typer.Option(..., "--by", help="Who proved it."),
    findings: str = typer.Option(..., "--findings", help="What was cut and measured."),
    deviation: float = typer.Option(0.0, "--deviation", help="Largest error [mm]."),
    reject: bool = typer.Option(False, "--reject", help="Record a failed proving."),
) -> None:
    """Record that a program from here was cut on a real machine and measured."""
    from .cnc.proving import Proof, default_record

    record = default_record()
    try:
        proof = record.record(Proof(
            driver=driver, machine=machine, proved_by=by,
            findings=findings, largest_deviation=deviation,
            accepted=not reject,
        ))
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(f"[green]{proof.describe()}[/green]")
    banner = record.banner(driver, machine)
    if banner:
        console.print(f"[yellow]{banner}[/yellow]")
    else:
        console.print(
            "[green]Programs for this post-processor on this machine no "
            "longer carry the unproven banner.[/green]"
        )


@cnc_app.command("proven")
def cnc_proven() -> None:
    """Which post-processor has been proved on which machine."""
    from .cnc.proving import default_record

    record = default_record()
    if not len(record):
        console.print(
            "[yellow]Nothing has been proved on a machine yet. Every posted "
            "program carries the unproven banner, and it should.[/yellow]"
        )
        return

    table = Table(title="Proving record", header_style="dim")
    table.add_column("Post-processor", style="cyan")
    table.add_column("Machine")
    table.add_column("By")
    table.add_column("On")
    table.add_column("Largest error", justify="right")
    table.add_column("Result")
    for proof in record:
        table.add_row(
            proof.driver, proof.machine, proof.proved_by, proof.on,
            f"{proof.largest_deviation:.2f} mm" if proof.largest_deviation else "-",
            "[green]accepted[/green]" if proof.accepted else "[red]rejected[/red]",
        )
    console.print(table)


@import_app.command("preview")
def import_preview(
    kind: str = typer.Argument(..., help="customers, jobs or prices."),
    path: Path = typer.Argument(..., help="The exported CSV."),
) -> None:
    """Read the file and say what an import would do. Writes nothing."""
    from .migration import PLANNERS

    planner = PLANNERS.get(kind)
    if planner is None:
        _fail(f"Unknown kind {kind!r}. One of: " + ", ".join(PLANNERS))
    try:
        plan = planner(path)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    _show_plan(plan)
    if plan.is_safe:
        console.print(f"[green]Run `profileos import {kind} {path}` to apply.[/green]")


def _run_import(kind: str, path: Path, yes: bool) -> None:
    from .migration import IMPORTERS, PLANNERS

    planner, importer = PLANNERS.get(kind), IMPORTERS.get(kind)
    if planner is None or importer is None:
        _fail(f"Unknown kind {kind!r}. One of: " + ", ".join(PLANNERS))
    try:
        plan = planner(path)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    _show_plan(plan)
    if plan.problems:
        raise typer.Exit(code=1)
    if not (plan.creates or plan.updates):
        console.print("[yellow]Nothing to import.[/yellow]")
        raise typer.Exit(code=1)

    # An import writes hundreds of records into the shop's own data. It asks.
    if not yes and not typer.confirm(
        f"Import {plan.creates} new and {plan.updates} updated {kind}?"
    ):
        console.print("[dim]Nothing was written.[/dim]")
        raise typer.Exit(code=1)

    result = importer(plan)
    console.print(
        f"[green]{result['created']} created, {result['updated']} updated"
        + (f", {result['failed']} failed" if result["failed"] else "")
        + "[/green]"
    )
    for row in plan.rows:
        if row.action != "skip" and row.reason and "קיים" not in row.reason:
            console.print(f"[yellow]{row.label}: {row.reason}[/yellow]")


@import_app.command("customers")
def import_customers_command(
    path: Path = typer.Argument(..., help="Customer list exported to CSV."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Bring a customer list across from whatever the shop uses now."""
    _run_import("customers", path, yes)


@import_app.command("jobs")
def import_jobs_command(
    path: Path = typer.Argument(..., help="Job list exported to CSV."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Bring the order book across. Jobs link to customers already imported."""
    _run_import("jobs", path, yes)


@import_app.command("prices")
def import_prices_command(
    path: Path = typer.Argument(..., help="Supplier price list as CSV."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Load a supplier's price list."""
    _run_import("prices", path, yes)


@hw_app.command("list")
def hardware_list(
    kind: str = typer.Option("", "--kind", "-k", help="hinge, roller, handle…"),
) -> None:
    """What the shop can fit, and what each item is rated to carry."""
    from .hardware import PartKind, default_library

    library = default_library()
    if not len(library):
        console.print("[yellow]The hardware library is empty.[/yellow]")
        console.print(
            "[dim]profileos hardware template -o hardware.json, fill in a "
            "supplier's load chart, then profileos hardware import[/dim]"
        )
        return

    parts = library.of_kind(PartKind(kind)) if kind else list(library)
    table = Table(title="Hardware", header_style="dim")
    table.add_column("Code", style="cyan")
    table.add_column("What")
    table.add_column("Maker")
    table.add_column("Max sash", justify="right")
    table.add_column("Max leaf", justify="right")
    table.add_column("Figures")
    for part in sorted(parts, key=lambda item: (item.kind.value, item.code)):
        table.add_row(
            part.code, part.hebrew, part.maker or "-",
            f"{part.max_sash_kg:,.0f} kg" if part.max_sash_kg else "-",
            (
                f"{part.max_width:,.0f} x {part.max_height:,.0f}"
                if part.max_width or part.max_height else "-"
            ),
            part.confidence.hebrew,
        )
    console.print(table)
    console.print(
        f"[dim]{len(library.rated())} of {len(library)} carry a "
        f"manufacturer's own figures; only those may be fitted to a load."
        "[/dim]"
    )


@hw_app.command("template")
def hardware_template(
    maker: str = typer.Option("", "--maker", "-m"),
    out: Optional[Path] = typer.Option(None, "--out", "-o"),
) -> None:
    """Write a blank hardware file to fill in from a supplier's load chart."""
    from .hardware import template as hardware_form

    destination = out or Path("hardware.json")
    destination.write_text(
        json.dumps(hardware_form(maker), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(f"[green]{destination}[/green] — fill it in, then:")
    console.print(f"  profileos hardware import {destination}")


@hw_app.command("import")
def hardware_import(
    path: Path = typer.Argument(..., help="A filled-in hardware file."),
) -> None:
    """Load a supplier's parts and ratings into the shop's library."""
    from .hardware import default_library
    from .hardware.library import _part_from

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _fail(f"Could not read {path}: {exc}")

    library = default_library()
    added, refused = 0, []
    for entry in raw.get("parts", []):
        try:
            library.add(_part_from(entry), save=False)
            added += 1
        except Exception as exc:  # noqa: BLE001 - name what was refused and why
            refused.append(f"{entry.get('code', '?')}: {exc}")
    library.save()

    console.print(f"[green]{added}[/green] part(s) in the library.")
    for reason in refused:
        console.print(f"[yellow]{reason}[/yellow]")


@hw_app.command("select")
def hardware_select(
    opening_type: str = typer.Argument(..., help="casement, tilt_turn, sliding…"),
    width: float = typer.Argument(..., help="Leaf width [mm]."),
    height: float = typer.Argument(..., help="Leaf height [mm]."),
    glass: float = typer.Option(25.0, "--glass", help="Glass mass [kg/m2]."),
) -> None:
    """Choose the hardware for one leaf, against what it actually weighs."""
    from .hardware import default_library

    library = default_library()
    try:
        selection = library.select_for(
            opening_type=opening_type, width=width, height=height,
            glass_mass_per_m2=glass,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(
        f"[cyan]Leaf {width:,.0f} x {height:,.0f} mm weighs "
        f"{selection.sash_mass:,.1f} kg[/cyan]"
    )
    if selection.parts:
        table = Table(header_style="dim")
        table.add_column("Code", style="cyan")
        table.add_column("What")
        table.add_column("Qty", justify="right")
        table.add_column("Figures")
        for part, quantity in selection.parts:
            table.add_row(
                part.code, part.describe(), str(quantity), part.confidence.hebrew
            )
        console.print(table)
        if selection.price:
            console.print(f"[dim]{selection.price:,.2f} per leaf[/dim]")

    for reason in selection.unmet:
        console.print(f"[red]{reason}[/red]")
    for warning in selection.warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    if selection.may_be_ordered:
        console.print("[green]Every load-bearing choice rests on a real chart.[/green]")


@systems_app.command("figures")
def systems_figures() -> None:
    """The numbers to read out of a supplier catalogue, in catalogue order."""
    from .systems import FIGURES

    table = Table(title="What a series needs to be cuttable", header_style="dim")
    table.add_column("Key", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Where to find it")
    table.add_column("Range", justify="right")
    table.add_column("Required", justify="right")
    for figure in FIGURES:
        table.add_row(
            figure.key, figure.hebrew, figure.where,
            f"{figure.minimum:g}-{figure.maximum:g}",
            "yes" if figure.required else "no",
        )
    console.print(table)
    console.print(
        "[dim]Eleven numbers, once per series. `profileos systems template "
        "<series> -o file.json` writes a form to fill in at the bench.[/dim]"
    )


@systems_app.command("template")
def systems_template(
    entry_id: str = typer.Argument(..., help="Series id, e.g. klil-7300."),
    out: Optional[Path] = typer.Option(None, "--out", "-o"),
) -> None:
    """Write a blank form to fill in from the catalogue."""
    from .systems import DIRECTORY, write_template

    entry = DIRECTORY.get(entry_id)
    if entry is None:
        _fail(
            f"No series {entry_id}. List them with `profileos systems list`."
        )
    destination = out or Path(f"{entry_id}-figures.json")
    write_template(entry_id, destination)
    console.print(f"[green]{destination}[/green] — fill it in and run:")
    console.print(f"  profileos systems confirm {destination}")


@systems_app.command("confirm")
def systems_confirm(
    path: Path = typer.Argument(..., help="A filled-in figures file."),
) -> None:
    """Confirm a series from its catalogue figures, so it may be cut to."""
    from .systems import DIRECTORY, default_confirmations, read_confirmation

    try:
        confirmation = read_confirmation(path)
    except Exception as exc:  # noqa: BLE001
        _fail(f"Could not read {path}: {exc}")

    problems = confirmation.problems()
    if problems:
        console.print("[yellow]Not confirmed yet:[/yellow]")
        for problem in problems:
            console.print(f"  · {problem}")
        raise typer.Exit(code=1)

    try:
        default_confirmations().record(confirmation)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    entry = DIRECTORY.get(confirmation.entry_id)
    readiness_note = DIRECTORY.readiness(confirmation.entry_id)
    console.print(
        f"[green]{entry.display if entry else confirmation.entry_id}[/green] "
        f"confirmed from {confirmation.source}"
    )
    console.print(
        f"[dim]may quote: {readiness_note.may_quote} · "
        f"may cut: {readiness_note.may_cut}[/dim]"
    )
    if readiness_note.may_cut:
        console.print(
            "[green]Cut sheets for this series no longer carry the "
            "not-for-production banner.[/green]"
        )


@systems_app.command("confirmed")
def systems_confirmed() -> None:
    """Every series the shop has entered supplier figures for."""
    from .systems import DIRECTORY, default_confirmations

    book = default_confirmations()
    if not len(book):
        console.print(
            "[yellow]No series confirmed yet — nothing here may be cut to.[/yellow]"
        )
        console.print(
            "[dim]profileos systems template <series> -o figures.json[/dim]"
        )
        return

    table = Table(title="Confirmed series", header_style="dim")
    table.add_column("Series", style="cyan")
    table.add_column("Source")
    table.add_column("Entered by")
    table.add_column("On")
    for confirmation in book:
        entry = DIRECTORY.get(confirmation.entry_id)
        table.add_row(
            entry.display if entry else confirmation.entry_id,
            confirmation.source, confirmation.entered_by or "-",
            confirmation.entered_on,
        )
    console.print(table)


@time_app.command("book")
def time_book(
    person: str = typer.Argument(..., help="Who did the work."),
    job_id: str = typer.Argument(..., help="Which job."),
    hours: str = typer.Argument(..., help="Hours, or a span like 07:30-16:15."),
    operation: str = typer.Option("", "--operation", "-o"),
    rate: float = typer.Option(0.0, "--rate", help="Hourly cost to the shop."),
    rework: bool = typer.Option(False, "--rework", help="Time spent doing it twice."),
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD; default today."),
) -> None:
    """Book hours against a job."""
    from datetime import date as _date

    from .erp.timesheets import default_timebook, minutes_between

    if "-" in hours and ":" in hours:
        start, _, end = hours.partition("-")
        minutes = minutes_between(start, end)
    else:
        try:
            minutes = int(round(float(hours) * 60))
        except ValueError:
            _fail(f"Could not read {hours!r} as hours or a span like 07:30-16:15.")

    book = default_timebook()
    try:
        entry = book.book(
            person, job_id, minutes, operation=operation, rate=rate,
            rework=rework, on=_date.fromisoformat(on) if on else None,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    console.print(f"[green]{entry.describe()}[/green]")
    console.print(f"[dim]{book.hours_on_job(job_id):.2f} hours on {job_id} so far[/dim]")


@time_app.command("job")
def time_job(
    job_id: str = typer.Argument(..., help="The job to look at."),
    estimate: float = typer.Option(0.0, "--estimate", help="Hours it was quoted at."),
) -> None:
    """Where the hours on one job went, and how that compares with the quote."""
    from .erp.timesheets import default_timebook

    book = default_timebook()
    if not book.for_job(job_id):
        console.print(f"[yellow]No hours booked against {job_id}.[/yellow]")
        return

    report = book.against_estimate(job_id, estimate)
    table = Table(title=f"{job_id} — hours", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value", justify="right")
    for label, value in (
        ("Booked", f"{report['actual_hours']:.2f} h"),
        ("Quoted at", f"{report['estimated_hours']:.2f} h" if estimate else "-"),
        ("Difference", f"{report['difference_hours']:+.2f} h" if estimate else "-"),
        ("Rework", f"{report['rework_pct']:.0f}%"),
        ("Cost", f"{book.cost_of_job(job_id):,.2f}"),
    ):
        table.add_row(label, value)
    console.print(table)

    operations = Table(header_style="dim")
    operations.add_column("Operation", style="cyan")
    operations.add_column("Hours", justify="right")
    for name, value in report["by_operation"].items():
        operations.add_row(name, f"{value:.2f}")
    console.print(operations)

    if estimate:
        console.print(f"[cyan]{report['verdict']}[/cyan]")


@time_app.command("week")
def time_week(
    start: str = typer.Option("", "--from", help="First day YYYY-MM-DD."),
) -> None:
    """Everybody's hours for a week."""
    from datetime import date as _date, timedelta

    from .erp.timesheets import default_timebook

    first = _date.fromisoformat(start) if start else _date.today() - timedelta(days=6)
    last = first + timedelta(days=6)
    book = default_timebook()
    totals = book.by_person(first, last)
    if not totals:
        console.print(f"[yellow]No hours booked {first} to {last}.[/yellow]")
        return

    table = Table(title=f"{first:%d/%m} - {last:%d/%m}", header_style="dim")
    table.add_column("Person", style="cyan")
    table.add_column("Hours", justify="right")
    for person, value in sorted(totals.items(), key=lambda pair: -pair[1]):
        table.add_row(person, f"{value:.2f}")
    console.print(table)
    console.print(f"[dim]{sum(totals.values()):.2f} hours in total[/dim]")


@fx_app.command("set")
def fx_set(
    currency: str = typer.Argument(..., help="EUR, USD, GBP."),
    per_unit: float = typer.Argument(..., help="Shekels for one unit."),
    source: str = typer.Option(..., "--source", help="Where the rate came from."),
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD; default today."),
) -> None:
    """Record an exchange rate, with the date and the source it came from."""
    from datetime import date as _date

    from .erp.currency import Rate, default_rates

    book = default_rates()
    try:
        rate = book.record(Rate(
            currency=currency.upper(), per_unit=per_unit,
            on=_date.fromisoformat(on) if on else _date.today(),
            source=source,
        ))
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    console.print(f"[green]{rate.describe()}[/green] — {rate.source}")


@fx_app.command("rates")
def fx_rates() -> None:
    """Every rate the shop has recorded."""
    from .erp.currency import CURRENCIES, default_rates

    book = default_rates()
    if not len(book):
        console.print("[yellow]No exchange rates recorded.[/yellow]")
        console.print("[dim]profileos fx set EUR 4.05 --source 'bank'[/dim]")
        return

    table = Table(title="Exchange rates", header_style="dim")
    table.add_column("Currency", style="cyan")
    table.add_column("Shekels per unit", justify="right")
    table.add_column("On")
    table.add_column("Age", justify="right")
    table.add_column("Source")
    for currency in book.currencies():
        rate = book.latest(currency)
        age = rate.age()
        table.add_row(
            f"{currency} — {CURRENCIES.get(currency, '')}",
            f"{rate.per_unit:.4f}", rate.on.strftime("%d/%m/%Y"),
            f"[yellow]{age} d[/yellow]" if rate.is_stale() else f"{age} d",
            rate.source or "[yellow]not recorded[/yellow]",
        )
    console.print(table)


@fx_app.command("convert")
def fx_convert(
    amount: float = typer.Argument(..., help="How much."),
    currency: str = typer.Argument(..., help="In which currency."),
    on: str = typer.Option("", "--on", help="At the rate of this date."),
) -> None:
    """Convert a figure into shekels at the rate that applied on a date."""
    from datetime import date as _date

    from .erp.currency import default_rates

    conversion = default_rates().convert(
        amount, currency.upper(), on=_date.fromisoformat(on) if on else None
    )
    console.print(f"[cyan]{conversion.describe()}[/cyan]")
    for warning in conversion.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@backup_app.command("write")
def backup_write(
    destination: Optional[Path] = typer.Argument(None, help="Folder or .zip path."),
    keep: int = typer.Option(14, "--keep", help="How many backups to keep there."),
) -> None:
    """Write one dated copy of everything the shop's folder holds."""
    from .core.backup import (
        default_backup_folder, prune, read_manifest, write_backup,
    )

    folder = destination or default_backup_folder()
    try:
        archive = write_backup(folder)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    manifest = read_manifest(archive)
    console.print(f"[green]{archive}[/green]")
    console.print(f"[dim]{manifest.describe()}[/dim]")

    table = Table(header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Count", justify="right")
    for label, value in manifest.summary_rows():
        table.add_row(label, value)
    console.print(table)

    if keep > 0 and archive.parent.is_dir():
        removed = prune(archive.parent, keep=keep)
        if removed:
            console.print(f"[dim]Removed {len(removed)} older backup(s).[/dim]")


@backup_app.command("list")
def backup_list(
    folder: Optional[Path] = typer.Argument(None, help="Where the backups are."),
) -> None:
    """Every backup in a folder, newest first."""
    from .core.backup import default_backup_folder, list_backups

    where = folder or default_backup_folder()
    if not Path(where).is_dir():
        console.print(f"[yellow]No backup folder at {where}.[/yellow]")
        console.print("[dim]profileos backup write[/dim]")
        return

    found = list_backups(where)
    if not found:
        console.print(f"[yellow]No backups in {where}.[/yellow]")
        return

    table = Table(title=f"Backups in {where}", header_style="dim")
    table.add_column("File", style="cyan")
    table.add_column("Created")
    table.add_column("Version")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for path, manifest in found:
        table.add_row(
            path.name, manifest.created[:16].replace("T", " "), manifest.version,
            str(manifest.files), f"{manifest.bytes / 1_048_576:.1f} MB",
        )
    console.print(table)


@backup_app.command("restore")
def backup_restore(
    archive: Path = typer.Argument(..., help="The backup to restore."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Restore a backup, keeping the current folder aside rather than deleting it."""
    from .core.backup import plan_restore, restore as run_restore

    try:
        plan = plan_restore(archive)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(f"[cyan]{plan.describe()}[/cyan]")
    table = Table(header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Now", justify="right")
    table.add_column("In the backup", justify="right")
    for label, current, inside in plan.comparison():
        table.add_row(label, current, inside)
    console.print(table)
    for warning in plan.warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    if not yes and not typer.confirm("Restore this backup?"):
        console.print("[dim]Nothing was changed.[/dim]")
        raise typer.Exit(code=1)

    root, aside = run_restore(archive)
    console.print(f"[green]Restored into {root}[/green]")
    if aside:
        console.print(
            f"[dim]The folder that was there is at {aside} — move it back to "
            f"undo this.[/dim]"
        )


@glass_app.command("order")
def glass_order(
    job_id: str = typer.Argument(..., help="Which job's glazing to order."),
    supplier: str = typer.Option("", "--supplier", help="Which glazier."),
    wanted_by: str = typer.Option("", "--by", help="Wanted by YYYY-MM-DD."),
    out: Path = typer.Option(
        Path("glass-order.html"), "--out", "-o", help="Where to write it."
    ),
    provisional: bool = typer.Option(
        False, "--provisional",
        help="The series figures behind these sizes are not confirmed.",
    ),
) -> None:
    """The order to the glazier: sizes, make-up, toughening and mass."""
    from datetime import date as _date

    from .elements import build_elements
    from .glazing.order import order_from_builds, write_glass_order
    from .projects import default_store

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    if job.schedule is None:
        _fail(f"Job {job_id} has no openings scheduled.")

    order = order_from_builds(
        build_elements(job.schedule.openings),
        job_id=job.job_id, job_name=job.name, supplier=supplier,
        wanted_by=_date.fromisoformat(wanted_by) if wanted_by else None,
        sizes_confirmed=not provisional,
        provisional_reason="נתוני הסדרה לא אושרו" if provisional else "",
    )
    write_glass_order(order, out)
    console.print(f"[green]{out}[/green]")
    console.print(f"[dim]{order.describe()}[/dim]")

    table = Table(header_style="dim")
    for heading in ("Build-up", "Panes", "m2", "kg"):
        table.add_column(heading)
    for row in order.by_build_up():
        table.add_row(
            row["build_up"], str(row["panes"]),
            f"{row['area']:.3f}", f"{row['mass']:.1f}",
        )
    console.print(table)
    for problem in order.problems():
        console.print(f"[yellow]{problem}[/yellow]")


# --------------------------------------------------------------------------- #
# Site measurement
# --------------------------------------------------------------------------- #
@measure_app.command("open")
def measure_open(
    job_id: str = typer.Argument(..., help="Which job to measure."),
    clearance: float = typer.Option(
        0.0, "--clearance", help="Fitting clearance per side [mm], from the system."
    ),
    sheet: Optional[Path] = typer.Option(
        None, "--sheet", help="Write the blank site sheet here as CSV."
    ),
) -> None:
    """Open a measurement sheet with a blank line per opening in the job."""
    import csv

    from .delivery.survey import Survey, default_surveys, survey_for_job
    from .projects import default_store

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    survey = survey_for_job(job, clearance_per_side=clearance or None)
    if not len(survey):
        _fail(f"Job {job_id} has no openings scheduled to measure.")
    default_surveys().add(survey)

    console.print(f"[green]{survey.survey_id}[/green] — {len(survey)} openings")
    if clearance <= 0:
        console.print(
            "[yellow]No fitting clearance given, so no frame size will be "
            "derived. It is a figure from the system's own catalogue.[/yellow]"
        )
    if sheet is not None:
        with Path(sheet).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(Survey.SHEET_HEADERS)
            writer.writerows(survey.sheet_rows())
        console.print(f"[dim]{sheet}[/dim]")


@measure_app.command("enter")
def measure_enter(
    survey_id: str = typer.Argument(..., help="Which sheet."),
    reference: str = typer.Argument(..., help="Which opening."),
    widths: str = typer.Option(
        ..., "--widths", help="Head, middle and sill, e.g. 1502,1496,1488."
    ),
    heights: str = typer.Option(
        ..., "--heights", help="Left, middle and right."
    ),
    diagonals: str = typer.Option(
        "", "--diagonals", help="Both diagonals, e.g. 1921,1939."
    ),
    by: str = typer.Option("", "--by"),
) -> None:
    """Write one opening's measurements onto the sheet."""
    from datetime import date as _date

    from .delivery.survey import default_surveys

    def numbers(text: str, wanted: int, what: str) -> list[float]:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        try:
            values = [float(part) for part in parts]
        except ValueError:
            _fail(f"Could not read {what} from {text!r}.")
        if values and len(values) != wanted:
            _fail(f"Give {wanted} figures for {what}, not {len(values)}.")
        return values

    book = default_surveys()
    survey = book.get(survey_id)
    entry = survey.opening(reference)

    three = numbers(widths, 3, "widths")
    entry.width_head, entry.width_middle, entry.width_sill = three
    three = numbers(heights, 3, "heights")
    entry.height_left, entry.height_middle, entry.height_right = three
    if diagonals:
        entry.diagonal_a, entry.diagonal_b = numbers(diagonals, 2, "diagonals")
    entry.measured_by = by
    entry.measured_on = _date.today()
    book.save()

    console.print(entry.describe())
    for problem in entry.problems():
        console.print(f"[yellow]{problem}[/yellow]")


@measure_app.command("show")
def measure_show(
    survey_id: str = typer.Argument(..., help="Which sheet."),
) -> None:
    """What the sheet says, and what still stands between it and a saw."""
    from .delivery.survey import default_surveys

    survey = default_surveys().get(survey_id)
    console.print(survey.describe())

    table = Table(header_style="dim")
    for heading in ("Mark", "Width", "Height", "Diagonals", "Frame", "By", "State"):
        table.add_column(heading)
    for entry in survey:
        frame = entry.frame_size()
        square = entry.out_of_square
        table.add_row(
            entry.reference or "—",
            f"{entry.smallest_width:g}" if entry.smallest_width else "—",
            f"{entry.smallest_height:g}" if entry.smallest_height else "—",
            f"{square:g}" if square is not None else "—",
            f"{frame[0]:g}×{frame[1]:g}" if frame else "—",
            entry.measured_by or "—",
            "ready" if entry.may_be_made
            else ("unmeasured" if not entry.is_measured else "check"),
        )
    console.print(table)
    for problem in survey.problems():
        console.print(f"[yellow]{problem}[/yellow]")


@deliver_app.command("handover")
def deliver_handover(
    job_id: str = typer.Argument(..., help="Which job is being handed over."),
    out: Path = typer.Option(
        Path("handover.html"), "--out", "-o", help="Where to write the pack."
    ),
    on: str = typer.Option("", "--on", help="Handover date YYYY-MM-DD."),
    profile_months: int = typer.Option(
        0, "--profile-months", help="Warranty on the aluminium."
    ),
    finish_months: int = typer.Option(0, "--finish-months"),
    glass_months: int = typer.Option(0, "--glass-months"),
    hardware_months: int = typer.Option(0, "--hardware-months"),
    sealing_months: int = typer.Option(0, "--sealing-months"),
    installation_months: int = typer.Option(0, "--installation-months"),
) -> None:
    """The handover pack: what was fitted, how to look after it, and the warranty.

    Warranty periods are the shop's own promise. Any left at zero print as
    "not stated" rather than as a figure this software made up.
    """
    from datetime import date as _date

    from .delivery.handover import Cover, pack_from_job, write_handover
    from .elements import build_elements
    from .projects import default_store

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    if job.schedule is None:
        _fail(f"Job {job_id} has no openings scheduled.")

    months = {
        cover: value
        for cover, value in (
            (Cover.PROFILE, profile_months),
            (Cover.FINISH, finish_months),
            (Cover.GLASS, glass_months),
            (Cover.HARDWARE, hardware_months),
            (Cover.SEALING, sealing_months),
            (Cover.INSTALLATION, installation_months),
        )
        if value > 0
    }

    from .branding import active_brand

    brand = active_brand()
    pack = pack_from_job(
        job,
        builds=build_elements(job.schedule.openings),
        handed_over_on=_date.fromisoformat(on) if on else _date.today(),
        warranty_months=months,
        service_contact=" · ".join(
            part for part in (brand.display_name, getattr(brand, "phone", ""))
            if part
        ),
    )
    write_handover(pack, out)
    console.print(f"[green]{out}[/green]")
    console.print(f"[dim]{pack.describe()}[/dim]")
    for entry in pack.warranties:
        console.print(entry.describe())
    for problem in pack.problems():
        console.print(f"[yellow]{problem}[/yellow]")


@erp_app.command("po")
def erp_purchase_order(
    order_id: str = typer.Argument(..., help="Which purchase order."),
    supplier: str = typer.Option("", "--supplier", help="The supplier's name."),
    alloy: str = typer.Option("", "--alloy", help="e.g. 6063."),
    temper: str = typer.Option("", "--temper", help="e.g. T6."),
    mill_length: float = typer.Option(
        0.0, "--mill-length", help="Bar length ordered [mm]."
    ),
    finish: str = typer.Option("", "--finish"),
    price_source: str = typer.Option(
        "", "--price-source", help="Where the prices came from."
    ),
    out: Path = typer.Option(
        Path("purchase-order.html"), "--out", "-o"
    ),
) -> None:
    """Print a purchase order the way the supplier receives it.

    The extrusion specification matters: a supplier sent a code and a quantity
    sends whatever that code means in their catalogue. Anything not given here
    is printed on the order as a question rather than left blank.
    """
    from .erp import company_for_brand
    from .erp.po_document import (
        Specification, document_from_order, write_purchase_order,
    )

    company = company_for_brand()
    orders = getattr(company, "purchase_orders", None) or {}
    order = orders.get(order_id) if isinstance(orders, dict) else None
    if order is None:
        _fail(f"No purchase order {order_id!r} in this installation.")

    spec = Specification(
        alloy=alloy, temper=temper,
        mill_length=mill_length or None, finish=finish,
    )
    codes = [line.item for line in order.lines]
    document = document_from_order(
        order, supplier_name=supplier,
        specifications={code: spec for code in codes} if alloy or temper
        or mill_length else None,
        price_sources=(
            {code: price_source for code in codes} if price_source else None
        ),
    )
    write_purchase_order(document, out)
    console.print(f"[green]{out}[/green]")
    console.print(f"[dim]{document.describe()}[/dim]")
    for problem in document.problems():
        console.print(f"[yellow]{problem}[/yellow]")


@element_app.command("shape")
def element_shape(
    shape: str = typer.Argument(
        ..., help="rectangle | raked | triangle | arched | half_round | circle"
    ),
    width: float = typer.Argument(..., help="Width [mm]."),
    height: float = typer.Argument(..., help="Height [mm]; left height if raked."),
    height_right: float = typer.Option(
        0.0, "--height-right", help="Right-hand height of a raked opening [mm]."
    ),
    rise: float = typer.Option(
        0.0, "--rise", help="Arch rise above the springing [mm]."
    ),
    min_radius: float = typer.Option(
        0.0, "--min-radius", help="Smallest radius the profile bends to [mm]."
    ),
    grip: float = typer.Option(
        0.0, "--grip", help="Straight length the bender grips at each end [mm]."
    ),
    source: str = typer.Option(
        "", "--bend-source", help="Where the bending figures came from."
    ),
    min_mitre: float = typer.Option(
        22.5, "--min-mitre", help="Smallest angle the saw will swing [deg]."
    ),
) -> None:
    """Work out a shaped opening: real area, members, corners and cuts."""
    from .elements.shapes import Bending, outline

    bending = Bending(
        minimum_radius=min_radius or None,
        grip_allowance=grip if grip or min_radius else None,
        source=source,
    )
    try:
        shaped = outline(
            shape, width=width, height=height,
            height_right=height_right or None, rise=rise or None,
            bending=bending, min_mitre=min_mitre,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(shaped.describe())

    table = Table(header_style="dim")
    for heading in ("What", "Length", "Start", "End", "Radius", "Note"):
        table.add_column(heading)
    for member in shaped.members:
        table.add_row(
            member.role,
            f"{member.length:.1f}" if member.is_orderable else "—",
            "—" if member.is_curved else f"{member.angle_start:g}",
            "—" if member.is_curved else f"{member.angle_end:g}",
            f"{member.radius:.0f}" if member.radius else "—",
            member.note,
        )
    console.print(table)

    corners = Table(header_style="dim")
    for heading in ("Corner", "Included", "Mitre each side"):
        corners.add_column(heading)
    for corner in shaped.corners:
        corners.add_row(
            corner.name, f"{corner.included:.2f}", f"{corner.mitre:.2f}"
        )
    if shaped.corners:
        console.print(corners)

    for warning in shaped.warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    if not shaped.is_orderable:
        console.print(
            "[red]Not orderable: a bar cut to a guessed developed length is a "
            "bar in the skip.[/red]"
        )


@app.command()
def ifc(
    job_id: str = typer.Argument(..., help="Which job's openings to export."),
    out: Path = typer.Option(
        Path("model.ifc"), "--out", "-o", help="Where to write the model."
    ),
    storey: str = typer.Option("Ground floor", "--storey"),
    elevation: float = typer.Option(
        0.0, "--elevation", help="Storey level above the datum [mm]."
    ),
    properties: bool = typer.Option(
        True, "--properties/--no-properties",
        help="Attach the system, glazing and U-value as a property set.",
    ),
) -> None:
    """Export the openings as IFC2x3, for the architect's own software."""
    from .elements import build_elements
    from .exchange.ifc import LIMITATIONS_HE, IfcOptions, write_ifc
    from .projects import default_store

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    if job.schedule is None:
        _fail(f"Job {job_id} has no openings scheduled.")

    target = write_ifc(
        build_elements(job.schedule.openings), out,
        IfcOptions(
            project_name=job.name or job.job_id,
            site_name=job.site_address or "Site",
            storey_name=storey, storey_elevation=elevation,
            include_properties=properties,
        ),
    )
    console.print(f"[green]{target}[/green]")
    for limit in LIMITATIONS_HE:
        console.print(f"[dim]{limit}[/dim]")


# --------------------------------------------------------------------------- #
# Follow-ups
# --------------------------------------------------------------------------- #
@task_app.command("today")
def task_today(
    person: str = typer.Option("", "--person", help="Only this person's list."),
    week: bool = typer.Option(False, "--week", help="The next seven days too."),
) -> None:
    """What has to be done, oldest first."""
    from .projects.followups import default_tasks

    book = default_tasks()
    tasks = (
        book.for_person(person) if person
        else (book.this_week() if week else book.due_by())
    )
    if not tasks:
        console.print("[green]Nothing outstanding.[/green]")
        return

    table = Table(header_style="dim")
    for heading in ("Due", "Kind", "About", "What", "Who", "State"):
        table.add_column(heading)
    for task in tasks:
        table.add_row(
            task.due.isoformat(), task.kind.value,
            task.subject_name or task.about or "—", task.what,
            task.assigned_to or "—",
            f"{task.days_late()}d late" if task.is_overdue() else "open",
        )
    console.print(table)

    summary = book.summary()
    console.print(
        f"[dim]{summary['open']} open · {summary['overdue']} overdue[/dim]"
    )


@task_app.command("chase")
def task_chase(
    job_id: str = typer.Argument(..., help="The job whose quotation went out."),
    by: str = typer.Option("", "--by", help="Who is chasing it."),
    sent_on: str = typer.Option("", "--sent", help="When it was sent."),
) -> None:
    """Schedule the follow-up touches for a quotation that has gone out."""
    from datetime import date as _date

    from .projects import default_store
    from .projects.followups import default_tasks

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    made = default_tasks().chase_quote(
        job, sent_on=_date.fromisoformat(sent_on) if sent_on else None,
        assigned_to=by,
    )
    if not made:
        console.print("[dim]This quotation already has open follow-ups.[/dim]")
        return
    for task in made:
        console.print(task.describe())


@task_app.command("add")
def task_add(
    what: str = typer.Argument(..., help="What has to be done."),
    about: str = typer.Option("", "--about", help="job:2026-114 or customer:C-1."),
    kind: str = typer.Option("other", "--kind"),
    due: str = typer.Option("", "--due", help="YYYY-MM-DD; default today."),
    by: str = typer.Option("", "--by", help="Who is doing it."),
) -> None:
    """Add one thing to the list."""
    from datetime import date as _date

    from .projects.followups import Kind, default_tasks

    try:
        which = Kind(kind)
    except ValueError:
        _fail(f"Unknown kind {kind!r}; one of: " + ", ".join(k.value for k in Kind))

    task = default_tasks().create(
        which, what, about=about,
        due=_date.fromisoformat(due) if due else None,
        assigned_to=by,
    )
    console.print(f"[green]{task.task_id}[/green] {task.describe()}")


@task_app.command("close")
def task_close(
    task_id: str = typer.Argument(..., help="Which task."),
    outcome: str = typer.Option("done", "--outcome"),
    result: str = typer.Option("", "--result", help="What actually happened."),
) -> None:
    """Close a task with what happened, which is worth more than a tick."""
    from .projects.followups import Outcome, default_tasks

    try:
        how = Outcome(outcome)
    except ValueError:
        _fail(
            f"Unknown outcome {outcome!r}; one of: "
            + ", ".join(o.value for o in Outcome if o is not Outcome.OPEN)
        )
    try:
        task = default_tasks().close(task_id, how, result=result)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    console.print(f"[green]{task.describe()}[/green]")
    if task.closed_silently:
        console.print(
            "[dim]Closed without a note. Allowed, but next year nobody will "
            "know what was said.[/dim]"
        )


@task_app.command("forgotten")
def task_forgotten() -> None:
    """Quotations sitting with a customer that nobody is chasing."""
    from .projects import default_store
    from .projects.followups import default_tasks

    jobs = list(default_store().all())
    forgotten = default_tasks().unchased_quotes(jobs)
    if not forgotten:
        console.print("[green]Every open quotation has a follow-up.[/green]")
        return

    table = Table(header_style="dim")
    for heading in ("Job", "Customer", "Value", "Quoted"):
        table.add_column(heading)
    for job in forgotten:
        table.add_row(
            job.job_id, job.customer_name or "—",
            f"{job.quote_total:,.0f}" if job.quote_total else "—",
            job.quoted_on[:10] if job.quoted_on else "—",
        )
    console.print(table)
    console.print(
        "[yellow]A shop that never sets these concludes its prices are too "
        "high.[/yellow]"
    )


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
@template_app.command("list")
def template_list(
    search: str = typer.Argument("", help="Words from the name, in any order."),
) -> None:
    """Every saved configuration, most used first."""
    from .projects.templates import default_templates

    book = default_templates()
    found = book.search(search) if search else book.popular()
    if not found:
        console.print("[dim]No templates saved yet.[/dim]")
        return

    table = Table(header_style="dim")
    for heading in ("Id", "Name", "System", "Used", "Price/m2", "Priced"):
        table.add_column(heading)
    for template in found:
        age = template.price_age_days
        table.add_row(
            template.template_id, template.name, template.system_id,
            str(template.times_used),
            f"{template.last_price_per_m2:,.0f}"
            if template.last_price_per_m2 else "—",
            "—" if age is None else (
                f"{age}d{' STALE' if template.price_is_stale else ''}"
            ),
        )
    console.print(table)


@template_app.command("save")
def template_save(
    job_id: str = typer.Argument(..., help="Job holding the opening."),
    mark: str = typer.Argument(..., help="Which opening in it."),
    name: str = typer.Option("", "--name", help="What to call the template."),
    price: float = typer.Option(0.0, "--price", help="Charged per square metre."),
) -> None:
    """Save one of a job's openings as a template."""
    from .projects import default_store
    from .projects.templates import default_templates, template_from_opening

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    if job.schedule is None:
        _fail(f"Job {job_id} has no openings.")

    for opening in job.schedule.openings:
        if opening.name == mark or opening.reference == mark:
            break
    else:
        _fail(f"No opening {mark!r} in {job_id}.")

    book = default_templates()
    template = book.add(template_from_opening(
        opening, name=name or opening.name, from_job=job.job_id,
        price_per_m2=price or None,
    ))
    console.print(f"[green]{template.template_id}[/green] {template.describe()}")


@template_app.command("use")
def template_use(
    template_id: str = typer.Argument(..., help="Which template."),
    width: float = typer.Argument(..., help="Width [mm]."),
    height: float = typer.Argument(..., help="Height [mm]."),
) -> None:
    """Build an opening from a template at a new size."""
    from .projects.templates import default_templates

    book = default_templates()
    try:
        opening = book.use(template_id, width, height)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(
        f"[green]{opening.name}[/green] {opening.width:g}×{opening.height:g} "
        f"{opening.system_id}"
    )
    if opening.mullion_positions:
        console.print(
            "[dim]mullions at "
            + ", ".join(f"{p:g}" for p in opening.mullion_positions)
            + "[/dim]"
        )
    template = book.get(template_id)
    if template.price_is_stale and template.last_price_per_m2:
        console.print(f"[yellow]{template.price_line()}[/yellow]")


@template_app.command("reprice")
def template_reprice(
    template_id: Optional[str] = typer.Argument(None, help="Which template."),
    price: float = typer.Option(0.0, "--price", help="New price per square metre."),
) -> None:
    """Update a template's remembered price, or list what needs it."""
    from datetime import date as _date

    from .projects.templates import default_templates

    book = default_templates()
    if template_id is None:
        stale = book.needing_repricing()
        if not stale:
            console.print("[green]Every priced template is current.[/green]")
            return
        for template in stale:
            console.print(f"{template.template_id} {template.describe()}")
        return

    if price <= 0:
        _fail("Give a price per square metre with --price.")
    template = book.get(template_id)
    template.last_price_per_m2 = price
    template.priced_on = _date.today()
    book.save()
    console.print(f"[green]{template.describe()}[/green]")


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
@audit_app.command("show")
def audit_show(
    subject: Optional[str] = typer.Option(
        None, "--subject", help="Only this record, e.g. quote:2026-114."
    ),
    person: Optional[str] = typer.Option(None, "--person"),
    limit: int = typer.Option(40, "--limit"),
) -> None:
    """What has been changed, newest first."""
    from .core.audit import audit

    log = audit()
    if subject:
        entries = list(reversed(log.for_subject(subject)))
    elif person:
        entries = list(reversed(log.by_person(person)))
    else:
        entries = log.recent(limit)

    if not entries:
        console.print("[dim]Nothing recorded yet.[/dim]")
        return
    for entry in entries[:limit]:
        console.print(entry.describe())


@audit_app.command("verify")
def audit_verify() -> None:
    """Walk the chain and say whether a line has been removed or edited."""
    from .core.audit import audit

    result = audit().verify()
    console.print(result.describe())
    if not result.ok:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
@app.command()
def labels(
    source: Optional[str] = typer.Argument(
        None, help="Job id whose pieces to label."
    ),
    out: Path = typer.Option(
        Path("labels.html"), "--out", "-o", help="Where to write the sheet."
    ),
    stock: str = typer.Option(
        "a4-24", "--stock", help="Which label sheet; see --list-stock."
    ),
    start_at: int = typer.Option(
        0, "--start-at", help="Skip this many places on the first sheet."
    ),
    list_stock: bool = typer.Option(
        False, "--list-stock", help="Show the label sheets this knows."
    ),
) -> None:
    """One label per piece: job, position, length, end cuts and a barcode."""
    from .elements import build_elements
    from .mes import work_order_from_builds
    from .mes.labels import STOCKS, labels_for_order, write_labels
    from .projects import default_store

    if list_stock:
        table = Table(header_style="dim")
        for heading in ("Key", "Sheet", "Per sheet"):
            table.add_column(heading)
        for key, sheet in sorted(STOCKS.items()):
            table.add_row(key, sheet.name, str(sheet.per_sheet))
        console.print(table)
        return

    if source is None:
        _fail("Name a job id, or pass --list-stock to see the sheets.")

    try:
        job = default_store().load(str(source))
    except Exception:  # noqa: BLE001
        job = None
    if job is None or job.schedule is None:
        _fail(f"No job schedule found for {source}.")

    builds = build_elements(job.schedule.openings)
    order = work_order_from_builds(builds, project_id=job.job_id, name=job.name)
    pieces = labels_for_order(order)
    if not pieces:
        _fail("That job has no pieces to label.")

    try:
        run = write_labels(pieces, out, stock=stock, start_at=start_at)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    console.print(f"[green]{out}[/green]")
    console.print(f"[dim]{run.describe()}[/dim]")
    for warning in run.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


# --------------------------------------------------------------------------- #
# Stocktake
# --------------------------------------------------------------------------- #
def _stock_ledger():
    """The shop's stock book, however this installation holds it."""
    from .erp import company_for_brand

    company = company_for_brand()
    ledger = getattr(company, "stock", None)
    if ledger is None:
        _fail("This installation has no stock book yet.")
    return company, ledger


@count_app.command("open")
def stocktake_open(
    scope: str = typer.Option("", "--scope", help="What this count covers."),
    by: str = typer.Option("", "--by", help="Who opened it."),
    sheet: Optional[Path] = typer.Option(
        None, "--sheet", help="Write the count sheet here as CSV."
    ),
) -> None:
    """Freeze what the book claims, as a sheet to carry to the racks."""
    import csv

    from .erp.stocktake import default_stocktakes, open_stocktake

    _company, ledger = _stock_ledger()
    take = open_stocktake(ledger, scope=scope, by=by)
    default_stocktakes().add(take)

    console.print(f"[green]{take.sheet_id}[/green] — {len(take)} lines")
    if sheet is not None:
        # windows-1255 with a BOM-less UTF-8 fallback: the sheet gets opened in
        # whatever the office has, and a count sheet full of question marks is
        # a count that does not happen.
        with Path(sheet).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(take.COUNT_SHEET_HEADERS)
            writer.writerows(take.count_sheet_rows())
        console.print(f"[dim]{sheet}[/dim]")


@count_app.command("enter")
def stocktake_enter(
    sheet_id: str = typer.Argument(..., help="Which sheet."),
    code: str = typer.Argument(..., help="Item code."),
    counted: float = typer.Argument(..., help="What was actually found."),
    by: str = typer.Option("", "--by"),
) -> None:
    """Write a counted quantity against one line."""
    from .erp.stocktake import default_stocktakes

    book = default_stocktakes()
    take = book.get(sheet_id)
    try:
        line = take.enter(code, counted, by=by)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    book.save()
    console.print(line.describe())


@count_app.command("show")
def stocktake_show(
    sheet_id: str = typer.Argument(..., help="Which sheet."),
) -> None:
    """What the sheet says so far."""
    from .erp.stocktake import default_stocktakes

    take = default_stocktakes().get(sheet_id)
    console.print(take.describe())

    table = Table(header_style="dim")
    for heading in ("Code", "Book", "Counted", "Difference", "Value"):
        table.add_column(heading, justify="right" if heading != "Code" else "left")
    for line in take.differences:
        table.add_row(
            line.code, f"{line.book:g}", f"{line.counted:g}",
            f"{line.difference:+g}", f"{line.value_difference:,.2f}",
        )
    if take.differences:
        console.print(table)
    for warning in take.warnings():
        console.print(f"[yellow]{warning}[/yellow]")


@count_app.command("post")
def stocktake_post(
    sheet_id: str = typer.Argument(..., help="Which sheet."),
    by: str = typer.Option("", "--by"),
    yes: bool = typer.Option(False, "--yes", help="Post without confirming."),
) -> None:
    """Bring the book into line with what was counted. Counted lines only."""
    from .erp.stocktake import default_stocktakes

    book = default_stocktakes()
    take = book.get(sheet_id)
    for warning in take.warnings():
        console.print(f"[yellow]{warning}[/yellow]")
    if not yes and not typer.confirm(
        f"Post {len(take.counted)} counted lines, {take.net_value:,.2f} value change?"
    ):
        raise typer.Abort()

    _company, ledger = _stock_ledger()
    try:
        movements = take.post(ledger, by=by)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    book.save()
    console.print(
        f"[green]{len(movements)} movements written, "
        f"{take.net_value:,.2f} value change[/green]"
    )


@count_app.command("list")
def stocktake_list() -> None:
    """Every count sheet, newest first."""
    from .erp.stocktake import default_stocktakes

    book = default_stocktakes()
    if not len(book):
        console.print("[dim]No stocktakes yet.[/dim]")
        return
    table = Table(header_style="dim")
    for heading in ("Sheet", "Opened", "Scope", "State", "Counted", "Accuracy"):
        table.add_column(heading)
    for take in book:
        table.add_row(
            take.sheet_id, take.opened.isoformat(), take.scope or "—",
            take.status.value, f"{len(take.counted)}/{len(take)}",
            f"{take.accuracy:.0f}%" if take.counted else "—",
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def _all_jobs():
    from .projects import default_store

    return list(default_store().all())


@report_app.command("sales")
def report_sales(
    of_year: int = typer.Option(0, "--year", help="Which year; default this one."),
) -> None:
    """Quoted, ordered and lost, with the win rate both ways."""
    from datetime import date as _date

    from . import reports

    which = of_year or _date.today().year
    report = reports.sales(_all_jobs(), reports.year(which))
    console.print(report.describe())

    table = Table(header_style="dim")
    for heading in ("What", "Jobs", "Value"):
        table.add_column(heading)
    for row in report.rows():
        table.add_row(*row)
    console.print(table)

    for figure in (report.win_rate(), report.value_win_rate(), report.average_order()):
        console.print(f"[cyan]{figure.label}[/cyan]: {figure.format()}")
    for warning in report.warnings():
        console.print(f"[yellow]{warning}[/yellow]")


@report_app.command("months")
def report_months(
    of_year: int = typer.Option(0, "--year"),
) -> None:
    """A year as twelve rows, so it reads as a shape rather than a total."""
    from datetime import date as _date

    from . import reports

    which = of_year or _date.today().year
    table = Table(header_style="dim", title=f"{which}")
    for heading in ("Month", "Quoted", "Value", "Won", "Value", "Win %"):
        table.add_column(heading)
    for row in reports.by_month(_all_jobs(), which):
        table.add_row(
            row["label"], str(row["quoted"]), f"{row['quoted_value']:,.0f}",
            str(row["won"]), f"{row['won_value']:,.0f}",
            f"{row['win_rate_pct']:.0f}",
        )
    console.print(table)


@report_app.command("customers")
def report_customers(
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Every customer, best first by what they have actually ordered."""
    from . import reports

    lines = reports.by_customer(_all_jobs())
    if not lines:
        console.print("[dim]No customers yet.[/dim]")
        return
    table = Table(header_style="dim")
    for heading in ("Customer", "Jobs", "Won", "Lost", "Value", "Win %", "Margin"):
        table.add_column(heading)
    for line in lines[:limit]:
        table.add_row(
            line.name or "—", str(line.jobs), str(line.won), str(line.lost),
            f"{line.value:,.0f}", f"{line.win_rate:.0f}",
            "—" if line.margin is None else f"{line.margin:.1f}%",
        )
    console.print(table)
    console.print(
        "[dim]A margin of — means nothing was ever costed against that "
        "customer's jobs, which is not the same as no margin.[/dim]"
    )


@report_app.command("pipeline")
def report_pipeline() -> None:
    """Live work by stage, and what has passed its date."""
    from . import reports

    live = reports.pipeline(_all_jobs())
    console.print(live.describe())

    table = Table(header_style="dim")
    for heading in ("Stage", "Jobs", "Value"):
        table.add_column(heading)
    for row in live.rows():
        table.add_row(*row)
    console.print(table)

    for job_id, name, days in live.overdue[:10]:
        console.print(f"[red]{job_id} {name} — {days} days late[/red]")
    for job_id, name, days in live.due_this_week[:10]:
        console.print(f"[yellow]{job_id} {name} — due in {days} days[/yellow]")
    if live.undated:
        console.print(f"[dim]{live.undated} open jobs have no due date.[/dim]")


@app.command()
def reset(
    yes: bool = typer.Option(
        False, "--yes", help="Clear it without asking. For scripts, not for shops."
    ),
) -> None:
    """Clear the sample jobs and customers `seed` put in, before real work starts.

    `seed` exists so a brand-new installation is not an empty screen — it
    writes a handful of realistic-looking jobs, customers and figures. Real
    ones, exactly because they look real, are the wrong thing to still be on
    screen the first time an actual customer looks at it. This removes them
    and nothing else: confirmed series, hardware, licensing and the audit
    trail are untouched.
    """
    from .projects import default_customers, default_store

    store, book = default_store(), default_customers()
    jobs = store.all()
    customers = book.all()
    if not jobs and not customers:
        console.print("[dim]Nothing to clear — no jobs or customers on file.[/dim]")
        return

    console.print(f"This removes {len(jobs)} job(s) and {len(customers)} customer(s):")
    for job in jobs[:10]:
        console.print(f"  · {job.job_id} — {job.name}")
    if len(jobs) > 10:
        console.print(f"  · … and {len(jobs) - 10} more")
    for customer in customers[:10]:
        console.print(f"  · {customer.name}")
    if len(customers) > 10:
        console.print(f"  · … and {len(customers) - 10} more")

    if not yes and not typer.confirm("Remove all of it?"):
        raise typer.Abort()

    for job in jobs:
        store.delete(job.job_id)
    book.save_all([])
    console.print(
        f"[green]Cleared {len(jobs)} job(s) and {len(customers)} customer(s).[/green]"
    )


@app.command()
def readiness() -> None:
    """What this installation can actually do this morning, and what is missing.

    Not a score: a list. Every package in this trade is complete only after
    the shop's own facts are in it, and this says which of them are.
    """
    from .readiness import State, readiness as run_readiness

    report = run_readiness()
    colour = {
        State.READY: "green", State.PARTIAL: "cyan",
        State.EMPTY: "yellow", State.ATTENTION: "red",
    }

    table = Table(title="Readiness", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("State")
    table.add_column("Detail")
    for check in report:
        table.add_row(
            check.hebrew,
            f"[{colour[check.state]}]{check.state.hebrew}[/{colour[check.state]}]",
            check.detail,
        )
    console.print(table)
    console.print(f"[cyan]{report.verdict()}[/cyan]")

    outstanding = [check for check in report if not check.state.is_ready and check.blocks]
    if outstanding:
        console.print("\n[yellow]What is blocked, and how to close it:[/yellow]")
        for check in outstanding:
            console.print(f"  [dim]{check.hebrew}[/dim] — {check.blocks}")
            if check.fix:
                console.print(f"    [green]→[/green] {check.fix}")

    if not report.may_cut:
        console.print(
            "\n[yellow]No bar may be cut from this installation yet: no series "
            "carries its supplier's own deductions. Quoting is fine; every cut "
            "sheet will carry the not-for-production banner.[/yellow]"
        )


@calendar_app.command("year")
def calendar_year(
    year: int = typer.Argument(0, help="Gregorian year; default this one."),
) -> None:
    """Every day the Hebrew calendar takes out of the working year."""
    from datetime import date as _date

    from .hebrew_calendar import holidays_between

    year = year or _date.today().year
    table = Table(title=f"Working year {year}", header_style="dim")
    table.add_column("Date", style="cyan")
    table.add_column("Day")
    table.add_column("Hebrew")
    table.add_column("What it is")
    table.add_column("Hours", justify="right")

    from .erp.scheduling import Calendar

    israeli = Calendar.israeli()
    for holiday in holidays_between(_date(year, 1, 1), _date(year, 12, 31)):
        table.add_row(
            holiday.day.strftime("%d/%m/%Y"),
            holiday.day.strftime("%a"),
            holiday.hebrew,
            holiday.kind.hebrew,
            f"{israeli.hours_on(holiday.day):.1f}",
        )
    console.print(table)


@calendar_app.command("date")
def calendar_date(
    day: str = typer.Argument("", help="Gregorian date YYYY-MM-DD; default today."),
) -> None:
    """What one day is, in both calendars."""
    from datetime import date as _date

    from .erp.scheduling import Calendar
    from .hebrew_calendar import describe, holiday_on

    when = _date.fromisoformat(day) if day else _date.today()
    israeli = Calendar.israeli()
    console.print(f"[cyan]{when:%d/%m/%Y}[/cyan] {when:%A}")
    console.print(f"  {describe(when)}")
    festival = holiday_on(when)
    if festival is not None:
        console.print(f"  [yellow]{festival.describe()}[/yellow]")
    console.print(f"  working hours: {israeli.hours_on(when):.1f}")
    if not israeli.is_working(when):
        console.print(f"  next working day: {israeli.next_working_day(when):%d/%m/%Y}")


@service_app.command("list")
def service_list(
    everything: bool = typer.Option(False, "--all", help="Closed calls too."),
) -> None:
    """The calls on the board."""
    from datetime import date as _date

    from .service import default_register

    register = default_register()
    calls = register.all() if everything else register.open_calls()
    if not calls:
        console.print("[green]No open service calls.[/green]")
        return

    table = Table(title="Service calls", header_style="dim")
    table.add_column("Id", style="cyan")
    table.add_column("Customer")
    table.add_column("Symptom")
    table.add_column("Opened")
    table.add_column("Due")
    table.add_column("State")
    table.add_column("Warranty")
    today = _date.today()
    for call in calls:
        covered = call.under_warranty
        overdue = call.is_overdue(today)
        table.add_row(
            call.call_id, call.customer_name, call.symptom.hebrew,
            call.opened.strftime("%d/%m/%Y"),
            f"[red]{call.due_by():%d/%m/%Y}[/red]" if overdue
            else call.due_by().strftime("%d/%m/%Y"),
            call.state.hebrew,
            "?" if covered is None else ("yes" if covered else "no"),
        )
    console.print(table)


@service_app.command("open")
def service_open(
    customer: str = typer.Argument(..., help="Who rang."),
    symptom: str = typer.Argument(..., help="water, dropped, shutter, ..."),
    job: str = typer.Option("", "--job", "-j"),
    element: str = typer.Option("", "--element", "-e"),
    delivered: str = typer.Option("", "--delivered", help="Handover date YYYY-MM-DD."),
    note: str = typer.Option("", "--note", "-n"),
) -> None:
    """Log a call while the customer is still on the phone."""
    from datetime import date as _date

    from .service import ServiceCall, Symptom, default_register

    try:
        kind = Symptom(symptom)
    except ValueError:
        _fail(
            f"Unknown symptom {symptom!r}. One of: "
            + ", ".join(entry.value for entry in Symptom)
        )
    call = ServiceCall(
        job_id=job, customer_name=customer, element_name=element,
        symptom=kind, description=note,
        delivered=_date.fromisoformat(delivered) if delivered else None,
    )
    default_register().add(call)

    covered = call.under_warranty
    console.print(f"[green]{call.call_id}[/green] {call.describe()}")
    console.print(
        f"[dim]severity {call.severity.hebrew}, be there by "
        f"{call.due_by():%d/%m/%Y}[/dim]"
    )
    if covered is None:
        console.print("[yellow]Handover date unknown, so warranty is unknown.[/yellow]")
    else:
        until = call.warranty_until()
        console.print(
            f"[dim]{call.warranty_component_hebrew}: "
            + (f"covered until {until:%d/%m/%Y}" if covered else "out of warranty")
            + "[/dim]"
        )


@service_app.command("close")
def service_close(
    call_id: str = typer.Argument(..., help="The call to close."),
    cause: str = typer.Argument(..., help="manufacture, installation, building, ..."),
    minutes: int = typer.Option(60, "--minutes", "-m"),
    engineer: str = typer.Option("", "--engineer"),
    charged: float = typer.Option(0.0, "--charged"),
) -> None:
    """Shut a call with what it turned out to be."""
    from datetime import date as _date

    from .service import Cause, default_register

    register = default_register()
    call = register.get(call_id)
    if call is None:
        _fail(f"No service call {call_id}.")
    try:
        why = Cause(cause)
    except ValueError:
        _fail(
            f"Unknown cause {cause!r}. One of: "
            + ", ".join(entry.value for entry in Cause)
        )
    call.close(_date.today(), why, minutes=minutes, engineer=engineer, charged=charged)
    register.update(call)
    console.print(f"[green]{call_id}[/green] closed: {why.hebrew}")
    if why.is_ours:
        console.print("[yellow]Counted against this job's margin.[/yellow]")


@service_app.command("report")
def service_report() -> None:
    """What comes back, why, and what going back has cost."""
    from .service import default_register

    register = default_register()
    if not len(register):
        console.print("[dim]No service calls recorded yet.[/dim]")
        return

    quality = register.cost_of_quality()
    performance = register.response_performance()
    table = Table(title="After-sales", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value", justify="right")
    for label, value in (
        ("Calls", str(len(register))),
        ("Open", str(len(register.open_calls()))),
        ("Overdue", str(len(register.overdue()))),
        ("Hours our fault", f"{quality['hours_our_fault']:.1f}"),
        ("Hours chargeable", f"{quality['hours_chargeable']:.1f}"),
        ("Recovered", f"{quality['recovered']:,.0f}"),
        ("Median days to close", f"{performance['median_days']:.0f}"),
        ("Within target", f"{performance['within_target']:.0f}%"),
    ):
        table.add_row(label, value)
    console.print(table)

    recurring = register.recurring(minimum=2)
    if recurring:
        console.print("[yellow]Coming back more than once:[/yellow]")
        for label, count in recurring:
            console.print(f"  {count}x  {label}")


@money_app.command("cheques")
def money_cheques() -> None:
    """What a cheque book would look like — the shape of the register."""
    from .erp.collection import ChequeState

    table = Table(title="Cheque states", header_style="dim")
    table.add_column("State", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Is money", justify="right")
    for state in ChequeState:
        table.add_row(state.value, state.hebrew, "yes" if state.is_money else "no")
    console.print(table)
    console.print(
        "[dim]A cheque is a promise. It is never posted as revenue here, "
        "because a promise booked as revenue hides a bad debtor.[/dim]"
    )


@money_app.command("job")
def money_job(
    job_id: str = typer.Argument(..., help="The job to cost."),
) -> None:
    """What one job has earned, read from every side that has an entry."""
    from .projects import default_store
    from .projects.costing import cost_job
    from .service import default_register

    job = default_store().get(job_id)
    if job is None:
        _fail(f"No job {job_id}. List them with `profileos jobs list`.")
    costing = cost_job(job, service=default_register())

    table = Table(title=f"{job.job_id} — {job.name}", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value", justify="right")
    for label, value in costing.summary_rows():
        table.add_row(label, value)
    console.print(table)
    console.print(f"[cyan]{costing.verdict()}[/cyan]")
    for warning in costing.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@erp_app.command("terms")
def erp_terms(
    issued: str = typer.Option("", "--on", help="Invoice date YYYY-MM-DD; default today."),
) -> None:
    """What each payment term actually means for when the money arrives."""
    from datetime import date as _date

    from .erp.israel import PaymentTerms

    on = _date.fromisoformat(issued) if issued else _date.today()
    table = Table(title=f"Payment terms on {on:%d/%m/%Y}", header_style="dim")
    table.add_column("Term", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Money due")
    table.add_column("Days", justify="right")
    for term in PaymentTerms:
        due = term.due(on)
        table.add_row(term.value, term.hebrew, f"{due:%d/%m/%Y}", str((due - on).days))
    console.print(table)


@erp_app.command("tax-document")
def erp_tax_document(
    number: str = typer.Argument(..., help="Document number."),
    customer: str = typer.Argument(..., help="Customer name."),
    net: float = typer.Argument(..., help="Net amount in shekels."),
    kind: str = typer.Option("invoice", "--kind", "-k"),
    allocation: str = typer.Option("", "--allocation", help="מספר הקצאה."),
    terms: str = typer.Option("eom_30", "--terms"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Write the HTML here."),
) -> None:
    """Draft an Israeli tax document and check it before it is issued."""
    from datetime import date as _date

    from .branding import active_brand
    from .erp.israel import (
        DocumentKind, PaymentTerms, TaxDocument, TaxIdentity, render_document,
    )

    brand = active_brand()
    document = TaxDocument(
        kind=DocumentKind(kind),
        number=number,
        issued=_date.today(),
        issuer=TaxIdentity(
            name=brand.display_name,
            # Not invented: the brand carries the registration number only once
            # somebody has entered it, and an invoice without it is flagged.
            vat_number=brand.registration_number or "",
            address=", ".join(
                part for part in (brand.address_line, brand.city) if part
            ),
            phone=brand.phone or "",
            email=brand.email or "",
        ),
        customer_name=customer,
        lines=[{
            "description": "עבודות אלומיניום",
            "quantity": 1, "unit": "יח", "unit_price": net, "net": net,
        }],
        net=net,
        terms=PaymentTerms(terms),
        allocation_number=allocation,
    )

    table = Table(header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value")
    for label, value in document.summary_rows():
        table.add_row(label, value)
    console.print(table)

    problems = document.problems()
    for problem in problems:
        console.print(f"[yellow]{problem}[/yellow]")
    if not problems:
        console.print("[green]אפשר להוציא[/green]")

    if out is not None:
        out.write_text(render_document(document), encoding="utf-8")
        console.print(f"[dim]Written to {out}[/dim]")


@spec_app.command("standards")
def spec_standards(
    topic: str = typer.Argument("", help="Search, e.g. רוח or glazing."),
) -> None:
    """The standards a window is judged against, and what we can say to each."""
    from .compliance import standards_for

    found = standards_for(topic)
    if not found:
        _fail(f"No standard matches {topic!r}.")
    for entry in found:
        console.print(f"[cyan]{entry.number}[/cyan] {entry.hebrew} — {entry.english}")
        console.print(f"  {entry.scope}")
        if entry.covered:
            console.print(f"  [green]covered:[/green] {entry.covered}")
        if entry.not_covered:
            console.print(f"  [yellow]not covered:[/yellow] {entry.not_covered}")
        console.print(f"  [dim]{entry.confidence.hebrew}[/dim]\n")


@spec_app.command("wind")
def spec_wind(
    velocity: float = typer.Argument(..., help="Basic wind velocity [m/s], from the map."),
    height: float = typer.Option(10.0, "--height", "-h", help="Height above ground [m]."),
    terrain: str = typer.Option("suburban", "--terrain", "-t"),
    zone: str = typer.Option("field", "--zone", "-z"),
    source: str = typer.Option("", "--source", help="Where the velocity was read."),
) -> None:
    """Design wind pressure on a facade element, and the classes it calls for."""
    from .compliance import FacadeZone, Terrain, design_pressure, required_classes

    try:
        case = design_pressure(
            velocity, height=height, terrain=Terrain(terrain),
            zone=FacadeZone(zone), source=source,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    table = Table(title="Wind", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value")
    for label, value in case.summary_rows():
        table.add_row(label, value)
    console.print(table)

    classes = required_classes(case)
    console.print(
        f"[cyan]Ask the supplier for:[/cyan] {classes.wind} wind, "
        f"{classes.water} water, {classes.air} air"
    )
    for note in [*case.notes, *classes.notes]:
        console.print(f"[yellow]{note}[/yellow]")


@spec_app.command("window")
def spec_window(
    width: float = typer.Argument(..., help="Width [mm]."),
    height: float = typer.Argument(..., help="Height [mm]."),
    glass: str = typer.Option("dgu-6-16-6", "--glass", "-g"),
    columns: int = typer.Option(1, "--columns", "-c"),
    frame: str = typer.Option("thermal_break", "--frame", "-f"),
    velocity: float = typer.Option(30.0, "--wind", help="Basic wind velocity [m/s]."),
) -> None:
    """Uw, Rw and the wind case for one window, from end to end."""
    from .compliance import FrameClass, Site, check_compliance
    from .elements import ElementBuilder, Opening

    opening = Opening(name="SPEC", width=width, height=height, glass_spec_id=glass)
    opening.divide_evenly(columns, 1)
    build = ElementBuilder().build(opening, sill_height=900)
    report = check_compliance(
        build, Site(basic_velocity=velocity), frame_class=FrameClass(frame)
    )

    table = Table(title=f"{width:,.0f} x {height:,.0f} mm", header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value")
    for section in (report.thermal, report.acoustic, report.wind, report.classes):
        if section is not None:
            for label, value in section.summary_rows():
                table.add_row(label, value)
    console.print(table)

    console.print(f"[cyan]{report.verdict()}[/cyan]")
    for finding in report.findings:
        colour = {"pass": "green", "check": "yellow", "fail": "red"}[finding.verdict.value]
        citation = f"[dim]{finding.citation}[/dim] " if finding.citation else ""
        console.print(f"[{colour}]{finding.verdict.hebrew}[/{colour}] {citation}{finding.text}")


@fit_app.command("shutter")
def fit_shutter(
    width: float = typer.Argument(..., help="Window width [mm]."),
    height: float = typer.Argument(..., help="Window height [mm]."),
    slat: str = typer.Option("alu_45", "--slat", "-s", help="Curtain profile."),
    drive: str = typer.Option("motor", "--drive", "-d"),
    box: str = typer.Option("built_in", "--box"),
) -> None:
    """Size a rolling shutter: the box, the shaft, the motor, the cut list."""
    from .accessories import BoxPosition, Drive, ShutterSpec, size_shutter

    try:
        fitted = size_shutter(
            width, height,
            ShutterSpec(slat_id=slat, drive=Drive(drive), box=BoxPosition(box)),
        )
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        _fail(str(exc))

    meta = fitted.metadata
    table = Table(title=fitted.hebrew, header_style="dim")
    table.add_column("What", style="cyan")
    table.add_column("Value", justify="right")
    for label, value in (
        ("Curtain", f"{fitted.width:,.0f} x {fitted.height:,.0f} mm"),
        ("Curtain length on roll", f"{meta['curtain_length_mm']:,.0f} mm"),
        ("Slats", f"{meta['slat_count']}"),
        ("Coil diameter", f"{meta['coil_diameter_mm']:,.1f} mm"),
        ("Shaft", f"{meta['shaft_mm']:,.0f} mm octagonal"),
        ("Box", f"{meta['box_mm']:,.0f} mm"),
        ("Curtain weight", f"{meta['mass_kg']:,.1f} kg"),
        ("Structural opening", "{:,.0f} x {:,.0f} mm".format(
            *fitted.structural_opening(width, height))),
    ):
        table.add_row(label, value)
    console.print(table)

    cuts = Table(title="Cut list", header_style="dim")
    cuts.add_column("Part", style="cyan")
    cuts.add_column("Profile")
    cuts.add_column("Length", justify="right")
    cuts.add_column("Qty", justify="right")
    for cut in fitted.cuts:
        cuts.add_row(cut.role, cut.profile_id, f"{cut.length:,.1f}", str(cut.quantity))
    console.print(cuts)

    for part in fitted.parts:
        console.print(f"[dim]- {part.code}: {part.quantity} {part.unit}[/dim]")
    for note in fitted.notes:
        console.print(f"[cyan]{note}[/cyan]")
    for warning in fitted.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@fit_app.command("screen")
def fit_screen(
    width: float = typer.Argument(..., help="Window width [mm]."),
    height: float = typer.Argument(..., help="Window height [mm]."),
    kind: str = typer.Option("sliding", "--kind", "-k"),
    mesh: str = typer.Option("fibreglass", "--mesh", "-m"),
) -> None:
    """Size an insect screen, splitting it into leaves if it is too wide."""
    from .accessories import MeshKind, ScreenKind, ScreenSpec, size_screen

    try:
        fitted = size_screen(
            width, height,
            ScreenSpec(kind=ScreenKind(kind), mesh=MeshKind(mesh)),
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(f"[cyan]{fitted.hebrew}[/cyan]")
    console.print(
        f"[dim]{fitted.metadata['leaves']} leaf(s) of "
        f"{fitted.metadata['leaf_width_mm']:,.0f} mm, "
        f"{fitted.metadata['mesh_area_m2']:.2f} m2 mesh[/dim]"
    )
    table = Table(header_style="dim")
    table.add_column("Part", style="cyan")
    table.add_column("Length", justify="right")
    table.add_column("Qty", justify="right")
    for cut in fitted.cuts:
        table.add_row(cut.hebrew, f"{cut.length:,.1f}", str(cut.quantity))
    console.print(table)
    for warning in fitted.warnings:
        console.print(f"[yellow]{warning}[/yellow]")


@fit_app.command("slats")
def fit_slats() -> None:
    """Every curtain profile stocked, and what each one weighs."""
    from .accessories import SLATS

    table = Table(title="Shutter curtains", header_style="dim")
    table.add_column("Id", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Pitch", justify="right")
    table.add_column("Rolled", justify="right")
    table.add_column("kg/m2", justify="right")
    table.add_column("Max width", justify="right")
    table.add_column("Source")
    for slat_profile in SLATS:
        table.add_row(
            slat_profile.slat_id, slat_profile.hebrew,
            f"{slat_profile.pitch:,.0f}", f"{slat_profile.thickness:,.1f}",
            f"{slat_profile.mass:,.1f}", f"{slat_profile.max_width:,.0f}",
            slat_profile.source,
        )
    console.print(table)


@library_app.command("types")
def library_types() -> None:
    """Every opening type this shop makes, and the sizes each is made at."""
    from .library import catalogue_size, families

    table = Table(title="Opening types", header_style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Leaves")
    table.add_column("Width", justify="right")
    table.add_column("Height", justify="right")
    for family in families():
        table.add_row(
            family.family_id,
            family.hebrew,
            ", ".join(str(count) for count in family.leaves),
            f"{family.min_width:,.0f}-{family.max_width:,.0f}",
            f"{family.min_height:,.0f}-{family.max_height:,.0f}",
        )
    console.print(table)
    console.print(
        f"[dim]{catalogue_size():,} combinations at 50 mm steps — "
        f"any size in between is made on request.[/dim]"
    )


@library_app.command("find")
def library_find(
    query: str = typer.Argument(..., help='For example: "הזזה 4 כנפיים 6000/2200 קליל 9000".'),
    limit: int = typer.Option(15, "--limit", "-n"),
) -> None:
    """Search the library the way the shop says it out loud."""
    from .library import parse_query, search_openings

    parsed = parse_query(query)
    found = search_openings(query, limit=limit)
    if not found:
        _fail(f"Nothing matches {query!r}. Try a type: הזזה, בלגי, דלת, קיר מסך.")

    table = Table(title=f"{len(found)} matches", header_style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Leaves", justify="right")
    table.add_column("Series")
    table.add_column("Id", style="dim")
    for preset in found:
        table.add_row(
            preset.hebrew,
            f"{preset.width:,.0f} x {preset.height:,.0f}",
            str(preset.columns),
            preset.system_hebrew or "-",
            preset.preset_id,
        )
    console.print(table)
    console.print(
        f"[dim]read as: width={parsed.width or '-'} height={parsed.height or '-'} "
        f"leaves={parsed.leaves or '-'} series={parsed.system_id or '-'}[/dim]"
    )


@library_app.command("build")
def library_build(
    preset_id: str = typer.Argument(..., help="An id from `profileos library find`."),
    quantity: int = typer.Option(1, "--quantity", "-q"),
) -> None:
    """Build one opening from the library and show what it takes to make."""
    from .elements import Cell, ElementBuilder, ElementKind, Opening, OpeningType, Sash
    from .library import opening as library_opening

    preset = library_opening(preset_id)
    if preset is None:
        _fail(f"No such opening: {preset_id}. List them with `profileos library find`.")

    unit = Opening(
        name=preset.preset_id,
        kind=ElementKind(preset.kind),
        width=preset.width,
        height=preset.height,
        quantity=quantity,
        glass_spec_id=preset.glass,
        system_id=preset.system_id,
    )
    unit.divide_evenly(preset.columns, preset.rows)
    sash_type = OpeningType(preset.sash_type)
    if sash_type is not OpeningType.FIXED:
        unit.set_cell(Cell(
            column=min(preset.sash_column, unit.column_count - 1),
            row=min(preset.sash_row, unit.row_count - 1),
            sash=Sash(opening_type=sash_type),
        ))
    build = ElementBuilder().build(unit, sill_height=preset.sill)

    summary = build.summary()
    table = Table(title=preset.hebrew, header_style="dim")
    table.add_column("Role", style="cyan")
    table.add_column("Profile")
    table.add_column("Length", justify="right")
    table.add_column("Qty", justify="right")
    for cut in sorted(build.cuts, key=lambda c: (c.role, -c.length)):
        table.add_row(cut.role, cut.profile_id, f"{cut.length:,.1f}", str(cut.quantity))
    console.print(table)
    console.print(
        f"[dim]{summary['pieces']} pieces, {summary['glass_panes']} panes, "
        f"{summary['glass_area_m2']:.2f} m2 glass, {summary['hardware_items']} hardware items[/dim]"
    )


@jobs_app.command("list")
def jobs_list(
    all_jobs: bool = typer.Option(False, "--all", help="Include installed and lost jobs."),
    customer: Optional[str] = typer.Option(None, "--customer", "-c", help="Filter by customer name."),
) -> None:
    """The order book: every job the shop has open."""
    from .projects import default_store

    store = default_store()
    jobs = store.all() if all_jobs else store.open_jobs()
    if customer:
        needle = customer.lower()
        jobs = [job for job in jobs if needle in job.customer_name.lower()]

    if not jobs:
        console.print("[yellow]No jobs yet. Open one with `profileos jobs new`.[/yellow]")
        return

    table = Table(title="Jobs", header_style="dim")
    table.add_column("Number", style="cyan")
    table.add_column("Name")
    table.add_column("Customer")
    table.add_column("Status")
    table.add_column("Units", justify="right")
    table.add_column("Quoted", justify="right")
    table.add_column("Updated")
    for job in jobs:
        table.add_row(
            job.job_id, job.name, job.customer_name or "-",
            f"{job.status.hebrew} ({job.status.value})",
            str(job.unit_count),
            f"{job.quote_total:,.0f}" if job.quote_total else "-",
            job.updated,
        )
    console.print(table)
    console.print(
        f"[dim]{len(jobs)} job(s); backlog {store.backlog_value():,.0f} "
        f"of work won but not installed.[/dim]"
    )


@jobs_app.command("new")
def jobs_new(
    name: str = typer.Argument(..., help="What the job is called."),
    customer: Optional[str] = typer.Option(None, "--customer", "-c", help="Customer name or id."),
    system: str = typer.Option("generic", "--system", "-s"),
    reference: str = typer.Option("", "--reference", "-r"),
    site: str = typer.Option("", "--site"),
) -> None:
    """Open a job. Only a name is required; everything else can follow."""
    from .projects import default_customers, default_store

    book = default_customers()
    record = None
    if customer:
        needle = customer.strip().lower()
        record = next(
            (c for c in book.all()
             if c.customer_id.lower() == needle or c.name.lower() == needle),
            None,
        )
        if record is None:
            record = book.add(customer.strip())
            console.print(f"[dim]Added customer {record.customer_id} {record.name}.[/dim]")

    job = default_store().create(
        name, customer=record, system_id=system, reference=reference, site_address=site
    )
    console.print(f"[green]Opened {job.job_id}[/green] {job.name}")


@jobs_app.command("show")
def jobs_show(job_id: str = typer.Argument(..., help="Job number, e.g. J-2026-0001.")) -> None:
    """Everything one job holds, including its status history."""
    from .projects import default_store

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    console.print(f"[bold]{job.job_id}[/bold]  {job.name}")
    for label, value in (
        ("Customer", job.customer_name), ("Site", job.site_address),
        ("Reference", job.reference), ("System", job.system_id),
        ("Status", f"{job.status.hebrew} ({job.status.value})"),
        ("Openings", str(job.opening_count)),
        ("Units", str(job.unit_count)),
        ("Area", f"{job.total_area:.2f} m2"),
        ("Quoted", f"{job.quote_total:,.2f} {job.currency}" if job.quote_total else "-"),
        ("Created", job.created), ("Updated", job.updated),
    ):
        if value:
            console.print(f"  {label:<10} {value}")
    if job.history:
        console.print("\n[dim]History[/dim]")
        for event in job.history:
            console.print(f"  {event.at}  {event.status.value}  {event.note}")


@jobs_app.command("status")
def jobs_status(
    job_id: str = typer.Argument(...),
    to: str = typer.Argument(..., help="enquiry, quoted, won, in_production, installed, lost"),
    note: str = typer.Option("", "--note", "-n"),
) -> None:
    """Move a job along: quoted, won, in production, installed."""
    from .projects import JobStatus, default_store

    store = default_store()
    try:
        job = store.load(job_id)
        target = JobStatus(to.lower())
    except ValueError:
        _fail(f"Unknown status {to!r}. Known: {', '.join(s.value for s in JobStatus)}")
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    try:
        job.advance(target, note)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    store.save(job)
    console.print(f"[green]{job.job_id} -> {target.value}[/green]")


@jobs_app.command("customers")
def jobs_customers(
    add: Optional[str] = typer.Option(None, "--add", help="Add a customer by name."),
    phone: str = typer.Option("", "--phone"),
    city: str = typer.Option("", "--city"),
) -> None:
    """The customer book."""
    from .projects import default_customers

    book = default_customers()
    if add:
        customer = book.add(add, phone=phone, city=city)
        console.print(f"[green]Added {customer.customer_id}[/green] {customer.name}")
        return

    customers = book.all()
    if not customers:
        console.print("[yellow]No customers yet. Add one with --add.[/yellow]")
        return
    table = Table(title="Customers", header_style="dim")
    table.add_column("Code", style="cyan")
    table.add_column("Name")
    table.add_column("Contact")
    table.add_column("Phone")
    table.add_column("City")
    for customer in customers:
        table.add_row(
            customer.customer_id, customer.name, customer.contact or "-",
            customer.phone or "-", customer.city or "-",
        )
    console.print(table)


@jobs_app.command("pack")
def jobs_pack(
    job_id: str = typer.Argument(..., help="Job number, e.g. J-2026-0001."),
    out: Path = typer.Option(Path("job-pack.html"), "--out", "-o"),
) -> None:
    """Print pack: cover, elevations, cut list, glass and hardware, in one page."""
    from .elements.builder import ElementBuilder
    from .projects import default_store, write_dossier

    try:
        job = default_store().load(job_id)
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    builds = []
    if job.schedule is not None:
        for opening in job.schedule.openings:
            try:
                builder = ElementBuilder.for_system(opening.system_id or job.system_id)
            except Exception:  # noqa: BLE001 - generic rules will do
                builder = ElementBuilder()
            try:
                builds.append(builder.build(opening, sill_height=job.schedule.sill_height))
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]{opening.element_id}: {exc}[/yellow]")

    written = write_dossier(job, builds, out)
    console.print(
        f"[green]Wrote[/green] {written} — {len(builds)} element(s), "
        f"{job.unit_count} unit(s)."
    )


@pipe_app.command("fixtures")
def pipe_fixtures(
    dwellings: int = typer.Option(1, "--dwellings", "-d", help="Typical dwellings."),
    add: Optional[str] = typer.Option(
        None, "--add", help="Extra fixtures, e.g. 'urinal=4,basin=2'."
    ),
    valves: bool = typer.Option(False, "--valves", help="Flush valves rather than cisterns."),
) -> None:
    """Count what is connected, and what it therefore demands."""
    from .plumbing import FIXTURES, FixtureSchedule, SupplyKind, typical_dwelling

    kind = SupplyKind.VALVE if valves else SupplyKind.TANK
    schedule = typical_dwelling(dwellings, kind=kind) if dwellings else FixtureSchedule(kind=kind)
    if add:
        for pair in add.split(","):
            if not pair.strip():
                continue
            name, _, count = pair.partition("=")
            try:
                schedule.add(name.strip(), int(count or 1))
            except Exception as exc:  # noqa: BLE001
                _fail(str(exc))

    table = Table(title="Fixture schedule", header_style="dim")
    table.add_column("Fixture", style="cyan")
    table.add_column("Hebrew")
    table.add_column("Qty", justify="right")
    table.add_column("Cold LU", justify="right")
    table.add_column("Hot LU", justify="right")
    table.add_column("DFU", justify="right")
    for fixture_id, hebrew, quantity, cold, hot, dfu in schedule.rows():
        table.add_row(fixture_id, hebrew, str(quantity), f"{cold:g}", f"{hot:g}", f"{dfu:g}")
    console.print(table)

    summary = schedule.summary()
    console.print(
        _kv_table(
            "Demand",
            [
                ("Fixtures", summary["fixtures"]),
                ("Loading units", f"{summary['cold_lu']:g} cold / {summary['hot_lu']:g} hot"),
                ("Cold demand", f"{summary['cold_lps']:.2f} l/s"),
                ("Hot demand", f"{summary['hot_lps']:.2f} l/s"),
                ("Combined main", f"{summary['total_lps']:.2f} l/s"),
                ("Drainage units", f"{summary['dfu']:g}"),
                ("Largest trap", f"{summary['largest_trap_mm']:.0f} mm"),
            ],
        )
    )
    console.print(f"[dim]Available fixtures: {', '.join(f.id for f in FIXTURES)}[/dim]")


@pipe_app.command("drainage")
def pipe_drainage(
    dwellings: int = typer.Option(8, "--dwellings", "-d"),
    floors: int = typer.Option(4, "--floors", "-f"),
    fall: float = typer.Option(0.02, "--fall", help="Branch fall, e.g. 0.02 for 1:50."),
    vent_length: float = typer.Option(25.0, "--vent-length", help="Developed vent run [m]."),
) -> None:
    """Size the branch, the stack, its vent and the house drain."""
    from .plumbing import design_drainage, typical_dwelling

    try:
        design = design_drainage(
            typical_dwelling(dwellings), floors=floors, fall=fall,
            vent_length_m=vent_length,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    table = Table(title="Drainage", header_style="dim")
    table.add_column("Part", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Reasoning")
    for part, size, reason in design.rows():
        table.add_row(part, size, reason)
    console.print(table)
    for note in design.notes():
        console.print(f"[yellow]{note}[/yellow]")
    if not design.ok:
        raise typer.Exit(code=1)


@pipe_app.command("circulation")
def pipe_circulation(
    length: float = typer.Option(120.0, "--length", "-l", help="Loop length [m]."),
    diameter: float = typer.Option(28.0, "--diameter", help="Flow pipe outside diameter [mm]."),
    insulation: float = typer.Option(25.0, "--insulation", help="Insulation thickness [mm]."),
    material: str = typer.Option("elastomeric", "--insulation-type"),
    catalogue: str = typer.Option("copper-en1057", "--catalogue", "-c"),
    dead_leg: float = typer.Option(5.0, "--dead-leg", help="Longest uncirculated tail [m]."),
) -> None:
    """Size a hot water circulation loop: flow, return, pump and dead legs."""
    from .plumbing import DeadLeg, design_circulation, get_catalogue

    try:
        design = design_circulation(
            length, diameter, get_catalogue(catalogue),
            insulation_mm=insulation, material=material,
            dead_legs=[DeadLeg("longest tail", dead_leg, 16.0)],
        )
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))

    summary = design.summary()
    console.print(
        _kv_table(
            "Hot water circulation",
            [
                ("Loss per metre", f"{summary['loss_per_metre_w']:.1f} W/m"),
                ("Loop loss", f"{summary['total_watts']:.0f} W"),
                ("Circulation flow", f"{summary['flow_lps']:.3f} l/s"),
                ("Return pipe", summary["return"]),
                ("Pump head", f"{summary['pump_head_kpa']:.1f} kPa"),
                ("Pump power", f"{summary['pump_watts']:.1f} W"),
                ("Standing loss", f"{summary['annual_kwh']:,.0f} kWh/year"),
            ],
        )
    )
    for leg in design.dead_legs:
        console.print(("[green]" if leg.ok else "[red]") + leg.describe())
    for note in design.notes:
        console.print(f"[yellow]{note}[/yellow]")


@app.command("seed")
def seed(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Say nothing on success."),
    force: bool = typer.Option(False, "--force", help="Seed even if jobs already exist."),
) -> None:
    """Put a starting order book in place, so the first launch is not empty.

    A brand new installation with no jobs and no customers teaches nobody
    anything: every screen shows its empty state and the operator has to
    invent data before they can look around. This writes a handful of
    realistic jobs and customers — and refuses to run twice, so it can never
    scribble over a shop's real work.
    """
    from .projects import JobStatus, default_customers, default_store

    store, book = default_store(), default_customers()
    if store.all() and not force:
        if not quiet:
            console.print("[yellow]Jobs already exist; nothing seeded.[/yellow]")
        return

    customers = [
        book.add("משפחת לוי", contact="יוסי לוי", phone="052-8841200",
                 city="בית אל", address="הגפן 4"),
        book.add('כהן בנייה בע"מ', contact="אבי כהן", phone="02-9971234",
                 city="עפרה", tax_id="514882201"),
        book.add("שוקרון יזמות", contact="דוד שוקרון", phone="050-7712399",
                 city="שילה"),
    ]

    plan: list[tuple[str, int, str, str, list[JobStatus], float]] = [
        ("וילה משפחת לוי", 0, "klil-7300", "הגפן 4, בית אל",
         [JobStatus.QUOTED], 86_420.0),
        ('בניין מגורים — 8 יח"ד', 1, "klil-4300", "עפרה, מגרש 22",
         [JobStatus.QUOTED, JobStatus.WON], 412_750.0),
        ("חזית מסחרית שילה", 2, "klil-9000", "מרכז מסחרי שילה",
         [JobStatus.QUOTED, JobStatus.WON, JobStatus.IN_PRODUCTION], 238_900.0),
        ("החלפת חלונות — דירה", 0, "klil-4300", "", [], 0.0),
    ]
    for name, customer_index, system_id, site, statuses, quoted in plan:
        job = store.create(
            name, customer=customers[customer_index],
            system_id=system_id, site_address=site,
        )
        for status in statuses:
            job.advance(status)
            if status is JobStatus.QUOTED and quoted:
                job.record_quote(quoted)
        store.save(job)

    if not quiet:
        console.print(
            f"[green]Seeded[/green] {len(plan)} job(s) and "
            f"{len(customers)} customer(s)."
        )


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except ProfileOSError as exc:  # pragma: no cover - top-level safety net
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
