"""DXF ingestion.

Reads a profile cross-section drawing and turns it into flattened
:class:`~profileos.geometry.contour.Segment` objects ready for chaining.

Handled entities
----------------
``LINE``, ``LWPOLYLINE``, ``POLYLINE`` (2D), ``ARC``, ``CIRCLE``, ``ELLIPSE``,
``SPLINE``, ``SOLID``/``TRACE`` outlines, and ``INSERT`` block references, which
are resolved recursively through ezdxf's virtual-entity expansion so nested
blocks, scaling and rotation are all applied.

Deliberately ignored
--------------------
Dimensions, text, hatches, leaders, centre marks and construction geometry on
non-printing layers. Those are annotation, not profile boundary, and including
them is the most common reason a naive importer computes a nonsense area.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.config import GeometryDefaults, get_settings
from ..core.errors import DxfReadError
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..core.units import dxf_insunits_to_mm
from ..models.results import GeometryReport
from .contour import Segment
from .primitives import Point, flatten_vertices

_log = get_logger("geometry.dxf")

#: Entity types that carry boundary geometry.
BOUNDARY_TYPES = frozenset(
    {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "SOLID", "TRACE"}
)

#: Entity types that are annotation and never contribute boundary.
ANNOTATION_TYPES = frozenset(
    {
        "DIMENSION",
        "TEXT",
        "MTEXT",
        "LEADER",
        "MULTILEADER",
        "HATCH",
        "ATTDEF",
        "ATTRIB",
        "POINT",
        "IMAGE",
        "WIPEOUT",
        "TOLERANCE",
        "ARC_DIMENSION",
    }
)

#: Layer names matching any of these patterns are skipped by default.
DEFAULT_IGNORED_LAYER_PATTERNS: tuple[str, ...] = (
    r"^dim",
    r"^bemassung",
    r"dimension",
    r"^text",
    r"^annot",
    r"^hatch",
    r"^schraffur",
    r"^defpoints$",
    r"^achse",
    r"^center",
    r"^mittellinie",
    r"^hidden",
    r"^construction",
    r"^hilfslinie",
    r"^title",
    r"^rahmen",
)


@dataclass
class DxfReadOptions:
    """Controls which entities are taken from the drawing and how finely."""

    #: Maximum chord deviation when flattening curves [drawing units].
    sagitta: float = 0.02
    #: Only read these layers (case-insensitive). Empty means "all".
    include_layers: tuple[str, ...] = ()
    #: Skip these layers in addition to the default annotation patterns.
    exclude_layers: tuple[str, ...] = ()
    #: Regex patterns of layers to skip; set to () to keep everything.
    ignored_layer_patterns: tuple[str, ...] = DEFAULT_IGNORED_LAYER_PATTERNS
    #: Expand INSERT block references.
    explode_blocks: bool = True
    #: Maximum recursion depth for nested blocks.
    max_block_depth: int = 8
    #: Skip entities on layers that are frozen or switched off in the drawing.
    respect_layer_state: bool = True
    #: Override the drawing units instead of trusting ``$INSUNITS``.
    force_unit_scale: float | None = None

    def layer_filter(self) -> "LayerFilter":
        return LayerFilter(
            include=tuple(layer.lower() for layer in self.include_layers),
            exclude=tuple(layer.lower() for layer in self.exclude_layers),
            patterns=tuple(re.compile(p, re.IGNORECASE) for p in self.ignored_layer_patterns),
        )


@dataclass
class LayerFilter:
    """Decides whether a layer contributes boundary geometry."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()

    def accepts(self, layer: str | None) -> bool:
        name = (layer or "0").strip().lower()
        if self.include:
            return name in self.include
        if name in self.exclude:
            return False
        return not any(pattern.search(name) for pattern in self.patterns)


@dataclass
class DxfExtraction:
    """Everything pulled out of one DXF document."""

    segments: list[Segment] = field(default_factory=list)
    report: GeometryReport = field(default_factory=GeometryReport)
    scale_to_mm: float = 1.0
    layers_seen: set[str] = field(default_factory=set)
    layers_used: set[str] = field(default_factory=set)

    def __len__(self) -> int:
        return len(self.segments)


class DxfReader:
    """Extracts boundary segments from a DXF document."""

    def __init__(
        self,
        options: DxfReadOptions | None = None,
        defaults: GeometryDefaults | None = None,
    ) -> None:
        self.defaults = defaults or get_settings().geometry
        self.options = options or DxfReadOptions(sagitta=self.defaults.arc_sagitta_mm)
        self._filter = self.options.layer_filter()

    # -- entry points ------------------------------------------------------ #
    @timed("geometry.read_dxf")
    def read_file(self, path: str | Path) -> DxfExtraction:
        """Read a DXF (or DXB/ZIP-wrapped DXF) file from disk."""
        try:
            import ezdxf
            from ezdxf.document import Drawing
        except ImportError as exc:  # pragma: no cover - dependency is required
            raise DxfReadError(
                "ezdxf is required to read DXF files (pip install ezdxf)"
            ) from exc

        path = Path(path)
        if not path.is_file():
            raise DxfReadError("DXF file not found", path=str(path))

        try:
            doc: Drawing = ezdxf.readfile(str(path))
        except IOError as exc:
            raise DxfReadError(f"Cannot open DXF: {exc}", path=str(path)) from exc
        except ezdxf.DXFStructureError as exc:
            # Try the recovery reader before giving up — files exported by
            # older CAD systems are frequently structurally sloppy but readable.
            _log.warning("DXF structure error, attempting recovery: %s", exc)
            try:
                from ezdxf import recover

                doc, auditor = recover.readfile(str(path))
                if auditor.has_errors:
                    _log.warning("DXF recovered with %d errors", len(auditor.errors))
            except Exception as recover_exc:  # noqa: BLE001
                raise DxfReadError(
                    f"Invalid DXF structure: {exc}", path=str(path)
                ) from recover_exc

        extraction = self.read_document(doc)
        extraction.report.source = str(path)
        return extraction

    def read_document(self, doc: Any) -> DxfExtraction:
        """Read an already-open ``ezdxf`` document."""
        extraction = DxfExtraction()
        report = extraction.report

        scale = self.options.force_unit_scale
        if scale is None:
            insunits = doc.header.get("$INSUNITS", 0)
            scale = dxf_insunits_to_mm(insunits)
            if insunits == 0:
                report.add_warning(
                    "Drawing has no $INSUNITS header; assuming millimetres."
                )
        extraction.scale_to_mm = scale
        report.scale_to_mm = scale

        try:
            layout = doc.modelspace()
        except Exception as exc:  # noqa: BLE001
            raise DxfReadError(f"Cannot access modelspace: {exc}") from exc

        frozen = self._frozen_layers(doc) if self.options.respect_layer_state else set()

        counts: dict[str, int] = {}
        for entity in self._iter_entities(layout, frozen=frozen):
            dxftype = entity.dxftype()
            counts[dxftype] = counts.get(dxftype, 0) + 1
            layer = str(getattr(entity.dxf, "layer", "0"))
            extraction.layers_seen.add(layer)

            segments = self._entity_to_segments(entity, scale)
            if segments:
                extraction.layers_used.add(layer)
                extraction.segments.extend(segments)

        report.entity_counts = counts
        if not extraction.segments:
            report.add_error(
                "No boundary geometry found. Check that the profile is on a "
                "readable layer and is drawn with lines, arcs or polylines."
            )
        _log.info(
            "Extracted %d segments from %d entity types (scale %.6g mm/unit)",
            len(extraction.segments),
            len(counts),
            scale,
        )
        return extraction

    # -- entity traversal -------------------------------------------------- #
    def _frozen_layers(self, doc: Any) -> set[str]:
        """Names of layers that are frozen or switched off."""
        hidden: set[str] = set()
        try:
            for layer in doc.layers:
                if layer.is_frozen() or layer.is_off():
                    hidden.add(str(layer.dxf.name).lower())
        except Exception:  # noqa: BLE001 - layer table quirks must not stop the read
            _log.debug("Could not inspect layer states", exc_info=True)
        return hidden

    def _iter_entities(
        self, container: Iterable[Any], *, frozen: set[str], depth: int = 0
    ) -> Iterator[Any]:
        """Yield boundary entities, expanding INSERTs recursively."""
        for entity in container:
            dxftype = entity.dxftype()
            layer = str(getattr(entity.dxf, "layer", "0"))

            if dxftype in ANNOTATION_TYPES:
                continue
            if self.options.respect_layer_state and layer.lower() in frozen:
                continue
            if not self._filter.accepts(layer):
                continue

            if dxftype == "INSERT":
                if not self.options.explode_blocks or depth >= self.options.max_block_depth:
                    continue
                try:
                    virtual = list(entity.virtual_entities())
                except Exception as exc:  # noqa: BLE001 - broken block reference
                    _log.warning("Cannot expand block reference %s: %s", entity, exc)
                    continue
                yield from self._iter_entities(virtual, frozen=frozen, depth=depth + 1)
                continue

            if dxftype in BOUNDARY_TYPES:
                yield entity

    # -- entity conversion -------------------------------------------------- #
    def _entity_to_segments(self, entity: Any, scale: float) -> list[Segment]:
        """Flatten one entity into segments, in millimetres."""
        dxftype = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", "0"))
        handle = str(getattr(entity.dxf, "handle", "") or "")
        # Flatten in drawing units, then scale — so the sagitta stays meaningful
        # relative to the drawing's own coordinates.
        sagitta = max(self.options.sagitta / max(scale, 1e-12), 1e-9)

        try:
            if dxftype == "LINE":
                points = [
                    (entity.dxf.start.x, entity.dxf.start.y),
                    (entity.dxf.end.x, entity.dxf.end.y),
                ]
                return [self._make(points, handle, layer, False, scale)]

            if dxftype == "LWPOLYLINE":
                raw = entity.get_points("xyb")
                vertices = [(float(x), float(y), float(b)) for x, y, b in raw]
                closed = bool(entity.closed)
                points = flatten_vertices(vertices, closed=closed, sagitta=sagitta)
                return [self._make(points, handle, layer, closed, scale)]

            if dxftype == "POLYLINE":
                if not entity.is_2d_polyline:
                    # A 3D polyline projected onto XY is still usable boundary.
                    points = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
                    closed = bool(entity.is_closed)
                    return [self._make(points, handle, layer, closed, scale)]
                vertices = [
                    (
                        float(v.dxf.location.x),
                        float(v.dxf.location.y),
                        float(getattr(v.dxf, "bulge", 0.0) or 0.0),
                    )
                    for v in entity.vertices
                ]
                closed = bool(entity.is_closed)
                points = flatten_vertices(vertices, closed=closed, sagitta=sagitta)
                return [self._make(points, handle, layer, closed, scale)]

            if dxftype in ("ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
                points = [(float(p.x), float(p.y)) for p in entity.flattening(sagitta)]
                closed = dxftype == "CIRCLE" or (
                    dxftype == "ELLIPSE" and self._ellipse_is_closed(entity)
                ) or (dxftype == "SPLINE" and bool(getattr(entity, "closed", False)))
                return [self._make(points, handle, layer, closed, scale)]

            if dxftype in ("SOLID", "TRACE"):
                corners = [entity.dxf.vtx0, entity.dxf.vtx1, entity.dxf.vtx3, entity.dxf.vtx2]
                points = [(float(p.x), float(p.y)) for p in corners]
                return [self._make(points, handle, layer, True, scale)]

        except Exception as exc:  # noqa: BLE001 - one bad entity must not abort
            _log.warning("Skipping %s (%s): %s", dxftype, handle or "no handle", exc)
            return []

        return []

    @staticmethod
    def _ellipse_is_closed(entity: Any) -> bool:
        import math

        start = float(getattr(entity.dxf, "start_param", 0.0))
        end = float(getattr(entity.dxf, "end_param", math.tau))
        return abs((end - start) - math.tau) < 1e-9

    @staticmethod
    def _make(
        points: list[Point], handle: str, layer: str, closed: bool, scale: float
    ) -> Segment:
        if scale != 1.0:
            points = [(x * scale, y * scale) for x, y in points]
        return Segment(points=points, source=handle or None, layer=layer, closed=closed)


def read_dxf(
    path: str | Path, options: DxfReadOptions | None = None
) -> DxfExtraction:
    """Read ``path`` and return its boundary segments."""
    return DxfReader(options).read_file(path)


__all__ = [
    "BOUNDARY_TYPES",
    "ANNOTATION_TYPES",
    "DEFAULT_IGNORED_LAYER_PATTERNS",
    "DxfReadOptions",
    "LayerFilter",
    "DxfExtraction",
    "DxfReader",
    "read_dxf",
]
