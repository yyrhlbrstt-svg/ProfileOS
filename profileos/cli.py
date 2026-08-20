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
