"""Structural engine: exact section properties, plastic moduli, torsion, checks.

Typical use::

    from profileos.geometry import load_section
    from profileos.structural import analyse_section

    section = load_section("MB70_mullion.dxf")
    props = analyse_section(section.polygon, topology=section.topology)
    print(props.ixx, props.zx, props.j)
"""

from __future__ import annotations

from .checks import (
    CheckResult,
    DeflectionLimit,
    LoadCase,
    MemberCheck,
    SupportCondition,
    check_member,
    maximum_span,
    wind_line_load,
)
from .green import (
    CentroidalMoments,
    RawMoments,
    moments_from_polygon,
    polygon_perimeter,
    ring_moments,
    section_moments,
)
from .plastic import (
    PlasticResult,
    plastic_modulus_x,
    plastic_modulus_y,
    shape_factor,
)
from .properties import (
    analyse_dxf,
    analyse_profile,
    analyse_section,
    transformed_section_properties,
)
from .torsion import (
    TorsionResult,
    bredt_torsion_constant,
    compute_torsion,
    open_section_torsion_constant,
    sectionproperties_available,
    torsion_fea,
    torsion_thin_wall,
)

__all__ = [
    # green
    "RawMoments",
    "CentroidalMoments",
    "ring_moments",
    "section_moments",
    "moments_from_polygon",
    "polygon_perimeter",
    # plastic
    "PlasticResult",
    "plastic_modulus_x",
    "plastic_modulus_y",
    "shape_factor",
    # torsion
    "TorsionResult",
    "compute_torsion",
    "torsion_fea",
    "torsion_thin_wall",
    "sectionproperties_available",
    "bredt_torsion_constant",
    "open_section_torsion_constant",
    # orchestration
    "analyse_section",
    "analyse_profile",
    "analyse_dxf",
    "transformed_section_properties",
    # checks
    "SupportCondition",
    "DeflectionLimit",
    "CheckResult",
    "MemberCheck",
    "LoadCase",
    "wind_line_load",
    "check_member",
    "maximum_span",
]
