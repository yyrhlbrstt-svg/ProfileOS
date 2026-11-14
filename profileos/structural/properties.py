"""Section-property orchestration.

:func:`analyse_section` runs the whole structural characterisation of a
cross-section and returns a populated
:class:`~profileos.models.results.SectionProperties`:

1. Green's-theorem integrals give the area, centroid and second moments.
2. The parallel-axis shift moves the moments to the centroid.
3. Mohr's-circle rotation gives the principal moments and axis angle.
4. Extreme-fibre distances give the elastic section moduli.
5. Equal-area bisection gives the plastic neutral axes and plastic moduli.
6. A Tri6 finite-element solve (or a thin-walled estimate) gives J and C_w.
7. The material density converts the area into a linear mass.
"""

from __future__ import annotations

import math
from typing import Any

from ..core.config import AnalysisDefaults, get_settings
from ..core.errors import DegenerateSectionError, StructuralError, WarpingAnalysisError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..models.materials import Material, get_material
from ..models.profile import ProfileDefinition
from ..models.results import SectionProperties
from .green import moments_from_polygon, polygon_outer_perimeter, polygon_perimeter
from .plastic import plastic_modulus_x, plastic_modulus_y
from .torsion import compute_torsion

_log = get_logger("structural.properties")


@timed("structural.analyse")
def analyse_section(
    polygon: Any,
    *,
    topology: Any = None,
    material: Material | str | None = None,
    profile_id: str | None = None,
    defaults: AnalysisDefaults | None = None,
    compute_plastic: bool = True,
    compute_torsion_constants: bool = True,
) -> SectionProperties:
    """Compute the full property set for a Shapely section.

    Parameters
    ----------
    polygon:
        Shapely ``Polygon`` or ``MultiPolygon`` in millimetres.
    topology:
        Optional resolved topology, used for the thin-walled torsion fallback.
    material:
        A :class:`Material`, a material id, or ``None`` for the default alloy.
    compute_plastic, compute_torsion_constants:
        Switch off the expensive stages for a fast preview.

    Raises
    ------
    DegenerateSectionError
        The section has zero or negative area.
    """
    defaults = defaults or get_settings().analysis
    resolved_material = (
        material if isinstance(material, Material) else get_material(material)
    )

    publish(Topic.ANALYSIS_STARTED, source=profile_id, stage="green")

    # -- 1-2. Green's theorem and the parallel-axis shift ------------------- #
    raw = moments_from_polygon(polygon)
    if raw.area <= 1e-9:
        raise DegenerateSectionError(
            "Section has zero or negative area; check contour winding and topology",
            area=raw.area,
            profile_id=profile_id,
        )
    centroidal = raw.to_centroidal()

    # -- 3. Principal system ------------------------------------------------ #
    i11, i22, principal_angle = centroidal.principal
    rx, ry = centroidal.radii_of_gyration

    # -- 4. Extreme fibres and elastic moduli ------------------------------- #
    min_x, min_y, max_x, max_y = (float(v) for v in polygon.bounds)
    c_top = max_y - centroidal.centroid_y
    c_bottom = centroidal.centroid_y - min_y
    c_left = centroidal.centroid_x - min_x
    c_right = max_x - centroidal.centroid_x

    def modulus(inertia: float, distance: float) -> float:
        return inertia / distance if distance > 1e-12 else 0.0

    properties = SectionProperties(
        profile_id=profile_id,
        area=centroidal.area,
        perimeter=polygon_perimeter(polygon),
        outer_perimeter=polygon_outer_perimeter(polygon),
        centroid_x=centroidal.centroid_x,
        centroid_y=centroidal.centroid_y,
        bounds=(min_x, min_y, max_x, max_y),
        ixx=centroidal.ixx,
        iyy=centroidal.iyy,
        ixy=centroidal.ixy,
        i11=i11,
        i22=i22,
        principal_angle=principal_angle,
        c_top=c_top,
        c_bottom=c_bottom,
        c_left=c_left,
        c_right=c_right,
        sx_top=modulus(centroidal.ixx, c_top),
        sx_bottom=modulus(centroidal.ixx, c_bottom),
        sy_left=modulus(centroidal.iyy, c_left),
        sy_right=modulus(centroidal.iyy, c_right),
        rx=rx,
        ry=ry,
        material_id=resolved_material.id,
        mass_per_metre=resolved_material.mass_per_metre(centroidal.area),
    )

    # -- 5. Plastic properties ---------------------------------------------- #
    if compute_plastic:
        publish(Topic.ANALYSIS_PROGRESS, source=profile_id, stage="plastic")
        try:
            px = plastic_modulus_x(polygon)
            py = plastic_modulus_y(polygon)
            properties.plastic_neutral_axis_y = px.axis_position
            properties.plastic_neutral_axis_x = py.axis_position
            properties.zx = px.modulus
            properties.zy = py.modulus
            if not px.converged or not py.converged:
                properties.warnings.append(
                    "Plastic neutral axis bisection did not fully converge."
                )
        except (StructuralError, ValueError) as exc:
            properties.warnings.append(f"Plastic properties unavailable: {exc}")
            _log.warning("Plastic analysis failed: %s", exc)

    # -- 6. Torsion and warping --------------------------------------------- #
    if compute_torsion_constants and defaults.enable_warping:
        publish(Topic.ANALYSIS_PROGRESS, source=profile_id, stage="torsion")
        try:
            torsion = compute_torsion(
                polygon,
                topology,
                mesh_size=defaults.mesh_size_mm2,
                min_angle=defaults.min_mesh_angle_deg,
            )
            properties.j = torsion.j
            properties.cw = torsion.cw
            properties.torsion_method = torsion.method
            properties.mesh_element_count = torsion.element_count
            if torsion.shear_centre is not None:
                properties.shear_centre_x, properties.shear_centre_y = torsion.shear_centre
            properties.warnings.extend(torsion.warnings)
        except WarpingAnalysisError as exc:
            properties.warnings.append(f"Torsion analysis unavailable: {exc}")
            _log.warning("Torsion analysis failed: %s", exc)

    publish(
        Topic.ANALYSIS_COMPLETED,
        source=profile_id,
        area=properties.area,
        ixx=properties.ixx,
        iyy=properties.iyy,
    )
    _log.info(
        "Section %s: A = %.1f mm^2, Ix = %.4g mm^4, Iy = %.4g mm^4, mass = %.3f kg/m",
        profile_id or "<unnamed>",
        properties.area,
        properties.ixx,
        properties.iyy,
        properties.mass_per_metre or 0.0,
    )
    return properties


def analyse_profile(
    profile: ProfileDefinition,
    *,
    defaults: AnalysisDefaults | None = None,
    **kwargs: Any,
) -> SectionProperties:
    """Analyse a stored :class:`ProfileDefinition` (rebuilding its geometry)."""
    from ..geometry import section_from_profile

    section = section_from_profile(profile)
    return analyse_section(
        section.polygon,
        topology=section.topology,
        material=profile.material,
        profile_id=profile.profile_id,
        defaults=defaults,
        **kwargs,
    )


def analyse_dxf(
    source: str,
    *,
    profile_id: str | None = None,
    material: Material | str | None = None,
    defaults: AnalysisDefaults | None = None,
    **kwargs: Any,
) -> tuple[SectionProperties, Any]:
    """Load a DXF and analyse it, returning ``(properties, loaded_section)``."""
    from ..geometry import load_section

    section = load_section(source)
    properties = analyse_section(
        section.polygon,
        topology=section.topology,
        material=material,
        profile_id=profile_id,
        defaults=defaults,
        **kwargs,
    )
    return properties, section


# --------------------------------------------------------------------------- #
# Composite (multi-material) sections
# --------------------------------------------------------------------------- #

def transformed_section_properties(
    parts: list[tuple[Any, Material]],
    *,
    reference_material: Material | None = None,
    profile_id: str | None = None,
) -> SectionProperties:
    """Analyse a multi-material section by the transformed-area method.

    Each part's area is scaled by the modular ratio ``n = E_part / E_ref``, so
    the resulting ``I`` values are those of an equivalent section made entirely
    of the reference material. This is how a thermally broken profile with
    polyamide strips is assessed when full composite action is assumed —
    an upper bound, since real strips transfer only part of the shear.

    Parameters
    ----------
    parts:
        ``(shapely_polygon, material)`` pairs.
    reference_material:
        Defaults to the material of the largest part by area.
    """
    if not parts:
        raise StructuralError("No parts supplied for composite analysis")

    reference = reference_material or max(parts, key=lambda p: p[0].area)[1]
    e_ref = reference.elastic_modulus

    total_area = 0.0
    total_qx = 0.0
    total_qy = 0.0
    total_ixx_o = 0.0
    total_iyy_o = 0.0
    total_ixy_o = 0.0
    real_area = 0.0
    total_mass = 0.0

    for polygon, material in parts:
        ratio = material.elastic_modulus / e_ref
        moments = moments_from_polygon(polygon)
        total_area += moments.area * ratio
        total_qx += moments.qx * ratio
        total_qy += moments.qy * ratio
        total_ixx_o += moments.ixx_o * ratio
        total_iyy_o += moments.iyy_o * ratio
        total_ixy_o += moments.ixy_o * ratio
        real_area += moments.area
        total_mass += material.mass_per_metre(moments.area)

    if total_area <= 1e-9:
        raise DegenerateSectionError("Composite section has zero transformed area")

    cx = total_qy / total_area
    cy = total_qx / total_area
    ixx = total_ixx_o - total_area * cy * cy
    iyy = total_iyy_o - total_area * cx * cx
    ixy = total_ixy_o - total_area * cx * cy

    mean = 0.5 * (ixx + iyy)
    half_diff = 0.5 * (ixx - iyy)
    radius = math.hypot(half_diff, ixy)
    angle = 0.0 if radius < 1e-12 else math.degrees(0.5 * math.atan2(-ixy, half_diff))

    bounds_list = [p.bounds for p, _ in parts]
    bounds = (
        min(b[0] for b in bounds_list),
        min(b[1] for b in bounds_list),
        max(b[2] for b in bounds_list),
        max(b[3] for b in bounds_list),
    )

    c_top, c_bottom = bounds[3] - cy, cy - bounds[1]
    c_left, c_right = cx - bounds[0], bounds[2] - cx

    return SectionProperties(
        profile_id=profile_id,
        area=real_area,
        centroid_x=cx,
        centroid_y=cy,
        bounds=bounds,
        ixx=ixx,
        iyy=iyy,
        ixy=ixy,
        i11=mean + radius,
        i22=mean - radius,
        principal_angle=angle,
        c_top=c_top,
        c_bottom=c_bottom,
        c_left=c_left,
        c_right=c_right,
        sx_top=ixx / c_top if c_top > 1e-12 else 0.0,
        sx_bottom=ixx / c_bottom if c_bottom > 1e-12 else 0.0,
        sy_left=iyy / c_left if c_left > 1e-12 else 0.0,
        sy_right=iyy / c_right if c_right > 1e-12 else 0.0,
        rx=math.sqrt(max(ixx, 0.0) / total_area),
        ry=math.sqrt(max(iyy, 0.0) / total_area),
        material_id=reference.id,
        mass_per_metre=total_mass,
        metadata={
            "composite": True,
            "reference_material": reference.id,
            "transformed_area": total_area,
            "parts": len(parts),
        },
        warnings=[
            "Transformed-section properties assume full composite action "
            "between materials; real thermal breaks transfer only part of the shear."
        ],
    )


__all__ = [
    "analyse_section",
    "analyse_profile",
    "analyse_dxf",
    "transformed_section_properties",
]
