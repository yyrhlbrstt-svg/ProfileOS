"""The routes the phone talks to.

Mounted under ``/m`` on the same service the office already runs, so there is
one process, one set of data and one thing to shut down. Everything except the
page itself and the pairing exchange requires a device token, and each route
also names the scope it needs — a phone paired for measuring cannot advance a
production stage even if it asks.

The whole surface is deliberately small. This is the part of the system exposed
to a device that can be left in a taxi.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .measure import SiteMeasurement
from .pairing import Device, PairingError
from .state import STATE, MobileState
from .ui import render

_log = get_logger("mobile.app")

#: Stages a phone may set. Scrapping something and shipping it are decisions
#: with money attached, and they are made at a desk.
FLOOR_STAGES = ("cut", "machined", "assembled", "glazed", "inspected", "rework")

def _stage_label(stage: str, language: str | None = None) -> str:
    """A stage name in the operator's language, from the shared vocabulary."""
    from ..i18n import translate

    return translate(f"stage.{stage}", language)

#: Flow order, so the counts read the way work moves rather than alphabetically.
_STAGE_ORDER = {
    "planned": 0, "cut": 1, "machined": 2, "assembled": 3,
    "glazed": 4, "inspected": 5, "shipped": 6, "rework": 7, "scrapped": 8,
}

_STAGE_TONE = {
    "planned": "", "cut": "", "machined": "", "assembled": "",
    "glazed": "ok", "inspected": "ok", "shipped": "ok",
    "rework": "warn", "scrapped": "bad",
}


# The request bodies live at module level rather than inside the factory. With
# ``from __future__ import annotations`` every annotation is a string, and
# FastAPI resolves those against the *module* globals — a model defined inside
# the function is invisible to it and every field silently becomes a query
# parameter. The symptom is a 422 on a request that carried a perfectly good
# body, which is a long way from the cause.
class PairRequest(BaseModel):
    code: str
    description: str = ""


class ScanRequest(BaseModel):
    payload: str
    stage: str


class MeasurementRequest(BaseModel):
    reference: str
    widths: list[float] = Field(default_factory=list)
    heights: list[float] = Field(default_factory=list)
    diagonals: list[float] | None = None
    note: str = ""
    project_id: str = ""
    #: Set by the phone, so a measurement sent twice is kept once.
    client_id: str = ""
    #: When it was taken, by the phone's clock — a measurement kept on the
    #: phone without signal and sent hours later keeps the time it was made.
    measured_at: str = ""


class HandoverRequest(BaseModel):
    job_id: str
    #: Who signed — the customer, or whoever received the work for them.
    signed_by: str
    #: The signature, drawn on the phone: ``data:image/png;base64,…``.
    signature: str
    #: What is left to put right, one line each — the punch list.
    snags: list[str] = Field(default_factory=list)
    note: str = ""


#: What a photo from site can be of, in the job's own words.
PHOTO_KINDS = ("opening_photo", "progress_photo", "defect_photo", "before_photo",
               "after_photo", "handover_photo", "survey_photo")


class CheckRequest(BaseModel):
    width: float
    height: float
    opening_type: str = "fixed"
    sill_height: float = 0.0


try:  # FastAPI is an optional extra; the desktop application does not need it.
    from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
    from fastapi.responses import HTMLResponse, PlainTextResponse
except ImportError:  # pragma: no cover - exercised only without the extra
    APIRouter = Header = HTTPException = Request = None  # type: ignore[assignment]
    File = Form = UploadFile = None  # type: ignore[assignment]
    HTMLResponse = PlainTextResponse = None  # type: ignore[assignment]


def build_router(state: MobileState | None = None) -> Any:
    """Create the mobile router, mounted under ``/m``."""
    if APIRouter is None:
        raise ProfileOSError(
            "The mobile terminal needs FastAPI (pip install 'profileos[api]')."
        )

    router = APIRouter(prefix="/m", tags=["mobile"])

    def _t(key: str, language: str | None = None) -> str:
        from ..i18n import translate

        return translate(key, language)

    def current() -> MobileState:
        # Looked up per request rather than captured, so `configure()` during a
        # test or a reload in the office is picked up without a restart.
        from . import state as state_module

        return state if state is not None else state_module.STATE

    def device_for(
        device_id: str | None,
        token: str | None,
        scope: str,
        language: str | None = None,
    ) -> Device:
        from ..i18n import translate

        if not device_id or not token:
            raise HTTPException(status_code=401, detail=translate("mobile.not_paired", language))
        found = current().registry.authenticate(device_id, token)
        if found is None:
            raise HTTPException(status_code=401, detail=translate("mobile.not_paired", language))
        if not found.may(scope):
            raise HTTPException(
                status_code=403, detail=translate("mobile.no_permission", language)
            )
        return found

    # -- the page ----------------------------------------------------------- #
    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def page(
        request: Request,
        lang: str | None = None,
        accept_language: str | None = Header(default=None),
    ) -> str:
        """The terminal, in the reader's language.

        An explicit ``?lang=`` wins, because somebody who picked a language
        meant it; otherwise the phone's own setting decides, which is right far
        more often than the office's default is.
        """
        from ..branding import active_brand
        from ..i18n import get_locale, negotiate

        brand = active_brand()
        locale = get_locale(lang) if lang else negotiate(accept_language)
        station = current().station or request.url.hostname or ""
        return render(
            title=getattr(brand, "name", None) or "ProfileOS",
            subtitle=station,
            base=str(request.url_for("mobile_pair")).rsplit("/api/pair", 1)[0],
            language=locale.language,
        )

    # -- pairing ------------------------------------------------------------ #
    @router.post("/api/pair", name="mobile_pair")
    def pair(request: PairRequest) -> dict[str, Any]:
        """Exchange a code issued on the unlocked office machine for a token."""
        try:
            device, token = current().registry.redeem(
                request.code, description=request.description
            )
        except PairingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "device_id": device.device_id,
            "token": token,
            "name": device.name,
            "scopes": list(device.scopes),
            "station": current().station,
        }

    @router.get("/api/me")
    def me(
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        device = device_for(x_device_id, x_device_token, "jobs")
        return {
            "name": device.name,
            "scopes": list(device.scopes),
            "paired_at": device.paired_at.isoformat(),
        }

    # -- the floor ---------------------------------------------------------- #
    def _saved_orders() -> list[tuple[str, Any]]:
        from ..mes.store import open_work_orders

        try:
            return open_work_orders()
        except Exception:  # noqa: BLE001 - the phone shows an empty floor
            _log.exception("Could not read the jobs' work orders")
            return []

    def _floor_order() -> Any:
        """What the floor is building: an order set on this server, or every
        order released in the jobs being built, as one list."""
        if current().work_order is not None:
            return current().work_order
        saved = _saved_orders()
        if not saved:
            return None
        if len(saved) == 1:
            return saved[0][1]
        from ..mes.tracking import WorkOrder

        combined = WorkOrder(work_order_id="", name=f"⁦{len(saved)}⁩ פרויקטים בייצור")
        for _job_id, order in saved:
            combined.items.extend(order.items)
        return combined

    def _order_holding(payload: str) -> tuple[Any, str]:
        """The order a scanned code belongs to, and its job (empty when the
        order lives only on this server)."""
        if current().work_order is not None:
            return current().work_order, ""
        for job_id, order in _saved_orders():
            if order.by_barcode(payload) or order.find(payload):
                return order, job_id
        saved = _saved_orders()
        return (saved[0][1], saved[0][0]) if saved else (None, "")

    @router.get("/api/jobs")
    def jobs(
        lang: str | None = None,
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
        accept_language: str | None = Header(default=None),
    ) -> dict[str, Any]:
        from ..i18n import get_locale, negotiate

        language = (get_locale(lang) if lang else negotiate(accept_language)).code
        device_for(x_device_id, x_device_token, "jobs", language)
        order = _floor_order()
        stages = [{"id": s, "label": _stage_label(s, language)} for s in FLOOR_STAGES]
        if order is None:
            return {"name": "", "items": [], "total": 0, "done": 0,
                    "progress": 0.0, "stages": stages}
        items = [
            {
                "ref": item.item_id,
                "description": item.description,
                "stage": _stage_label(item.stage.value, language),
                "tone": _STAGE_TONE.get(item.stage.value, ""),
            }
            # The floor cares about what is still open, so the finished items
            # are not what fills a phone screen.
            for item in sorted(order, key=lambda i: (i.stage.order, i.item_id))[:60]
        ]
        counts = [
            {"label": _stage_label(stage, language), "n": count}
            for stage, count in sorted(
                order.stage_counts().items(),
                key=lambda pair: _STAGE_ORDER.get(pair[0], 99),
            )
            if count
        ]
        return {
            "name": order.name or order.work_order_id,
            "items": items,
            "total": len(order),
            "counts": counts,
            "progress": order.progress * 100.0,
            "stages": stages,
        }

    @router.post("/api/scan")
    def scan(
        request: ScanRequest,
        lang: str | None = None,
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
        accept_language: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Advance one item. The refusal reasons come from the tracker itself."""
        from ..i18n import get_locale, negotiate
        from ..mes.tracking import Stage

        language = (get_locale(lang) if lang else negotiate(accept_language)).code
        device = device_for(x_device_id, x_device_token, "jobs", language)
        order, job_id = _order_holding(request.payload)
        if order is None:
            raise HTTPException(
                status_code=409,
                detail=_t("mobile.no_work_order", language),
            )
        if request.stage not in FLOOR_STAGES:
            raise HTTPException(
                status_code=403,
                detail=_t("mobile.office_decision", language),
            )
        # The tracker answers (ok, message) rather than raising: its message
        # names the item and says which transitions were allowed, which is
        # exactly what the operator standing at the bench needs to read.
        ok, message = order.scan(
            request.payload,
            Stage(request.stage),
            operator=device.name,
            station=current().station or "mobile",
        )
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        if job_id:
            # Kept in the job, merged with the office's own scans — the
            # desktop's shop-floor page shows it the next time it looks.
            from ..mes.store import save_work_order

            try:
                order = save_work_order(order, job_id)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=409, detail=f"הסריקה לא נשמרה: {exc}"
                ) from exc
        item = order.by_barcode(request.payload) or order.find(request.payload)
        return {
            "ref": item.item_id if item else request.payload,
            "stage": _stage_label(item.stage.value, language) if item else "",
        }

    # -- site measuring ----------------------------------------------------- #
    import threading

    _MEASURING = threading.Lock()

    def _when_taken(text: str) -> Any:
        """The phone's time for the measurement, if it is a believable one:
        not in the future, not older than a month."""
        from datetime import datetime, timedelta, timezone

        if not text:
            return None
        try:
            taken = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if taken > now + timedelta(minutes=5) or taken < now - timedelta(days=31):
            return None
        return taken

    def _triple(values: list[float]) -> tuple[float, float, float]:
        padded = list(values[:3]) + [0.0] * (3 - len(values[:3]))
        return (padded[0], padded[1], padded[2])

    @router.post("/api/measurements")
    def add_measurement(
        request: MeasurementRequest,
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        device = device_for(x_device_id, x_device_token, "measure")
        with _MEASURING:
            return _add_measurement(request, device)

    def _add_measurement(request: MeasurementRequest, device: Any) -> dict[str, Any]:
        # One at a time: two copies of the same measurement arriving together
        # (two tabs flushing on "online") must both see the first one.
        client_id = request.client_id.strip()[:64]
        already = current().measurements.by_client_id(client_id)
        if already is not None:
            return {
                "reference": already.reference,
                "width": round(already.width),
                "height": round(already.height),
                "problems": already.problems(),
                "duplicate": True,
            }
        if not request.reference.strip():
            raise HTTPException(
                status_code=422,
                detail=_t("mobile.need_reference"),
            )
        record = SiteMeasurement(
            reference=request.reference.strip().upper(),
            project_id=request.project_id,
            widths=_triple(request.widths),
            heights=_triple(request.heights),
            diagonals=(
                (request.diagonals[0], request.diagonals[1])
                if request.diagonals and len(request.diagonals) >= 2
                else None
            ),
            note=request.note,
            measured_by=device.name,
            device=device.device_id,
            client_id=client_id,
        )
        taken = _when_taken(request.measured_at)
        if taken is not None:
            record.measured_at = taken
        if record.width <= 0 or record.height <= 0:
            raise HTTPException(
                status_code=422,
                detail=_t("mobile.need_sizes"),
            )
        from .measure import impossible

        wrong = impossible(record.widths, record.heights)
        if wrong:
            raise HTTPException(status_code=422, detail=wrong)
        current().measurements.add(record)
        return {
            "reference": record.reference,
            "width": round(record.width),
            "height": round(record.height),
            "problems": record.problems(),
        }

    @router.get("/api/projects")
    def projects(
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """The jobs still to be measured or made, with their openings' marks."""
        from ..projects import JobStatus, default_store

        device_for(x_device_id, x_device_token, "measure")
        try:
            jobs = default_store().all()
        except Exception:  # noqa: BLE001 - the phone measures without a list
            _log.exception("Could not list the jobs for the phone")
            jobs = []
        listed = []
        for job in jobs:
            if job.status in (JobStatus.INSTALLED, JobStatus.LOST):
                continue
            openings = [
                str(opening.reference or opening.name)
                for opening in (job.schedule.openings if job.schedule else [])
            ]
            name = " · ".join(part for part in (job.customer_name, job.name) if part)
            listed.append({"id": job.job_id, "name": name or job.job_id, "openings": openings})
        return {"projects": listed[:80]}

    # -- on site: photos and the handover ---------------------------------- #
    def _open_job(job_id: str) -> Any:
        from ..projects import JobStatus, default_store

        try:
            job = default_store().load(job_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail="הפרויקט לא נמצא במשרד") from exc
        if job.status is JobStatus.LOST:
            raise HTTPException(status_code=409, detail="הפרויקט סגור")
        return job

    @router.post("/api/photos")
    def add_photo(
        job_id: str = Form(...),
        kind: str = Form("progress_photo"),
        element: str = Form(""),
        caption: str = Form(""),
        photo: UploadFile = File(...),
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """A photograph taken on site, straight into the job's files."""
        import tempfile
        from pathlib import Path as _Path

        from ..projects.attachments import MAX_BYTES, AttachmentKind, attachments_for

        device = device_for(x_device_id, x_device_token, "measure")
        if kind not in PHOTO_KINDS:
            raise HTTPException(status_code=422, detail="סוג צילום לא מוכר")
        job = _open_job(job_id)
        # Read in the server's worker thread, not its event loop: copying,
        # checksumming and writing a photo must not stall the other phones.
        data = photo.file.read(MAX_BYTES + 1)
        if not data:
            raise HTTPException(status_code=422, detail="הצילום ריק — צלמו שוב")
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail="הצילום גדול מדי")
        suffix = _Path(photo.filename or "photo.jpg").suffix.lower() or ".jpg"
        with tempfile.TemporaryDirectory() as scratch:
            source = _Path(scratch) / f"site{suffix}"
            source.write_bytes(data)
            try:
                saved = attachments_for(job.job_id).add(
                    source, kind=AttachmentKind(kind), caption=caption.strip()[:200],
                    added_by=device.name, element=element.strip().upper()[:20],
                )
            except Exception as exc:  # noqa: BLE001 - said to the phone in words
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"name": saved.name, "kind": saved.kind.hebrew, "job": job.name}

    @router.post("/api/handover")
    def handover(
        request: HandoverRequest,
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """The customer signs on the phone that the work was handed over —
        with what is still to put right written down at that moment."""
        import base64
        import tempfile
        from pathlib import Path as _Path

        from ..projects import JobStatus, Snag, default_store
        from ..projects.attachments import AttachmentKind, attachments_for

        device = device_for(x_device_id, x_device_token, "measure")
        signed_by = request.signed_by.strip()
        if not signed_by:
            raise HTTPException(status_code=422, detail="מי חותם? כתבו את שם המקבל")
        prefix = "data:image/png;base64,"
        if not request.signature.startswith(prefix):
            raise HTTPException(status_code=422, detail="אין חתימה — בקשו מהלקוח לחתום")
        try:
            image = base64.b64decode(request.signature[len(prefix):], validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="החתימה לא נקראה — חתמו שוב") from exc
        if len(image) < 200 or len(image) > 2_000_000:
            raise HTTPException(status_code=422, detail="החתימה לא נקראה — חתמו שוב")
        job = _open_job(request.job_id)
        snags = [line.strip() for line in request.snags if line.strip()][:40]
        try:
            with tempfile.TemporaryDirectory() as scratch:
                source = _Path(scratch) / "signature.png"
                source.write_bytes(image)
                caption = f"מסירה — נחתם ע״י {signed_by}"
                if snags:
                    caption += f" · ⁦{len(snags)}⁩ תיקונים פתוחים"
                attachments_for(job.job_id).add(
                    source, kind=AttachmentKind.SIGNED_DELIVERY, caption=caption,
                    added_by=device.name,
                )
            store = default_store()
            fresh = store.load(job.job_id)
            for line in snags:
                fresh.snags.append(Snag(description=line[:300]))
            moved = False
            note = f"נמסר — נחתם בטלפון ע״י {signed_by}"
            if fresh.status is JobStatus.WON:
                fresh.advance(JobStatus.IN_PRODUCTION, note)
            if fresh.status is JobStatus.IN_PRODUCTION:
                fresh.advance(JobStatus.INSTALLED, note)
                moved = True
            store.save(fresh)
        except Exception as exc:  # noqa: BLE001 - said to the installer in words
            _log.exception("Handover of %s not saved", job.job_id)
            raise HTTPException(
                status_code=500, detail=f"המסירה לא נשמרה במשרד — נסו שוב ({exc})"
            ) from exc
        return {
            "job": fresh.name,
            "status": fresh.status.hebrew,
            "installed": moved,
            "snags": len(snags),
        }

    @router.get("/api/measurements")
    def list_measurements(
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        device_for(x_device_id, x_device_token, "measure")
        return {
            "records": [
                {
                    "reference": record.reference,
                    "width": round(record.width),
                    "height": round(record.height),
                    "when": record.measured_at.strftime("%d/%m %H:%M"),
                    "by": record.measured_by,
                    "problems": record.problems(),
                }
                for record in current().measurements.records[:40]
            ]
        }

    # -- a quick check ------------------------------------------------------ #
    @router.post("/api/check")
    def check(
        request: CheckRequest,
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Whether an opening this size can be made, answered on site."""
        from ..elements.builder import ElementBuilder
        from ..elements.feasibility import Severity, check_element
        from ..elements.model import Cell, Opening, OpeningType, Sash
        from ..systems import DIRECTORY

        device_for(x_device_id, x_device_token, "measure")
        if request.width <= 0 or request.height <= 0:
            raise HTTPException(
                status_code=422,
                detail=_t("mobile.need_sizes"),
            )
        from .measure import impossible

        wrong = impossible([request.width], [request.height])
        if wrong:
            raise HTTPException(status_code=422, detail=wrong)
        try:
            opening_type = OpeningType(request.opening_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="סוג פתיחה לא מוכר") from exc

        cells = (
            [Cell(column=0, row=0, sash=Sash(opening_type=opening_type))]
            if opening_type.is_operable
            else [Cell(column=0, row=0)]
        )
        system = current().system_id
        builder = (
            ElementBuilder.for_system(system)
            if DIRECTORY.get(system) is not None
            else ElementBuilder()
        )
        try:
            build = builder.build(
                Opening(
                    element_id="CHECK",
                    width=request.width,
                    height=request.height,
                    cells=cells,
                ),
                sill_height=request.sill_height,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the phone as-is
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        report = check_element(build, sill_height=request.sill_height)
        tone = {Severity.BLOCKER: "bad", Severity.WARNING: "warn", Severity.NOTE: ""}
        pane = build.glass[0] if build.glass else None
        return {
            "can_be_made": report.can_be_made,
            "verdict": (
                "ניתן לייצור" if report.can_be_made
                else "לא ניתן לייצור: " + report.blockers[0].hebrew
            ),
            "glass": (
                f"{pane.width:.0f} × {pane.height:.0f} · {pane.mass:.0f} ק\"ג" if pane else ""
            ),
            "findings": [
                {
                    "severity": finding.severity.hebrew,
                    "what": finding.hebrew or finding.english,
                    # The opening checked on the phone has no mark of its own.
                    "where": finding.subject.replace("⁦CHECK⁩", "").replace("CHECK", "").strip(),
                    "tone": tone[finding.severity],
                }
                for finding in report.sorted()
            ],
        }

    # -- drawings ----------------------------------------------------------- #
    @router.get("/api/elements")
    def elements(
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        device_for(x_device_id, x_device_token, "drawings")
        seen: dict[str, Any] = {}
        for reference, build in current().builds.items():
            seen.setdefault(id(build), (reference, build))
        return {
            "elements": [
                {
                    "ref": reference,
                    "size": f"{build.opening.width:.0f} × {build.opening.height:.0f}",
                }
                for reference, build in seen.values()
            ]
        }

    @router.get("/api/elements/{reference}/elevation.svg", response_class=PlainTextResponse)
    def elevation_svg(
        reference: str,
        x_device_id: str | None = Header(default=None),
        x_device_token: str | None = Header(default=None),
    ) -> str:
        from ..drawing.elevation import ElevationStyle, elevation
        from ..drawing.svg import to_svg

        device_for(x_device_id, x_device_token, "drawings")
        build = current().builds.get(reference)
        if build is None:
            raise HTTPException(status_code=404, detail="אין אלמנט כזה")
        drawing = elevation(build, style=ElevationStyle(scale=20, show_glass_sizes=True))
        return to_svg(drawing, scale=20, background="#ffffff")

    return router


__all__ = ["FLOOR_STAGES", "build_router"]
