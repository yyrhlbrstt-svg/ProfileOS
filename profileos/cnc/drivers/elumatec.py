"""Elumatec drivers: NCX, ECX, NCW and DGX.

Covers the Elumatec / elusoft (eluCad) family:

``elumatec.ncx``
    XML program for SBZ machining centres (SBZ 122/140/151/630).
``elumatec.ecx``
    XML profile/part exchange document — geometry and machining without the
    machine-specific setup, used to move parts between eluCad installations.
``elumatec.ncw``
    Flat key/value text variant accepted by older SBZ controls.
``elumatec.dgx``
    Cut list for DG double-mitre saws (DG 104/244): lengths and angles only,
    no milling.

.. important::
   These formats are proprietary. The writers here follow the documented
   element and field structure and produce well-formed, self-consistent
   programs, but they have **not** been validated against a physical machine or
   an official DTD. Before production use, post a known part, diff it against a
   file eluCad produced for the same part, and adjust the field mapping in this
   module. The IR and driver split is designed to make exactly that adjustment
   a local change.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import ClassVar

from ...models.profile import Face
from ..job import MachiningJob, PieceProgram
from ..operations import (
    CircularPocket,
    Contour,
    Counterbore,
    Drill,
    EndNotch,
    Engrave,
    Operation,
    OperationType,
    RectangularPocket,
    SawCut,
    Slot,
    Thread,
)
from .base import BasePostProcessor, PostResult, register_driver

#: Face names as the eluCad coordinate model labels them.
ELU_FACE = {
    Face.TOP: "A",
    Face.FRONT: "B",
    Face.BACK: "C",
    Face.BOTTOM: "D",
    Face.LEFT: "E",
    Face.RIGHT: "F",
}


def _indent(element: ET.Element, level: int = 0) -> None:
    """Pretty-print an ElementTree in place (stdlib indent is 3.9+ only on trees)."""
    padding = "\n" + "  " * level
    if len(element):
        if not (element.text or "").strip():
            element.text = padding + "  "
        for child in element:
            _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = padding
    if level and not (element.tail or "").strip():
        element.tail = padding


def _num(value: float, digits: int = 3) -> str:
    """Format a number the way the controls expect: fixed point, no exponent."""
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") or "0"


@register_driver
class NcxPostProcessor(BasePostProcessor):
    """Elumatec NCX program for SBZ machining centres."""

    key: ClassVar[str] = "elumatec.ncx"
    display_name: ClassVar[str] = "Elumatec NCX (SBZ)"
    vendor: ClassVar[str] = "Elumatec"
    extension: ClassVar[str] = ".ncx"
    format_version: ClassVar[str] = "3.2"
    supports_cutter_compensation: ClassVar[bool] = True
    supported_operations: ClassVar[frozenset[OperationType]] = frozenset(
        {
            OperationType.DRILL,
            OperationType.COUNTERBORE,
            OperationType.THREAD,
            OperationType.RECTANGULAR_POCKET,
            OperationType.CIRCULAR_POCKET,
            OperationType.SLOT,
            OperationType.CONTOUR,
            OperationType.END_NOTCH,
            OperationType.SAW_CUT,
            OperationType.ENGRAVE,
        }
    )

    def _render(self, job: MachiningJob) -> str:
        root = ET.Element(
            "NCX_Document",
            {"Version": self.format_version, "Generator": "ProfileOS"},
        )

        header = ET.SubElement(root, "Header")
        ET.SubElement(header, "ProjectName").text = job.name
        ET.SubElement(header, "JobId").text = job.job_id
        if job.customer:
            ET.SubElement(header, "Customer").text = job.customer
        ET.SubElement(header, "CreatedTimestamp").text = self.timestamp()
        ET.SubElement(
            header,
            "TargetMachine",
            {
                "Model": job.machine.model,
                "Vendor": job.machine.vendor,
                "Axes": str(job.machine.axis_count),
            },
        )

        if job.tool_library is not None:
            tools = ET.SubElement(root, "Tools")
            for number in job.tool_numbers():
                tool = job.tool_library.by_number(number)
                if tool is None:
                    self.warn(f"Tool {number} is referenced but missing from the library")
                    continue
                ET.SubElement(
                    tools,
                    "Tool",
                    {
                        "Number": str(tool.number),
                        "Name": tool.name,
                        "Type": tool.tool_type.value,
                        "Diameter": _num(tool.diameter),
                        "SpindleSpeed": str(tool.spindle_rpm),
                        "FeedRate": _num(tool.feed_mm_min, 1),
                    },
                )

        # Group pieces by profile, mirroring the NCX Profile/Piece nesting.
        by_profile: dict[str, list[PieceProgram]] = {}
        for piece in job.pieces:
            by_profile.setdefault(piece.profile_id, []).append(piece)

        for profile_id, pieces in by_profile.items():
            profile_element = ET.SubElement(
                root,
                "Profile",
                {
                    "ProfileName": profile_id,
                    "BarID": f"BAR_{profile_id}",
                    "StandardLength": _num(max(p.length for p in pieces), 1),
                },
            )
            for piece in pieces:
                self._write_piece(profile_element, job, piece)

        _indent(root)
        xml = ET.tostring(root, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n"

    def _write_piece(
        self, parent: ET.Element, job: MachiningJob, piece: PieceProgram
    ) -> None:
        attributes = {
            "PieceID": piece.piece_id,
            "CutLength": _num(piece.length, 1),
            "AngleLeft": _num(piece.angle_left, 1),
            "AngleRight": _num(piece.angle_right, 1),
            "Quantity": str(piece.quantity),
        }
        if piece.mark:
            attributes["Mark"] = piece.mark
        if piece.barcode:
            attributes["Barcode"] = piece.barcode
        piece_element = ET.SubElement(parent, "Piece", attributes)

        # Clamp positions come first: the control must set up before it cuts.
        if piece.clamp_plan is not None:
            clamps = ET.SubElement(piece_element, "Clamps")
            for clamp in piece.clamp_plan.active_clamps():
                ET.SubElement(
                    clamps,
                    "Clamp",
                    {
                        "ID": clamp.id,
                        "Position": _num(clamp.position, 1),
                        "Width": _num(clamp.width, 1),
                    },
                )
            for move in piece.clamp_plan.moves:
                ET.SubElement(
                    clamps,
                    "ClampMove",
                    {
                        "ID": move.clamp_id,
                        "From": _num(move.from_position, 1),
                        "To": _num(move.to_position, 1),
                    },
                )

        operations = ET.SubElement(piece_element, "Operations")
        for op in piece.operations.sorted_for_machining():
            self._write_operation(operations, job, op)

    def _write_operation(
        self, parent: ET.Element, job: MachiningJob, op: Operation
    ) -> None:
        element = ET.SubElement(
            parent,
            "Operation",
            {"Type": _NCX_TYPE[op.op_type], "ID": op.op_id},
        )
        ET.SubElement(element, "Face").text = ELU_FACE[op.face]

        def put(tag: str, value: object) -> None:
            ET.SubElement(element, tag).text = (
                _num(float(value)) if isinstance(value, (int, float)) else str(value)
            )

        if isinstance(op, Drill):
            put("X", op.x)
            put("Y", op.y)
            put("Z", 0.0)
            put("Depth", op.depth)
            put("Diameter", op.diameter)
            if op.through:
                put("Through", "1")
            if op.peck_depth:
                put("PeckDepth", op.peck_depth)
        elif isinstance(op, Counterbore):
            put("X", op.x)
            put("Y", op.y)
            put("PilotDiameter", op.pilot_diameter)
            put("PilotDepth", op.pilot_depth)
            put("BoreDiameter", op.bore_diameter)
            put("BoreDepth", op.bore_depth)
        elif isinstance(op, Thread):
            put("X", op.x)
            put("Y", op.y)
            put("Diameter", op.nominal_diameter)
            put("Pitch", op.pitch)
            put("Depth", op.depth)
            put("Method", "MILL" if op.milled else "TAP")
        elif isinstance(op, RectangularPocket):
            put("X", op.x)
            put("Y", op.y)
            put("Length", op.length)
            put("Width", op.width)
            put("Depth", op.depth)
            if op.corner_radius:
                put("CornerRadius", op.corner_radius)
            if op.rotation:
                put("Rotation", op.rotation)
            if op.through:
                put("Through", "1")
        elif isinstance(op, CircularPocket):
            put("X", op.x)
            put("Y", op.y)
            put("Diameter", op.diameter)
            put("Depth", op.depth)
        elif isinstance(op, Slot):
            put("X1", op.x1)
            put("Y1", op.y1)
            put("X2", op.x2)
            put("Y2", op.y2)
            put("Width", op.width)
            put("Depth", op.depth)
        elif isinstance(op, Contour):
            put("Depth", op.depth)
            put("Closed", "1" if op.closed else "0")
            put("Compensation", op.compensation.value.upper())
            points = ET.SubElement(element, "Points")
            for x, y in op.points:
                ET.SubElement(points, "P", {"X": _num(x), "Y": _num(y)})
        elif isinstance(op, EndNotch):
            lo, hi = op.extent_x()
            put("X", lo)
            put("Length", op.length)
            put("Depth", op.depth)
            put("End", "RIGHT" if op.from_right else "LEFT")
            if op.width:
                put("Width", op.width)
        elif isinstance(op, SawCut):
            put("Position", op.position)
            put("Angle", op.angle)
            if op.tilt:
                put("Tilt", op.tilt)
            put("Through", "1" if op.is_through else "0")
            if not op.is_through:
                put("Depth", op.depth)
        elif isinstance(op, Engrave):
            put("X", op.x)
            put("Y", op.y)
            put("Text", op.text)
            put("Height", op.height)
            put("Depth", op.depth)

        if op.tool_number is not None:
            put("ToolNumber", op.tool_number)
        put("SpindleSpeed", self.speed_for(job, op))
        put("FeedRate", self.feed_for(job, op))
        if op.comment:
            ET.SubElement(element, "Comment").text = op.comment


_NCX_TYPE: dict[OperationType, str] = {
    OperationType.DRILL: "Drill",
    OperationType.COUNTERBORE: "Counterbore",
    OperationType.THREAD: "Thread",
    OperationType.RECTANGULAR_POCKET: "RectangularPocket",
    OperationType.CIRCULAR_POCKET: "CircularPocket",
    OperationType.SLOT: "Slot",
    OperationType.CONTOUR: "Contour",
    OperationType.END_NOTCH: "EndNotch",
    OperationType.SAW_CUT: "SawCut",
    OperationType.ENGRAVE: "Engrave",
}


@register_driver
class EcxPostProcessor(NcxPostProcessor):
    """eluCad ECX part-exchange document.

    Structurally the same feature tree as NCX but wrapped in an exchange
    envelope and without machine-specific setup (no clamps, no spindle data),
    because the receiving installation supplies those.
    """

    key: ClassVar[str] = "elumatec.ecx"
    display_name: ClassVar[str] = "Elumatec ECX (eluCad exchange)"
    extension: ClassVar[str] = ".ecx"
    format_version: ClassVar[str] = "2.0"

    def _render(self, job: MachiningJob) -> str:
        # Reuse the NCX tree, then strip the machine-specific parts.
        xml = super()._render(job)
        root = ET.fromstring(xml.split("?>", 1)[1])
        root.tag = "ECX_Document"
        for piece in root.iter("Piece"):
            for clamps in list(piece.findall("Clamps")):
                piece.remove(clamps)
        for operation in root.iter("Operation"):
            for tag in ("SpindleSpeed", "FeedRate"):
                for node in list(operation.findall(tag)):
                    operation.remove(node)
        _indent(root)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode")
            + "\n"
        )


@register_driver
class NcwPostProcessor(BasePostProcessor):
    """Flat key/value NCW variant for older SBZ controls.

    One record per line, ``KEY=VALUE`` pairs separated by semicolons, with
    ``[SECTION]`` markers — the shape older elusoft controls parse.
    """

    key: ClassVar[str] = "elumatec.ncw"
    display_name: ClassVar[str] = "Elumatec NCW (legacy SBZ)"
    vendor: ClassVar[str] = "Elumatec"
    extension: ClassVar[str] = ".ncw"
    format_version: ClassVar[str] = "1.4"
    supported_operations: ClassVar[frozenset[OperationType]] = frozenset(
        {
            OperationType.DRILL,
            OperationType.RECTANGULAR_POCKET,
            OperationType.CIRCULAR_POCKET,
            OperationType.SLOT,
            OperationType.END_NOTCH,
            OperationType.SAW_CUT,
        }
    )

    def _render(self, job: MachiningJob) -> str:
        lines: list[str] = ["[HEADER]"]
        for comment in self.header_comment_lines(job):
            lines.append(f"; {comment}")
        lines.append(f"PROJECT={job.name}")
        lines.append(f"JOBID={job.job_id}")
        lines.append(f"MACHINE={job.machine.model}")
        lines.append(f"CREATED={self.timestamp()}")
        lines.append("")

        for index, piece in enumerate(job.pieces, start=1):
            lines.append(f"[PART {index}]")
            lines.append(
                f"ID={piece.piece_id};PROFILE={piece.profile_id};"
                f"LENGTH={_num(piece.length, 1)};"
                f"ANGL={_num(piece.angle_left, 1)};ANGR={_num(piece.angle_right, 1)};"
                f"QTY={piece.quantity}"
            )
            if piece.clamp_plan is not None:
                for clamp in piece.clamp_plan.active_clamps():
                    lines.append(f"CLAMP={clamp.id};POS={_num(clamp.position, 1)}")

            for op in piece.operations.sorted_for_machining():
                lines.append(self._operation_line(job, op))
            lines.append("")

        return "\n".join(lines)

    def _operation_line(self, job: MachiningJob, op: Operation) -> str:
        fields = [
            f"OP={_NCX_TYPE[op.op_type].upper()}",
            f"FACE={ELU_FACE[op.face]}",
        ]
        if isinstance(op, Drill):
            fields += [
                f"X={_num(op.x)}",
                f"Y={_num(op.y)}",
                f"D={_num(op.diameter)}",
                f"T={_num(op.depth)}",
            ]
        elif isinstance(op, RectangularPocket):
            fields += [
                f"X={_num(op.x)}",
                f"Y={_num(op.y)}",
                f"L={_num(op.length)}",
                f"W={_num(op.width)}",
                f"T={_num(op.depth)}",
            ]
        elif isinstance(op, CircularPocket):
            fields += [
                f"X={_num(op.x)}",
                f"Y={_num(op.y)}",
                f"D={_num(op.diameter)}",
                f"T={_num(op.depth)}",
            ]
        elif isinstance(op, Slot):
            fields += [
                f"X1={_num(op.x1)}",
                f"Y1={_num(op.y1)}",
                f"X2={_num(op.x2)}",
                f"Y2={_num(op.y2)}",
                f"W={_num(op.width)}",
                f"T={_num(op.depth)}",
            ]
        elif isinstance(op, EndNotch):
            lo, _ = op.extent_x()
            fields += [
                f"X={_num(lo)}",
                f"L={_num(op.length)}",
                f"T={_num(op.depth)}",
                f"END={'R' if op.from_right else 'L'}",
            ]
        elif isinstance(op, SawCut):
            fields += [f"POS={_num(op.position)}", f"ANG={_num(op.angle)}"]

        if op.tool_number is not None:
            fields.append(f"TOOL={op.tool_number}")
        fields.append(f"S={self.speed_for(job, op)}")
        fields.append(f"F={_num(self.feed_for(job, op), 1)}")
        return ";".join(fields)


@register_driver
class DgxPostProcessor(BasePostProcessor):
    """Elumatec DGX cut list for DG double-mitre saws.

    A saw takes lengths and angles, nothing else. Milling operations present in
    the job are reported as warnings rather than silently dropped, because a
    part that still needs machining must not be marked complete at the saw.
    """

    key: ClassVar[str] = "elumatec.dgx"
    display_name: ClassVar[str] = "Elumatec DGX (DG double-mitre saw)"
    vendor: ClassVar[str] = "Elumatec"
    extension: ClassVar[str] = ".dgx"
    format_version: ClassVar[str] = "2.1"
    supported_operations: ClassVar[frozenset[OperationType]] = frozenset(
        {OperationType.SAW_CUT}
    )
    supported_faces: ClassVar[frozenset[Face]] = frozenset(Face)

    def _check_capabilities(self, job: MachiningJob) -> None:
        # A saw legitimately receives jobs that also carry milling for a later
        # station, so those are warned about rather than rejected.
        milling = [
            op for op in job.all_operations() if op.op_type is not OperationType.SAW_CUT
        ]
        if milling:
            self.warn(
                f"{len(milling)} milling operation(s) are not emitted to the saw; "
                "the pieces still need machining at a machining centre."
            )

    def _render(self, job: MachiningJob) -> str:
        root = ET.Element("DGX", {"Version": self.format_version, "Generator": "ProfileOS"})
        header = ET.SubElement(root, "Order")
        ET.SubElement(header, "Name").text = job.name
        ET.SubElement(header, "JobId").text = job.job_id
        ET.SubElement(header, "Machine").text = job.machine.model
        ET.SubElement(header, "Created").text = self.timestamp()
        ET.SubElement(header, "Kerf").text = _num(job.machine.blade_kerf, 2)

        cut_list = ET.SubElement(root, "CutList")
        for index, piece in enumerate(job.pieces, start=1):
            attributes = {
                "Pos": str(index),
                "PieceID": piece.piece_id,
                "Profile": piece.profile_id,
                "Length": _num(piece.length, 1),
                "AngleLeft": _num(piece.angle_left, 1),
                "AngleRight": _num(piece.angle_right, 1),
                "Quantity": str(piece.quantity),
            }
            if piece.mark:
                attributes["Mark"] = piece.mark
            if piece.bar_index is not None:
                attributes["Bar"] = str(piece.bar_index)
            if piece.barcode:
                attributes["Barcode"] = piece.barcode
            ET.SubElement(cut_list, "Cut", attributes)

        _indent(root)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode")
            + "\n"
        )


__all__ = [
    "ELU_FACE",
    "NcxPostProcessor",
    "EcxPostProcessor",
    "NcwPostProcessor",
    "DgxPostProcessor",
]
