"""Accessories: what is fitted to an opening besides the opening.

The entry point is :func:`accessories_for`, which takes an opening and a
specification and returns everything that hangs off it, sized and costed. A
specification is deliberately small — which shutter, which screen, which sill
— because the sizes are the software's job, not the estimator's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Accessory, AccessoryCut, AccessoryKind, AccessoryPart, AccessorySet
from .screens import (
    MAX_HEIGHT,
    MAX_LEAF_WIDTH,
    MeshKind,
    ScreenKind,
    ScreenSpec,
    SillKind,
    size_screen,
    size_sill,
    size_trim,
)
from .shutters import (
    BOX_SIZES,
    MOTORS,
    SHAFTS,
    SLATS,
    BoxPosition,
    Drive,
    ShutterSpec,
    Slat,
    SlatKind,
    choose_box,
    choose_motor,
    choose_shaft,
    coil_diameter,
    size_shutter,
    slat,
)


@dataclass
class AccessorySpec:
    """What is fitted to this opening. Everything is optional.

    Kept as one object because that is how it is sold: a customer asks for "a
    window with a shutter and a screen", and an estimator who has to remember
    three separate switches will one day remember two.
    """

    shutter: ShutterSpec | None = None
    screen: ScreenSpec | None = None
    sill: SillKind | None = None
    sill_projection: float = 150.0
    trim: bool = False
    trim_face: float = 40.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.shutter or self.screen or self.sill or self.trim)

    @classmethod
    def typical_dwelling(cls) -> "AccessorySpec":
        """What an Israeli flat window is ordinarily fitted with."""
        return cls(
            shutter=ShutterSpec(slat_id="alu_45", drive=Drive.MOTOR),
            screen=ScreenSpec(kind=ScreenKind.SLIDING),
            sill=SillKind.ALUMINIUM,
        )

    def to_dict(self) -> dict[str, Any]:
        """A form that survives being written into a job file."""
        data: dict[str, Any] = {}
        if self.shutter:
            data["shutter"] = {
                "slat_id": self.shutter.slat_id,
                "drive": self.shutter.drive.value,
                "box": self.shutter.box.value,
                "guide_width": self.shutter.guide_width,
                "quantity": self.shutter.quantity,
            }
        if self.screen:
            data["screen"] = {
                "kind": self.screen.kind.value,
                "mesh": self.screen.mesh.value,
                "leaves": self.screen.leaves,
                "quantity": self.screen.quantity,
            }
        if self.sill:
            data["sill"] = self.sill.value
            data["sill_projection"] = self.sill_projection
        if self.trim:
            data["trim"] = True
            data["trim_face"] = self.trim_face
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AccessorySpec":
        if not data:
            return cls()
        shutter = None
        if "shutter" in data:
            raw = data["shutter"]
            shutter = ShutterSpec(
                slat_id=raw.get("slat_id", "alu_45"),
                drive=Drive(raw.get("drive", "motor")),
                box=BoxPosition(raw.get("box", "built_in")),
                guide_width=float(raw.get("guide_width", 45.0)),
                quantity=int(raw.get("quantity", 1)),
            )
        screen = None
        if "screen" in data:
            raw = data["screen"]
            screen = ScreenSpec(
                kind=ScreenKind(raw.get("kind", "sliding")),
                mesh=MeshKind(raw.get("mesh", "fibreglass")),
                leaves=int(raw.get("leaves", 0)),
                quantity=int(raw.get("quantity", 1)),
            )
        sill = SillKind(data["sill"]) if data.get("sill") else None
        return cls(
            shutter=shutter,
            screen=screen,
            sill=sill,
            sill_projection=float(data.get("sill_projection", 150.0)),
            trim=bool(data.get("trim", False)),
            trim_face=float(data.get("trim_face", 40.0)),
        )


def accessories_for(opening: Any, spec: AccessorySpec | None = None) -> AccessorySet:
    """Everything fitted to this opening, sized from it.

    The specification may also travel on the opening itself, under
    ``metadata["accessories"]`` — which is how it survives a job file being
    saved, mailed and reopened.
    """
    if spec is None:
        spec = AccessorySpec.from_dict(getattr(opening, "metadata", {}).get("accessories"))
    found = AccessorySet()
    if spec.is_empty:
        return found

    width, height = opening.width, opening.height
    quantity = getattr(opening, "quantity", 1)

    if spec.shutter is not None:
        found.add(size_shutter(width, height, spec.shutter, quantity=quantity))
    if spec.screen is not None:
        found.add(size_screen(width, height, spec.screen, quantity=quantity))
    if spec.sill is not None and spec.sill is not SillKind.NONE:
        found.add(size_sill(
            width, projection=spec.sill_projection, kind=spec.sill, quantity=quantity,
        ))
    if spec.trim:
        found.add(size_trim(width, height, face=spec.trim_face, quantity=quantity))
    return found


__all__ = [
    "BOX_SIZES",
    "MAX_HEIGHT",
    "MAX_LEAF_WIDTH",
    "MOTORS",
    "SHAFTS",
    "SLATS",
    "Accessory",
    "AccessoryCut",
    "AccessoryKind",
    "AccessoryPart",
    "AccessorySet",
    "AccessorySpec",
    "BoxPosition",
    "Drive",
    "MeshKind",
    "ScreenKind",
    "ScreenSpec",
    "ShutterSpec",
    "SillKind",
    "Slat",
    "SlatKind",
    "accessories_for",
    "choose_box",
    "choose_motor",
    "choose_shaft",
    "coil_diameter",
    "size_screen",
    "size_shutter",
    "size_sill",
    "size_trim",
    "slat",
]
