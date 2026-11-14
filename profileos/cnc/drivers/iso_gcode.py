"""ISO G-code driver for open controls (Fanuc, Siemens Sinumerik, Heidenhain-ISO).

Unlike the vendor formats, G-code needs explicit motion, so this driver lowers
the IR through :mod:`profileos.cnc.toolpath` and emits blocks per DIN 66025.

Dialects
--------
``fanuc``
    Canned cycles ``G81``/``G83``, cancelled by ``G80``. Cutter compensation
    ``G41``/``G42`` with a ``D`` offset register. Programs numbered ``Onnnn``.
``siemens``
    Sinumerik cycle calls ``CYCLE81``/``CYCLE83`` instead of canned cycles, and
    ``CYCLE800``-style comments. Compensation words are the same.

Both dialects share the motion blocks, so the difference is confined to
:meth:`IsoGCodePostProcessor._drill_cycle` and the program header.

Face handling
-------------
A 3-axis router reaches one face per setup. Operations on other faces are
emitted as separate programs with a setup comment, rather than silently folded
into one program that the machine cannot execute. A 5-axis machine (declared by
``axis_count >= 5``) gets a single program with ``A``/``C`` orientation blocks.
"""

from __future__ import annotations

from typing import ClassVar

from ...models.profile import Face
from ..job import MachiningJob, PieceProgram
from ..operations import Operation, OperationType, SawCut, Thread
from ..toolpath import Move, MoveType, Toolpath, generate_toolpath
from .base import BasePostProcessor, PostResult, register_driver

#: Tool-orientation angles (A, C) that present each face to a vertical spindle.
FACE_ORIENTATION: dict[Face, tuple[float, float]] = {
    Face.TOP: (0.0, 0.0),
    Face.FRONT: (90.0, 0.0),
    Face.BACK: (90.0, 180.0),
    Face.BOTTOM: (180.0, 0.0),
    Face.LEFT: (90.0, 90.0),
    Face.RIGHT: (90.0, 270.0),
}


def _word(letter: str, value: float | None, digits: int = 3) -> str:
    """Format one G-code word, or an empty string when the value is absent."""
    if value is None:
        return ""
    return f"{letter}{value:.{digits}f}".rstrip("0").rstrip(".") if "." in f"{value:.{digits}f}" else f"{letter}{value:.0f}"


@register_driver
class IsoGCodePostProcessor(BasePostProcessor):
    """Universal ISO G-code (DIN 66025)."""

    key: ClassVar[str] = "iso.gcode"
    display_name: ClassVar[str] = "ISO G-code (Fanuc / Siemens)"
    vendor: ClassVar[str] = "Generic"
    extension: ClassVar[str] = ".nc"
    format_version: ClassVar[str] = "1.0"
    supports_cutter_compensation: ClassVar[bool] = True
    single_file_per_job: ClassVar[bool] = False
    supported_operations: ClassVar[frozenset[OperationType]] = frozenset(
        {
            OperationType.DRILL,
            OperationType.RECTANGULAR_POCKET,
            OperationType.CIRCULAR_POCKET,
            OperationType.SLOT,
            OperationType.CONTOUR,
            OperationType.END_NOTCH,
        }
    )

    def __init__(self, defaults=None, *, dialect: str = "fanuc") -> None:
        super().__init__(defaults)
        self.dialect = dialect.strip().lower()
        if self.dialect not in {"fanuc", "siemens"}:
            raise ValueError(f"Unknown G-code dialect: {dialect!r}")
        self._line_number = 0

    # -- capability handling ------------------------------------------------ #
    def _check_capabilities(self, job: MachiningJob) -> None:
        saw_cuts = [op for op in job.all_operations() if isinstance(op, SawCut)]
        if saw_cuts:
            self.warn(
                f"{len(saw_cuts)} saw cut(s) omitted: a router has no saw. "
                "Post the cutting list to a saw driver as well."
            )
        threads = [op for op in job.all_operations() if isinstance(op, Thread)]
        if threads:
            self.warn(
                f"{len(threads)} thread(s) omitted: rigid tapping cycles are "
                "control specific and are not emitted generically."
            )
        super()._check_capabilities(job)

    # -- rendering ---------------------------------------------------------- #
    def _render(self, job: MachiningJob) -> list[PostResult]:
        results: list[PostResult] = []
        multi_axis = job.machine.axis_count >= 5

        for index, piece in enumerate(job.pieces, start=1):
            if multi_axis:
                text = self._piece_program(job, piece, piece.faces(), index)
                results.append(
                    PostResult(
                        filename=self.piece_filename(job, piece),
                        content=text,
                        encoding=self.defaults.output_encoding,
                        stats={"piece": piece.piece_id, "faces": len(piece.faces())},
                    )
                )
            else:
                # One setup per face on a 3-axis machine.
                for face in piece.faces():
                    text = self._piece_program(job, piece, [face], index)
                    stem = self.piece_filename(job, piece).removesuffix(self.extension)
                    results.append(
                        PostResult(
                            filename=f"{stem}_{face.value}{self.extension}",
                            content=text,
                            encoding=self.defaults.output_encoding,
                            stats={"piece": piece.piece_id, "face": face.value},
                        )
                    )
        if not results:
            results.append(
                PostResult(
                    filename=self.default_filename(job),
                    content=self._empty_program(job),
                    encoding=self.defaults.output_encoding,
                )
            )
        return results

    def _piece_program(
        self, job: MachiningJob, piece: PieceProgram, faces: list[Face], number: int
    ) -> str:
        self._line_number = 0
        lines: list[str] = []
        safe_z = self.defaults.safe_z_mm

        if self.dialect == "fanuc":
            lines.append("%")
            lines.append(f"O{number:04d} ({piece.label[:20]})")
        else:
            lines.append(f"; {piece.label}")

        for comment in self.header_comment_lines(job):
            lines.append(self._comment(comment))
        lines.append(self._comment(f"Piece {piece.piece_id} length {piece.length:.1f} mm"))
        if piece.clamp_plan is not None:
            positions = ", ".join(
                f"{c.id}@{c.position:.0f}" for c in piece.clamp_plan.active_clamps()
            )
            lines.append(self._comment(f"Clamps: {positions}"))

        # Safety block: metric, absolute, XY plane, no compensation, no cycle.
        lines.append("G21 G90 G17 G40 G80 G94")
        lines.append(f"G0 Z{safe_z:.3f}")

        current_tool: int | None = None
        for face in faces:
            operations = [
                op for op in piece.operations.sorted_for_machining() if op.face == face
            ]
            if not operations:
                continue

            lines.append("")
            lines.append(self._comment(f"--- face {face.value} ---"))
            if job.machine.axis_count >= 5:
                a_angle, c_angle = FACE_ORIENTATION[face]
                lines.append(f"G0 A{a_angle:.3f} C{c_angle:.3f}")
            else:
                lines.append(self._comment(f"SETUP: present face {face.value} to the spindle"))

            for op in operations:
                tool = self.tool_for(job, op)
                if op.tool_number is not None and op.tool_number != current_tool:
                    lines.append(f"G0 Z{safe_z:.3f}")
                    lines.append("M5")
                    name = tool.name if tool else "tool"
                    lines.append(f"T{op.tool_number} M6 {self._comment(name)}")
                    lines.append(f"S{self.speed_for(job, op)} M3")
                    lines.append("M8")
                    current_tool = op.tool_number

                lines.append(self._comment(f"{op.op_id} {op.op_type.value}"))
                lines.extend(self._operation_blocks(job, op, tool, safe_z))

        lines.append("")
        lines.append(f"G0 Z{safe_z:.3f}")
        lines.append("G40")
        lines.append("M9")
        lines.append("M5")
        lines.append("M30" if self.dialect == "fanuc" else "M30")
        if self.dialect == "fanuc":
            lines.append("%")
        return "\n".join(lines) + "\n"

    def _operation_blocks(
        self, job: MachiningJob, op: Operation, tool, safe_z: float
    ) -> list[str]:
        path: Toolpath = generate_toolpath(
            op,
            tool,
            safe_z=safe_z,
            feed=self.feed_for(job, op),
            spindle_speed=self.speed_for(job, op),
            use_control_compensation=True,
        )

        blocks: list[str] = []
        compensation_active = False
        for move in path:
            if move.move_type is MoveType.DRILL_CYCLE:
                blocks.extend(self._drill_cycle(move))
                continue

            if (
                path.compensation.value != "none"
                and move.move_type is MoveType.LINEAR
                and not compensation_active
            ):
                offset = op.tool_number if op.tool_number is not None else 1
                blocks.append(f"{path.compensation.gcode} D{offset}")
                compensation_active = True

            blocks.append(self._motion_block(move))

        if compensation_active:
            blocks.append("G40")
        return [b for b in blocks if b]

    def _motion_block(self, move: Move) -> str:
        code = {
            MoveType.RAPID: "G0",
            MoveType.LINEAR: "G1",
            MoveType.ARC_CW: "G2",
            MoveType.ARC_CCW: "G3",
        }.get(move.move_type)
        if code is None:
            return ""

        words = [code]
        for letter, value in (("X", move.x), ("Y", move.y), ("Z", move.z)):
            if value is not None:
                words.append(f"{letter}{value:.3f}")
        if move.move_type in (MoveType.ARC_CW, MoveType.ARC_CCW):
            for letter, value in (("I", move.i), ("J", move.j)):
                if value is not None:
                    words.append(f"{letter}{value:.3f}")
        if move.is_cutting and move.feed:
            words.append(f"F{move.feed:.1f}")
        if move.comment:
            words.append(self._comment(move.comment))
        return " ".join(words)

    def _drill_cycle(self, move: Move) -> list[str]:
        """Emit a drilling cycle in the active dialect."""
        depth = move.z if move.z is not None else 0.0
        retract = move.retract_z if move.retract_z is not None else self.defaults.safe_z_mm
        feed = move.feed or self.defaults.default_feed_mm_min

        if self.dialect == "fanuc":
            cycle = "G83" if move.peck else "G81"
            words = [
                cycle,
                f"X{move.x:.3f}" if move.x is not None else "",
                f"Y{move.y:.3f}" if move.y is not None else "",
                f"Z{depth:.3f}",
                f"R{retract:.3f}",
            ]
            if move.peck:
                words.append(f"Q{move.peck:.3f}")
            words.append(f"F{feed:.1f}")
            return [" ".join(w for w in words if w), "G80"]

        # Sinumerik cycle call.
        if move.peck:
            call = (
                f"CYCLE83(RTP:={retract:.3f}, RFP:=0, SDIS:=2, DP:={depth:.3f}, "
                f"FDEP:={-abs(move.peck):.3f}, DAM:=0, FRF:=1, VARI:=0)"
            )
        else:
            call = f"CYCLE81(RTP:={retract:.3f}, RFP:=0, SDIS:=2, DP:={depth:.3f})"
        position = " ".join(
            w
            for w in (
                f"X{move.x:.3f}" if move.x is not None else "",
                f"Y{move.y:.3f}" if move.y is not None else "",
            )
            if w
        )
        return [f"G1 {position} F{feed:.1f}" if position else "", call]

    def _comment(self, text: str) -> str:
        safe = text.replace("(", "[").replace(")", "]")
        return f"({safe})" if self.dialect == "fanuc" else f"; {safe}"

    def _empty_program(self, job: MachiningJob) -> str:
        lines = [self._comment(c) for c in self.header_comment_lines(job)]
        lines.append(self._comment("No machinable operations in this job"))
        lines.append("M30")
        return "\n".join(lines) + "\n"


@register_driver
class SiemensGCodePostProcessor(IsoGCodePostProcessor):
    """Sinumerik-dialect G-code."""

    key: ClassVar[str] = "iso.gcode.siemens"
    display_name: ClassVar[str] = "Siemens Sinumerik G-code"
    vendor: ClassVar[str] = "Siemens"
    extension: ClassVar[str] = ".mpf"

    def __init__(self, defaults=None) -> None:
        super().__init__(defaults, dialect="siemens")


__all__ = [
    "FACE_ORIENTATION",
    "IsoGCodePostProcessor",
    "SiemensGCodePostProcessor",
]
