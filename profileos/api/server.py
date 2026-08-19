"""HTTP service API.

Exposes the engines over REST so the desktop application, a tablet on the shop
floor, or another system can drive them. FastAPI gives typed request/response
models and an OpenAPI schema for free, and every response model here reuses the
same pydantic types the engines use internally — so the API cannot drift from
the library.

Start it with ``profileos serve`` and read the generated docs at ``/docs``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .. import __version__
from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.profiling import REGISTRY as PROFILER

_log = get_logger("api")

try:
    from fastapi import Body, FastAPI, HTTPException, UploadFile, File
    from fastapi.responses import HTMLResponse, PlainTextResponse
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError(
        "The service API needs FastAPI (pip install 'profileos[api]')"
    ) from exc


app = FastAPI(
    title="ProfileOS",
    version=__version__,
    description=(
        "CAD/CAM, structural analysis, cutting optimisation, CNC post-processing, "
        "glazing, costing and shop-floor tracking for architectural aluminium."
    ),
)


def _handle(exc: ProfileOSError) -> HTTPException:
    """Convert an engine error into a 422 with its structured context."""
    return HTTPException(status_code=422, detail={"message": exc.message, **exc.context})


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

@app.get("/health", tags=["service"])
def health() -> dict[str, Any]:
    """Liveness probe plus the optional components that are installed."""
    from ..mes.barcode import qr_available
    from ..nesting.milp import ortools_available
    from ..structural.torsion import sectionproperties_available

    return {
        "status": "ok",
        "version": __version__,
        "components": {
            "torsion_fea": sectionproperties_available(),
            "milp_nesting": ortools_available(),
            "qr_labels": qr_available(),
        },
    }


@app.get("/performance", tags=["service"])
def performance() -> list[dict[str, Any]]:
    """Timing measurements collected since the process started."""
    return PROFILER.snapshot()


@app.get("/drivers", tags=["cnc"])
def drivers() -> list[dict[str, Any]]:
    """Machine post-processors available for posting."""
    from ..cnc import available_drivers

    return available_drivers()


@app.get("/registries", tags=["service"])
def registries() -> dict[str, list[dict[str, Any]]]:
    """Every plugin registry and its contents."""
    from ..core.registry import registry_report

    return registry_report()


# --------------------------------------------------------------------------- #
# Section analysis
# --------------------------------------------------------------------------- #

@app.post("/section/analyse", tags=["structural"])
async def analyse_upload(
    file: UploadFile = File(..., description="DXF profile cross-section"),
    material: str = "en-aw-6060-t66",
    torsion: bool = True,
) -> dict[str, Any]:
    """Analyse an uploaded DXF cross-section."""
    from ..structural import analyse_dxf

    suffix = Path(file.filename or "section.dxf").suffix or ".dxf"
    # The DXF reader works on paths, so the upload is staged to a temp file
    # that is removed as soon as the analysis completes.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(await file.read())
        temp_path = Path(handle.name)

    try:
        properties, section = analyse_dxf(
            str(temp_path),
            profile_id=Path(file.filename or "section").stem,
            material=material,
            compute_torsion_constants=torsion,
        )
    except ProfileOSError as exc:
        raise _handle(exc) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    return {
        "properties": properties.model_dump(mode="json"),
        "geometry": {
            "area": section.area,
            "width": section.width,
            "height": section.height,
            "regions": len(section.topology.regions),
            "chambers": section.topology.chamber_count,
        },
        "validation": [
            {"severity": i.severity, "code": i.code, "message": i.message}
            for i in section.validation.issues
        ],
    }


# --------------------------------------------------------------------------- #
# Elements
# --------------------------------------------------------------------------- #

class ElementRequest(BaseModel):
    """One element to design."""

    name: str = "Element"
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    system_id: str = "generic"
    kind: str = "window"
    mullion_positions: list[float] = Field(default_factory=list)
    transom_positions: list[float] = Field(default_factory=list)
    #: ``[{"column": 1, "row": 0, "opening_type": "tilt_turn"}]``
    sashes: list[dict[str, Any]] = Field(default_factory=list)
    glass_spec_id: Optional[str] = None
    sill_height: float = 0.0


def _to_opening(request: ElementRequest):
    from ..elements import Cell, ElementKind, Opening, OpeningType, Sash

    opening = Opening(
        name=request.name,
        kind=ElementKind(request.kind),
        width=request.width,
        height=request.height,
        quantity=request.quantity,
        system_id=request.system_id,
        mullion_positions=list(request.mullion_positions),
        transom_positions=list(request.transom_positions),
        glass_spec_id=request.glass_spec_id,
    )
    for entry in request.sashes:
        opening.set_cell(
            Cell(
                column=int(entry.get("column", 0)),
                row=int(entry.get("row", 0)),
                sash=Sash(opening_type=OpeningType(entry.get("opening_type", "casement"))),
            )
        )
    return opening


@app.post("/elements/build", tags=["elements"])
def build_element(request: ElementRequest) -> dict[str, Any]:
    """Design one element and return its cut list, glass and hardware."""
    from ..elements import ElementBuilder

    try:
        build = ElementBuilder().build(_to_opening(request), sill_height=request.sill_height)
    except ProfileOSError as exc:
        raise _handle(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "summary": build.summary(),
        "cuts": [
            {
                "profile_id": cut.profile_id, "length": cut.length, "quantity": cut.quantity,
                "angle_left": cut.angle_left, "angle_right": cut.angle_right, "role": cut.role,
            }
            for cut in build.cuts
        ],
        "glass": [
            {
                "mark": panel.mark, "width": panel.width, "height": panel.height,
                "specification": panel.build_up.describe(),
                "u_value": round(panel.build_up.u_value(), 3),
                "area_m2": round(panel.area, 4), "mass_kg": round(panel.mass, 2),
                "safety_required": panel.safety_required,
                "safety_reason": panel.safety_reason,
                "compliant": panel.compliant,
            }
            for panel in build.glass
        ],
        "hardware": [
            {"code": h.code, "name": h.name, "quantity": h.quantity, "unit": h.unit}
            for h in build.hardware
        ],
        "gaskets": [
            {"code": g.code, "name": g.name, "length": g.length, "quantity": g.quantity}
            for g in build.gaskets
        ],
        "warnings": build.warnings,
    }


# --------------------------------------------------------------------------- #
# Nesting
# --------------------------------------------------------------------------- #

class NestRequest(BaseModel):
    """A cutting list to optimise."""

    project_name: str = "Project"
    #: ``[{"profile_id": "P", "length": 2450, "quantity": 4, "angle_left": 45, "angle_right": 45}]``
    items: list[dict[str, Any]]
    stock_lengths: list[float] = Field(default_factory=lambda: [6000.0])
    kerf: float = 3.5
    profile_depth: float = 0.0
    strategy: str = "auto"


@app.post("/nesting/optimise", tags=["nesting"])
def optimise(request: NestRequest) -> dict[str, Any]:
    """Optimise a cutting list onto stock bars."""
    from ..models.orders import CutItem, Project
    from ..nesting import build_problem, nest

    try:
        items = [CutItem.model_validate(entry) for entry in request.items]
    except Exception as exc:  # noqa: BLE001 - user-supplied payload
        raise HTTPException(status_code=422, detail=f"Invalid cut item: {exc}") from exc

    project = Project(name=request.project_name, items=items)
    results: dict[str, Any] = {}
    layouts: dict[str, Any] = {}

    try:
        for profile_id in project.profile_ids():
            problem = build_problem(
                profile_id,
                project.expand_pieces(profile_id),
                stock_lengths=request.stock_lengths,
                kerf=request.kerf,
                profile_depth=request.profile_depth,
            )
            result = nest(problem, strategy=request.strategy)  # type: ignore[arg-type]
            results[profile_id] = result.summary()
            layouts[profile_id] = [
                {
                    "bar": layout.bar_index,
                    "stock_length": layout.stock_length,
                    "remnant": round(layout.remnant_length, 1),
                    "reusable": layout.is_reusable_remnant(problem.min_reusable_remnant),
                    "pieces": [
                        {
                            "length": placement.demand_key.length,
                            "position": round(placement.position, 1),
                            "angle_left": placement.demand_key.angle_left,
                            "angle_right": placement.demand_key.angle_right,
                            "label": placement.label,
                        }
                        for placement in layout.placements
                    ],
                }
                for layout in result.layouts
            ]
    except ProfileOSError as exc:
        raise _handle(exc) from exc

    return {"profiles": results, "layouts": layouts}


# --------------------------------------------------------------------------- #
# Glazing
# --------------------------------------------------------------------------- #

class GlazingRequest(BaseModel):
    """A glazing build-up to evaluate."""

    #: Pane thicknesses [mm], outermost first.
    panes: list[float] = Field(default_factory=lambda: [6.0, 4.0])
    #: Cavity widths [mm]; must be one fewer than the pane count.
    cavities: list[float] = Field(default_factory=lambda: [16.0])
    gas: str = "argon"
    low_e_emissivity: float = Field(default=0.03, gt=0, le=1)
    #: Coated surfaces in the industry's numbering: surfaces run 1..2n from the
    #: outside in, so a double unit has 1 (outdoor face), 2 and 3 (the cavity)
    #: and 4 (indoor face). A coating on 1 or 4 does nothing for the U-value
    #: because it does not face a cavity -- the calculation will show that.
    coated_surfaces: list[int] = Field(default_factory=lambda: [3])
    spacer: str = "warm_edge"


@app.post("/glazing/evaluate", tags=["glazing"])
def evaluate_glazing(request: GlazingRequest) -> dict[str, Any]:
    """Compute the U-value, thickness and mass of a glazing build-up."""
    from ..glazing import Cavity, GasType, GlassBuildUp, Pane, SpacerType

    if len(request.cavities) != len(request.panes) - 1:
        raise HTTPException(
            status_code=422,
            detail=f"{len(request.panes)} panes need {len(request.panes) - 1} cavities",
        )
    surface_count = 2 * len(request.panes)
    for surface in request.coated_surfaces:
        if not (1 <= surface <= surface_count):
            raise HTTPException(
                status_code=422,
                detail=f"Surface {surface} does not exist on a {len(request.panes)}-pane "
                       f"unit (surfaces 1-{surface_count})",
            )

    try:
        panes = []
        for index, thickness in enumerate(request.panes):
            # Pane i owns surfaces 2i+1 (outward) and 2i+2 (inward).
            outward, inward = 2 * index + 1, 2 * index + 2
            panes.append(
                Pane(
                    thickness=thickness,
                    emissivity_outer=(
                        request.low_e_emissivity if outward in request.coated_surfaces else 0.837
                    ),
                    emissivity_inner=(
                        request.low_e_emissivity if inward in request.coated_surfaces else 0.837
                    ),
                )
            )

        unit = GlassBuildUp(
            id="custom",
            name="Custom build-up",
            panes=panes,
            cavities=[Cavity(width=w, gas=GasType(request.gas)) for w in request.cavities],
            spacer=SpacerType(request.spacer),
        )
    except (ValueError, ProfileOSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "description": unit.describe(),
        "u_value": round(unit.u_value(), 4),
        "total_thickness": unit.total_thickness,
        "mass_per_m2": round(unit.mass_per_m2, 2),
        "pane_count": unit.pane_count,
        "is_safety_glass": unit.is_safety_glass,
        "spacer_psi": unit.spacer.psi_value,
        "coated_surfaces": request.coated_surfaces,
    }


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #

class PipeSizingRequest(BaseModel):
    """A pipe run to size."""

    flow_lps: float = Field(gt=0)
    length_m: float = Field(ge=0)
    catalogue: str = "copper-en1057"
    service: str = "cold_water"
    height_gain_m: float = 0.0
    available_pressure_kpa: Optional[float] = None
    fittings: dict[str, int] = Field(default_factory=dict)


@app.post("/plumbing/size", tags=["plumbing"])
def size_pipe_endpoint(request: PipeSizingRequest) -> dict[str, Any]:
    """Select the smallest pipe satisfying the design constraints."""
    from ..plumbing import ServiceType, get_catalogue, size_pipe

    try:
        result = size_pipe(
            request.flow_lps,
            request.length_m,
            get_catalogue(request.catalogue),
            service=ServiceType(request.service),
            fittings=request.fittings,
            height_gain_m=request.height_gain_m,
            available_pressure=(
                request.available_pressure_kpa * 1000.0
                if request.available_pressure_kpa is not None
                else None
            ),
        )
    except ProfileOSError as exc:
        raise _handle(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not result.ok:
        return {"ok": False, "reasons": result.reasons, "rejected": result.rejected}

    return {
        "ok": True,
        "designation": result.size.designation,
        "internal_diameter": result.size.internal_diameter,
        "velocity": round(result.velocity, 3),
        "reynolds": round(result.reynolds, 0),
        "friction_factor": round(result.friction_factor, 6),
        "friction_loss_kpa": round(result.friction_loss / 1000.0, 3),
        "fitting_loss_kpa": round(result.fitting_loss / 1000.0, 3),
        "static_loss_kpa": round(result.static_loss / 1000.0, 3),
        "total_loss_kpa": round(result.total_loss / 1000.0, 3),
        "loss_per_metre_pa": round(result.loss_per_metre, 1),
        "water_content_l_per_m": round(result.size.water_content(), 4),
        "notes": result.reasons,
    }


# --------------------------------------------------------------------------- #
# Shop floor
# --------------------------------------------------------------------------- #

_WORK_ORDERS: dict[str, Any] = {}


class ScanRequest(BaseModel):
    """A shop-floor barcode scan."""

    payload: str
    stage: str
    operator: Optional[str] = None
    station: Optional[str] = None


@app.post("/mes/work-orders", tags=["mes"])
def create_work_order(elements: list[ElementRequest]) -> dict[str, Any]:
    """Design a set of elements and release them as a work order."""
    from ..elements import ElementBuilder
    from ..mes import work_order_from_builds

    builder = ElementBuilder()
    try:
        builds = [builder.build(_to_opening(request)) for request in elements]
    except ProfileOSError as exc:
        raise _handle(exc) from exc

    order = work_order_from_builds(builds, name=elements[0].name if elements else "Work order")
    _WORK_ORDERS[order.work_order_id] = (order, builds)
    return order.summary()


@app.get("/mes/work-orders/{work_order_id}", tags=["mes"])
def get_work_order(work_order_id: str) -> dict[str, Any]:
    """Current state of a work order."""
    entry = _WORK_ORDERS.get(work_order_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown work order")
    order, _ = entry
    return {
        "summary": order.summary(),
        "items": [item.as_dict() for item in order.items],
    }


@app.post("/mes/work-orders/{work_order_id}/scan", tags=["mes"])
def scan(work_order_id: str, request: ScanRequest) -> dict[str, Any]:
    """Record a scan against a work order.

    A refused scan returns 200 with ``ok: false`` and an operator-readable
    message, not an error status: the tablet needs to display the reason, and
    an invalid transition is a normal shop-floor event, not a fault.
    """
    from ..mes import Stage

    entry = _WORK_ORDERS.get(work_order_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown work order")
    order, _ = entry

    try:
        stage = Stage(request.stage)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown stage: {request.stage}") from exc

    ok, message = order.scan(
        request.payload, stage, operator=request.operator, station=request.station
    )
    return {"ok": ok, "message": message, "progress_pct": round(order.progress * 100.0, 1)}


@app.get("/mes/work-orders/{work_order_id}/job-card", response_class=HTMLResponse, tags=["mes"])
def job_card(work_order_id: str) -> str:
    """The tablet-friendly job card for a work order."""
    from ..mes import render_job_card

    entry = _WORK_ORDERS.get(work_order_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown work order")
    order, builds = entry
    return render_job_card(order, builds)


@app.get("/mes/label/{payload}", response_class=PlainTextResponse, tags=["mes"])
def label(payload: str, kind: str = "code128") -> str:
    """Render a scannable label as SVG."""
    from ..mes import code128_svg, qr_available, qr_svg

    try:
        if kind == "qr":
            if not qr_available():
                raise HTTPException(status_code=503, detail="QR backend not installed")
            return qr_svg(payload)
        return code128_svg(payload)
    except ProfileOSError as exc:
        raise _handle(exc) from exc


# --------------------------------------------------------------------------- #
# 3D views
# --------------------------------------------------------------------------- #

class ViewRequest(ElementRequest):
    """An element to model, plus how it should be shown."""

    #: "presentation" (three-quarter, perspective) or "elevation" (head-on).
    view: str = "presentation"
    #: "natural" or "bronze".
    finish: str = "natural"
    show_glass: bool = True


@app.post("/view/svg", response_class=PlainTextResponse, tags=["view"])
def view_svg(request: ViewRequest) -> str:
    """Render an element to printable SVG.

    Text, not an image: the response scales to any sheet and can be dropped
    straight into a quotation or a submittal.
    """
    from ..elements import ElementBuilder
    from ..viz3d import (
        BRONZE_MATERIALS,
        DEFAULT_MATERIALS,
        RenderOptions,
        ViewStyle,
        build_element_scene,
        elevation_camera,
        presentation_camera,
        render_svg,
    )

    try:
        build = ElementBuilder().build(_to_opening(request), sill_height=request.sill_height)
        scene = build_element_scene(build, style=ViewStyle(show_glass=request.show_glass))
        camera = (
            elevation_camera(scene) if request.view == "elevation"
            else presentation_camera(scene)
        )
        return render_svg(
            scene, camera,
            RenderOptions(
                materials=dict(
                    BRONZE_MATERIALS if request.finish == "bronze" else DEFAULT_MATERIALS
                )
            ),
        )
    except ProfileOSError as exc:
        raise _handle(exc) from exc


@app.post("/view/gltf", tags=["view"])
def view_gltf(request: ViewRequest) -> dict[str, Any]:
    """Export an element as glTF 2.0, ready for any 3D tool."""
    import json as _json

    from ..elements import ElementBuilder
    from ..viz3d import ViewStyle, build_element_scene, to_gltf

    try:
        build = ElementBuilder().build(_to_opening(request), sill_height=request.sill_height)
        scene = build_element_scene(build, style=ViewStyle(show_glass=request.show_glass))
        return _json.loads(to_gltf(scene))
    except ProfileOSError as exc:
        raise _handle(exc) from exc


@app.post("/view/viewer", response_class=HTMLResponse, tags=["view"])
def view_viewer(request: ViewRequest) -> str:
    """A self-contained interactive viewer for one element.

    Everything is inlined, so the page works from a memory stick in a site
    office with no network — which is where it is usually opened.
    """
    from ..elements import ElementBuilder
    from ..viz3d import ViewStyle, build_element_scene, render_viewer

    try:
        build = ElementBuilder().build(_to_opening(request), sill_height=request.sill_height)
        scene = build_element_scene(build, style=ViewStyle(show_glass=request.show_glass))
        return render_viewer(scene)
    except ProfileOSError as exc:
        raise _handle(exc) from exc


# --------------------------------------------------------------------------- #
# ERP
# --------------------------------------------------------------------------- #

class ScheduleRequest(BaseModel):
    """A job's work content, and when it is wanted."""

    job_id: str = "JOB"
    elements: int = Field(default=1, ge=0)
    cuts: int = Field(default=0, ge=0)
    machining_operations: int = Field(default=0, ge=0)
    panes: int = Field(default=0, ge=0)
    start: Optional[str] = None
    due: Optional[str] = None


@app.post("/erp/schedule", tags=["erp"])
def erp_schedule(request: ScheduleRequest) -> dict[str, Any]:
    """Place a job on the shop's finite capacity and say when it finishes."""
    from datetime import date as _date

    from ..erp import DEFAULT_WORK_CENTRES, JobDemand, Scheduler

    def parse(value: Optional[str]) -> Optional[_date]:
        if not value:
            return None
        try:
            return _date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail={"message": f"Not a date: {value!r}"}
            ) from exc

    demand = JobDemand(
        job_id=request.job_id, elements=request.elements, cuts=request.cuts,
        machining_operations=request.machining_operations, panes=request.panes,
        due=parse(request.due),
    )
    plan = Scheduler().schedule([demand], start=parse(request.start))
    return {
        "completion": plan.completion[demand.job_id].isoformat(),
        "late_days": plan.late[demand.job_id],
        "operations": [
            {
                "operation": str(operation.operation),
                "work_centre": operation.work_centre,
                "start": operation.start.isoformat(),
                "finish": operation.finish.isoformat(),
                "hours": round(operation.hours, 3),
            }
            for operation in sorted(plan.operations, key=lambda o: (o.start, o.operation))
        ],
        "utilisation": plan.utilisation(DEFAULT_WORK_CENTRES),
        "bottleneck": plan.bottleneck(DEFAULT_WORK_CENTRES),
        "warnings": plan.warnings,
    }


class RequirementsRequest(BaseModel):
    """Gross demand, and what is already on the rack or on order."""

    #: ``{"4301": 486.0}`` — item code to quantity.
    demand: dict[str, float]
    on_hand: dict[str, float] = Field(default_factory=dict)
    allocated: dict[str, float] = Field(default_factory=dict)
    on_order: dict[str, float] = Field(default_factory=dict)


@app.post("/erp/requirements", tags=["erp"])
def erp_requirements(request: RequirementsRequest) -> list[dict[str, Any]]:
    """Turn gross demand into what actually has to be bought."""
    from ..erp import StockItem, StockLedger, requirements

    stock = StockLedger()
    for code in set(request.demand) | set(request.on_hand):
        stock.add_item(StockItem(code, code))
        if request.on_hand.get(code):
            stock.receive(code, request.on_hand[code], 0.0)
        if request.allocated.get(code):
            stock.allocate(code, request.allocated[code])
        if request.on_order.get(code):
            stock.order(code, request.on_order[code])

    return [
        {
            "item": row.item,
            "gross": round(row.gross, 3),
            "free": round(row.free, 3),
            "on_order": round(row.on_order, 3),
            "net": round(row.net, 3),
            "must_order": row.must_order,
            "explain": row.explain(),
        }
        for row in requirements(request.demand, stock)
    ]


@app.get("/erp/vat", tags=["erp"])
def erp_vat(on: str) -> dict[str, Any]:
    """The statutory VAT rate in force on a date."""
    from datetime import date as _date

    from ..erp import ISRAELI_VAT_HISTORY, vat_rate

    try:
        when = _date.fromisoformat(on)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"message": f"Not a date: {on!r}"}
        ) from exc
    return {
        "date": when.isoformat(),
        "rate": vat_rate(when),
        "history": [
            {"from": effective.isoformat(), "rate": rate}
            for effective, rate in ISRAELI_VAT_HISTORY
        ],
    }



__all__ = ["app"]
