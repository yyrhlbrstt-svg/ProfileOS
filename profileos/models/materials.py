"""Material definitions for aluminium alloys, thermal breaks and accessories.

Design values follow EN 1999-1-1 (Eurocode 9) for wrought aluminium alloys.
The characteristic strengths ``f_o`` (0.2 % proof strength) and ``f_u``
(ultimate tensile strength) are thickness dependent in the code; the values
carried here are those for the thickness range typical of architectural
extrusions (t <= 25 mm), which is what profile systems use.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MaterialClass(StrEnum):
    """Broad family a material belongs to, used for filtering and costing."""

    ALUMINIUM = "aluminium"
    STEEL = "steel"
    STAINLESS = "stainless"
    POLYAMIDE = "polyamide"
    EPDM = "epdm"
    GLASS = "glass"
    PVC = "pvc"
    TIMBER = "timber"
    OTHER = "other"


class Material(BaseModel):
    """An isotropic structural material.

    All stresses and moduli are in MPa (N/mm^2), density in kg/m^3, and the
    thermal expansion coefficient in 1/K.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Stable identifier, e.g. 'en-aw-6060-t66'")
    name: str
    material_class: MaterialClass = MaterialClass.ALUMINIUM
    version: str = "1.0"

    elastic_modulus: float = Field(gt=0, description="Young's modulus E [MPa]")
    poissons_ratio: float = Field(default=0.30, ge=0.0, lt=0.5, description="nu [-]")
    density: float = Field(gt=0, description="rho [kg/m^3]")

    yield_strength: float = Field(
        gt=0, description="Characteristic 0.2% proof strength f_o [MPa]"
    )
    ultimate_strength: float | None = Field(
        default=None, gt=0, description="Characteristic ultimate strength f_u [MPa]"
    )
    #: Partial safety factor for cross-section resistance (EN 1999-1-1 gamma_M1).
    gamma_m1: float = Field(default=1.10, gt=0)
    #: Partial safety factor for net-section / connection resistance (gamma_M2).
    gamma_m2: float = Field(default=1.25, gt=0)

    thermal_expansion: float = Field(
        default=23.4e-6, gt=0, description="alpha [1/K]"
    )
    thermal_conductivity: float | None = Field(
        default=None, gt=0, description="lambda [W/(m.K)]"
    )

    #: Purchase price per kilogram in the catalogue currency; used by the quoting engine.
    price_per_kg: float | None = Field(default=None, ge=0)
    currency: str = "EUR"

    #: Free-form notes (temper details, standards, supplier remarks).
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _normalise_id(cls, v: str) -> str:
        normalised = v.strip().lower().replace(" ", "-")
        if not normalised:
            raise ValueError("material id must not be empty")
        return normalised

    # -- derived properties ------------------------------------------------ #
    @property
    def shear_modulus(self) -> float:
        """G = E / (2 (1 + nu)) [MPa]."""
        return self.elastic_modulus / (2.0 * (1.0 + self.poissons_ratio))

    @property
    def design_strength(self) -> float:
        """Design strength f_o / gamma_M1 [MPa]."""
        return self.yield_strength / self.gamma_m1

    def mass_per_metre(self, area_mm2: float) -> float:
        """Linear mass [kg/m] of a bar with the given cross-sectional area."""
        return area_mm2 * 1e-6 * self.density

    def cost_per_metre(self, area_mm2: float) -> float | None:
        """Material cost per metre, or ``None`` when no price is set."""
        if self.price_per_kg is None:
            return None
        return self.mass_per_metre(area_mm2) * self.price_per_kg


# --------------------------------------------------------------------------- #
# Built-in catalogue
# --------------------------------------------------------------------------- #

def _alu(
    ident: str,
    name: str,
    f_o: float,
    f_u: float,
    *,
    price: float | None = 3.40,
) -> Material:
    """Helper building a wrought-aluminium entry with the standard constants."""
    return Material(
        id=ident,
        name=name,
        material_class=MaterialClass.ALUMINIUM,
        elastic_modulus=70000.0,
        poissons_ratio=0.30,
        density=2700.0,
        yield_strength=f_o,
        ultimate_strength=f_u,
        thermal_expansion=23.4e-6,
        thermal_conductivity=160.0,
        price_per_kg=price,
    )


#: Alloys commonly extruded for windows, doors and curtain walling.
BUILTIN_MATERIALS: dict[str, Material] = {
    m.id: m
    for m in (
        _alu("en-aw-6060-t5", "EN AW-6060 T5", 120.0, 160.0),
        _alu("en-aw-6060-t6", "EN AW-6060 T6", 140.0, 170.0),
        _alu("en-aw-6060-t66", "EN AW-6060 T66", 160.0, 195.0),
        _alu("en-aw-6063-t5", "EN AW-6063 T5", 130.0, 175.0),
        _alu("en-aw-6063-t6", "EN AW-6063 T6", 160.0, 195.0),
        _alu("en-aw-6005a-t6", "EN AW-6005A T6", 200.0, 250.0, price=3.65),
        _alu("en-aw-6082-t6", "EN AW-6082 T6", 250.0, 290.0, price=3.90),
        Material(
            id="pa66-gf25",
            name="Polyamide PA66 GF25 thermal break",
            material_class=MaterialClass.POLYAMIDE,
            elastic_modulus=7500.0,
            poissons_ratio=0.35,
            density=1350.0,
            yield_strength=110.0,
            ultimate_strength=130.0,
            thermal_expansion=30.0e-6,
            thermal_conductivity=0.30,
            price_per_kg=6.20,
            notes="Glass-fibre reinforced insulating strip (Insulbar-type).",
        ),
        Material(
            id="s235jr",
            name="Structural steel S235JR",
            material_class=MaterialClass.STEEL,
            elastic_modulus=210000.0,
            poissons_ratio=0.30,
            density=7850.0,
            yield_strength=235.0,
            ultimate_strength=360.0,
            gamma_m1=1.00,
            gamma_m2=1.25,
            thermal_expansion=12.0e-6,
            thermal_conductivity=50.0,
            price_per_kg=1.15,
            notes="Reinforcement inserts inside aluminium chambers.",
        ),
        Material(
            id="epdm",
            name="EPDM gasket",
            material_class=MaterialClass.EPDM,
            elastic_modulus=5.0,
            poissons_ratio=0.49,
            density=1200.0,
            yield_strength=7.0,
            thermal_expansion=160.0e-6,
            thermal_conductivity=0.25,
            price_per_kg=4.50,
        ),
    )
}

#: Sensible default when a profile does not name its alloy.
DEFAULT_MATERIAL_ID = "en-aw-6060-t66"


def get_material(material_id: str | None) -> Material:
    """Look up a material, falling back to the default alloy.

    Checks the hot-reloadable ``MATERIALS`` registry first so a plugin can add
    or override alloys at runtime, then the built-in catalogue.
    """
    from ..core.registry import MATERIALS  # local import avoids a cycle

    key = (material_id or DEFAULT_MATERIAL_ID).strip().lower()
    registered = MATERIALS.get_or_none(key)
    if isinstance(registered, Material):
        return registered
    if key in BUILTIN_MATERIALS:
        return BUILTIN_MATERIALS[key]
    return BUILTIN_MATERIALS[DEFAULT_MATERIAL_ID]


def all_materials() -> list[Material]:
    """Every known material: built-ins overlaid with registry entries."""
    from ..core.registry import MATERIALS

    merged: dict[str, Material] = dict(BUILTIN_MATERIALS)
    for key, item in MATERIALS.items():
        if isinstance(item, Material):
            merged[key] = item
    return sorted(merged.values(), key=lambda m: (m.material_class, m.id))


__all__ = [
    "MaterialClass",
    "Material",
    "BUILTIN_MATERIALS",
    "DEFAULT_MATERIAL_ID",
    "get_material",
    "all_materials",
]
