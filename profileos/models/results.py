"""Result models shared by the analysis engines and the presentation layers.

:class:`SectionProperties` is the full geometric/structural characterisation of
a profile cross-section. Every value is in the canonical unit system described
in :mod:`profileos.core.units` — millimetres and derived powers of millimetres.

Sign and axis conventions
-------------------------
* The origin of the *raw* properties is the DXF drawing origin.
* Centroidal properties (``ixx``, ``iyy``, ``ixy``) are referred to the
  centroid, with axes parallel to the drawing axes.
* ``principal_angle`` is measured counter-clockwise from the X axis to the
  major principal axis (axis 1), in degrees, in ``(-90, 90]``.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class SectionProperties(BaseModel):
    """Geometric and structural properties of one cross-section."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # -- primary geometry -------------------------------------------------- #
    area: float = Field(description="Cross-sectional area A [mm^2]")
    perimeter: float = Field(default=0.0, description="Total wetted perimeter [mm]")
    centroid_x: float = Field(description="x-bar, in drawing coordinates [mm]")
    centroid_y: float = Field(description="y-bar, in drawing coordinates [mm]")

    #: Bounding box in drawing coordinates: (min_x, min_y, max_x, max_y).
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    # -- second moments about the centroid --------------------------------- #
    ixx: float = Field(description="I_x about the centroidal x axis [mm^4]")
    iyy: float = Field(description="I_y about the centroidal y axis [mm^4]")
    ixy: float = Field(default=0.0, description="Product of inertia I_xy [mm^4]")

    # -- principal system --------------------------------------------------- #
    i11: float = Field(description="Major principal second moment I_1 [mm^4]")
    i22: float = Field(description="Minor principal second moment I_2 [mm^4]")
    principal_angle: float = Field(
        default=0.0, description="Angle from x to principal axis 1, CCW [deg]"
    )

    # -- extreme fibre distances ------------------------------------------- #
    c_top: float = Field(default=0.0, description="Centroid to topmost fibre [mm]")
    c_bottom: float = Field(default=0.0, description="Centroid to bottommost fibre [mm]")
    c_left: float = Field(default=0.0, description="Centroid to leftmost fibre [mm]")
    c_right: float = Field(default=0.0, description="Centroid to rightmost fibre [mm]")

    # -- elastic section moduli -------------------------------------------- #
    sx_top: float = Field(default=0.0, description="I_x / c_top [mm^3]")
    sx_bottom: float = Field(default=0.0, description="I_x / c_bottom [mm^3]")
    sy_left: float = Field(default=0.0, description="I_y / c_left [mm^3]")
    sy_right: float = Field(default=0.0, description="I_y / c_right [mm^3]")

    # -- plastic properties ------------------------------------------------ #
    plastic_neutral_axis_y: float | None = Field(
        default=None, description="y of the horizontal PNA, drawing coords [mm]"
    )
    plastic_neutral_axis_x: float | None = Field(
        default=None, description="x of the vertical PNA, drawing coords [mm]"
    )
    zx: float | None = Field(default=None, description="Plastic modulus Z_x [mm^3]")
    zy: float | None = Field(default=None, description="Plastic modulus Z_y [mm^3]")

    # -- radii of gyration -------------------------------------------------- #
    rx: float = Field(default=0.0, description="sqrt(I_x / A) [mm]")
    ry: float = Field(default=0.0, description="sqrt(I_y / A) [mm]")

    # -- torsion and warping ------------------------------------------------ #
    j: float | None = Field(default=None, description="St Venant torsion constant J [mm^4]")
    cw: float | None = Field(default=None, description="Warping constant C_w [mm^6]")
    shear_centre_x: float | None = Field(default=None, description="Shear centre x [mm]")
    shear_centre_y: float | None = Field(default=None, description="Shear centre y [mm]")

    # -- material-derived --------------------------------------------------- #
    material_id: str | None = None
    mass_per_metre: float | None = Field(default=None, description="[kg/m]")

    # -- provenance --------------------------------------------------------- #
    #: How J and C_w were obtained: "fea", "thin_wall", or None when not computed.
    torsion_method: str | None = None
    mesh_element_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- derived ------------------------------------------------------------ #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def sx(self) -> float:
        """Governing elastic modulus about x: the smaller of top and bottom."""
        candidates = [s for s in (self.sx_top, self.sx_bottom) if s > 0]
        return min(candidates) if candidates else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sy(self) -> float:
        """Governing elastic modulus about y: the smaller of left and right."""
        candidates = [s for s in (self.sy_left, self.sy_right) if s > 0]
        return min(candidates) if candidates else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def shape_factor_x(self) -> float | None:
        """Z_x / S_x — the reserve between first yield and a full plastic hinge."""
        if self.zx is None or self.sx <= 0:
            return None
        return self.zx / self.sx

    @property
    def shape_factor_y(self) -> float | None:
        if self.zy is None or self.sy <= 0:
            return None
        return self.zy / self.sy

    @property
    def torsion_modulus(self) -> float | None:
        """J / t_max is not general; expose J alone and let callers decide."""
        return self.j

    def polar_moment(self) -> float:
        """I_p = I_x + I_y about the centroid [mm^4]."""
        return self.ixx + self.iyy

    def radius_of_gyration_polar(self) -> float:
        """r_0 = sqrt((I_x + I_y) / A), used in flexural-torsional buckling."""
        if self.area <= 0:
            return 0.0
        return math.sqrt(self.polar_moment() / self.area)

    def slenderness(self, length: float, axis: str = "x", k: float = 1.0) -> float | None:
        """Slenderness ratio lambda = k L / r about ``axis`` ("x" or "y")."""
        r = self.rx if axis.lower() == "x" else self.ry
        if r <= 0:
            return None
        return k * length / r

    def summary_rows(self) -> list[tuple[str, str, str]]:
        """``(symbol, value, unit)`` rows for the UI property panel."""

        def fmt(value: float | None, digits: int = 2) -> str:
            if value is None:
                return "—"
            # Symmetric sections give I_xy and theta as floating-point dust
            # (~1e-13) rather than exact zero. Showing that as "1.005e-13"
            # implies a precision that is not there and reads as a defect, so
            # anything that small relative to the section is simply zero.
            if abs(value) < 1e-9:
                return "0"
            if abs(value) >= 1e7 or abs(value) < 1e-3:
                return f"{value:.{digits}e}"
            return f"{value:,.{digits}f}"

        return [
            ("A", fmt(self.area), "mm²"),
            ("x̄", fmt(self.centroid_x, 3), "mm"),
            ("ȳ", fmt(self.centroid_y, 3), "mm"),
            ("Iₓ", fmt(self.ixx), "mm⁴"),
            ("I_y", fmt(self.iyy), "mm⁴"),
            ("I_xy", fmt(self.ixy), "mm⁴"),
            ("I₁", fmt(self.i11), "mm⁴"),
            ("I₂", fmt(self.i22), "mm⁴"),
            ("θ", fmt(self.principal_angle, 3), "°"),
            ("Sₓ", fmt(self.sx), "mm³"),
            ("S_y", fmt(self.sy), "mm³"),
            ("Zₓ", fmt(self.zx), "mm³"),
            ("Z_y", fmt(self.zy), "mm³"),
            ("rₓ", fmt(self.rx, 3), "mm"),
            ("r_y", fmt(self.ry, 3), "mm"),
            ("J", fmt(self.j), "mm⁴"),
            ("C_w", fmt(self.cw), "mm⁶"),
            ("mass", fmt(self.mass_per_metre, 3), "kg/m"),
        ]


class WallThicknessReport(BaseModel):
    """Result of the wall-thickness scan over a profile section."""

    model_config = ConfigDict(extra="forbid")

    min_thickness: float
    max_thickness: float
    mean_thickness: float
    #: Locations where thickness fell below the configured alarm threshold.
    thin_spots: list[tuple[float, float, float]] = Field(
        default_factory=list, description="(x, y, thickness) samples below the limit"
    )
    threshold: float = 1.2
    sample_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.thin_spots


class GeometryReport(BaseModel):
    """Diagnostics produced while reconstructing a profile from DXF."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    entity_counts: dict[str, int] = Field(default_factory=dict)
    contours_found: int = 0
    closed_contours: int = 0
    open_chains: int = 0
    holes_found: int = 0
    scale_to_mm: float = 1.0
    repaired_gaps: int = 0
    discarded_tiny: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_error(self, message: str) -> None:
        self.errors.append(message)


__all__ = ["SectionProperties", "WallThicknessReport", "GeometryReport"]
