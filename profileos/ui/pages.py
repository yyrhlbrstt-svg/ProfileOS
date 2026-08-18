"""Application pages.

One page per stage of the workflow, in the order work actually moves:
profile -> element -> nesting -> machining -> quotation -> shop floor.

Pages share a :class:`~profileos.ui.session.Session`, so a profile analysed on
the first page is available to the last without any page importing another.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .theme import METRICS, Palette
from .views import ClampView, ElevationView, NestingView, SectionView
from .widgets import Badge, Card, DataTable, FieldGrid, PageHeader, StatRow, page_layout

_log = get_logger("ui.pages")


class Page(QWidget):
    """Base page: a header, a body, and access to the shared session."""

    title = "Page"
    subtitle = ""

    def __init__(self, session: Any, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.colours = palette

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = PageHeader(self.title, self.subtitle)
        outer.addWidget(self.header)

        body = QWidget()
        self.body = page_layout(body)
        outer.addWidget(body, 1)

        self.build()

    def build(self) -> None:
        """Override to populate :attr:`body`."""

    def refresh(self) -> None:
        """Called when the page becomes visible."""

    # -- error handling ------------------------------------------------------ #
    def report(self, exc: Exception, context: str = "") -> None:
        """Show an engine error to the user rather than losing it to a log."""
        _log.error("%s: %s", context or "Operation failed", exc, exc_info=True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(context or "Operation failed")
        box.setText(str(exc))
        if not isinstance(exc, ProfileOSError):
            box.setDetailedText(traceback.format_exc())
        box.exec()

    def status(self, message: str) -> None:
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(message, 6000)


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #

class ProfilePage(Page):
    """Import a DXF cross-section and analyse it."""

    title = "Profile"
    subtitle = "Import a cross-section and compute its structural properties"

    def build(self) -> None:
        self.open_button = QPushButton("Open DXF...")
        self.open_button.setObjectName("Primary")
        self.open_button.clicked.connect(self.open_dxf)
        self.header.add_action(self.open_button)

        self.sample_button = QPushButton("Load sample")
        self.sample_button.clicked.connect(self.load_sample)
        self.header.add_action(self.sample_button)

        self.stats = StatRow(
            [("area", "Area mm²"), ("ix", "Iₓ mm⁴"), ("iy", "I_y mm⁴"),
             ("j", "J mm⁴"), ("mass", "Mass kg/m")]
        )
        self.body.addWidget(self.stats)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.view = SectionView(self.colours)
        splitter.addWidget(self.view)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(METRICS.space(3))

        properties_card = Card("Section properties")
        self.properties = DataTable(["Symbol", "Value", "Unit"])
        properties_card.add(self.properties, 1)
        side_layout.addWidget(properties_card, 1)

        check_card = Card("Wind load check")
        fields = FieldGrid()
        self.span = QDoubleSpinBox(); self.span.setRange(100, 20000); self.span.setValue(3000); self.span.setSuffix(" mm")
        self.pressure = QDoubleSpinBox(); self.pressure.setRange(0.1, 10.0); self.pressure.setValue(1.2); self.pressure.setSingleStep(0.1); self.pressure.setSuffix(" kN/m²")
        self.tributary = QDoubleSpinBox(); self.tributary.setRange(100, 10000); self.tributary.setValue(1500); self.tributary.setSuffix(" mm")
        fields.add("Span", self.span)
        fields.add("Wind pressure", self.pressure)
        fields.add("Tributary width", self.tributary)
        check_card.add(fields)

        run = QPushButton("Verify")
        run.clicked.connect(self.run_check)
        check_card.add(run)
        self.checks = DataTable(["Check", "Demand", "Capacity", "Use"])
        check_card.add(self.checks, 1)
        self.max_span = QLabel("—")
        self.max_span.setObjectName("StatLabel")
        check_card.add(self.max_span)
        side_layout.addWidget(check_card, 1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.body.addWidget(splitter, 1)

    def load_sample(self) -> None:
        from ..core.config import PROJECT_ROOT

        sample = PROJECT_ROOT / "data" / "samples" / "mullion_mb70.dxf"
        if not sample.is_file():
            self.report(ProfileOSError("Sample drawings not generated yet"), "No sample")
            return
        self.load(sample)

    def open_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open profile DXF", "", "DXF (*.dxf);;All files (*)")
        if path:
            self.load(Path(path))

    def load(self, path: Path) -> None:
        from ..structural import analyse_dxf

        try:
            properties, section = analyse_dxf(str(path), profile_id=path.stem)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.report(exc, "Could not analyse the DXF")
            return

        self.session.set_section(properties, section, path)
        self.view.set_section(section.polygon, properties, section.validation)
        self.properties.set_rows(properties.summary_rows(), numeric_columns=(1,))

        def fmt(value: float | None, digits: int = 0) -> str:
            return "—" if value is None else f"{value:,.{digits}f}"

        self.stats.update_many({
            "area": (fmt(properties.area, 1), path.stem),
            "ix": (fmt(properties.ixx), ""),
            "iy": (fmt(properties.iyy), ""),
            "j": (fmt(properties.j), properties.torsion_method or ""),
            "mass": (fmt(properties.mass_per_metre, 3), properties.material_id or ""),
        })
        self.header.set_subtitle(
            f"{path.name} — {section.width:.0f} × {section.height:.0f} mm, "
            f"{section.topology.chamber_count} chamber(s)"
        )
        self.status(f"Analysed {path.name}")
        self.run_check()

    def run_check(self) -> None:
        if self.session.section_properties is None:
            return
        from ..models.materials import get_material
        from ..structural import LoadCase, check_member, maximum_span, wind_line_load

        properties = self.session.section_properties
        material = get_material(properties.material_id)
        load = LoadCase(
            lateral_line_load=wind_line_load(self.pressure.value(), self.tributary.value())
        )
        try:
            check = check_member(properties, material, span=self.span.value(), load=load)
            limit = maximum_span(
                properties, material,
                pressure_kn_m2=self.pressure.value(),
                tributary_width_mm=self.tributary.value(),
            )
        except ProfileOSError as exc:
            self.report(exc, "Verification failed")
            return

        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, entry in enumerate(check.results):
            rows.append([
                entry.name, f"{entry.demand:.4g}", f"{entry.capacity:.4g}",
                f"{entry.utilisation * 100:.1f}%",
            ])
            colours[(index, 3)] = self.colours.success if entry.passes else self.colours.danger
        self.checks.set_rows(rows, numeric_columns=(1, 2, 3), colours=colours)

        verdict = "passes" if check.passes else "FAILS"
        self.max_span.setText(
            f"Member {verdict} at {self.span.value():.0f} mm. "
            f"Maximum span for these loads: {limit:,.0f} mm."
        )


# --------------------------------------------------------------------------- #
# Element
# --------------------------------------------------------------------------- #

class ElementPage(Page):
    """Design a window, door or curtain-wall element."""

    title = "Element"
    subtitle = "Lay out an opening and derive its cut list, glass and hardware"

    def build(self) -> None:
        build_button = QPushButton("Build element")
        build_button.setObjectName("Primary")
        build_button.clicked.connect(self.build_element)
        self.header.add_action(build_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        form_card = Card("Opening")
        fields = FieldGrid()
        self.name = QComboBox(); self.name.setEditable(True); self.name.addItems(["W-04", "D-01", "CW-01"])
        self.width = QDoubleSpinBox(); self.width.setRange(200, 12000); self.width.setValue(2400); self.width.setSuffix(" mm")
        self.height = QDoubleSpinBox(); self.height.setRange(200, 6000); self.height.setValue(1800); self.height.setSuffix(" mm")
        self.quantity = QSpinBox(); self.quantity.setRange(1, 999); self.quantity.setValue(4)
        self.kind = QComboBox(); self.kind.addItems(["window", "door", "curtain_wall", "shopfront", "sliding_unit"])
        self.columns = QSpinBox(); self.columns.setRange(1, 12); self.columns.setValue(3)
        self.rows = QSpinBox(); self.rows.setRange(1, 12); self.rows.setValue(1)
        self.sash_column = QSpinBox(); self.sash_column.setRange(0, 11); self.sash_column.setValue(1)
        self.sash_row = QSpinBox(); self.sash_row.setRange(0, 11); self.sash_row.setValue(0)
        self.sash_type = QComboBox()
        self.sash_type.addItems(["fixed", "casement", "tilt_turn", "top_hung", "sliding", "door"])
        self.sash_type.setCurrentText("tilt_turn")
        self.sill = QDoubleSpinBox(); self.sill.setRange(0, 100000); self.sill.setValue(900); self.sill.setSuffix(" mm")
        self.glass = QComboBox()

        from ..glazing import STANDARD_BUILDUPS

        for key, unit in STANDARD_BUILDUPS.items():
            self.glass.addItem(f"{unit.name}  (U {unit.u_value():.2f})", key)
        self.glass.setCurrentIndex(min(1, self.glass.count() - 1))

        for label, widget in [
            ("Name", self.name), ("Type", self.kind), ("Width", self.width),
            ("Height", self.height), ("Quantity", self.quantity), ("Columns", self.columns),
            ("Rows", self.rows), ("Sash column", self.sash_column), ("Sash row", self.sash_row),
            ("Sash type", self.sash_type), ("Sill height", self.sill), ("Glass", self.glass),
        ]:
            fields.add(label, widget)
        form_card.add(fields)
        form_card.body.addStretch(1)
        form_card.setMaximumWidth(METRICS.panel_width)
        splitter.addWidget(form_card)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(METRICS.space(3))

        self.stats = StatRow([
            ("pieces", "Pieces"), ("glass", "Glass m²"), ("mass", "Glass kg"),
            ("gasket", "Gasket m"), ("hardware", "Hardware"),
        ])
        right_layout.addWidget(self.stats)

        inner = QSplitter(Qt.Orientation.Vertical)
        self.view = ElevationView(self.colours)
        inner.addWidget(self.view)

        tabs = QTabWidget()
        self.cuts = DataTable(["Role", "Profile", "Length", "Qty", "Angles"])
        self.panes = DataTable(["Mark", "Size", "Specification", "Area m²", "Mass kg", "U", "Safety"])
        self.hardware = DataTable(["Code", "Item", "Qty", "Unit"])
        self.warnings = QPlainTextEdit(); self.warnings.setReadOnly(True)
        tabs.addTab(self.cuts, "Cut list")
        tabs.addTab(self.panes, "Glass")
        tabs.addTab(self.hardware, "Hardware")
        tabs.addTab(self.warnings, "Warnings")
        inner.addWidget(tabs)
        inner.setStretchFactor(0, 3)
        inner.setStretchFactor(1, 2)
        right_layout.addWidget(inner, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

    def build_element(self) -> None:
        from ..elements import Cell, ElementBuilder, ElementKind, Opening, OpeningType, Sash

        try:
            opening = Opening(
                name=self.name.currentText() or "Element",
                kind=ElementKind(self.kind.currentText()),
                width=self.width.value(), height=self.height.value(),
                quantity=self.quantity.value(),
                glass_spec_id=self.glass.currentData(),
            )
            opening.divide_evenly(self.columns.value(), self.rows.value())

            sash_type = OpeningType(self.sash_type.currentText())
            if sash_type is not OpeningType.FIXED:
                column = min(self.sash_column.value(), opening.column_count - 1)
                row = min(self.sash_row.value(), opening.row_count - 1)
                opening.set_cell(Cell(column=column, row=row, sash=Sash(opening_type=sash_type)))

            build = ElementBuilder().build(opening, sill_height=self.sill.value())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Could not build the element")
            return

        self.session.add_build(build)
        self.view.set_build(build)

        summary = build.summary()
        self.stats.update_many({
            "pieces": (str(summary["pieces"]), f"×{opening.quantity}"),
            "glass": (f"{summary['glass_area_m2']:.2f}", f"{summary['glass_panes']} panes"),
            "mass": (f"{summary['glass_mass_kg']:.0f}", ""),
            "gasket": (f"{summary['gasket_m']:.1f}", ""),
            "hardware": (str(summary["hardware_items"]), ""),
        })

        self.cuts.set_rows(
            [[c.role, c.profile_id, f"{c.length:.1f}", c.quantity,
              f"{c.angle_left:g}/{c.angle_right:g}"]
             for c in sorted(build.cuts, key=lambda c: (c.role, -c.length))],
            numeric_columns=(2, 3, 4),
        )

        colours: dict[tuple[int, int], str] = {}
        pane_rows = []
        for index, panel in enumerate(build.glass):
            safety = "ok" if panel.compliant else ("REQUIRED" if panel.safety_required else "—")
            if not panel.compliant:
                colours[(index, 6)] = self.colours.danger
            pane_rows.append([
                panel.mark, f"{panel.width:.0f} × {panel.height:.0f}",
                panel.build_up.describe(), f"{panel.area:.3f}", f"{panel.mass:.1f}",
                f"{panel.build_up.u_value():.2f}", safety,
            ])
        self.panes.set_rows(pane_rows, numeric_columns=(3, 4, 5), colours=colours)

        self.hardware.set_rows(
            [[h.code, h.name, h.quantity, h.unit] for h in build.hardware], numeric_columns=(2,)
        )
        self.warnings.setPlainText(
            "\n".join(f"• {w}" for w in build.warnings) or "No warnings."
        )
        self.header.set_subtitle(
            f"{opening.describe()} — {len(self.session.builds)} element(s) in the project"
        )
        self.status(f"Built {opening.name}")


# --------------------------------------------------------------------------- #
# Nesting
# --------------------------------------------------------------------------- #

class NestingPage(Page):
    """Optimise the project's cut list onto stock bars."""

    title = "Nesting"
    subtitle = "Optimise the cutting list onto stock bars"

    def build(self) -> None:
        run = QPushButton("Optimise")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        self.stats = StatRow([
            ("bars", "Bars"), ("pieces", "Pieces"), ("yield", "Yield"),
            ("waste", "Waste"), ("remnants", "Remnants"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("Parameters")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))
        self.kerf = QDoubleSpinBox(); self.kerf.setRange(0, 20); self.kerf.setValue(3.5); self.kerf.setSuffix(" mm")
        self.stock = QComboBox(); self.stock.setEditable(True); self.stock.addItems(["6000", "6500", "6000,6500", "7000"])
        self.strategy = QComboBox(); self.strategy.addItems(["auto", "milp", "ffd", "bfd"])
        self.profile = QComboBox()
        for label, widget in [("Kerf", self.kerf), ("Stock lengths", self.stock),
                              ("Strategy", self.strategy), ("Profile", self.profile)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Vertical)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.view = NestingView(self.colours)
        scroll.setWidget(self.view)
        splitter.addWidget(scroll)

        self.summary = DataTable(["Profile", "Bars", "Pieces", "Yield", "Waste", "Strategy", "Optimal"])
        splitter.addWidget(self.summary)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

        self.profile.currentTextChanged.connect(self._show_profile)

    def refresh(self) -> None:
        self.header.set_subtitle(
            f"{len(self.session.builds)} element(s) queued"
            if self.session.builds else "Design an element first"
        )

    def run(self) -> None:
        from ..elements import collect_cut_items
        from ..models.orders import Project
        from ..nesting import nest_project

        if not self.session.builds:
            self.report(ProfileOSError("No elements have been designed yet"), "Nothing to nest")
            return

        from ..core.config import get_settings

        settings = get_settings()
        settings.nesting.kerf_mm = self.kerf.value()
        try:
            settings.nesting.stock_lengths_mm = [
                float(v) for v in self.stock.currentText().split(",") if v.strip()
            ]
        except ValueError:
            self.report(ProfileOSError("Stock lengths must be numbers"), "Bad input")
            return

        project = Project(name="Project", items=collect_cut_items(self.session.builds))
        try:
            report = nest_project(project, strategy=self.strategy.currentText())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Nesting failed")
            return

        self.session.set_nesting(project, report)

        remnants = sum(len(r.reusable_remnants(300.0)) for r in report.results.values())
        self.stats.update_many({
            "bars": (str(report.total_bars), f"{report.total_stock_length / 1000:.1f} m"),
            "pieces": (str(sum(r.total_pieces for r in report.results.values())), ""),
            "yield": (f"{report.overall_yield_pct:.2f}%", "target 97.5%"),
            "waste": (f"{100 - report.overall_yield_pct:.2f}%", ""),
            "remnants": (str(remnants), "reusable"),
        })

        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, (profile_id, result) in enumerate(sorted(report.results.items())):
            rows.append([
                profile_id, result.bar_count, result.total_pieces,
                f"{result.yield_pct:.2f}%", f"{result.waste_pct:.2f}%",
                result.strategy, "yes" if result.optimal else "—",
            ])
            colours[(index, 3)] = (
                self.colours.success if result.yield_pct >= 95 else self.colours.warning
            )
        self.summary.set_rows(rows, numeric_columns=(1, 2, 3, 4), colours=colours)

        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItems(sorted(report.results))
        self.profile.blockSignals(False)
        if report.results:
            self._show_profile(self.profile.currentText())

        self.status(
            f"{report.total_bars} bars at {report.overall_yield_pct:.2f}% yield "
            f"in {report.solve_time_s:.2f} s"
        )

    def _show_profile(self, profile_id: str) -> None:
        report = self.session.nesting_report
        if report is None or profile_id not in report.results:
            return
        self.view.set_result(report.results[profile_id])


# --------------------------------------------------------------------------- #
# Machining
# --------------------------------------------------------------------------- #

class MachiningPage(Page):
    """Plan clamps and post machine code."""

    title = "Machining"
    subtitle = "Plan the setup and post native machine code"

    def build(self) -> None:
        post = QPushButton("Post program")
        post.setObjectName("Primary")
        post.clicked.connect(self.post)
        self.header.add_action(post)

        save = QPushButton("Save to disk...")
        save.clicked.connect(self.save)
        self.header.add_action(save)

        controls = Card("Job")
        row = QHBoxLayout(); row.setSpacing(METRICS.space(4))
        self.driver = QComboBox()

        from ..cnc import available_drivers

        for entry in available_drivers():
            self.driver.addItem(entry.get("display_name") or entry["key"], entry["key"])
        # Default to a machining centre rather than whichever driver sorts
        # first: this page is about machining, and a saw driver emits only a
        # cut list, which looks like the post has silently dropped the work.
        default = self.driver.findData("elumatec.ncx")
        if default >= 0:
            self.driver.setCurrentIndex(default)
        self.length = QDoubleSpinBox(); self.length.setRange(200, 8000); self.length.setValue(2450); self.length.setSuffix(" mm")
        self.angle_left = QDoubleSpinBox(); self.angle_left.setRange(15, 165); self.angle_left.setValue(45); self.angle_left.setSuffix("°")
        self.angle_right = QDoubleSpinBox(); self.angle_right.setRange(15, 165); self.angle_right.setValue(45); self.angle_right.setSuffix("°")
        self.clearance = QDoubleSpinBox(); self.clearance.setRange(0, 100); self.clearance.setValue(15); self.clearance.setSuffix(" mm")
        for label, widget in [("Driver", self.driver), ("Bar length", self.length),
                              ("Left angle", self.angle_left), ("Right angle", self.angle_right),
                              ("Clamp clearance", self.clearance)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        setup_card = Card("Setup — operations above the bar, clamps below")
        self.clamp_view = ClampView(self.colours)
        setup_card.add(self.clamp_view, 1)
        self.clamp_status = QLabel("—")
        self.clamp_status.setObjectName("StatLabel")
        setup_card.add(self.clamp_status)
        self.body.addWidget(setup_card, 1)

        code_card = Card("Machine code")
        self.code = QPlainTextEdit(); self.code.setReadOnly(True)
        self.code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        code_card.add(self.code, 1)
        self.body.addWidget(code_card, 1)

        self._results: list[Any] = []

    def _build_job(self) -> Any:
        from ..cnc import MachiningJob, PieceProgram, expand_macros
        from ..models.machines import Clamp, MachineDefinition, Tool, ToolLibrary, ToolType
        from ..models.profile import Face, MachiningMacro

        tools = ToolLibrary(id="ui", name="Magazine", tools=[
            Tool(number=3, name="D5 drill", tool_type=ToolType.DRILL, diameter=5.0, flute_length=40.0),
            Tool(number=5, name="D8 end mill", tool_type=ToolType.END_MILL, diameter=8.0, flute_length=35.0),
            Tool(number=7, name="D6 slot mill", tool_type=ToolType.SLOT_MILL, diameter=6.0, flute_length=30.0),
            Tool(number=9, name="D12 end mill", tool_type=ToolType.END_MILL, diameter=12.0, flute_length=50.0),
        ])
        machine = MachineDefinition(
            id="ui-machine", name="Machining centre", vendor="Generic", model="5-axis",
            post_processor=self.driver.currentData(), axis_count=5,
            machinable_faces=set(Face), clamp_clearance=self.clearance.value(),
            clamps=[Clamp(id=f"C{i}", position=p, width=120.0)
                    for i, p in enumerate([400.0, 1200.0, 2000.0], 1)],
        )
        length = self.length.value()
        macros = [
            MachiningMacro(macro_id="lock.euro_cylinder", face=Face.FRONT,
                           position_x=length * 0.5, position_y=30.0, depth=25.0, tool_id=5),
            MachiningMacro(macro_id="hinge.standard", face=Face.FRONT,
                           position_x=length * 0.16, position_y=30.0, depth=12.0, tool_id=9),
            MachiningMacro(macro_id="drainage.slots", face=Face.BOTTOM, position_x=length * 0.12,
                           position_y=0.0, depth=8.0, tool_id=7,
                           parameters={"count": 2, "spacing": length * 0.35}),
            MachiningMacro(macro_id="notch.akm", face=Face.TOP, position_x=0.0, position_y=0.0,
                           depth=18.0, tool_id=9, from_right_end=True, parameters={"length": 25.0}),
        ]
        piece = PieceProgram(
            piece_id="PC-101", profile_id="MB70-MULLION", length=length,
            angle_left=self.angle_left.value(), angle_right=self.angle_right.value(),
            operations=expand_macros(macros, bar_length=length), mark="mullion",
        )
        return MachiningJob(machine=machine, name="Project", pieces=[piece], tool_library=tools)

    def post(self) -> None:
        from ..cnc import detect_collisions, get_driver

        try:
            job = self._build_job()
            piece = job.pieces[0]
            before = detect_collisions(
                list(piece.operations), job.machine.active_clamps(),
                clearance=self.clearance.value(),
            )
            job.plan_all_clamps()
            plan = piece.clamp_plan
            self.clamp_view.set_piece(piece, plan.unresolved)
            self.clamp_status.setText(
                f"{len(before)} interference(s) detected before planning. {plan.summary()}"
                + ("" if plan.ok else "  UNRESOLVED — do not run this program.")
            )

            results = get_driver(self.driver.currentData()).post(job)
        except Exception as exc:  # noqa: BLE001
            self.code.setPlainText("")
            self.report(exc, "Posting failed")
            return

        self._results = results
        self.session.set_machining(job, results)
        preview = results[0]
        self.code.setPlainText(preview.content[:40000])
        self.header.set_subtitle(
            f"{preview.filename} — {preview.size:,} bytes, {len(job.all_operations())} operations"
        )
        self.status(f"Posted {len(results)} file(s) with {self.driver.currentData()}")

    def save(self) -> None:
        if not self._results:
            self.report(ProfileOSError("Post a program first"), "Nothing to save")
            return
        directory = QFileDialog.getExistingDirectory(self, "Save machine code to")
        if not directory:
            return
        for result in self._results:
            result.write(directory)
        self.status(f"Wrote {len(self._results)} file(s) to {directory}")


# --------------------------------------------------------------------------- #
# Quotation
# --------------------------------------------------------------------------- #

class QuotePage(Page):
    """Cost the project and produce a quotation."""

    title = "Quotation"
    subtitle = "Price the bill of materials and produce a customer quotation"

    def build(self) -> None:
        run = QPushButton("Calculate")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        self.stats = StatRow([
            ("material", "Materials"), ("labour", "Labour"), ("cost", "Total cost"),
            ("price", "Net price"), ("rate", "Per m²"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("Commercial parameters")
        row = QHBoxLayout(); row.setSpacing(METRICS.space(4))
        self.margin = QDoubleSpinBox(); self.margin.setRange(0, 90); self.margin.setValue(26); self.margin.setSuffix(" %")
        self.overhead = QDoubleSpinBox(); self.overhead.setRange(0, 90); self.overhead.setValue(12); self.overhead.setSuffix(" %")
        self.tax = QDoubleSpinBox(); self.tax.setRange(0, 50); self.tax.setValue(17); self.tax.setSuffix(" %")
        self.rate = QDoubleSpinBox(); self.rate.setRange(1, 500); self.rate.setValue(52); self.rate.setSuffix(" /h")
        for label, widget in [("Margin", self.margin), ("Overhead", self.overhead),
                              ("Tax", self.tax), ("Labour rate", self.rate)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        bom_card = Card("Bill of materials")
        self.bom = DataTable(["Category", "Code", "Description", "Qty", "Unit", "Total"])
        bom_card.add(self.bom, 1)
        splitter.addWidget(bom_card)

        quote_card = Card("Cost to price")
        self.waterfall = DataTable(["Item", "Amount"])
        quote_card.add(self.waterfall, 1)
        self.notes = QPlainTextEdit(); self.notes.setReadOnly(True); self.notes.setMaximumHeight(120)
        quote_card.add(self.notes)
        splitter.addWidget(quote_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.body.addWidget(splitter, 1)

    def run(self) -> None:
        from ..quoting import LabourRates, PricingPolicy, build_bom, build_quotation

        if not self.session.builds:
            self.report(ProfileOSError("No elements have been designed yet"), "Nothing to price")
            return

        try:
            bom = build_bom(
                self.session.builds, project_name="Project",
                nesting=self.session.nesting_report, currency="EUR",
            )
            quote = build_quotation(
                self.session.builds, bom, project_name="Project",
                policy=PricingPolicy(
                    margin_pct=self.margin.value(), overhead_pct=self.overhead.value(),
                    tax_pct=self.tax.value(), currency="EUR",
                ),
                labour=LabourRates(hourly_rate=self.rate.value(), currency="EUR"),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Costing failed")
            return

        self.session.set_quote(bom, quote)

        self.bom.set_rows(
            [[line.category.value, line.code, line.description,
              f"{line.quantity:,.3f}", line.unit.value,
              f"{line.total_price:,.2f}" if line.total_price is not None else "—"]
             for line in bom.sorted_lines()],
            numeric_columns=(3, 5),
        )
        self.waterfall.set_rows(
            [[label, f"{value:,.2f}"] for label, value in quote.breakdown()],
            numeric_columns=(1,),
        )
        area = quote.metadata.get("total_area_m2") or 0.0
        self.stats.update_many({
            "material": (f"{quote.material_cost:,.0f}", "EUR"),
            "labour": (f"{quote.labour_cost:,.0f}", f"{sum(quote.labour_hours.values()):.1f} h"),
            "cost": (f"{quote.total_cost:,.0f}", "EUR"),
            "price": (f"{quote.net_price:,.0f}", f"gross {quote.gross_price:,.0f}"),
            "rate": (f"{quote.metadata.get('price_per_m2') or 0:,.0f}", f"{area:.1f} m²"),
        })
        self.notes.setPlainText("\n".join(f"• {w}" for w in quote.warnings) or "No warnings.")
        self.header.set_subtitle(
            f"{quote.quote_id} — valid until {quote.valid_until.isoformat()}"
        )
        self.status(f"Quoted {quote.net_price:,.2f} EUR net")


# --------------------------------------------------------------------------- #
# Shop floor
# --------------------------------------------------------------------------- #

class ShopFloorPage(Page):
    """Release the project to production and track it."""

    title = "Shop floor"
    subtitle = "Release the work order and track production"

    def build(self) -> None:
        release = QPushButton("Release work order")
        release.setObjectName("Primary")
        release.clicked.connect(self.release)
        self.header.add_action(release)

        card = QPushButton("Export job card...")
        card.clicked.connect(self.export_card)
        self.header.add_action(card)

        self.stats = StatRow([
            ("items", "Items"), ("progress", "Progress"), ("stage", "Bottleneck"),
            ("rework", "Rework"), ("scrap", "Scrapped"),
        ])
        self.body.addWidget(self.stats)

        scan_card = Card("Record a scan")
        row = QHBoxLayout(); row.setSpacing(METRICS.space(3))
        self.item = QComboBox()
        self.stage = QComboBox()

        from ..mes import Stage

        self.stage.addItems([s.value for s in Stage if s.value != "planned"])
        self.operator = QComboBox(); self.operator.setEditable(True)
        self.operator.addItems(["Dana", "Yossi", "Maya"])
        scan = QPushButton("Scan")
        scan.clicked.connect(self.scan)
        for label, widget in [("Item", self.item), ("Stage", self.stage), ("Operator", self.operator)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addWidget(scan)
        row.addStretch(1)
        scan_card.add_layout(row)
        self.scan_result = QLabel("—")
        self.scan_result.setObjectName("StatLabel")
        scan_card.add(self.scan_result)
        self.body.addWidget(scan_card)

        items_card = Card("Production items")
        self.items = DataTable(["ID", "Kind", "Description", "Stage", "Progress"])
        items_card.add(self.items, 1)
        self.body.addWidget(items_card, 1)

    def release(self) -> None:
        from ..mes import work_order_from_builds

        if not self.session.builds:
            self.report(ProfileOSError("No elements have been designed yet"), "Nothing to release")
            return
        order = work_order_from_builds(self.session.builds, project_id="PRJ", name="Project")
        self.session.set_work_order(order)

        self.item.clear()
        for entry in order.items:
            self.item.addItem(f"{entry.item_id} — {entry.description[:40]}", entry.item_id)
        self._refresh_items()
        self.status(f"Released {len(order)} items as {order.work_order_id}")

    def scan(self) -> None:
        from ..mes import Stage

        order = self.session.work_order
        if order is None:
            self.report(ProfileOSError("Release a work order first"), "Nothing to scan")
            return

        item_id = self.item.currentData()
        ok, message = order.scan(
            item_id or "", Stage(self.stage.currentText()),
            operator=self.operator.currentText(), station="UI",
        )
        colour = self.colours.success if ok else self.colours.danger
        self.scan_result.setText(message)
        self.scan_result.setStyleSheet(f"color: {colour};")
        self._refresh_items()

    def _refresh_items(self) -> None:
        order = self.session.work_order
        if order is None:
            return
        self.items.set_rows(
            [[i.item_id, i.kind.value, i.description, i.stage.value,
              f"{i.progress() * 100:.0f}%"] for i in order.items],
            numeric_columns=(4,),
        )
        summary = order.summary()
        bottleneck = order.bottleneck()
        self.stats.update_many({
            "items": (str(summary["items"]), order.work_order_id),
            "progress": (f"{summary['progress_pct']:.0f}%", ""),
            "stage": (bottleneck[0].value if bottleneck else "—",
                      f"{bottleneck[1]} items" if bottleneck else ""),
            "rework": (str(summary["rework"]), ""),
            "scrap": (str(summary["scrapped"]), ""),
        })

    def export_card(self) -> None:
        from ..mes import write_job_card

        order = self.session.work_order
        if order is None:
            self.report(ProfileOSError("Release a work order first"), "Nothing to export")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save job card", "job-card.html", "HTML (*.html)")
        if not path:
            return
        write_job_card(order, path, self.session.builds)
        self.status(f"Wrote {path}")


PAGES: list[type[Page]] = [
    ProfilePage, ElementPage, NestingPage, MachiningPage, QuotePage, ShopFloorPage,
]

__all__ = [
    "Page", "ProfilePage", "ElementPage", "NestingPage",
    "MachiningPage", "QuotePage", "ShopFloorPage", "PAGES",
]
