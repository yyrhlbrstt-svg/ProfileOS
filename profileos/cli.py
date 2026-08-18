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
plugin_app = typer.Typer(help="Plugin registries and hot reload.")

app.add_typer(geometry_app, name="section")
app.add_typer(nest_app, name="nest")
app.add_typer(cnc_app, name="cnc")
app.add_typer(element_app, name="element")
app.add_typer(pipe_app, name="pipe")
app.add_typer(plugin_app, name="plugin")


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
        build = ElementBuilder().build(opening, sill_height=sill)
    except (ProfileOSError, ValueError) as exc:
        _fail(str(exc))

    console.print(Panel(opening.describe(), border_style="cyan"))

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
    for warning in build.warnings:
        console.print(f"  [yellow]-[/] {warning}")


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
    """Start the HTTP service API."""
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


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except ProfileOSError as exc:  # pragma: no cover - top-level safety net
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
