"""The machining job: what gets handed to a driver.

A :class:`MachiningJob` is the machine-neutral description of everything one
machine has to do for one production batch. Drivers consume it and emit native
code; nothing in it is specific to any vendor.

The hierarchy mirrors the shop floor:

``MachiningJob``
    One batch for one machine.
``PieceProgram``
    One physical piece: its finished length, end angles, the operations on it,
    and the clamp arrangement that lets those operations happen.
``OperationSet``
    The machine-neutral features (:mod:`profileos.cnc.operations`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from ..models.machines import MachineDefinition, ToolLibrary
from ..models.orders import CutPiece
from ..models.profile import Face, ProfileDefinition
from .clamps import ClampPlan, plan_clamps_for_machine
from .operations import Operation, OperationSet, SawCut


@dataclass
class PieceProgram:
    """One piece and everything to be done to it."""

    piece_id: str
    profile_id: str
    length: float
    angle_left: float = 90.0
    angle_right: float = 90.0
    operations: OperationSet = field(default_factory=OperationSet)
    clamp_plan: ClampPlan | None = None

    #: Where this piece sits in the nesting plan, for traceability.
    bar_index: int | None = None
    position_on_bar: float | None = None
    mark: str | None = None
    element_ref: str | None = None
    quantity: int = 1
    #: Populated by the MES module; drivers may engrave it.
    barcode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_mitred(self) -> bool:
        return abs(self.angle_left - 90.0) > 1e-9 or abs(self.angle_right - 90.0) > 1e-9

    @property
    def label(self) -> str:
        return self.mark or self.piece_id

    def faces(self) -> list[Face]:
        return self.operations.faces()

    def validate(self) -> list[str]:
        problems = list(self.operations.validate())
        if self.length <= 0:
            problems.append(f"{self.piece_id}: piece length must be positive")

        # An operation off the end of the bar is a modelling error that would
        # otherwise become a crash at the machine.
        for op in self.operations:
            lo, hi = op.extent_x()
            if lo < -1e-6 or hi > self.length + 1e-6:
                problems.append(
                    f"{self.piece_id}/{op.op_id}: operation spans X {lo:.1f}..{hi:.1f} mm, "
                    f"outside the {self.length:.1f} mm piece"
                )
        return problems

    def plan_clamps(self, machine: MachineDefinition) -> ClampPlan:
        """Compute and attach the clamp plan for this piece."""
        self.clamp_plan = plan_clamps_for_machine(
            machine, self.length, list(self.operations)
        )
        return self.clamp_plan

    @classmethod
    def from_cut_piece(
        cls,
        piece: CutPiece,
        *,
        profile: ProfileDefinition | None = None,
        operations: OperationSet | None = None,
    ) -> "PieceProgram":
        """Build a program from a nested :class:`CutPiece`.

        Machining macros carried on the piece are expanded into concrete
        operations when no explicit ``operations`` set is supplied.
        """
        from .macros import expand_macros

        resolved = operations
        if resolved is None:
            resolved = expand_macros(piece.machining_macros, bar_length=piece.length)

        return cls(
            piece_id=piece.piece_id,
            profile_id=piece.profile_id,
            length=piece.length,
            angle_left=piece.angle_left,
            angle_right=piece.angle_right,
            operations=resolved,
            mark=piece.mark,
            element_ref=piece.element_ref,
        )


@dataclass
class MachiningJob:
    """A batch of pieces destined for one machine."""

    machine: MachineDefinition
    pieces: list[PieceProgram] = field(default_factory=list)
    job_id: str = field(default_factory=lambda: f"JOB-{uuid4().hex[:8].upper()}")
    name: str = "Untitled job"
    project_ref: str | None = None
    customer: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tool_library: ToolLibrary | None = None
    #: Free-form values made available to driver templates.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[PieceProgram]:
        return iter(self.pieces)

    def __len__(self) -> int:
        return len(self.pieces)

    def add(self, piece: PieceProgram) -> "MachiningJob":
        self.pieces.append(piece)
        return self

    @property
    def total_pieces(self) -> int:
        return sum(piece.quantity for piece in self.pieces)

    def profiles(self) -> list[str]:
        seen: dict[str, None] = {}
        for piece in self.pieces:
            seen.setdefault(piece.profile_id, None)
        return list(seen)

    def all_operations(self) -> list[Operation]:
        return [op for piece in self.pieces for op in piece.operations]

    def tool_numbers(self) -> list[int]:
        return sorted(
            {op.tool_number for op in self.all_operations() if op.tool_number is not None}
        )

    def has_saw_cuts(self) -> bool:
        return any(isinstance(op, SawCut) for op in self.all_operations())

    def plan_all_clamps(self) -> list[str]:
        """Plan clamps for every piece; returns the accumulated warnings."""
        warnings: list[str] = []
        for piece in self.pieces:
            plan = piece.plan_clamps(self.machine)
            for message in plan.warnings:
                warnings.append(f"{piece.label}: {message}")
            for collision in plan.unresolved:
                warnings.append(f"{piece.label}: UNRESOLVED {collision}")
        return warnings

    def validate(self) -> list[str]:
        """Check every piece against the machine envelope and its own geometry."""
        problems: list[str] = []
        for piece in self.pieces:
            problems.extend(piece.validate())

            ok, reason = self.machine.accepts_bar(
                piece.length,
                self.machine.max_profile_width,
                self.machine.max_profile_height,
            )
            if not ok and reason and "bar length" in reason:
                problems.append(f"{piece.label}: {reason}")

            for face in piece.faces():
                if not self.machine.supports_face(face):
                    problems.append(
                        f"{piece.label}: machine {self.machine.name} cannot reach "
                        f"the {face.value} face"
                    )

            for angle in (piece.angle_left, piece.angle_right):
                if not self.machine.accepts_cut_angle(angle):
                    problems.append(
                        f"{piece.label}: cut angle {angle:g} deg is outside the "
                        f"machine range {self.machine.min_cut_angle:g}-"
                        f"{self.machine.max_cut_angle:g} deg"
                    )
        return problems

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "machine": self.machine.name,
            "post_processor": self.machine.post_processor,
            "pieces": len(self.pieces),
            "total_quantity": self.total_pieces,
            "profiles": self.profiles(),
            "operations": len(self.all_operations()),
            "tools": self.tool_numbers(),
        }


__all__ = ["PieceProgram", "MachiningJob"]
