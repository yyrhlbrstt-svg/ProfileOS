"""Application pages.

One page per stage of the workflow, in the order work actually moves:
profile -> element -> nesting -> machining -> quotation -> shop floor.

Pages share a :class:`~profileos.ui.session.Session`, so a profile analysed on
the first page is available to the last without any page importing another.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from .views import ClampView, ElevationView, NestingView, SectionView, SheetView
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

        properties_card = Card("Section")
        tabs = QTabWidget()
        self.properties = DataTable(["Symbol", "Value", "Unit"])
        tabs.addTab(self.properties, "Properties")

        features_panel = QWidget()
        features_layout = QVBoxLayout(features_panel)
        features_layout.setContentsMargins(0, 0, 0, 0)
        features_layout.setSpacing(METRICS.space(2))
        self.feature_summary = DataTable(["", ""])
        self.feature_summary.horizontalHeader().setVisible(False)
        self.feature_summary.setMaximumHeight(METRICS.row_height * 6 + 4)
        features_layout.addWidget(self.feature_summary)
        self.features = DataTable(["Feature", "Mouth", "Depth", "Undercut"])
        features_layout.addWidget(self.features, 1)
        self.feature_notes = QLabel("")
        self.feature_notes.setWordWrap(True)
        self.feature_notes.setObjectName("Muted")
        features_layout.addWidget(self.feature_notes)
        tabs.addTab(features_panel, "Features")

        properties_card.add(tabs, 1)
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
        """A DWG is accepted too; it is converted on the way in."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open profile drawing",
            "",
            "Drawings (*.dxf *.dwg);;DXF (*.dxf);;DWG (*.dwg);;All files (*)",
        )
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
        self.show_features(section, properties.material_id)
        self.status(f"Analysed {path.name}")
        self.run_check()

    def show_features(self, section: Any, material: str | None) -> None:
        """Fill the Features tab with what was read off the drawing.

        A failure here must not cost the operator the structural analysis they
        actually asked for, so it is reported in the panel rather than raised.
        """
        from ..geometry.features import features_for_section

        try:
            report = features_for_section(section, material=material)
        except Exception as exc:  # noqa: BLE001 - the section is still usable
            _log.warning("Feature recognition failed: %s", exc)
            self.feature_summary.set_rows([["Feature recognition", "unavailable"]])
            self.features.set_rows([])
            self.feature_notes.setText(str(exc))
            return

        self.session.set_features(report)
        self.feature_summary.set_rows(
            [[label, value] for label, value in report.summary_rows()]
        )
        rows = []
        colours: dict[tuple[int, int], str] = {}
        for index, feature in enumerate(report.features):
            pocket = feature.pocket
            rows.append([
                f"{feature.kind.hebrew} · {feature.kind.value}",
                f"{pocket.mouth:.2f}",
                f"{pocket.depth:.2f}",
                f"{pocket.undercut:.2f}" if pocket.undercut > 0.4 else "—",
            ])
            if feature.kind.value != "pocket":
                colours[(index, 0)] = self.colours.accent
        self.features.set_rows(rows, numeric_columns=(1, 2, 3), colours=colours)

        notes = [strip.evidence and
                 f"Polyamide strip {strip.width:.1f} mm ({', '.join(strip.evidence)})"
                 for strip in report.strips]
        notes.extend(report.warnings)
        self.feature_notes.setText("  ".join(note for note in notes if note))

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




# --------------------------------------------------------------------------- #
# Glass
# --------------------------------------------------------------------------- #

class GlassPage(Page):
    """Nest the project's glass onto stock sheets."""

    title = "Glass"
    subtitle = "Nest the project's glazing onto stock sheets"

    def build(self) -> None:
        run = QPushButton("Nest glass")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        export = QPushButton("Export maps")
        export.clicked.connect(self.export_maps)
        self.header.add_action(export)

        self.stats = StatRow([
            ("sheets", "Sheets"), ("panes", "Panes"), ("yield", "Yield"),
            ("offcuts", "Off-cuts"), ("stages", "Stages"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("Machine and stock")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))

        self.kerf = QDoubleSpinBox()
        self.kerf.setRange(0, 20)
        self.kerf.setValue(0.0)
        self.kerf.setSuffix(" mm")
        self.kerf.setToolTip(
            "A glass scoring wheel removes nothing — it scores and the piece is "
            "snapped — so glass is zero. A beam saw cutting composite panel "
            "removes its blade width, typically 4 to 5 mm."
        )

        self.trim = QDoubleSpinBox()
        self.trim.setRange(0, 200)
        self.trim.setValue(20.0)
        self.trim.setSuffix(" mm")
        self.trim.setToolTip(
            "Taken off all four sides before any pane is placed. Float glass "
            "arrives with damaged arrises and, on coated stock, a deleted "
            "coating band at the edge."
        )

        self.stages = QComboBox()
        self.stages.addItems(["unlimited", "2", "3"])
        self.stages.setToolTip(
            "How many times the cutting line may turn. An unattended line "
            "manages two: cross cuts into strips, then rip cuts into panes."
        )

        self.stock = QComboBox()
        self.stock.setEditable(True)
        self.stock.addItems([
            "all standard", "3210x2250", "3210x2550", "6000x3210",
            "3210x2250,6000x3210",
        ])
        self.stock.setToolTip(
            "Sheet sizes as WIDTHxHEIGHT. Stock choice usually matters more "
            "than the packing: a 2334 mm pane will not come off a 2250 plate."
        )

        for label, widget in [("Kerf", self.kerf), ("Edge trim", self.trim),
                              ("Stages", self.stages), ("Stock", self.stock)]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Vertical)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.view = SheetView(self.colours)
        scroll.setWidget(self.view)
        splitter.addWidget(scroll)

        self.summary = DataTable(
            ["Build-up", "Panes", "Sheets", "Yield", "Off-cuts", "Stages", "Proof"]
        )
        splitter.addWidget(self.summary)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

        self.material = QComboBox()
        self.material.currentTextChanged.connect(self._show_material)
        picker = QHBoxLayout()
        caption = QLabel("Build-up shown")
        caption.setObjectName("FieldLabel")
        picker.addWidget(caption)
        picker.addWidget(self.material, 1)
        self.body.addLayout(picker)

    def refresh(self) -> None:
        panes = sum(
            sum(pane.quantity for pane in build.glass) * build.opening.quantity
            for build in self.session.builds
        )
        self.header.set_subtitle(
            f"{panes} pane(s) from {len(self.session.builds)} element(s)"
            if self.session.builds
            else "Design an element first"
        )

    def _spec(self) -> Any:
        from ..nesting import SheetSpec

        stages = self.stages.currentText()
        return SheetSpec(
            kerf=self.kerf.value(),
            edge_trim=self.trim.value(),
            stages=None if stages == "unlimited" else int(stages),
        )

    def _stock(self) -> list[Any]:
        from ..nesting import SheetStock
        from ..nesting.sheet import STANDARD_GLASS_STOCK

        text = self.stock.currentText().strip()
        if not text or text == "all standard":
            return list(STANDARD_GLASS_STOCK)
        sheets: list[Any] = []
        for token in text.split(","):
            token = token.strip()
            width, _, height = token.lower().partition("x")
            sheets.append(SheetStock(float(width), float(height), label=token))
        return sheets

    def run(self) -> None:
        from ..nesting import nest_project_glass, sheet_parts_from_builds

        if not self.session.builds:
            self.report(
                ProfileOSError("No elements have been designed yet"), "Nothing to nest"
            )
            return

        try:
            stock = self._stock()
        except ValueError:
            self.report(
                ProfileOSError("Sheet sizes must be written as WIDTHxHEIGHT"),
                "Bad input",
            )
            return

        parts = sheet_parts_from_builds(self.session.builds)
        if not parts:
            self.report(
                ProfileOSError("These elements have no glass"), "Nothing to nest"
            )
            return

        try:
            report = nest_project_glass(parts, stock=stock, spec=self._spec())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Glass nesting failed")
            return

        self.session.set_glass(report)

        offcuts = sum(len(r.reusable_offcuts()) for r in report.results.values())
        stages = max(
            (r.stages_used or 0 for r in report.results.values()), default=0
        )
        self.stats.update_many({
            "sheets": (str(report.sheet_count), f"{report.total_stock_area / 1e6:.1f} m²"),
            "panes": (str(sum(r.total_pieces for r in report.results.values())), ""),
            "yield": (f"{report.yield_pct:.2f}%", f"{report.total_placed_area / 1e6:.1f} m² glazed"),
            "offcuts": (str(offcuts), "back to the rack"),
            "stages": (str(stages) if stages else "—", "cuts turn"),
        })

        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, (material, result) in enumerate(sorted(report.results.items())):
            if result.optimal:
                proof = "optimal"
            elif result.metadata.get("optimal_within_stage_limit"):
                proof = "optimal ≤3 stages"
            else:
                proof = f"bound {result.lower_bound}"
            rows.append([
                material, result.total_pieces, result.sheet_count,
                f"{result.yield_pct:.2f}%", len(result.reusable_offcuts()),
                result.stages_used or "—", proof,
            ])
            colours[(index, 3)] = (
                self.colours.success if result.yield_pct >= 80 else self.colours.warning
            )
        self.summary.set_rows(rows, numeric_columns=(1, 2, 3, 4, 5), colours=colours)

        self.material.blockSignals(True)
        self.material.clear()
        self.material.addItems(sorted(report.results))
        self.material.blockSignals(False)
        if report.results:
            self._show_material(self.material.currentText())

        for warning in report.warnings:
            self.status(warning)
        self.status(
            f"{report.sheet_count} sheets at {report.yield_pct:.2f}% yield"
        )

    def _show_material(self, material: str) -> None:
        report = self.session.glass_report
        if report is None or material not in report.results:
            return
        self.view.set_result(report.results[material])

    def export_maps(self) -> None:
        from ..nesting import render_layout_svg

        report = self.session.glass_report
        if report is None:
            self.report(
                ProfileOSError("Nest the glass first"), "Nothing to export"
            )
            return
        folder = QFileDialog.getExistingDirectory(self, "Where should the maps go?")
        if not folder:
            return
        target = Path(folder)
        written = 0
        for material, result in report.results.items():
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in material)
            for layout in result.layouts:
                path = target / f"{safe}-sheet{layout.sheet_index + 1:02d}.svg"
                path.write_text(render_layout_svg(layout), encoding="utf-8")
                written += 1
        self.status(f"Wrote {written} cutting map(s) to {target}")


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #

class CataloguePage(Page):
    """Build an owned profile library from what suppliers publish."""

    title = "Catalogue"
    subtitle = "Read supplier drawings and tables into a library you own"

    def build(self) -> None:
        run = QPushButton("Ingest")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        export = QPushButton("Save as plugin")
        export.clicked.connect(self.export_plugin)
        self.header.add_action(export)

        self.stats = StatRow([
            ("articles", "Articles"), ("geometry", "With geometry"),
            ("verified", "Verified"), ("conflicts", "Conflicts"),
            ("unmatched", "Unmatched"),
        ])
        self.body.addWidget(self.stats)

        sources = Card("Sources")
        grid = FieldGrid()

        self.drawings_label = QLabel("no folder chosen")
        self.drawings_label.setObjectName("FieldValue")
        pick_drawings = QPushButton("Choose DXF folder…")
        pick_drawings.clicked.connect(self._pick_drawings)
        drawings_row = QHBoxLayout()
        drawings_row.addWidget(pick_drawings)
        drawings_row.addWidget(self.drawings_label, 1)

        self.table_label = QLabel("no table chosen")
        self.table_label.setObjectName("FieldValue")
        pick_table = QPushButton("Choose catalogue…")
        pick_table.clicked.connect(self._pick_table)
        table_row = QHBoxLayout()
        table_row.addWidget(pick_table)
        table_row.addWidget(self.table_label, 1)

        self.series = QComboBox()
        self.series.setEditable(True)
        self.series.addItems(["", "MB-70", "4300", "CW-50"])

        sources.add_layout(drawings_row)
        sources.add_layout(table_row)
        series_row = QHBoxLayout()
        caption = QLabel("System series")
        caption.setObjectName("FieldLabel")
        series_row.addWidget(caption)
        series_row.addWidget(self.series)
        series_row.addStretch(1)
        sources.add_layout(series_row)
        sources.add(grid)
        self.body.addWidget(sources)

        note = QLabel(
            "Every drawing is measured by the geometry and structural engines "
            "and set against the supplier's published table. Agreement is "
            "evidence; a disagreement is reported rather than resolved, and the "
            "article is kept out of the library until somebody decides which "
            "figure is right."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.body.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.entries = DataTable(
            ["Article", "Name", "Status", "Checked", "Conflicts", "Drawing"]
        )
        splitter.addWidget(self.entries)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setObjectName("Mono")
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

        self.entries.itemSelectionChanged.connect(self._show_detail)

        self._drawings: Path | None = None
        self._table: Path | None = None

    def _pick_drawings(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Supplier DXF folder")
        if folder:
            self._drawings = Path(folder)
            self.drawings_label.setText(str(self._drawings))

    def _pick_table(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Supplier catalogue", "", "Catalogues (*.pdf *.csv *.tsv *.txt)"
        )
        if path:
            self._table = Path(path)
            self.table_label.setText(self._table.name)

    def run(self) -> None:
        from ..catalogue import ingest

        if self._drawings is None and self._table is None:
            self.report(
                ProfileOSError("Choose a drawing folder, a catalogue table, or both"),
                "Nothing to ingest",
            )
            return

        try:
            report = ingest(
                table=self._table,
                drawings=self._drawings,
                system_series=self.series.currentText() or "unknown",
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Ingestion failed")
            return

        self.session.set_catalogue(report)
        stats = report.summary()
        self.stats.update_many({
            "articles": (str(stats["entries"]), ""),
            "geometry": (str(stats["with_geometry"]), "measured from DXF"),
            "verified": (str(stats["verified"]), "table agrees"),
            "conflicts": (str(stats["conflicts"]), "table disagrees"),
            "unmatched": (
                str(stats["unmatched_drawings"] + stats["unmatched_rows"]),
                "no counterpart",
            ),
        })

        colours: dict[tuple[int, int], str] = {}
        rows = []
        tint = {
            "verified": self.colours.success,
            "conflict": self.colours.danger,
            "unverified": self.colours.warning,
        }
        for index, entry in enumerate(report.entries):
            summary = entry.summary()
            rows.append([
                entry.profile_id, (entry.name or "")[:40], entry.status,
                summary["checked"], summary["conflicts"] or "—",
                Path(entry.dxf_path).name if entry.dxf_path else "—",
            ])
            if entry.status in tint:
                colours[(index, 2)] = tint[entry.status]
        self.entries.set_rows(rows, numeric_columns=(3, 4), colours=colours)

        for message in report.errors[:5]:
            self.status(message)
        self.status(
            f"{stats['entries']} articles, {stats['verified']} verified, "
            f"{stats['conflicts']} in conflict"
        )

    def _show_detail(self) -> None:
        report = self.session.catalogue_report
        rows = self.entries.selectionModel()
        if report is None or rows is None or not rows.selectedRows():
            return
        index = rows.selectedRows()[0].row()
        if index >= len(report.entries):
            return
        entry = report.entries[index]

        lines = [f"{entry.profile_id}  —  {entry.name or 'unnamed'}", ""]
        if entry.dxf_path:
            lines.append(f"drawing : {entry.dxf_path}")
        if entry.pdf_page:
            lines.append(f"page    : {entry.pdf_page}")
        lines.append(f"status  : {entry.status}")
        lines.append("")
        if entry.checks:
            lines.append("published vs measured")
            for check in entry.checks:
                mark = {"agree": "  ok", "disagree": "CONFLICT", "unchecked": "   -"}[
                    str(check.status)
                ]
                lines.append(f"  {mark}  {check.describe()}")
        else:
            lines.append("nothing was compared: only one source had figures")
        if entry.warnings:
            lines.append("")
            lines.append("warnings")
            lines.extend(f"  - {warning}" for warning in entry.warnings)
        self.detail.setPlainText("\n".join(lines))

    def export_plugin(self) -> None:
        from ..catalogue import to_plugin

        report = self.session.catalogue_report
        if report is None:
            self.report(ProfileOSError("Ingest a catalogue first"), "Nothing to save")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save profile library", "profile-library.json", "JSON (*.json)"
        )
        if not path:
            return
        target = Path(path)
        payload = to_plugin(
            report,
            plugin_id=target.stem,
            name=f"{self.series.currentText() or 'unknown'} profile library",
        )
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.status(
            f"Wrote {len(payload['profiles'])} profiles to {target}; "
            f"{len(payload['excluded_for_conflict'])} withheld for conflict"
        )


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #

class SystemPage(Page):
    """Updates, licence, brand, plugins and the capability comparison."""

    title = "System"
    subtitle = "Updates, licence, plugins and what this suite covers"

    def build(self) -> None:
        check = QPushButton("Check for updates")
        check.setObjectName("Primary")
        check.clicked.connect(self.check_updates)
        self.header.add_action(check)

        tabs = QTabWidget()
        tabs.addTab(self._updates_tab(), "Updates")
        tabs.addTab(self._licence_tab(), "Licence")
        tabs.addTab(self._brand_tab(), "Operator")
        tabs.addTab(self._plugins_tab(), "Plugins")

        # The comparison verifies every capability claim, which means importing
        # every engine in the suite — OR-Tools, sectionproperties, FastAPI and
        # the rest. Doing that while the window is still being built costs the
        # user twenty seconds before they see anything, to populate a tab most
        # sessions never open. It is filled the first time it is looked at.
        self._compare_page = QWidget()
        self._compare_layout = QVBoxLayout(self._compare_page)
        self._compare_layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        self._compare_layout.setSpacing(METRICS.space(3))
        self._compare_built = False
        self._compare_index = tabs.addTab(self._compare_page, "Comparison")
        tabs.currentChanged.connect(self._tab_changed)

        self.tabs = tabs
        self.body.addWidget(tabs, 1)

    def _tab_changed(self, index: int) -> None:
        if index == self._compare_index:
            self._build_comparison()

    # -- updates ------------------------------------------------------------- #
    def _updates_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        note = QLabel(
            "Content updates are signed manifests. Each package is downloaded, "
            "its signature checked and its contents validated before anything "
            "is installed; the batch is then installed atomically and rolled "
            "back entire if any part of it fails. Profile systems, rules and "
            "price lists go live without restarting."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        source_row = QHBoxLayout()
        source_row.setSpacing(METRICS.space(3))
        self.update_source = QLineEdit()
        self.update_source.setPlaceholderText("https://updates.example.com/ or a folder")
        self.update_key_label = QLabel("no key chosen")
        self.update_key_label.setObjectName("FieldValue")
        pick_key = QPushButton("Issuer key…")
        pick_key.clicked.connect(self._pick_update_key)
        self.update_channel = QComboBox()
        self.update_channel.addItems(["stable", "beta", "canary"])

        for label, widget in [("Source", self.update_source)]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            source_row.addWidget(caption)
            source_row.addWidget(widget, 1)
        source_row.addWidget(pick_key)
        source_row.addWidget(self.update_key_label)
        channel_caption = QLabel("Channel")
        channel_caption.setObjectName("FieldLabel")
        source_row.addWidget(channel_caption)
        source_row.addWidget(self.update_channel)
        layout.addLayout(source_row)

        key_note = QLabel(
            "Without the issuer's public key nothing can be installed: an "
            "unsigned manifest is refused rather than trusted, which is the "
            "whole point of the mechanism."
        )
        key_note.setObjectName("Hint")
        key_note.setWordWrap(True)
        layout.addWidget(key_note)

        self.update_table = DataTable(
            ["Package", "Version", "Kind", "Size", "Description"]
        )
        layout.addWidget(self.update_table, 1)

        self.apply_button = QPushButton("Apply updates")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_updates)
        layout.addWidget(self.apply_button)

        installed_title = QLabel("INSTALLED CONTENT")
        installed_title.setObjectName("CardTitle")
        layout.addWidget(installed_title)
        self.installed_table = DataTable(["Package", "Version", "Kind", "Installed"])
        layout.addWidget(self.installed_table, 1)

        self._update_key: Path | None = None
        self._update_plan: Any = None
        self._refresh_installed()
        return page

    def _pick_update_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Issuer public key", "", "PEM keys (*.pem *.pub);;All files (*)"
        )
        if path:
            self._update_key = Path(path)
            self.update_key_label.setText(self._update_key.name)

    def _engine(self, *, need_key: bool) -> Any:
        """Build an update engine, or explain what is missing.

        Reading the installed state does not need a key; fetching a manifest
        does, and refusing to build the engine without one is better than
        building it with a throwaway key that would reject every signature
        with a misleading error.
        """
        from ..core.config import get_settings
        from ..core.hotreload import PluginLoader
        from ..security.keys import SigningKey, VerifyKey
        from ..updates import DirectorySource, UpdateChannel, UpdateEngine, HttpSource

        settings = get_settings()
        settings.ensure_directories()

        if need_key:
            if self._update_key is None or not self._update_key.is_file():
                raise ProfileOSError(
                    "Choose the issuer's public key before checking for updates"
                )
            verify_key = VerifyKey.from_pem(self._update_key.read_bytes())
        elif self._update_key is not None and self._update_key.is_file():
            verify_key = VerifyKey.from_pem(self._update_key.read_bytes())
        else:
            # Only ever used for the read-only installed listing below.
            verify_key = SigningKey.generate().public_key()

        raw = self.update_source.text().strip()
        if raw.startswith(("http://", "https://")):
            source: Any = HttpSource(raw)
        else:
            source = DirectorySource(raw or settings.data_dir)

        return UpdateEngine(
            source,
            verify_key,
            settings,
            channel=UpdateChannel(self.update_channel.currentText()),
            loader=PluginLoader(settings, strict=False),
        )

    def _refresh_installed(self) -> None:
        try:
            engine = self._engine(need_key=False)
            installed = engine.installed()
        except Exception as exc:  # noqa: BLE001
            self.installed_table.set_rows([["error", "", "", str(exc)]])
            return
        self.installed_table.set_rows(
            [
                [p.package_id, p.version, p.kind, p.installed_at[:19]]
                for p in installed
            ]
        )

    def check_updates(self) -> None:
        try:
            engine = self._engine(need_key=True)
            plan = engine.check()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Update check failed")
            return

        self._update_plan = plan
        self.update_table.set_rows(
            [
                [
                    package.package_id,
                    package.version,
                    package.kind.value,
                    f"{package.size / 1024:.1f} kB",
                    package.description or "",
                ]
                for package in plan.packages
            ],
            numeric_columns=(3,),
        )
        self.apply_button.setEnabled(plan.has_updates)
        if plan.skipped:
            for package_id, reason in plan.skipped[:3]:
                self.status(f"skipped {package_id}: {reason}")
        self.status(
            f"{len(plan.packages)} update(s), {plan.total_size / 1024:.0f} kB"
            if plan.has_updates
            else "Everything is up to date"
        )

    def apply_updates(self) -> None:
        if self._update_plan is None or not self._update_plan.has_updates:
            self.report(ProfileOSError("Check for updates first"), "Nothing to apply")
            return
        try:
            engine = self._engine(need_key=True)
            result = engine.apply(self._update_plan)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Update failed and was rolled back")
            return

        if result.ok:
            self.status(
                f"Applied {len(result.applied)} update(s) in "
                f"{result.duration_s:.2f} s, {result.reloaded} reloaded live"
            )
        else:
            detail = "; ".join(f"{pid}: {why}" for pid, why in result.failed)
            self.report(
                ProfileOSError(
                    f"Update failed and was rolled back — {detail}"
                    if result.rolled_back
                    else f"Update partly failed — {detail}"
                ),
                "Update failed",
            )
        for warning in result.warnings[:3]:
            self.status(warning)
        self._update_plan = None
        self.apply_button.setEnabled(False)
        self.update_table.clear_rows()
        self._refresh_installed()

    # -- licence -------------------------------------------------------------- #
    def _licence_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        grid = FieldGrid()
        try:
            from ..security.hwid import current_fingerprint

            fingerprint = current_fingerprint()
            for label, text in [
                ("Machine fingerprint", fingerprint.short),
                ("Traits recorded", str(len(fingerprint.traits))),
                (
                    "Traits",
                    ", ".join(trait.name for trait in fingerprint.traits) or "none",
                ),
            ]:
                value = QLabel(text)
                value.setObjectName("FieldValue")
                value.setWordWrap(True)
                grid.add(label, value)
        except Exception as exc:  # noqa: BLE001
            value = QLabel(f"unavailable: {exc}")
            value.setObjectName("FieldValue")
            grid.add("Machine fingerprint", value)
        layout.addWidget(grid)

        note = QLabel(
            "A licence is sealed to this machine's fingerprint with AES-256-GCM "
            "and verified entirely offline. The fingerprint is a weighted set "
            "of traits rather than one serial number, so replacing a disk does "
            "not invalidate the licence, and only trait digests are stored — "
            "the licence file never carries a MAC address or serial in the "
            "clear. Past expiry the software degrades to read-only for the "
            "grace period rather than locking the shop out mid-job."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    # -- brand ---------------------------------------------------------------- #
    #: Field order on the operator tab, and where each value comes from.
    _BRAND_FIELDS: tuple[tuple[str, str], ...] = (
        ("Name", "display_name"),
        ("Legal name", "legal_name"),
        ("Address", "address_line"),
        ("City", "city"),
        ("Postcode", "postcode"),
        ("Country", "country"),
        ("Telephone", "phone"),
        ("Fax", "fax"),
        ("Email", "email"),
        ("Website", "website"),
    )

    def _brand_tab(self) -> QWidget:
        from ..branding import BRANDS, BUILTIN_BRANDS, configured_brand_id

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        chooser = QHBoxLayout()
        caption = QLabel("Operator")
        caption.setObjectName("FieldLabel")
        self.brand_picker = QComboBox()
        known = dict(BUILTIN_BRANDS)
        for key in BRANDS.keys():
            entry = BRANDS.get_or_none(key)
            if entry is not None:
                known[key] = entry
        for key, entry in sorted(known.items()):
            self.brand_picker.addItem(entry.display_name, key)
        index = self.brand_picker.findData(configured_brand_id())
        if index >= 0:
            self.brand_picker.setCurrentIndex(index)
        self.brand_picker.currentIndexChanged.connect(self._choose_brand)
        chooser.addWidget(caption)
        chooser.addWidget(self.brand_picker)
        chooser.addStretch(1)
        layout.addLayout(chooser)

        # Built once, then updated in place. Tearing the tab down and building
        # it again leaves the old labels on screen until Qt gets round to
        # deleting them, and the two sets of text overlap.
        grid = FieldGrid()
        self._brand_values: dict[str, QLabel] = {}
        for label, _ in self._BRAND_FIELDS:
            value = QLabel()
            value.setObjectName("FieldValue")
            value.setWordWrap(True)
            # Hebrew values are laid out right-to-left inside the label, which
            # is correct, but left to itself the label then parks the text at
            # the far edge of a wide column, half a screen from its caption.
            # Pinning the label's own alignment keeps value beside label while
            # the text inside it still reads in its own direction.
            # AlignLeft alone is the *leading* edge, which Qt resolves to the
            # right for a Hebrew paragraph; AlignAbsolute makes it mean left.
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignAbsolute
                | Qt.AlignmentFlag.AlignVCenter
            )
            self._brand_values[label] = grid.add(label, value)
        layout.addWidget(grid)

        self.brand_note = QLabel()
        self.brand_note.setObjectName("Hint")
        self.brand_note.setWordWrap(True)
        layout.addWidget(self.brand_note)

        note = QLabel(
            "These details appear on quotations, job cards and in the header of "
            "every machine program. Add another operator, or correct these "
            "details, with a brand plugin."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        self._show_brand()
        return page

    def _show_brand(self) -> None:
        from ..branding import active_brand

        brand = active_brand()
        for label, attribute in self._BRAND_FIELDS:
            text = getattr(brand, attribute, None)
            self._brand_values[label].setText(str(text) if text else "not set")
        self.brand_note.setText(brand.notes or "")
        self.brand_note.setVisible(bool(brand.notes))

    def _choose_brand(self) -> None:
        from ..branding import set_active_brand

        brand_id = self.brand_picker.currentData()
        if not brand_id:
            return
        try:
            brand = set_active_brand(brand_id, persist=True)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Could not change the operator")
            return
        self._show_brand()
        window = self.window()
        if hasattr(window, "refresh_brand"):
            window.refresh_brand()
        self.status(
            f"Operator set to {brand.display_name}; it will appear on documents "
            "and machine-program headers from now on"
        )

    # -- plugins --------------------------------------------------------------- #
    def _plugins_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        table = DataTable(["Registry", "Entries", "Contents"])
        try:
            from ..core.registry import registry_report

            rows = []
            for name, entries in sorted(registry_report().items()):
                keys = sorted(entry.get("key", "?") for entry in entries)
                shown = ", ".join(keys[:6])
                if len(keys) > 6:
                    shown += f", … {len(keys) - 6} more"
                rows.append([name, len(entries), shown])
        except Exception as exc:  # noqa: BLE001
            rows = [["error", 0, str(exc)]]
        table.set_rows(rows, numeric_columns=(1,))
        layout.addWidget(table, 1)

        note = QLabel(
            "A plugin's source is checked against an AST policy before it is "
            "executed: no imports outside the allowed set, no file or network "
            "access, no dynamic evaluation. Data plugins are validated against "
            "their schema instead."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    # -- comparison ------------------------------------------------------------ #
    def _build_comparison(self) -> None:
        if self._compare_built:
            return
        self._compare_built = True

        from .. import compare as cmp

        layout = self._compare_layout

        failures = cmp.verify_claims()
        stats = cmp.summary()
        header = QLabel(
            f"{stats['profileos_implemented']} of {stats['capabilities']} "
            f"capabilities implemented, {stats['not_documented_elsewhere']} that "
            f"none of the {stats['packages_compared']} compared packages "
            f"documents, {stats['profileos_gaps']} this suite does not have."
        )
        header.setObjectName("Hint")
        header.setWordWrap(True)
        layout.addWidget(header)
        if failures:
            broken = QLabel(
                f"{len(failures)} capability claim(s) no longer resolve to code."
            )
            broken.setObjectName("Hint")
            broken.setWordWrap(True)
            layout.addWidget(broken)

        headers = ["Capability", "ProfileOS"] + [p.heading for p in cmp.PACKAGES]
        table = DataTable(headers)
        marks = {
            cmp.Support.FULL: "yes",
            cmp.Support.PARTIAL: "part",
            cmp.Support.NOT_DOCUMENTED: "no",
            cmp.Support.UNKNOWN: "?",
        }
        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, capability in enumerate(cmp.CAPABILITIES):
            name = capability.name_en + (" *" if capability.differentiator else "")
            own = cmp.profileos_support(capability)
            rows.append(
                [name, marks[own]]
                + [marks[p.level(capability.id)] for p in cmp.PACKAGES]
            )
            colours[(index, 1)] = (
                self.colours.success
                if own is cmp.Support.FULL
                else self.colours.warning
            )
        table.set_rows(rows, colours=colours)
        # Capability names carry the meaning; the support columns are three
        # characters wide. Stretching all of them equally truncates the only
        # column a reader actually has to read.
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(table, 1)

        legend = QLabel(
            "yes = documented, part = partial or a paid module, no = not found "
            "in the vendor's public material, ? = not looked into. Nothing here "
            "has been tested against a competitor's installation, and \"no\" "
            "never means \"absent\".\n\n"
            + "\n".join(f"• {limit}" for limit in cmp.STANDING_LIMITATIONS)
        )
        legend.setObjectName("Hint")
        legend.setWordWrap(True)
        layout.addWidget(legend)



# --------------------------------------------------------------------------- #
# 3D views
# --------------------------------------------------------------------------- #

class ViewPage(Page):
    """See the element the way the customer will."""

    title = "3D view"
    subtitle = "Presentation and technical views of the designed elements"

    def build(self) -> None:
        from PySide6.QtSvgWidgets import QSvgWidget

        render = QPushButton("Render")
        render.setObjectName("Primary")
        render.clicked.connect(self.render_scene)
        self.header.add_action(render)

        export = QPushButton("Export")
        export.clicked.connect(self.export_scene)
        self.header.add_action(export)

        self.stats = StatRow([
            ("parts", "Parts"), ("triangles", "Triangles"),
            ("metal", "Metal"), ("size", "Size"), ("panes", "Panes"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("View")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))

        self.element = QComboBox()
        self.view = QComboBox()
        self.view.addItems(["presentation", "elevation"])
        self.finish = QComboBox()
        self.finish.addItems(["natural", "bronze"])
        self.glass = QComboBox()
        self.glass.addItems(["with glass", "frames only"])

        for label, widget in [("Element", self.element), ("View", self.view),
                              ("Finish", self.finish), ("Glazing", self.glass)]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        self.canvas = QSvgWidget()
        self.canvas.setMinimumHeight(420)
        self.body.addWidget(self.canvas, 1)

        note = QLabel(
            "The model is swept from the same profile sections and placed by the "
            "same system rules that produce the cut list, so what is drawn here "
            "is what the shop will make. Export writes printable SVG, glTF for "
            "any 3D tool, and a self-contained interactive viewer."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.body.addWidget(note)

        for widget in (self.element, self.view, self.finish, self.glass):
            widget.currentTextChanged.connect(self._redraw)
        self._scene = None

    def refresh(self) -> None:
        ids = [build.opening.element_id for build in self.session.builds]
        current = self.element.currentText()
        self.element.blockSignals(True)
        self.element.clear()
        self.element.addItems(ids)
        if current in ids:
            self.element.setCurrentText(current)
        self.element.blockSignals(False)
        self.header.set_subtitle(
            f"{len(ids)} element(s) designed" if ids else "Design an element first"
        )

    def _build_for(self, element_id: str):
        """The build the picker names — or the first one, if it names nothing.

        The picker is filled by :meth:`refresh`, which runs when the page is
        navigated to. Reaching the page another way — a keyboard shortcut, a
        restored layout — leaves it empty while the session plainly has
        elements in it, and erroring at the user in that state is a bug, not a
        message worth showing.
        """
        for build in self.session.builds:
            if build.opening.element_id == element_id:
                return build
        if self.session.builds and not element_id:
            self.refresh()
            return self.session.builds[0]
        return None

    def render_scene(self) -> None:
        from ..viz3d import ViewStyle, build_element_scene

        build = self._build_for(self.element.currentText())
        if build is None:
            self.report(
                ProfileOSError("No elements have been designed yet"), "Nothing to render"
            )
            return
        try:
            self._scene = build_element_scene(
                build,
                style=ViewStyle(show_glass=self.glass.currentText() == "with glass"),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Could not build the model")
            return

        scene = self._scene
        size = scene.size
        panes = sum(1 for mesh in scene.meshes if mesh.material == "glass")
        self.stats.update_many({
            "parts": (str(len(scene.meshes)), "modelled solids"),
            "triangles": (f"{scene.triangle_count:,}", ""),
            "metal": (f"{scene.aluminium_volume() * 2.70e-6:.1f} kg", "at 2.70 g/cm³"),
            "size": (f"{size[0]:.0f}×{size[1]:.0f}", f"{size[2]:.0f} mm deep"),
            "panes": (str(panes), ""),
        })
        self._redraw()

    def _redraw(self) -> None:
        if self._scene is None:
            return
        from ..viz3d import (
            BRONZE_MATERIALS,
            DEFAULT_MATERIALS,
            RenderOptions,
            elevation_camera,
            presentation_camera,
            render_svg,
        )

        options = RenderOptions(
            width=1200, height=760,
            materials=dict(
                BRONZE_MATERIALS if self.finish.currentText() == "bronze"
                else DEFAULT_MATERIALS
            ),
            background=self.colours.surface_sunken,
        )
        camera = (
            elevation_camera(self._scene)
            if self.view.currentText() == "elevation"
            else presentation_camera(self._scene)
        )
        try:
            svg = render_svg(self._scene, camera, options)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Could not render the view")
            return
        self.canvas.load(svg.encode("utf-8"))

    def export_scene(self) -> None:
        from ..viz3d import RenderOptions, render_viewer, render_views, write_gltf

        if self._scene is None:
            self.report(ProfileOSError("Render the element first"), "Nothing to export")
            return
        folder = QFileDialog.getExistingDirectory(self, "Where should the views go?")
        if not folder:
            return
        target = Path(folder)
        stem = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in self.element.currentText()
        ) or "element"
        written = 0
        for name, svg in render_views(self._scene, RenderOptions()).items():
            (target / f"{stem}-{name}.svg").write_text(svg, encoding="utf-8")
            written += 1
        (target / f"{stem}.html").write_text(
            render_viewer(self._scene), encoding="utf-8"
        )
        write_gltf(self._scene, target / f"{stem}.gltf")
        write_gltf(self._scene, target / f"{stem}.glb")
        self.status(f"Wrote {written + 3} file(s) to {target}")


# --------------------------------------------------------------------------- #
# ERP
# --------------------------------------------------------------------------- #

class AccountsPage(Page):
    """Stock, purchasing, the ledger and the shop's capacity."""

    title = "Accounts"
    subtitle = "Stock, purchasing, the ledger and what the shop can take on"

    def build(self) -> None:
        audit = QPushButton("Audit")
        audit.setObjectName("Primary")
        audit.clicked.connect(self.run_audit)
        self.header.add_action(audit)

        self.stats = StatRow([
            ("stock", "Stock"), ("debtors", "Debtors"), ("creditors", "Creditors"),
            ("result", "Result"), ("entries", "Entries"),
        ])
        self.body.addWidget(self.stats)

        tabs = QTabWidget()
        tabs.addTab(self._stock_tab(), "Stock")
        tabs.addTab(self._purchasing_tab(), "Purchasing")
        tabs.addTab(self._ledger_tab(), "Ledger")
        tabs.addTab(self._planning_tab(), "Capacity")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

    # -- shared company ------------------------------------------------------- #
    def _company(self):
        if self.session.company is None:
            from ..erp import company_for_brand

            self.session.set_company(company_for_brand())
        return self.session.company

    # -- stock ---------------------------------------------------------------- #
    def _stock_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.stock_table = DataTable(
            ["Item", "Name", "On hand", "Allocated", "On order",
             "Projected", "Unit cost", "Value", "Reorder"]
        )
        layout.addWidget(self.stock_table, 1)

        note = QLabel(
            "Value follows the physical movement: FIFO consumes the oldest "
            "delivery first, so what a bar cost depends on which delivery it "
            "came from. Issuing more than is on the rack is refused — a negative "
            "balance is a missing goods receipt, not a quantity."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _refresh_stock(self) -> None:
        from ..erp import format_money

        company = self._company()
        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, row in enumerate(company.stock.valuation_report()):
            rows.append([
                row["code"], row["name"], f"{row['on_hand']:.1f}",
                f"{row['allocated']:.1f}", f"{row['on_order']:.1f}",
                f"{row['projected']:.1f}",
                format_money(int(row["unit_cost"]), company.currency),
                format_money(row["value"], company.currency),
                "yes" if row["below_reorder"] else "",
            ])
            if row["below_reorder"]:
                colours[(index, 8)] = self.colours.warning
        self.stock_table.set_rows(rows, numeric_columns=(2, 3, 4, 5, 6, 7), colours=colours)

    # -- purchasing ------------------------------------------------------------ #
    def _purchasing_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        plan = QPushButton("Plan purchases for the designed elements")
        plan.clicked.connect(self.plan_purchases)
        layout.addWidget(plan)

        self.requirements_table = DataTable(
            ["Item", "Needed", "Free", "On order", "To buy", "Unit"]
        )
        layout.addWidget(self.requirements_table, 1)

        self.orders_table = DataTable(["Order", "Supplier", "Lines", "Net", "Promised"])
        layout.addWidget(self.orders_table, 1)

        note = QLabel(
            "Net requirement is what has to be bought: the gross, less what is "
            "free on the rack, less what is already on order. Orders are grouped "
            "one per supplier and rounded up to the stock length, because a "
            "supplier does not sell 11.4 metres of a 6 metre bar."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def plan_purchases(self) -> None:
        from ..erp import StockItem, money

        if not self.session.builds:
            self.report(
                ProfileOSError("No elements have been designed yet"), "Nothing to plan"
            )
            return

        company = self._company()
        demand: dict[str, float] = {}
        for build in self.session.builds:
            quantity = build.opening.quantity
            for cut in build.cuts:
                demand[cut.profile_id] = demand.get(cut.profile_id, 0.0) + (
                    cut.total_length * quantity / 1000.0
                )

        prices: dict[str, float] = {}
        for code, needed in demand.items():
            if code not in company.stock.items:
                company.add_item(
                    StockItem(code, code, supplier_id="unassigned",
                              reorder_quantity=6.0, lead_time_days=12)
                )
            prices[code] = float(money(42.0))

        try:
            rows, orders = company.plan_purchases(demand, prices)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "Could not plan the purchases")
            return

        self.requirements_table.set_rows(
            [
                [r.item, f"{r.gross:.1f}", f"{r.free:.1f}", f"{r.on_order:.1f}",
                 f"{r.net:.1f}", r.unit]
                for r in rows
            ],
            numeric_columns=(1, 2, 3, 4),
        )
        from ..erp import format_money

        self.orders_table.set_rows(
            [
                [o.order_id, o.supplier_id, str(len(o.lines)),
                 format_money(o.net, company.currency),
                 o.promised.isoformat() if o.promised else ""]
                for o in orders
            ],
            numeric_columns=(2, 3),
        )
        self.status(
            f"{sum(1 for r in rows if r.must_order)} item(s) to buy across "
            f"{len(orders)} order(s)"
        )
        self._refresh_stock()

    # -- ledger ----------------------------------------------------------------- #
    def _ledger_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.trial_table = DataTable(["Code", "Account", "Type", "Debit", "Credit"])
        layout.addWidget(self.trial_table, 1)

        self.position_grid = FieldGrid()
        layout.addWidget(self.position_grid)
        self._position_values: dict[str, QLabel] = {}
        for label in ("Income", "Expense", "Result", "Assets", "Liabilities",
                      "Equity", "Balance sheet difference"):
            value = QLabel("—")
            value.setObjectName("FieldValue")
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
                | Qt.AlignmentFlag.AlignVCenter
            )
            self._position_values[label] = self.position_grid.add(label, value)

        note = QLabel(
            "Double entry, in minor currency units. An entry that does not sum "
            "to zero is refused at the point it is written, so the trial balance "
            "cannot fail to balance — and Audit proves that rather than assuming "
            "it, and checks the stock accounts against the stock book."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _refresh_ledger(self) -> None:
        from ..erp import format_money

        company = self._company()
        self.trial_table.set_rows(
            [
                [row.account.code, row.account.name, str(row.account.type),
                 format_money(row.debits, company.currency) if row.debits else "",
                 format_money(row.credits, company.currency) if row.credits else ""]
                for row in company.ledger.trial_balance()
            ],
            numeric_columns=(3, 4),
        )
        profit = company.ledger.profit_and_loss()
        sheet = company.ledger.balance_sheet()
        for label, value in [
            ("Income", profit["income"]), ("Expense", profit["expense"]),
            ("Result", profit["result"]), ("Assets", sheet["assets"]),
            ("Liabilities", sheet["liabilities"]), ("Equity", sheet["equity"]),
            ("Balance sheet difference", sheet["difference"]),
        ]:
            self._position_values[label].setText(format_money(value, company.currency))

    # -- capacity ---------------------------------------------------------------- #
    def _planning_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        run = QPushButton("Schedule the designed elements")
        run.clicked.connect(self.run_schedule)
        layout.addWidget(run)

        self.schedule_table = DataTable(
            ["Operation", "Work centre", "Start", "Finish", "Hours"]
        )
        layout.addWidget(self.schedule_table, 1)
        self.load_table = DataTable(
            ["Code", "Work centre", "Hours", "Available", "Utilisation"]
        )
        layout.addWidget(self.load_table, 1)

        note = QLabel(
            "Finite capacity: a saw already committed to three jobs pushes the "
            "fourth out, which is what happens on the floor whether or not the "
            "plan says so. The week runs Sunday to Thursday with Friday a half "
            "day, and the glass lead time runs beside the shop work rather than "
            "after it."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def run_schedule(self) -> None:
        from ..erp import DEFAULT_WORK_CENTRES, Scheduler, demand_from_builds

        if not self.session.builds:
            self.report(
                ProfileOSError("No elements have been designed yet"), "Nothing to schedule"
            )
            return
        demand = demand_from_builds(self.session.builds, "JOB")
        plan = Scheduler().schedule([demand])
        self.schedule_table.set_rows(
            [
                [str(op.operation), op.work_centre, op.start.isoformat(),
                 op.finish.isoformat(), f"{op.hours:.2f}" if op.hours else "—"]
                for op in sorted(plan.operations, key=lambda o: (o.start, o.operation))
            ],
            numeric_columns=(4,),
        )
        colours: dict[tuple[int, int], str] = {}
        rows = plan.utilisation(DEFAULT_WORK_CENTRES)
        for index, row in enumerate(rows):
            colours[(index, 4)] = (
                self.colours.danger if row["utilisation_pct"] > 90
                else self.colours.warning if row["utilisation_pct"] > 70
                else self.colours.success
            )
        self.load_table.set_rows(
            [
                [r["code"], r["name"], f"{r['hours']:.1f}",
                 f"{r['available']:.1f}", f"{r['utilisation_pct']:.0f}%"]
                for r in rows
            ],
            numeric_columns=(2, 3, 4), colours=colours,
        )
        finish = plan.completion[demand.job_id]
        bottleneck = plan.bottleneck(DEFAULT_WORK_CENTRES)
        self.status(
            f"Complete {finish.isoformat()} ({finish.strftime('%A')})"
            + (f" · bottleneck {bottleneck['name']}" if bottleneck else "")
        )

    # -- audit -------------------------------------------------------------------- #
    def run_audit(self) -> None:
        from ..erp import format_money

        company = self._company()
        try:
            report = company.audit()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "The books and the racks disagree")
            return

        summary = company.summary()
        self.stats.update_many({
            "stock": (format_money(summary["stock_value"], company.currency), ""),
            "debtors": (format_money(summary["debtors"], company.currency), "owed to us"),
            "creditors": (format_money(summary["creditors"], company.currency), "we owe"),
            "result": (format_money(summary["result"], company.currency), "this period"),
            "entries": (str(report["entries"]), f"{report['movements']} movements"),
        })
        self._refresh_stock()
        self._refresh_ledger()
        self.status(
            "Ledger balanced, stock reconciled against its movement history, "
            "stock accounts agree with the stock book."
        )

    def refresh(self) -> None:
        self._refresh_stock()
        self._refresh_ledger()


PAGES: list[type[Page]] = [
    ProfilePage, ElementPage, ViewPage, NestingPage, GlassPage, MachiningPage,
    QuotePage, AccountsPage, ShopFloorPage, CataloguePage, SystemPage,
]

__all__ = [
    "Page", "ProfilePage", "ElementPage", "ViewPage", "NestingPage",
    "GlassPage", "MachiningPage", "QuotePage", "AccountsPage",
    "ShopFloorPage", "CataloguePage", "SystemPage", "PAGES",
]
