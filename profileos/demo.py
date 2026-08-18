"""End-to-end demonstration.

Runs the whole suite on the bundled sample drawings, in the order a real job
moves through a fabricator:

1. Import a DXF cross-section and compute its structural properties.
2. Check a mullion against wind load for the intended span.
3. Design two elements (a window and a door) and derive their cut lists,
   glass, gaskets and hardware.
4. Nest the cut list onto stock bars.
5. Generate machining programs for a five-axis centre and a saw.
6. Price the job through supplier catalogues.
7. Release it to the floor with barcoded job cards.
8. Size the associated water service.

It doubles as an integration test: every engine is exercised against the
others' real output rather than against fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.config import PROJECT_ROOT
from .core.errors import ProfileOSError
from .core.logging_setup import get_logger

_log = get_logger("demo")


def _rule(console: Any, title: str) -> None:
    if console is not None:
        console.rule(f"[bold cyan]{title}")
    else:  # pragma: no cover - plain fallback
        print(f"\n=== {title} ===")


def _say(console: Any, message: str) -> None:
    if console is not None:
        console.print(message)
    else:  # pragma: no cover
        print(message)


def run_demo(output: Path, console: Any = None) -> dict[str, Any]:
    """Run the full pipeline, writing artefacts into ``output``.

    Returns a dictionary of results so the demo can be asserted on in tests.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    sample_dir = PROJECT_ROOT / "data" / "samples"
    mullion_dxf = sample_dir / "mullion_mb70.dxf"
    if not mullion_dxf.is_file():
        raise ProfileOSError(
            "Sample drawings are missing; run tools/generate_sample_dxf.py first",
            expected=str(mullion_dxf),
        )

    # ---------------------------------------------------------------- 1. CAD
    _rule(console, "1. Section analysis")
    from .structural import analyse_dxf

    properties, section = analyse_dxf(
        str(mullion_dxf), profile_id="MB70-MULLION", material="en-aw-6060-t66"
    )
    results["section"] = properties
    _say(
        console,
        f"  {mullion_dxf.name}: A = {properties.area:,.1f} mm2, "
        f"Ix = {properties.ixx:,.0f} mm4, Iy = {properties.iyy:,.0f} mm4, "
        f"J = {properties.j:,.0f} mm4 ({properties.torsion_method}), "
        f"{properties.mass_per_metre:.3f} kg/m",
    )

    # ------------------------------------------------------- 2. Design check
    _rule(console, "2. Structural verification")
    from .models.materials import get_material
    from .structural import LoadCase, check_member, maximum_span, wind_line_load

    material = get_material("en-aw-6060-t66")
    check = check_member(
        properties, material, span=3000.0,
        load=LoadCase(lateral_line_load=wind_line_load(1.2, 1500.0)),
        member_name="MB70 mullion",
    )
    results["check"] = check
    for entry in check.results:
        marker = "[green]PASS[/]" if entry.passes else "[red]FAIL[/]"
        _say(console, f"  {marker} {entry.name}: {entry.utilisation * 100:.1f}% utilised")
    span_limit = maximum_span(properties, material, pressure_kn_m2=1.2, tributary_width_mm=1500.0)
    results["max_span"] = span_limit
    _say(console, f"  Maximum span at 1.2 kN/m2 over a 1.5 m bay: [bold]{span_limit:,.0f} mm[/]")

    # ------------------------------------------------------------ 3. Elements
    _rule(console, "3. Element design")
    from .elements import Cell, ElementKind, Opening, OpeningType, Sash, build_elements, collect_cut_items

    window = Opening(
        name="W-04", width=2400.0, height=1800.0, quantity=6, mullion_positions=[800.0, 1600.0]
    )
    window.set_cell(Cell(column=1, row=0, sash=Sash(opening_type=OpeningType.TILT_TURN)))
    door = Opening(name="D-01", kind=ElementKind.DOOR, width=1100.0, height=2400.0, quantity=2)
    door.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.DOOR)))

    builds = build_elements([window, door], sill_height=900.0)
    results["builds"] = builds
    for build in builds:
        summary = build.summary()
        _say(
            console,
            f"  {summary['name']}: {summary['pieces']} pieces, "
            f"{summary['glass_panes']} panes ({summary['glass_area_m2']} m2, "
            f"{summary['glass_mass_kg']} kg), {summary['hardware_items']} hardware items",
        )
    compliance = [w for build in builds for w in build.warnings if "safety glass" in w]
    if compliance:
        _say(console, f"  [yellow]{len(compliance)} pane(s) need safety glass[/]")

    # ------------------------------------------------------------- 4. Nesting
    _rule(console, "4. Cutting optimisation")
    from .models.orders import Project
    from .nesting import nest_project

    project = Project(name="Tower A", customer="Acme Facades", items=collect_cut_items(builds))
    report = nest_project(project)
    results["nesting"] = report
    for profile_id, result in sorted(report.results.items()):
        _say(
            console,
            f"  {profile_id}: {result.bar_count} bars, {result.total_pieces} pieces, "
            f"{result.yield_pct:.2f}% yield ({result.strategy})",
        )
    _say(
        console,
        f"  [bold]Overall {report.total_bars} bars, {report.overall_yield_pct:.2f}% yield[/]",
    )

    # ----------------------------------------------------------------- 5. CNC
    _rule(console, "5. Machine code")
    from .cnc import MachiningJob, PieceProgram, expand_macros, get_driver
    from .models.machines import Clamp, MachineDefinition, Tool, ToolLibrary, ToolType
    from .models.profile import Face, MachiningMacro

    tools = ToolLibrary(
        id="demo", name="Demo magazine",
        tools=[
            Tool(number=3, name="D5 drill", tool_type=ToolType.DRILL, diameter=5.0, flute_length=40.0),
            Tool(number=5, name="D8 end mill", tool_type=ToolType.END_MILL, diameter=8.0, flute_length=35.0),
            Tool(number=7, name="D6 slot mill", tool_type=ToolType.SLOT_MILL, diameter=6.0, flute_length=30.0),
            Tool(number=9, name="D12 end mill", tool_type=ToolType.END_MILL, diameter=12.0, flute_length=50.0),
        ],
    )
    machine = MachineDefinition(
        id="sbz151", name="SBZ 151", vendor="Elumatec", model="SBZ151",
        post_processor="elumatec.ncx", axis_count=5, machinable_faces=set(Face),
        clamps=[Clamp(id=f"C{i}", position=p, width=120.0) for i, p in enumerate([400.0, 1200.0, 2000.0], 1)],
    )
    macros = [
        MachiningMacro(macro_id="lock.euro_cylinder", face=Face.FRONT, position_x=1200.0,
                       position_y=30.0, depth=25.0, tool_id=5),
        MachiningMacro(macro_id="hinge.standard", face=Face.FRONT, position_x=400.0,
                       position_y=30.0, depth=12.0, tool_id=9),
        MachiningMacro(macro_id="drainage.slots", face=Face.BOTTOM, position_x=300.0,
                       position_y=0.0, depth=8.0, tool_id=7, parameters={"count": 2, "spacing": 900.0}),
        MachiningMacro(macro_id="notch.akm", face=Face.TOP, position_x=0.0, position_y=0.0,
                       depth=18.0, tool_id=9, from_right_end=True, parameters={"length": 25.0}),
    ]
    piece = PieceProgram(
        piece_id="PC-101", profile_id="MB70-MULLION", length=2450.0,
        angle_left=45.0, angle_right=45.0,
        operations=expand_macros(macros, bar_length=2450.0),
        mark="W-04 mullion",
    )
    job = MachiningJob(
        machine=machine, name="Tower A", pieces=[piece], tool_library=tools,
        customer="Acme Facades",
    )
    clamp_warnings = job.plan_all_clamps()
    plan = piece.clamp_plan
    _say(console, f"  Clamp planning: {plan.summary()}")
    for warning in clamp_warnings:
        _say(console, f"  [yellow]{warning}[/]")

    nc_dir = output / "nc"
    posted: dict[str, int] = {}
    for key in ("elumatec.ncx", "elumatec.dgx", "schueco.mco", "kaban.kbn",
                "emmegi.campro", "fom.cam", "iso.gcode"):
        files = get_driver(key).post(job)
        for result in files:
            result.write(nc_dir)
        posted[key] = sum(r.size for r in files)
    results["posted"] = posted
    _say(console, f"  Posted {len(posted)} formats into {nc_dir}")
    for key, size in posted.items():
        _say(console, f"    [dim]{key:<18} {size:>7,} bytes[/]")

    # -------------------------------------------------------------- 6. Quoting
    _rule(console, "6. Costing and quotation")
    from .quoting import (
        PriceBreak, PriceEntry, PricingPolicy, LabourRates, Supplier,
        build_bom, build_quotation, register_supplier,
    )

    register_supplier(Supplier(
        id="demo-alu", name="Demo Aluminium", currency="EUR", categories=["profile"],
        discount_pct=10.0, surcharge_pct=3.0,
        entries=[PriceEntry(code=code, price=price, unit="pc") for code, price in
                 [("GEN-FRAME", 62.0), ("GEN-SASH", 55.0), ("GEN-MULLION", 71.0),
                  ("GEN-TRANSOM", 68.0), ("GEN-BEAD", 21.0)]],
    ))
    register_supplier(Supplier(
        id="demo-glass", name="Demo Glass", currency="EUR", categories=["glass"],
        entries=[PriceEntry(code="dgu-6-16-4", price=48.0, unit="m2", minimum_quantity=0.5,
                            breaks=[PriceBreak(min_quantity=50.0, price=43.0)])],
    ))
    register_supplier(Supplier(
        id="demo-hw", name="Demo Hardware", currency="EUR", categories=["hardware", "gasket"],
        entries=[PriceEntry(code=code, price=price) for code, price in
                 [("HW-TT-KIT", 86.0), ("HW-HANDLE", 22.0), ("HW-CORNER", 11.0),
                  ("HW-HINGE-EXTRA", 14.0), ("HW-DOOR-HINGE", 19.0), ("HW-MPL", 96.0),
                  ("HW-CYL", 28.0), ("HW-DOOR-HANDLE", 44.0),
                  ("GK-IN", 1.8), ("GK-OUT", 1.8), ("GK-WS", 2.4)]],
    ))

    bom = build_bom(builds, project_id=project.project_id, project_name=project.name,
                    nesting=report, currency="EUR")
    quote = build_quotation(
        builds, bom, project_name=project.name, customer=project.customer,
        policy=PricingPolicy(margin_pct=26.0, overhead_pct=12.0, tax_pct=17.0, currency="EUR"),
        labour=LabourRates(hourly_rate=52.0, currency="EUR"),
    )
    results["bom"] = bom
    results["quote"] = quote
    for label, value in quote.breakdown():
        _say(console, f"  {label:<28} {value:>12,.2f} EUR")
    _say(console, f"  [bold]{quote.metadata['price_per_m2']:,.2f} EUR/m2[/] over "
                  f"{quote.metadata['total_area_m2']} m2")
    for warning in quote.warnings[:3]:
        _say(console, f"  [yellow]-[/] {warning}")

    # ------------------------------------------------------------------ 7. MES
    _rule(console, "7. Shop floor")
    from .mes import ItemKind, Stage, work_order_from_builds, write_job_card

    order = work_order_from_builds(builds, project_id=project.project_id, name=project.name)
    for item in order.by_kind(ItemKind.PROFILE_PIECE)[:12]:
        item.advance(Stage.CUT, operator="demo", station="SAW-1")
    card = write_job_card(order, str(output / "job-card.html"), builds)
    results["work_order"] = order
    _say(
        console,
        f"  Work order {order.work_order_id}: {len(order)} items, "
        f"{order.progress * 100:.1f}% complete",
    )
    bottleneck = order.bottleneck()
    if bottleneck:
        _say(console, f"  Busiest stage: {bottleneck[0].value} ({bottleneck[1]} items)")
    _say(console, f"  Job card written to {card}")

    # -------------------------------------------------------------- 8. Plumbing
    _rule(console, "8. Water service")
    from .plumbing import COPPER_EN1057, ServiceType, size_pipe

    sizing = size_pipe(
        1.2, 45.0, COPPER_EN1057, service=ServiceType.COLD_WATER,
        fittings={"elbow_90_long": 8, "gate_valve_open": 2}, height_gain_m=12.0,
        available_pressure=250_000.0,
    )
    results["pipe"] = sizing
    _say(console, f"  {sizing.describe()}")

    # ---------------------------------------------------------------- summary
    _rule(console, "Artefacts")
    for path in sorted(output.rglob("*")):
        if path.is_file():
            _say(console, f"  {path.relative_to(output)}  [dim]{path.stat().st_size:,} bytes[/]")

    _log.info("Demo complete; artefacts in %s", output)
    return results


__all__ = ["run_demo"]
