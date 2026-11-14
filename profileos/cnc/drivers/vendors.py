"""Drivers for Schueco, Kaban, Emmegi and FOM controls.

.. important::
   As with the Elumatec family, these are proprietary formats. Each writer
   below follows the documented/observed element structure and emits
   well-formed, internally consistent programs, but has **not** been validated
   against a physical machine. Before production use, post a known part and
   diff it against a file the vendor's own software produced for that part.
   The neutral IR means any correction is confined to this module.
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
from .base import BasePostProcessor, register_driver
from .elumatec import _indent, _num


@register_driver
class SchuecoMcoPostProcessor(BasePostProcessor):
    """Schueco MCO / Schuecal XML machining document.

    Schueco's model is oriented around *articles* (system profiles) carrying
    *machinings*, each referencing a hardware preparation. Faces are numbered
    1-6 rather than lettered.
    """

    key: ClassVar[str] = "schueco.mco"
    display_name: ClassVar[str] = "Schueco MCO / XML"
    vendor: ClassVar[str] = "Schueco"
    extension: ClassVar[str] = ".mco"
    format_version: ClassVar[str] = "1.6"
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
        }
    )

    #: Schueco numbers the faces; 1 is the reference (top) face.
    FACE_NUMBER: ClassVar[dict[Face, int]] = {
        Face.TOP: 1,
        Face.FRONT: 2,
        Face.BACK: 3,
        Face.BOTTOM: 4,
        Face.LEFT: 5,
        Face.RIGHT: 6,
    }

    def _render(self, job: MachiningJob) -> str:
        root = ET.Element(
            "SchuecoMachining",
            {"Version": self.format_version, "Origin": "ProfileOS"},
        )
        head = ET.SubElement(root, "Head")
        ET.SubElement(head, "Order").text = job.name
        ET.SubElement(head, "OrderId").text = job.job_id
        ET.SubElement(head, "Created").text = self.timestamp()
        ET.SubElement(head, "MachineType").text = job.machine.model
        if job.customer:
            ET.SubElement(head, "Customer").text = job.customer

        articles = ET.SubElement(root, "Articles")
        for piece in job.pieces:
            article = ET.SubElement(
                articles,
                "Article",
                {
                    "Number": piece.profile_id,
                    "PartId": piece.piece_id,
                    "Length": _num(piece.length, 1),
                    "Count": str(piece.quantity),
                },
            )
            ET.SubElement(
                article,
                "Cut",
                {
                    "AngleStart": _num(piece.angle_left, 1),
                    "AngleEnd": _num(piece.angle_right, 1),
                },
            )
            if piece.mark:
                ET.SubElement(article, "Position").text = piece.mark

            machinings = ET.SubElement(article, "Machinings")
            for op in piece.operations.sorted_for_machining():
                self._write_machining(machinings, job, op)

        _indent(root)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode")
            + "\n"
        )

    def _write_machining(
        self, parent: ET.Element, job: MachiningJob, op: Operation
    ) -> None:
        attributes = {
            "Id": op.op_id,
            "Type": op.op_type.value.upper(),
            "Side": str(self.FACE_NUMBER[op.face]),
            "Tool": str(op.tool_number) if op.tool_number is not None else "AUTO",
            "Speed": str(self.speed_for(job, op)),
            "Feed": _num(self.feed_for(job, op), 1),
        }
        element = ET.SubElement(parent, "Machining", attributes)

        geometry = ET.SubElement(element, "Geometry")
        values: dict[str, float | str] = {}
        if isinstance(op, Drill):
            values = {"X": op.x, "Y": op.y, "Diameter": op.diameter, "Depth": op.depth}
        elif isinstance(op, Counterbore):
            values = {
                "X": op.x,
                "Y": op.y,
                "Diameter": op.bore_diameter,
                "Depth": op.bore_depth,
                "PilotDiameter": op.pilot_diameter,
                "PilotDepth": op.pilot_depth,
            }
        elif isinstance(op, Thread):
            values = {
                "X": op.x,
                "Y": op.y,
                "Diameter": op.nominal_diameter,
                "Pitch": op.pitch,
                "Depth": op.depth,
            }
        elif isinstance(op, RectangularPocket):
            values = {
                "X": op.x,
                "Y": op.y,
                "DX": op.length,
                "DY": op.width,
                "Depth": op.depth,
                "Radius": op.corner_radius,
            }
        elif isinstance(op, CircularPocket):
            values = {"X": op.x, "Y": op.y, "Diameter": op.diameter, "Depth": op.depth}
        elif isinstance(op, Slot):
            values = {
                "X": op.x1,
                "Y": op.y1,
                "X2": op.x2,
                "Y2": op.y2,
                "Width": op.width,
                "Depth": op.depth,
            }
        elif isinstance(op, EndNotch):
            lo, _ = op.extent_x()
            values = {
                "X": lo,
                "Length": op.length,
                "Depth": op.depth,
                "Reference": "END" if op.from_right else "START",
            }
        elif isinstance(op, SawCut):
            values = {"X": op.position, "Angle": op.angle, "Tilt": op.tilt}
        elif isinstance(op, Contour):
            values = {"Depth": op.depth}
            path = ET.SubElement(element, "Path", {"Closed": "1" if op.closed else "0"})
            for x, y in op.points:
                ET.SubElement(path, "Pt", {"X": _num(x), "Y": _num(y)})

        for name, value in values.items():
            ET.SubElement(geometry, name).text = (
                _num(float(value)) if isinstance(value, (int, float)) else str(value)
            )


@register_driver
class KabanKbnPostProcessor(BasePostProcessor):
    """Kaban KBN machine format.

    A line-oriented text format: a header block, then one record per operation
    prefixed by an opcode. Kaban controls machine aluminium and PVC on the same
    line, so the profile material is carried in the header.
    """

    key: ClassVar[str] = "kaban.kbn"
    display_name: ClassVar[str] = "Kaban KBN"
    vendor: ClassVar[str] = "Kaban"
    extension: ClassVar[str] = ".kbn"
    format_version: ClassVar[str] = "1.2"
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

    OPCODE: ClassVar[dict[OperationType, str]] = {
        OperationType.DRILL: "DRL",
        OperationType.RECTANGULAR_POCKET: "PKT",
        OperationType.CIRCULAR_POCKET: "CIR",
        OperationType.SLOT: "SLT",
        OperationType.END_NOTCH: "NCH",
        OperationType.SAW_CUT: "CUT",
    }

    FACE_CODE: ClassVar[dict[Face, str]] = {
        Face.TOP: "T",
        Face.FRONT: "F",
        Face.BACK: "K",
        Face.BOTTOM: "B",
        Face.LEFT: "L",
        Face.RIGHT: "R",
    }

    def _render(self, job: MachiningJob) -> str:
        lines: list[str] = ["$HEADER"]
        lines.append(f"NAME|{job.name}")
        lines.append(f"JOB|{job.job_id}")
        lines.append(f"MACHINE|{job.machine.model}")
        lines.append(f"DATE|{self.timestamp()}")
        lines.append(f"UNITS|MM")
        lines.append("$END")
        lines.append("")

        for piece in job.pieces:
            lines.append("$PART")
            lines.append(f"PID|{piece.piece_id}")
            lines.append(f"PRF|{piece.profile_id}")
            lines.append(f"LEN|{_num(piece.length, 1)}")
            lines.append(f"ANG|{_num(piece.angle_left, 1)}|{_num(piece.angle_right, 1)}")
            lines.append(f"QTY|{piece.quantity}")
            if piece.mark:
                lines.append(f"MRK|{piece.mark}")
            if piece.barcode:
                lines.append(f"BCD|{piece.barcode}")

            if piece.clamp_plan is not None:
                for clamp in piece.clamp_plan.active_clamps():
                    lines.append(f"CLP|{clamp.id}|{_num(clamp.position, 1)}")

            for op in piece.operations.sorted_for_machining():
                lines.append(self._operation_record(job, op))

            lines.append("$END")
            lines.append("")

        return "\n".join(lines)

    def _operation_record(self, job: MachiningJob, op: Operation) -> str:
        opcode = self.OPCODE[op.op_type]
        parts: list[str] = [opcode, self.FACE_CODE[op.face]]

        if isinstance(op, Drill):
            parts += [_num(op.x), _num(op.y), _num(op.diameter), _num(op.depth)]
        elif isinstance(op, RectangularPocket):
            parts += [
                _num(op.x),
                _num(op.y),
                _num(op.length),
                _num(op.width),
                _num(op.depth),
            ]
        elif isinstance(op, CircularPocket):
            parts += [_num(op.x), _num(op.y), _num(op.diameter), _num(op.depth)]
        elif isinstance(op, Slot):
            parts += [
                _num(op.x1),
                _num(op.y1),
                _num(op.x2),
                _num(op.y2),
                _num(op.width),
                _num(op.depth),
            ]
        elif isinstance(op, EndNotch):
            lo, _ = op.extent_x()
            parts += [_num(lo), _num(op.length), _num(op.depth)]
        elif isinstance(op, SawCut):
            parts += [_num(op.position), _num(op.angle)]

        parts.append(str(op.tool_number if op.tool_number is not None else 0))
        parts.append(str(self.speed_for(job, op)))
        parts.append(_num(self.feed_for(job, op), 1))
        return "|".join(parts)


@register_driver
class EmmegiCamProPostProcessor(BasePostProcessor):
    """Emmegi CamPro / FpPro XML program.

    Emmegisoft's model nests ``<bar>`` elements holding ``<work>`` records, with
    the working plane given as a numeric ``face`` attribute and coordinates in
    the bar frame.
    """

    key: ClassVar[str] = "emmegi.campro"
    display_name: ClassVar[str] = "Emmegi CamPro / FpPro"
    vendor: ClassVar[str] = "Emmegi"
    extension: ClassVar[str] = ".xml"
    format_version: ClassVar[str] = "2.4"
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
        }
    )

    FACE_INDEX: ClassVar[dict[Face, int]] = {
        Face.TOP: 0,
        Face.FRONT: 1,
        Face.BACK: 2,
        Face.BOTTOM: 3,
        Face.LEFT: 4,
        Face.RIGHT: 5,
    }

    WORK_CODE: ClassVar[dict[OperationType, str]] = {
        OperationType.DRILL: "FORO",
        OperationType.COUNTERBORE: "SVASATURA",
        OperationType.THREAD: "FILETTO",
        OperationType.RECTANGULAR_POCKET: "TASCA",
        OperationType.CIRCULAR_POCKET: "TASCA_CIRC",
        OperationType.SLOT: "ASOLA",
        OperationType.CONTOUR: "PROFILO",
        OperationType.END_NOTCH: "INTAGLIO",
        OperationType.SAW_CUT: "TAGLIO",
    }

    def _render(self, job: MachiningJob) -> str:
        root = ET.Element("campro", {"version": self.format_version, "generator": "ProfileOS"})
        info = ET.SubElement(root, "info")
        ET.SubElement(info, "job").text = job.job_id
        ET.SubElement(info, "description").text = job.name
        ET.SubElement(info, "machine").text = job.machine.model
        ET.SubElement(info, "datetime").text = self.timestamp()
        ET.SubElement(info, "unit").text = "mm"

        for piece in job.pieces:
            bar = ET.SubElement(
                root,
                "bar",
                {
                    "id": piece.piece_id,
                    "code": piece.profile_id,
                    "length": _num(piece.length, 2),
                    "qty": str(piece.quantity),
                    "angle1": _num(piece.angle_left, 2),
                    "angle2": _num(piece.angle_right, 2),
                },
            )
            if piece.mark:
                bar.set("label", piece.mark)

            if piece.clamp_plan is not None:
                grips = ET.SubElement(bar, "grips")
                for clamp in piece.clamp_plan.active_clamps():
                    ET.SubElement(
                        grips, "grip", {"id": clamp.id, "pos": _num(clamp.position, 2)}
                    )

            for op in piece.operations.sorted_for_machining():
                self._write_work(bar, job, op)

        _indent(root)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode")
            + "\n"
        )

    def _write_work(self, parent: ET.Element, job: MachiningJob, op: Operation) -> None:
        attributes = {
            "id": op.op_id,
            "type": self.WORK_CODE[op.op_type],
            "face": str(self.FACE_INDEX[op.face]),
            "tool": str(op.tool_number) if op.tool_number is not None else "",
            "rpm": str(self.speed_for(job, op)),
            "feed": _num(self.feed_for(job, op), 1),
        }

        if isinstance(op, Drill):
            attributes.update(
                {"x": _num(op.x), "y": _num(op.y), "d": _num(op.diameter), "z": _num(op.depth)}
            )
        elif isinstance(op, Counterbore):
            attributes.update(
                {
                    "x": _num(op.x),
                    "y": _num(op.y),
                    "d": _num(op.bore_diameter),
                    "z": _num(op.bore_depth),
                    "d1": _num(op.pilot_diameter),
                    "z1": _num(op.pilot_depth),
                }
            )
        elif isinstance(op, Thread):
            attributes.update(
                {
                    "x": _num(op.x),
                    "y": _num(op.y),
                    "d": _num(op.nominal_diameter),
                    "p": _num(op.pitch),
                    "z": _num(op.depth),
                }
            )
        elif isinstance(op, RectangularPocket):
            attributes.update(
                {
                    "x": _num(op.x),
                    "y": _num(op.y),
                    "l": _num(op.length),
                    "h": _num(op.width),
                    "z": _num(op.depth),
                    "r": _num(op.corner_radius),
                }
            )
        elif isinstance(op, CircularPocket):
            attributes.update(
                {"x": _num(op.x), "y": _num(op.y), "d": _num(op.diameter), "z": _num(op.depth)}
            )
        elif isinstance(op, Slot):
            attributes.update(
                {
                    "x": _num(op.x1),
                    "y": _num(op.y1),
                    "x2": _num(op.x2),
                    "y2": _num(op.y2),
                    "w": _num(op.width),
                    "z": _num(op.depth),
                }
            )
        elif isinstance(op, EndNotch):
            lo, _ = op.extent_x()
            attributes.update(
                {"x": _num(lo), "l": _num(op.length), "z": _num(op.depth)}
            )
        elif isinstance(op, SawCut):
            attributes.update({"x": _num(op.position), "a": _num(op.angle)})

        element = ET.SubElement(parent, "work", attributes)
        if isinstance(op, Contour):
            element.set("z", _num(op.depth))
            element.set("closed", "1" if op.closed else "0")
            element.set("comp", op.compensation.value)
            for x, y in op.points:
                ET.SubElement(element, "p", {"x": _num(x), "y": _num(y)})


@register_driver
class FomCamPostProcessor(BasePostProcessor):
    """FOM Industrie CAM program.

    A compact CSV-style record format: one ``PART`` line per piece followed by
    one line per operation, comma separated with a fixed column order per
    opcode.
    """

    key: ClassVar[str] = "fom.cam"
    display_name: ClassVar[str] = "FOM Industrie CAM"
    vendor: ClassVar[str] = "FOM Industrie"
    extension: ClassVar[str] = ".fom"
    format_version: ClassVar[str] = "1.1"
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

    OPCODE: ClassVar[dict[OperationType, str]] = {
        OperationType.DRILL: "H",
        OperationType.RECTANGULAR_POCKET: "P",
        OperationType.CIRCULAR_POCKET: "C",
        OperationType.SLOT: "S",
        OperationType.END_NOTCH: "N",
        OperationType.SAW_CUT: "X",
    }

    FACE_CODE: ClassVar[dict[Face, int]] = {
        Face.TOP: 1,
        Face.FRONT: 2,
        Face.BACK: 3,
        Face.BOTTOM: 4,
        Face.LEFT: 5,
        Face.RIGHT: 6,
    }

    def _render(self, job: MachiningJob) -> str:
        lines: list[str] = []
        for comment in self.header_comment_lines(job):
            lines.append(f"# {comment}")
        lines.append("# format: opcode,face,params...,tool,rpm,feed")
        lines.append(f"JOB,{job.job_id},{job.name},{job.machine.model},{self.timestamp()}")

        for piece in job.pieces:
            lines.append(
                "PART,"
                + ",".join(
                    [
                        piece.piece_id,
                        piece.profile_id,
                        _num(piece.length, 2),
                        _num(piece.angle_left, 2),
                        _num(piece.angle_right, 2),
                        str(piece.quantity),
                        piece.mark or "",
                    ]
                )
            )
            for op in piece.operations.sorted_for_machining():
                lines.append(self._operation_row(job, op))
        lines.append("END")
        return "\n".join(lines) + "\n"

    def _operation_row(self, job: MachiningJob, op: Operation) -> str:
        row: list[str] = [self.OPCODE[op.op_type], str(self.FACE_CODE[op.face])]

        if isinstance(op, Drill):
            row += [_num(op.x), _num(op.y), _num(op.diameter), _num(op.depth)]
        elif isinstance(op, RectangularPocket):
            row += [_num(op.x), _num(op.y), _num(op.length), _num(op.width), _num(op.depth)]
        elif isinstance(op, CircularPocket):
            row += [_num(op.x), _num(op.y), _num(op.diameter), _num(op.depth)]
        elif isinstance(op, Slot):
            row += [
                _num(op.x1),
                _num(op.y1),
                _num(op.x2),
                _num(op.y2),
                _num(op.width),
                _num(op.depth),
            ]
        elif isinstance(op, EndNotch):
            lo, _ = op.extent_x()
            row += [_num(lo), _num(op.length), _num(op.depth)]
        elif isinstance(op, SawCut):
            row += [_num(op.position), _num(op.angle)]

        row += [
            str(op.tool_number if op.tool_number is not None else 0),
            str(self.speed_for(job, op)),
            _num(self.feed_for(job, op), 1),
        ]
        return ",".join(row)


__all__ = [
    "SchuecoMcoPostProcessor",
    "KabanKbnPostProcessor",
    "EmmegiCamProPostProcessor",
    "FomCamPostProcessor",
]
