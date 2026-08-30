"""Torsion and warping constants.

Two quantities matter for a profile in torsion:

``J`` — St Venant torsion constant [mm^4]
    Resistance to *uniform* torsion. Solving it exactly requires the Prandtl
    stress function ``phi`` satisfying ``grad^2 phi = -2`` over the section with
    ``phi = 0`` on the boundary (constant, generally non-zero, on each interior
    hole boundary).

``C_w`` — warping constant [mm^6]
    Resistance to *non-uniform* torsion, from the St Venant warping function
    ``psi`` satisfying Laplace's equation ``grad^2 psi = 0`` with
    ``d(psi)/dn = z n_y - y n_z`` on the boundary.

Neither has a closed form for an arbitrary multi-cell extrusion, so the primary
path is a finite-element solution on a six-noded (Tri6) triangular mesh via
``sectionproperties``. When that package is unavailable, or the mesh fails, a
thin-walled analytical estimate is used instead and the result is clearly
labelled ``"thin_wall"`` so a report never presents an approximation as exact.

Thin-walled fallback
--------------------
Closed cells follow Bredt's formula ``J = 4 A_m^2 / \\oint (ds/t)``; open
branches add ``J = (1/3) \\sum b t^3``. Bredt typically lands within a few
per cent below the FEA value for a single-cell tube, and degrades for multi-cell
sections, which is why it is only a fallback.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import WarpingAnalysisError
from ..core.logging_setup import get_logger
from ..core.profiling import timed

_log = get_logger("structural.torsion")


@dataclass
class TorsionResult:
    """Torsion and warping constants plus provenance."""

    #: St Venant torsion constant [mm^4].
    j: float
    #: Warping constant [mm^6]; ``None`` when only J could be estimated.
    cw: float | None = None
    shear_centre: tuple[float, float] | None = None
    #: ``"fea"`` or ``"thin_wall"``.
    method: str = "thin_wall"
    element_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_exact(self) -> bool:
        """True only for the finite-element solution."""
        return self.method == "fea"


# --------------------------------------------------------------------------- #
# Finite element solution
# --------------------------------------------------------------------------- #

def sectionproperties_available() -> bool:
    """True when the FEA backend can be imported."""
    try:
        import sectionproperties  # noqa: F401

        return True
    except ImportError:
        return False


@timed("structural.torsion_fea")
def torsion_fea(
    polygon: Any,
    *,
    mesh_size: float = 2.0,
    min_angle: float = 30.0,
    compute_warping: bool = True,
) -> TorsionResult:
    """Solve J and C_w by finite elements on a Tri6 mesh.

    Parameters
    ----------
    polygon:
        Shapely ``Polygon`` or ``MultiPolygon`` of the section.
    mesh_size:
        Target maximum element area [mm^2]. Smaller is more accurate and
        slower; 2 mm^2 resolves a 2 mm profile wall with several elements
        across its thickness.
    min_angle:
        Minimum triangle angle passed to the mesher [deg].

    Raises
    ------
    WarpingAnalysisError
        Meshing or the solve failed.
    """
    try:
        from sectionproperties.analysis.section import Section
        from sectionproperties.pre.geometry import CompoundGeometry, Geometry
    except ImportError as exc:
        raise WarpingAnalysisError(
            "sectionproperties is required for finite-element torsion analysis"
        ) from exc

    warnings_out: list[str] = []

    try:
        parts = getattr(polygon, "geoms", None)
        if parts is None:
            geometry = Geometry(polygon)
        else:
            pieces = [Geometry(part) for part in parts if not part.is_empty and part.area > 0]
            if not pieces:
                raise WarpingAnalysisError("Section contains no meshable regions")
            geometry = pieces[0] if len(pieces) == 1 else CompoundGeometry(pieces)

        meshed = geometry.create_mesh(mesh_sizes=[mesh_size], min_angle=min_angle)
        section = Section(meshed)
        section.calculate_geometric_properties()
    except WarpingAnalysisError:
        raise
    except Exception as exc:  # noqa: BLE001 - mesher raises a variety of types
        raise WarpingAnalysisError(f"Meshing failed: {exc}", mesh_size=mesh_size) from exc

    element_count: int | None = None
    try:
        element_count = int(len(section.mesh["triangles"]))
    except Exception:  # noqa: BLE001 - purely informational
        pass

    # sectionproperties reports problems (notably a disjoint section, where the
    # warping solution is not physically meaningful) as warnings rather than
    # exceptions. Capturing them keeps that judgement attached to the result
    # instead of scrolling past on stderr.
    captured: list[str] = []

    j_value: float
    cw_value: float | None = None
    shear_centre: tuple[float, float] | None = None

    if compute_warping:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                section.calculate_warping_properties()
                captured.extend(str(w.message).strip() for w in caught)
            j_value = float(section.get_j())
            cw_value = float(section.get_gamma())
            sc_x, sc_y = section.get_sc()
            shear_centre = (float(sc_x), float(sc_y))
        except Exception as exc:  # noqa: BLE001
            # A disconnected section (thermally broken, strips on their own
            # layer) has no single warping solution; fall back to J alone.
            warnings_out.append(f"Warping solution unavailable: {exc}")
            _log.warning("Warping analysis failed, reporting J only: %s", exc)
            try:
                j_value = float(section.get_j())
            except Exception as inner:  # noqa: BLE001
                raise WarpingAnalysisError(f"Torsion solve failed: {inner}") from inner
    else:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                section.calculate_warping_properties()
                captured.extend(str(w.message).strip() for w in caught)
            j_value = float(section.get_j())
        except Exception as exc:  # noqa: BLE001
            raise WarpingAnalysisError(f"Torsion solve failed: {exc}") from exc

    # A disjoint section has no single warping solution, so C_w and the shear
    # centre are dropped rather than reported as if they meant something. J
    # remains valid: it is the sum of the parts' individual torsion constants.
    disjoint = any("disjoint" in message.lower() for message in captured)
    if disjoint:
        cw_value = None
        shear_centre = None
        warnings_out.append(
            "Section has disconnected regions, so the warping constant and shear "
            "centre are undefined; J is the sum of the separate regions."
        )
    warnings_out.extend(
        message.replace("\n", " ") for message in captured if "disjoint" not in message.lower()
    )

    _log.info(
        "FEA torsion: J = %.4g mm^4, C_w = %s, %s elements",
        j_value,
        f"{cw_value:.4g} mm^6" if cw_value is not None else "n/a",
        element_count if element_count is not None else "?",
    )
    return TorsionResult(
        j=j_value,
        cw=cw_value,
        shear_centre=shear_centre,
        method="fea",
        element_count=element_count,
        warnings=warnings_out,
    )


# --------------------------------------------------------------------------- #
# Thin-walled analytical fallback
# --------------------------------------------------------------------------- #

@timed("structural.torsion_thin_wall")
def torsion_thin_wall(topology: Any) -> TorsionResult:
    """Estimate J from thin-walled theory using the resolved topology.

    Each chamber contributes a Bredt closed cell:

    .. math:: J_{cell} = \\frac{4 A_m^2}{\\oint ds/t} \\approx \\frac{4 A_m^2 t}{P_m}

    where the mid-line area ``A_m`` and perimeter ``P_m`` are averaged between
    the cell's bounding rings, and the wall thickness ``t`` is back-computed
    from the material area between them. Regions with no chamber are treated as
    open strips contributing ``(1/3) b t^3``.
    """
    from ..geometry.topology import Region, SectionTopology

    if not isinstance(topology, SectionTopology):  # pragma: no cover - defensive
        raise WarpingAnalysisError("torsion_thin_wall requires a SectionTopology")

    total_j = 0.0
    warnings_out: list[str] = [
        "J estimated from thin-walled theory; install sectionproperties for an "
        "exact finite-element value."
    ]

    for region in topology.regions:
        total_j += _region_torsion(region)

    if topology.chamber_count > 1:
        warnings_out.append(
            "Multi-cell section: Bredt's single-cell formula underestimates J. "
            "Treat the value as a lower bound."
        )

    return TorsionResult(
        j=total_j, cw=None, method="thin_wall", warnings=warnings_out
    )


def _region_torsion(region: Any) -> float:
    """Thin-walled J for one region (a shell and its direct chambers)."""
    shell_area = region.shell.area
    shell_perimeter = region.shell.perimeter

    if not region.holes:
        # Open/solid strip: J = (1/3) b t^3, with b and t inferred from the
        # ring's area and perimeter treating it as a long thin rectangle.
        # For a rectangle b x t with b >> t: A = b t and P ~ 2b, so t ~ 2A/P.
        if shell_perimeter <= 0:
            return 0.0
        thickness = 2.0 * shell_area / shell_perimeter
        breadth = shell_perimeter / 2.0
        return breadth * thickness**3 / 3.0

    total = 0.0
    for hole in region.holes:
        material_area = shell_area - hole.area
        if material_area <= 0:
            continue
        # Mid-line geometry, averaged between the outer and inner rings.
        mid_perimeter = 0.5 * (shell_perimeter + hole.perimeter)
        mid_area = 0.5 * (shell_area + hole.area)
        if mid_perimeter <= 0:
            continue
        thickness = material_area / mid_perimeter
        if thickness <= 0:
            continue
        total += 4.0 * mid_area**2 * thickness / mid_perimeter
    return total


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #

def compute_torsion(
    polygon: Any,
    topology: Any = None,
    *,
    mesh_size: float = 2.0,
    min_angle: float = 30.0,
    compute_warping: bool = True,
    prefer_fea: bool = True,
) -> TorsionResult:
    """Compute J (and C_w when possible), preferring the FEA solution.

    Falls back to the thin-walled estimate when ``sectionproperties`` is absent
    or the mesh/solve fails, provided ``topology`` was supplied.
    """
    if prefer_fea and sectionproperties_available():
        try:
            return torsion_fea(
                polygon,
                mesh_size=mesh_size,
                min_angle=min_angle,
                compute_warping=compute_warping,
            )
        except WarpingAnalysisError as exc:
            if topology is None:
                raise
            _log.warning("FEA torsion failed (%s); falling back to thin-walled theory", exc)
            result = torsion_thin_wall(topology)
            result.warnings.insert(0, f"Finite-element analysis failed: {exc}")
            return result

    if topology is None:
        raise WarpingAnalysisError(
            "Torsion analysis needs either sectionproperties or a resolved topology"
        )
    return torsion_thin_wall(topology)


def bredt_torsion_constant(mid_area: float, mid_perimeter: float, thickness: float) -> float:
    """Bredt's closed-cell torsion constant ``4 A_m^2 t / P_m`` [mm^4]."""
    if mid_perimeter <= 0 or thickness <= 0:
        return 0.0
    return 4.0 * mid_area**2 * thickness / mid_perimeter


def open_section_torsion_constant(segments: list[tuple[float, float]]) -> float:
    """Open-section constant ``(1/3) sum b t^3`` for ``(breadth, thickness)`` pairs."""
    return sum(b * t**3 for b, t in segments) / 3.0


__all__ = [
    "TorsionResult",
    "sectionproperties_available",
    "torsion_fea",
    "torsion_thin_wall",
    "compute_torsion",
    "bredt_torsion_constant",
    "open_section_torsion_constant",
]
