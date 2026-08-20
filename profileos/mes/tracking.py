"""Shop-floor production tracking.

Tracks every physical item through the workshop, from bar stock to shipped
element. The model is deliberately small — a stage machine plus an append-only
event log — because the value is in the log being trustworthy, not in the
model being elaborate.

Stage flow
----------
::

    PLANNED -> CUT -> MACHINED -> ASSEMBLED -> GLAZED -> INSPECTED -> SHIPPED
                                                   \\-> REWORK -> (back)
                                                   \\-> SCRAPPED (terminal)

Transitions are validated: an item cannot be glazed before it is assembled.
That is what stops a tablet mis-scan from silently corrupting the production
record, and it is why :meth:`ProductionItem.advance` returns a reason on
refusal rather than raising — the operator needs to be told, not crashed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable, Iterator
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.events import publish
from ..core.logging_setup import get_logger

_log = get_logger("mes.tracking")


class Stage(StrEnum):
    """Production stages, in flow order."""

    PLANNED = "planned"
    CUT = "cut"
    MACHINED = "machined"
    ASSEMBLED = "assembled"
    GLAZED = "glazed"
    INSPECTED = "inspected"
    SHIPPED = "shipped"
    REWORK = "rework"
    SCRAPPED = "scrapped"

    @property
    def is_terminal(self) -> bool:
        return self in (Stage.SHIPPED, Stage.SCRAPPED)

    def label(self, language: Any = None) -> str:
        """What this stage is called, in the operator's language."""
        from ..i18n import translate

        return translate(f"stage.{self.value}", language)

    @property
    def order(self) -> int:
        """Position in the normal flow; rework and scrap sit outside it."""
        flow = [
            Stage.PLANNED, Stage.CUT, Stage.MACHINED, Stage.ASSEMBLED,
            Stage.GLAZED, Stage.INSPECTED, Stage.SHIPPED,
        ]
        return flow.index(self) if self in flow else -1


#: Stages reachable from each stage.
TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.PLANNED: {Stage.CUT, Stage.SCRAPPED},
    Stage.CUT: {Stage.MACHINED, Stage.ASSEMBLED, Stage.REWORK, Stage.SCRAPPED},
    Stage.MACHINED: {Stage.ASSEMBLED, Stage.REWORK, Stage.SCRAPPED},
    Stage.ASSEMBLED: {Stage.GLAZED, Stage.INSPECTED, Stage.REWORK, Stage.SCRAPPED},
    Stage.GLAZED: {Stage.INSPECTED, Stage.REWORK, Stage.SCRAPPED},
    Stage.INSPECTED: {Stage.SHIPPED, Stage.REWORK, Stage.SCRAPPED},
    Stage.SHIPPED: set(),
    Stage.SCRAPPED: set(),
    # Rework returns to any pre-inspection stage.
    Stage.REWORK: {Stage.CUT, Stage.MACHINED, Stage.ASSEMBLED, Stage.GLAZED, Stage.SCRAPPED},
}


class ItemKind(StrEnum):
    PROFILE_PIECE = "profile_piece"
    GLASS_PANE = "glass_pane"
    ELEMENT = "element"
    HARDWARE_KIT = "hardware_kit"


@dataclass(frozen=True)
class StageEvent:
    """One immutable entry in an item's history."""

    stage: Stage
    at: datetime
    operator: str | None = None
    station: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "at": self.at.isoformat(),
            "operator": self.operator,
            "station": self.station,
            "note": self.note,
        }


@dataclass
class ProductionItem:
    """One trackable physical item."""

    item_id: str
    kind: ItemKind
    description: str = ""
    project_id: str = ""
    element_ref: str | None = None
    barcode: str | None = None
    quantity: int = 1

    stage: Stage = Stage.PLANNED
    history: list[StageEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.history:
            self.history.append(
                StageEvent(stage=self.stage, at=datetime.now(timezone.utc), note="created")
            )

    # -- transitions --------------------------------------------------------- #
    def can_advance(self, to: Stage) -> tuple[bool, str | None]:
        """Whether ``to`` is reachable, and why not if it is not."""
        if self.stage is to:
            return False, f"כבר בשלב {to.label('he')}"
        if self.stage.is_terminal:
            return False, f"{self.item_id} במצב {self.stage.label('he')} ולא יכול לזוז"
        if to not in TRANSITIONS[self.stage]:
            allowed = ", ".join(sorted(s.label("he") for s in TRANSITIONS[self.stage]))
            return False, (
                f"אי אפשר לעבור מ{self.stage.label('he')} ל{to.label('he')} "
                f"(מותר: {allowed})"
            )
        return True, None

    def advance(
        self,
        to: Stage,
        *,
        operator: str | None = None,
        station: str | None = None,
        note: str | None = None,
    ) -> tuple[bool, str | None]:
        """Move to ``to``, recording the event. Returns ``(ok, reason)``."""
        ok, reason = self.can_advance(to)
        if not ok:
            _log.warning("Rejected transition for %s: %s", self.item_id, reason)
            return False, reason

        self.stage = to
        self.history.append(
            StageEvent(
                stage=to,
                at=datetime.now(timezone.utc),
                operator=operator,
                station=station,
                note=note,
            )
        )
        publish(
            "mes.stage_changed",
            source="mes",
            item_id=self.item_id,
            stage=to.value,
            operator=operator,
        )
        return True, None

    # -- queries -------------------------------------------------------------- #
    @property
    def is_complete(self) -> bool:
        return self.stage is Stage.SHIPPED

    @property
    def entered_current_stage(self) -> datetime:
        return self.history[-1].at

    def time_in_stage(self) -> float:
        """Seconds spent in the current stage."""
        return (datetime.now(timezone.utc) - self.entered_current_stage).total_seconds()

    def progress(self) -> float:
        """Completion fraction 0..1 along the normal flow."""
        if self.stage is Stage.SCRAPPED:
            return 0.0
        if self.stage is Stage.REWORK:
            # Rework sits between stages; report the last good position.
            previous = [e.stage for e in self.history if e.stage.order >= 0]
            position = previous[-1].order if previous else 0
        else:
            position = max(self.stage.order, 0)
        return position / Stage.SHIPPED.order

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "description": self.description,
            "project_id": self.project_id,
            "element_ref": self.element_ref,
            "barcode": self.barcode,
            "quantity": self.quantity,
            "stage": self.stage.value,
            "progress": round(self.progress(), 3),
            "history": [event.as_dict() for event in self.history],
        }


@dataclass
class WorkOrder:
    """A batch of production items released to the floor together."""

    work_order_id: str = field(default_factory=lambda: f"WO-{uuid4().hex[:8].upper()}")
    project_id: str = ""
    name: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    due_date: datetime | None = None
    items: list[ProductionItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[ProductionItem]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def add(self, item: ProductionItem) -> "WorkOrder":
        if any(existing.item_id == item.item_id for existing in self.items):
            raise ProfileOSError("Duplicate item id in work order", item_id=item.item_id)
        self.items.append(item)
        return self

    def find(self, item_id: str) -> ProductionItem | None:
        return next((item for item in self.items if item.item_id == item_id), None)

    def by_barcode(self, payload: str) -> ProductionItem | None:
        """Look up an item by its scanned barcode payload."""
        return next((item for item in self.items if item.barcode == payload), None)

    def by_stage(self, stage: Stage) -> list[ProductionItem]:
        return [item for item in self.items if item.stage is stage]

    def by_kind(self, kind: ItemKind) -> list[ProductionItem]:
        return [item for item in self.items if item.kind is kind]

    # -- reporting ------------------------------------------------------------ #
    @property
    def progress(self) -> float:
        """Mean completion across all items, 0..1."""
        if not self.items:
            return 0.0
        return sum(item.progress() for item in self.items) / len(self.items)

    @property
    def is_complete(self) -> bool:
        return bool(self.items) and all(
            item.is_complete or item.stage is Stage.SCRAPPED for item in self.items
        )

    def stage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.stage.value] = counts.get(item.stage.value, 0) + 1
        return counts

    def scan(
        self,
        payload: str,
        to: Stage,
        *,
        operator: str | None = None,
        station: str | None = None,
    ) -> tuple[bool, str]:
        """Process a shop-floor scan.

        Returns ``(ok, message)`` — a message the tablet can show the operator
        directly, whether the scan succeeded or not.
        """
        item = self.by_barcode(payload) or self.find(payload)
        if item is None:
            return False, f"קוד לא מוכר: {payload}"

        ok, reason = item.advance(to, operator=operator, station=station)
        if not ok:
            return False, f"{item.description or item.item_id}: {reason}"
        return True, f"{item.description or item.item_id} ← {to.label('he')}"

    def bottleneck(self) -> tuple[Stage, int] | None:
        """The stage holding the most items — where the floor is jammed."""
        counts = {
            stage: len(self.by_stage(stage))
            for stage in Stage
            if not stage.is_terminal and stage is not Stage.PLANNED
        }
        counts = {s: n for s, n in counts.items() if n > 0}
        if not counts:
            return None
        stage = max(counts, key=lambda s: counts[s])
        return stage, counts[stage]

    def summary(self) -> dict[str, Any]:
        return {
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "name": self.name,
            "items": len(self.items),
            "progress_pct": round(self.progress * 100.0, 1),
            "complete": self.is_complete,
            "stages": self.stage_counts(),
            "scrapped": len(self.by_stage(Stage.SCRAPPED)),
            "rework": len(self.by_stage(Stage.REWORK)),
        }


def work_order_from_builds(
    builds: Iterable[Any],
    *,
    project_id: str = "",
    name: str = "",
    nesting: Any = None,
) -> WorkOrder:
    """Create a work order covering every piece, pane and element in a project.

    Each item is given a :class:`~profileos.mes.barcode.TrackingCode` payload so
    it can be scanned the moment it leaves the saw.
    """
    from .barcode import TrackingCode

    order = WorkOrder(project_id=project_id, name=name)

    for build in builds:
        opening = build.opening
        for copy_index in range(opening.quantity):
            suffix = f"-{copy_index + 1:02d}" if opening.quantity > 1 else ""

            role_hebrew = {
                "frame_horizontal": "מלבן אופקי", "frame_vertical": "מלבן אנכי",
                "sash_horizontal": "כנף אופקי", "sash_vertical": "כנף אנכי",
                "mullion": "אומנה", "transom": "משקוף רוחב",
                "bead_horizontal": "סרגל זיגוג אופקי", "bead_vertical": "סרגל זיגוג אנכי",
            }
            for cut_index, cut in enumerate(build.cuts, start=1):
                for piece_index in range(cut.quantity):
                    item_id = f"{opening.element_id}{suffix}-P{cut_index:02d}{piece_index + 1}"
                    code = TrackingCode(
                        project=project_id or "PRJ",
                        element=opening.element_id,
                        piece=item_id,
                        stage="CUT",
                    )
                    order.add(
                        ProductionItem(
                            item_id=item_id,
                            kind=ItemKind.PROFILE_PIECE,
                            description=(
                                f"{cut.profile_id} ⁦{cut.length:.1f}⁩ מ\"מ — "
                                f"{role_hebrew.get(cut.role, cut.role)}"
                            ),
                            project_id=project_id,
                            element_ref=opening.element_id,
                            barcode=code.payload(),
                            metadata={
                                "profile_id": cut.profile_id,
                                "length": cut.length,
                                "angle_left": cut.angle_left,
                                "angle_right": cut.angle_right,
                                "role": cut.role,
                            },
                        )
                    )

            for pane_index, panel in enumerate(build.glass, start=1):
                item_id = f"{opening.element_id}{suffix}-G{pane_index:02d}"
                code = TrackingCode(
                    project=project_id or "PRJ",
                    element=opening.element_id,
                    piece=item_id,
                    stage="GLAZED",
                )
                order.add(
                    ProductionItem(
                        item_id=item_id,
                        kind=ItemKind.GLASS_PANE,
                        description=(
                            f"זכוכית ⁦{panel.width:.0f} × {panel.height:.0f}⁩ — "
                            f"⁦{panel.build_up.name}⁩"
                        ),
                        project_id=project_id,
                        element_ref=opening.element_id,
                        barcode=code.payload(),
                        metadata={
                            "width": panel.width,
                            "height": panel.height,
                            "mass_kg": round(panel.mass, 1),
                            "safety_required": panel.safety_required,
                        },
                    )
                )

            element_id = f"{opening.element_id}{suffix}"
            code = TrackingCode(
                project=project_id or "PRJ",
                element=opening.element_id,
                piece=element_id,
                stage="ASSEMBLED",
            )
            order.add(
                ProductionItem(
                    item_id=element_id,
                    kind=ItemKind.ELEMENT,
                    description=f"{opening.name} ⁦{opening.width:.0f} × {opening.height:.0f}⁩ מ\"מ",
                    project_id=project_id,
                    element_ref=opening.element_id,
                    barcode=code.payload(),
                    metadata={"area_m2": round(opening.area, 3)},
                )
            )

    _log.info("Created work order %s with %d item(s)", order.work_order_id, len(order))
    return order


__all__ = [
    "Stage",
    "TRANSITIONS",
    "ItemKind",
    "StageEvent",
    "ProductionItem",
    "WorkOrder",
    "work_order_from_builds",
]
