"""A capability matrix ProfileOS cannot lie in.

Every claim this module makes about ProfileOS is bound to a real symbol in the
codebase. :func:`verify_claims` imports each one, and the test suite fails if a
single claimed capability has no code behind it. Delete the 2D nester and the
comparison stops claiming 2D nesting in the same commit — which is the only way
a document like this stays true past the week it was written.

Claims about other packages work differently and are weaker on purpose. They
record what the vendor documents publicly, with the page it was read from, and
default to :attr:`Support.UNKNOWN` rather than "no". Nothing here has been
tested against a competitor's installation, and absence from a marketing page
is not absence from a product. Where a package is marked as not having
something, that means it is not documented publicly — not that it is missing.

The comparison is therefore useful for one thing and misleading for another.
It is a fair map of what this suite covers. It is not a purchasing verdict, and
the honest caveats in :data:`STANDING_LIMITATIONS` are part of the output
rather than a footnote to it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class Support(StrEnum):
    """How well a package covers a capability."""

    #: Documented as a first-class feature.
    FULL = "full"
    #: Present but partial, or only through a separate paid module.
    PARTIAL = "partial"
    #: Not found in the vendor's public material. Not a claim of absence.
    NOT_DOCUMENTED = "not_documented"
    #: Not looked into.
    UNKNOWN = "unknown"


class Area(StrEnum):
    GEOMETRY = "geometry"
    STRUCTURAL = "structural"
    ELEMENTS = "elements"
    GLASS = "glass"
    OPTIMISATION = "optimisation"
    CNC = "cnc"
    SHOPFLOOR = "shopfloor"
    COMMERCIAL = "commercial"
    CATALOGUE = "catalogue"
    PLATFORM = "platform"
    ADJACENT = "adjacent"


@dataclass(frozen=True)
class Capability:
    """One thing a fabrication package may or may not do."""

    id: str
    area: Area
    name_en: str
    name_he: str
    detail: str
    #: Dotted path to the symbol that implements this in ProfileOS. Empty when
    #: ProfileOS does not have the capability — which is itself a claim the
    #: matrix has to be able to make.
    probe: str = ""
    #: Set when the capability is genuinely unusual rather than table stakes.
    differentiator: bool = False

    @property
    def implemented(self) -> bool:
        return bool(self.probe)


@dataclass(frozen=True)
class Package:
    """A competing product, and what its vendor documents."""

    id: str
    name: str
    vendor: str
    origin: str
    #: Column heading for a terminal table, where the full name will not fit.
    short: str = ""
    note: str = ""
    source: str = ""
    support: dict[str, Support] = field(default_factory=dict)

    @property
    def heading(self) -> str:
        return self.short or self.name

    def level(self, capability_id: str) -> Support:
        return self.support.get(capability_id, Support.UNKNOWN)


# --------------------------------------------------------------------------- #
# What a fabrication suite can be asked to do
# --------------------------------------------------------------------------- #
CAPABILITIES: tuple[Capability, ...] = (
    # -- geometry ----------------------------------------------------------- #
    Capability(
        "dxf_import", Area.GEOMETRY,
        "DXF profile import", "ייבוא חתכים מ-DXF",
        "Read a supplier's cross-section drawing and rebuild it as closed "
        "contours, resolving which rings are material and which are chambers.",
        probe="profileos.geometry:load_section",
    ),
    Capability(
        "arc_geometry", Area.GEOMETRY,
        "Bulge-encoded arcs", "קשתות מקודדות bulge",
        "Reconstruct polyline arcs exactly, including clockwise sweeps, rather "
        "than approximating them as chords.",
        probe="profileos.models.profile:bulge_to_arc",
    ),
    Capability(
        "wall_thickness", Area.GEOMETRY,
        "Wall-thickness scan", "סריקת עובי דופן",
        "Ray-cast the section to find its thinnest walls, which is where an "
        "extrusion fails and where a cutter breaks through.",
        probe="profileos.geometry.validation:measure_wall_thickness",
        differentiator=True,
    ),
    # -- structural ---------------------------------------------------------- #
    Capability(
        "section_properties", Area.STRUCTURAL,
        "Exact section properties", "תכונות חתך מדויקות",
        "Area, centroid, second moments and principal axes by exact line "
        "integrals over the contour, holes included, not by meshing.",
        probe="profileos.structural.green:section_moments",
    ),
    Capability(
        "plastic_moduli", Area.STRUCTURAL,
        "Plastic section moduli", "מודולי חתך פלסטיים",
        "Plastic neutral axis by equal-area bisection, and Z about both axes.",
        probe="profileos.structural.plastic:plastic_modulus_x",
        differentiator=True,
    ),
    Capability(
        "torsion_fea", Area.STRUCTURAL,
        "Torsion and warping by FEA", "פיתול ועיוות ב-FEA",
        "St Venant torsion constant J and warping constant C_w from a Tri6 "
        "finite-element solve, with a thin-walled Bredt fallback.",
        probe="profileos.structural.torsion:compute_torsion",
        differentiator=True,
    ),
    Capability(
        "member_checks", Area.STRUCTURAL,
        "EN 1999-1-1 member checks", "בדיקות לפי EN 1999-1-1",
        "Bending, deflection and span checks against the aluminium Eurocode, "
        "with the governing case named.",
        probe="profileos.structural.checks:check_member",
    ),
    # -- elements ------------------------------------------------------------ #
    Capability(
        "parametric_elements", Area.ELEMENTS,
        "Parametric windows and doors", "חלונות ודלתות פרמטריים",
        "Free mullion and transom positions, per-cell sashes, and a cut list "
        "generated from system rules rather than typed.",
        probe="profileos.elements.builder:ElementBuilder",
    ),
    Capability(
        "curtain_wall", Area.ELEMENTS,
        "Curtain walling", "קירות מסך",
        "Multi-bay grids on the same element model as a single window.",
        probe="profileos.elements.model:ElementKind",
    ),
    Capability(
        "system_rules", Area.ELEMENTS,
        "Editable system rules", "כללי מערכת ניתנים לעריכה",
        "Rebate depths, clearances and glazing deductions live in an editable "
        "rules plugin, not in the program.",
        probe="profileos.elements.rules:register_system_rules",
        differentiator=True,
    ),
    Capability(
        "3d_view", Area.ELEMENTS,
        "3D presentation views", "תצוגת תלת-ממד",
        "The element modelled in three dimensions from the same sections and "
        "the same rules that cut it, shown as a printable vector drawing, as "
        "glTF for any 3D tool, and as an interactive viewer that needs nothing "
        "installed.",
        probe="profileos.viz3d.scene:build_element_scene",
    ),
    Capability(
        "gltf_export", Area.ELEMENTS,
        "glTF export of the model", "ייצוא glTF של המודל",
        "The model itself, not a picture of it — so it drops into a visualiser, "
        "a BIM scene or an architect's own tool.",
        probe="profileos.viz3d.gltf:to_glb",
        differentiator=True,
    ),
    # -- glass --------------------------------------------------------------- #
    Capability(
        "glass_buildups", Area.GLASS,
        "Glass build-up library", "ספריית הרכבי זכוכית",
        "Panes, cavities, gases, spacers and coating positions as a modelled "
        "assembly rather than a text string.",
        probe="profileos.glazing.glass:GlassBuildUp",
    ),
    Capability(
        "u_value", Area.GLASS,
        "U-value to EN 673 / EN ISO 10077", "ערך U לפי EN 673 / 10077",
        "Centre-pane U from the radiative and gas conductances of each cavity, "
        "and whole-window U_w with the frame and the spacer's linear loss.",
        probe="profileos.glazing.glass:window_u_value",
        differentiator=True,
    ),
    Capability(
        "safety_glass", Area.GLASS,
        "Safety-glass compliance", "תאימות זכוכית בטיחות",
        "Flag panes that regulation requires to be safety glass but the "
        "specification is not.",
        probe="profileos.elements.builder:safety_glass_required",
    ),
    # -- optimisation --------------------------------------------------------- #
    Capability(
        "bar_nesting", Area.OPTIMISATION,
        "1D bar nesting", "אופטימיזציית חיתוך מוטות",
        "Cutting-stock optimisation over stock bars with kerf and mitre "
        "compensation.",
        probe="profileos.nesting.engine:nest",
    ),
    Capability(
        "nesting_optimality", Area.OPTIMISATION,
        "Proven-optimal cutting plans", "תוכנית חיתוך מוכחת אופטימלית",
        "Gilmore-Gomory column generation with an integer master, so the bar "
        "count is proved rather than asserted.",
        probe="profileos.nesting.milp:solve_column_generation",
        differentiator=True,
    ),
    Capability(
        "remnant_inventory", Area.OPTIMISATION,
        "Off-cut inventory", "מלאי שאריות",
        "Reusable off-cuts return to stock and are offered to the next job.",
        probe="profileos.nesting.inventory:RemnantInventory",
    ),
    Capability(
        "glass_nesting", Area.OPTIMISATION,
        "2D glass and panel nesting", "אופטימיזציית זכוכית ולוחות",
        "Sheet nesting with kerf, edge trim and grain direction, over the "
        "standard float plate sizes.",
        probe="profileos.nesting.sheet_engine:nest_sheets",
    ),
    Capability(
        "guillotine_proof", Area.OPTIMISATION,
        "Proven-cuttable glass layouts", "אימות חיתוך גיליוטינה",
        "Every sheet layout is checked for a real sequence of edge-to-edge "
        "cuts and reported with the number of stages it needs, so a layout no "
        "table can produce is caught before it reaches the table.",
        probe="profileos.nesting.guillotine:verify_guillotine",
        differentiator=True,
    ),
    # -- CNC ------------------------------------------------------------------ #
    Capability(
        "cnc_post", Area.CNC,
        "CNC post-processing", "פוסט-פרוססור CNC",
        "Machine programs generated from the element's machining features.",
        probe="profileos.cnc.job:MachiningJob",
    ),
    Capability(
        "machine_drivers", Area.CNC,
        "Multiple machine dialects", "ריבוי ניבי מכונה",
        "Elumatec, Schüco, Kaban and ISO G-code from one neutral feature "
        "representation, so a new machine is a driver, not a rewrite.",
        probe="profileos.cnc.drivers:available_drivers",
        differentiator=True,
    ),
    Capability(
        "clamp_collision", Area.CNC,
        "Clamp collision avoidance", "מניעת התנגשות מלחציים",
        "Detect where a machining position falls on a clamp and move the "
        "clamp, rather than discovering it on the machine.",
        probe="profileos.cnc.clamps:detect_collisions",
        differentiator=True,
    ),
    Capability(
        "cutter_comp", Area.CNC,
        "Cutter radius compensation", "פיצוי רדיוס כלי",
        "G41/G42 offsets emitted for the tool actually selected.",
        probe="profileos.cnc.toolpath:Toolpath",
    ),
    # -- shop floor ------------------------------------------------------------ #
    Capability(
        "job_cards", Area.SHOPFLOOR,
        "Shop-floor job cards", "כרטיסי עבודה",
        "Printable work packets per bar and per element.",
        probe="profileos.mes.jobcard:render_job_card",
    ),
    Capability(
        "barcodes", Area.SHOPFLOOR,
        "Barcodes and QR labels", "ברקוד ו-QR",
        "Code 128 and QR labels generated in-process, no label software.",
        probe="profileos.mes.barcode:code128_svg",
    ),
    Capability(
        "mes_tracking", Area.SHOPFLOOR,
        "Production tracking", "מעקב ייצור",
        "Scan a piece through the stations and see where the job actually is.",
        probe="profileos.mes.tracking:WorkOrder",
    ),
    Capability(
        "cutting_maps", Area.SHOPFLOOR,
        "Printed cutting maps", "מפות חיתוך מודפסות",
        "Self-contained SVG sheet maps with part marks, sizes and off-cuts.",
        probe="profileos.nesting.sheet_render:render_layout_svg",
    ),
    # -- commercial ------------------------------------------------------------ #
    Capability(
        "bom", Area.COMMERCIAL,
        "Bill of materials", "כתב כמויות",
        "Profiles, glass, gaskets and hardware rolled up per project.",
        probe="profileos.quoting.bom:build_bom",
    ),
    Capability(
        "quotations", Area.COMMERCIAL,
        "Customer quotations", "הצעות מחיר ללקוח",
        "Priced quotations off the bill of materials, in the operator's brand.",
        probe="profileos.quoting.pricing:build_quotation",
    ),
    Capability(
        "supplier_rfq", Area.COMMERCIAL,
        "Supplier enquiries", "בקשות מחיר לספקים",
        "Split the bill of materials by supplier and produce an enquiry per "
        "supplier.",
        probe="profileos.quoting.suppliers:find_price",
    ),
    Capability(
        "price_lists", Area.COMMERCIAL,
        "Hot-swappable price lists", "מחירונים מתחלפים חמים",
        "A price list is a signed data plugin, so a supplier increase goes "
        "live without a release.",
        probe="profileos.quoting.suppliers:PRICE_LIST_SCHEMA",
        differentiator=True,
    ),
    # -- catalogue -------------------------------------------------------------- #
    Capability(
        "catalogue_library", Area.CATALOGUE,
        "Supplier profile library", "ספריית פרופילים של ספקים",
        "A library of profile systems with geometry, weights and prices.",
        probe="profileos.models.profile:ProfileDefinition",
    ),
    Capability(
        "catalogue_ingestion", Area.CATALOGUE,
        "Catalogue ingestion from PDF and DXF", "קליטת קטלוגים מ-PDF ו-DXF",
        "Read the supplier's own published table and drawing pack into a "
        "library the fabricator owns, instead of subscribing to the vendor's "
        "copy of it.",
        probe="profileos.catalogue.ingest:ingest",
        differentiator=True,
    ),
    Capability(
        "catalogue_verification", Area.CATALOGUE,
        "Catalogue cross-verification", "אימות צולב של קטלוגים",
        "Set every published figure against the same figure measured from the "
        "drawing, and report the disagreements instead of picking a winner.",
        probe="profileos.catalogue.ingest:cross_check",
        differentiator=True,
    ),
    # -- platform ---------------------------------------------------------------- #
    Capability(
        "self_update", Area.PLATFORM,
        "Signed self-update", "עדכון עצמי חתום",
        "Signed manifests, atomic staged install, validation before execution "
        "and rollback on any failure in the batch.",
        probe="profileos.updates.engine:UpdateEngine",
        differentiator=True,
    ),
    Capability(
        "hot_reload", Area.PLATFORM,
        "Plugin hot reload", "טעינה חמה של תוספים",
        "New profile systems, rules and price lists go live without a "
        "restart, after their source passes an AST policy check.",
        probe="profileos.core.hotreload:HotReloadManager",
        differentiator=True,
    ),
    Capability(
        "offline_licence", Area.PLATFORM,
        "Offline licensing", "רישוי לא מקוון",
        "AES-256-GCM licences bound to a hardware fingerprint, with a grace "
        "period that degrades to read-only rather than locking the shop out.",
        probe="profileos.security.license:load_license",
        differentiator=True,
    ),
    Capability(
        "webauthn", Area.PLATFORM,
        "Hardware-key authentication", "אימות במפתח חומרה",
        "WebAuthn/FIDO2 with full attestation verification and signature "
        "counter clone detection.",
        probe="profileos.security.webauthn:WebAuthnServer",
        differentiator=True,
    ),
    Capability(
        "rest_api", Area.PLATFORM,
        "REST API", "ממשק REST",
        "The engines are reachable over HTTP for an ERP to drive.",
        probe="profileos.api.server:app",
    ),
    Capability(
        "cli", Area.PLATFORM,
        "Scriptable command line", "שורת פקודה לתסריטים",
        "Every engine is scriptable, so a nightly re-nest is a cron line.",
        probe="profileos.cli:app",
        differentiator=True,
    ),
    Capability(
        "hebrew_rtl", Area.PLATFORM,
        "Hebrew and right-to-left", "עברית וימין-לשמאל",
        "Hebrew branding, documents and interface direction as a first-class "
        "case rather than a translation layer.",
        probe="profileos.branding:DADI_BRAND",
        differentiator=True,
    ),
    Capability(
        "source_available", Area.PLATFORM,
        "Source available to the operator", "קוד מקור בידי המפעיל",
        "The fabricator holds the source, so the software cannot be "
        "discontinued out from under the shop.",
        probe="profileos:__version__",
        differentiator=True,
    ),
    # -- adjacent ------------------------------------------------------------------ #
    Capability(
        "plumbing", Area.ADJACENT,
        "Pipework sizing", "תכנון צנרת",
        "Darcy-Weisbach and Hazen-Williams pipe sizing with Hardy Cross loop "
        "balancing, for the plumbing that goes in alongside the aluminium.",
        probe="profileos.plumbing.network:PipeNetwork",
        differentiator=True,
    ),
    Capability(
        "erp", Area.ADJACENT,
        "Stock, purchasing and the ledger", "מלאי, רכש והנהלת חשבונות",
        "Purchase orders, goods receipts, stock movements valued FIFO or "
        "weighted average, sales invoicing with statutory VAT, and a "
        "double-entry general ledger every document posts to.",
        probe="profileos.erp.company:Company",
    ),
    Capability(
        "three_way_match", Area.ADJACENT,
        "Three-way invoice matching", "התאמה משולשת של חשבוניות",
        "A supplier invoice is set against the order and the goods actually "
        "received before it can be posted, and the leg that failed is named.",
        probe="profileos.erp.purchasing:three_way_match",
        differentiator=True,
    ),
    Capability(
        "books_audit", Area.ADJACENT,
        "Books reconciled to the racks", "התאמת ספרים למלאי בפועל",
        "The stock value is re-derived from the movement history, the trial "
        "balance from the postings, and the stock accounts checked against the "
        "stock book — three records of the same facts, cross-checked.",
        probe="profileos.erp.company:Company.audit",
        differentiator=True,
    ),
    Capability(
        "capacity_planning", Area.ADJACENT,
        "Capacity and delivery planning", "תכנון קיבולת ומועדי אספקה",
        "Finite-capacity scheduling across work centres on the shop's own "
        "working week, so a promised date is arithmetic rather than optimism, "
        "and a job that will be late says so.",
        probe="profileos.erp.scheduling:Scheduler",
    ),
)

CAPABILITY_BY_ID = {capability.id: capability for capability in CAPABILITIES}


# --------------------------------------------------------------------------- #
# The other packages
# --------------------------------------------------------------------------- #
F, P, N = Support.FULL, Support.PARTIAL, Support.NOT_DOCUMENTED
U = Support.UNKNOWN

PACKAGES: tuple[Package, ...] = (
    Package(
        "logikal", "LogiKal", "Orgadata", "Germany",
        short="LogiK",
        note="The reference product. Around 700 suppliers and seven million "
             "articles in its maintained catalogue — decades of curation that "
             "no amount of engineering substitutes for.",
        source="https://www.orgadata.com/global/en/solutions/logikal/modules-in-logikal.html",
        support={
            "catalogue_verification": N, "nesting_optimality": N,
            "guillotine_proof": N, "torsion_fea": N, "plastic_moduli": N,
            "hot_reload": N, "webauthn": N, "cli": N, "offline_licence": P,
            "wall_thickness": N,
            "dxf_import": F, "section_properties": P, "member_checks": P,
            "parametric_elements": F, "curtain_wall": F, "3d_view": F,
            "glass_buildups": F, "u_value": F, "safety_glass": P,
            "bar_nesting": F, "remnant_inventory": F, "glass_nesting": F,
            "cnc_post": F, "machine_drivers": F, "cutter_comp": F,
            "job_cards": F, "barcodes": F, "cutting_maps": F,
            "bom": F, "quotations": F, "supplier_rfq": F, "price_lists": F,
            "catalogue_library": F, "erp": P, "capacity_planning": P,
            "three_way_match": U, "books_audit": N, "gltf_export": U,
            "rest_api": P, "catalogue_ingestion": N, "self_update": F,
            "hebrew_rtl": N, "source_available": N, "plumbing": N,
        },
    ),
    Package(
        "klaes", "Klaes", "Horst Klaes GmbH", "Germany",
        short="Klaes",
        note="Window-industry ERP with a 3D façade and conservatory module; "
             "mixes aluminium, timber and PVC in one order.",
        source="https://www.klaes.de/en-product-lines",
        support={
            "catalogue_verification": N, "nesting_optimality": N,
            "guillotine_proof": N, "torsion_fea": N, "plastic_moduli": N,
            "hot_reload": N, "webauthn": N, "cli": N, "offline_licence": P,
            "parametric_elements": F, "curtain_wall": F, "3d_view": F,
            "glass_buildups": F, "u_value": F, "bar_nesting": F,
            "glass_nesting": F, "cnc_post": F, "machine_drivers": F,
            "job_cards": F, "barcodes": F, "bom": F, "quotations": F,
            "supplier_rfq": F, "price_lists": F, "catalogue_library": F,
            "erp": F, "capacity_planning": F, "mes_tracking": F,
            "three_way_match": U, "books_audit": N, "gltf_export": U,
            "catalogue_ingestion": N, "hebrew_rtl": N, "source_available": N,
            "plumbing": N,
        },
    ),
    Package(
        "prefsuite", "PrefSuite", "Preference", "Spain",
        short="PrefS",
        note="Strong on production data and shop-floor execution.",
        source="https://www.preference.es/",
        support={
            "catalogue_verification": N, "nesting_optimality": N,
            "guillotine_proof": N, "torsion_fea": N, "hot_reload": N,
            "webauthn": N, "cli": N, "offline_licence": P,
            "parametric_elements": F, "curtain_wall": F, "glass_buildups": F,
            "bar_nesting": F, "glass_nesting": F, "cnc_post": F,
            "machine_drivers": F, "job_cards": F, "barcodes": F,
            "mes_tracking": F, "bom": F, "quotations": F, "erp": F,
            "capacity_planning": F, "catalogue_library": F,
            "three_way_match": U, "books_audit": N, "gltf_export": U,
            "catalogue_ingestion": N, "hebrew_rtl": N, "source_available": N,
            "plumbing": N,
        },
    ),
    Package(
        "schucal", "SchüCal", "Schüco", "Germany",
        short="SchüC",
        note="System-house software. Excellent inside the Schüco range and "
             "not intended to leave it.",
        source="https://www.schueco.com/",
        support={
            "catalogue_verification": N, "guillotine_proof": N,
            "torsion_fea": N, "hot_reload": N, "webauthn": N, "cli": N,
            "parametric_elements": F, "curtain_wall": F, "3d_view": P,
            "glass_buildups": F, "u_value": F, "member_checks": P,
            "bar_nesting": F, "cnc_post": F, "machine_drivers": P,
            "bom": F, "quotations": F, "catalogue_library": F,
            "catalogue_ingestion": N, "hebrew_rtl": N, "source_available": N,
            "plumbing": N,
        },
    ),
    Package(
        "reynapro", "ReynaPro", "Reynaers Aluminium", "Belgium",
        short="Reyna",
        note="System-house software for the Reynaers range.",
        source="https://www.reynaers.com/",
        support={
            "catalogue_verification": N, "guillotine_proof": N,
            "torsion_fea": N, "hot_reload": N, "webauthn": N, "cli": N,
            "parametric_elements": F, "curtain_wall": F, "3d_view": F,
            "glass_buildups": F, "u_value": F, "bar_nesting": F,
            "cnc_post": F, "bom": F, "quotations": F, "catalogue_library": F,
            "catalogue_ingestion": N, "hebrew_rtl": N, "source_available": N,
            "plumbing": N,
        },
    ),
    Package(
        "elucad", "eluCad", "Elumatec", "Germany",
        short="eluCad",
        note="Machine-side CAM for Elumatec centres: the strongest link "
             "between a drawing and that manufacturer's spindle.",
        source="https://www.elumatec.com/",
        support={
            "catalogue_verification": N, "torsion_fea": N, "hot_reload": N,
            "webauthn": N, "cli": N,
            "dxf_import": F, "cnc_post": F, "machine_drivers": P,
            "clamp_collision": F, "cutter_comp": F, "job_cards": P,
            "bar_nesting": P, "parametric_elements": P,
            "catalogue_ingestion": N, "hebrew_rtl": N, "source_available": N,
            "plumbing": N, "erp": N, "quotations": N,
        },
    ),
    Package(
        "kaban", "KBN Control", "Kaban Makina", "Turkey",
        short="KBN",
        note="Machine controller and its own program format.",
        source="https://www.kabanmakina.com.tr/",
        support={
            "cnc_post": F, "machine_drivers": P, "clamp_collision": P,
            "job_cards": P, "catalogue_ingestion": N, "hebrew_rtl": N,
            "source_available": N, "plumbing": N, "erp": N,
        },
    ),
    Package(
        "shablul", "Shablul", "Israeli vendor", "Israel",
        short="Shabl",
        note="Israeli market software for aluminium fabricators; Hebrew "
             "interface and local commercial practice.",
        source="",
        support={
            "catalogue_verification": N, "torsion_fea": N, "guillotine_proof": N,
            "hot_reload": N, "webauthn": N, "cli": N,
            "parametric_elements": F, "quotations": F, "bom": F,
            "bar_nesting": F, "hebrew_rtl": F, "catalogue_library": P,
            "source_available": N, "catalogue_ingestion": N, "plumbing": N,
        },
    ),
    Package(
        "alkal", "Al-Kal", "Israeli vendor", "Israel",
        short="AlKal",
        note="Israeli estimating and production software for aluminium.",
        source="",
        support={
            "catalogue_verification": N, "torsion_fea": N, "guillotine_proof": N,
            "hot_reload": N, "webauthn": N, "cli": N,
            "parametric_elements": F, "quotations": F, "bom": F,
            "bar_nesting": F, "hebrew_rtl": F, "catalogue_library": P,
            "source_available": N, "catalogue_ingestion": N, "plumbing": N,
        },
    ),
)


#: Things that stay true no matter how the matrix is read, and that a
#: fabricator has to weigh before treating this suite as a replacement.
STANDING_LIMITATIONS: tuple[str, ...] = (
    "The bundled profile catalogues are examples. LogiKal ships around 700 "
    "suppliers' catalogues, verified and maintained over decades; the "
    "ingestion engine here lets a shop build its own library from what its "
    "suppliers publish, but the library still has to be built.",
    "The proprietary CNC formats — Elumatec NCX/ECX/NCW/DGX, Schüco MCO, "
    "Kaban KBN — have never been cut on a physical machine from this "
    "software. Prove them on scrap before production.",
    "The ledger is bookkeeping, not certified bookkeeping. Israeli law sets "
    "requirements for computerised accounting records and for issuing tax "
    "invoices, and nothing here has been submitted for approval against them. "
    "Treat it as the shop's own management accounts and keep issuing "
    "statutory documents from whatever is already approved, until it is.",
    "The standard times the scheduler works from are starting values, not "
    "measurements of your shop. A promised date is only as good as the "
    "minutes-per-cut behind it, so time a few real jobs and set them.",
    "Nothing in this comparison has been tested against a competitor's "
    "installation. Competitor rows record public documentation only, and "
    "\"not documented\" never means \"absent\".",
)


# --------------------------------------------------------------------------- #
# Verification and reporting
# --------------------------------------------------------------------------- #
def resolve_probe(probe: str) -> object:
    """Import the symbol a capability claims to be implemented by.

    The attribute half may be dotted — ``module:Class.method`` — because a
    capability is often one method rather than a whole class, and pinning the
    claim to the method is what makes deleting it break the claim.
    """
    module_path, _, attribute = probe.partition(":")
    resolved: object = importlib.import_module(module_path)
    for part in attribute.split(".") if attribute else []:
        resolved = getattr(resolved, part)
    return resolved


def verify_claims(
    capabilities: Iterable[Capability] = CAPABILITIES,
) -> dict[str, str]:
    """Check every claimed capability against the code. Empty means all hold.

    Returns the failures, keyed by capability id. A capability with no probe
    is not claimed and is not checked — that is the matrix admitting a gap,
    which it must be able to do for the rest of it to mean anything.
    """
    failures: dict[str, str] = {}
    for capability in capabilities:
        if not capability.probe:
            continue
        try:
            resolve_probe(capability.probe)
        except (ImportError, AttributeError) as exc:
            failures[capability.id] = f"{capability.probe}: {exc}"
    return failures


def profileos_support(capability: Capability) -> Support:
    return Support.FULL if capability.implemented else Support.NOT_DOCUMENTED


def coverage(package: Package | None = None) -> dict[str, int]:
    """Count capabilities by support level for one package, or for ProfileOS."""
    counts = {level: 0 for level in Support}
    for capability in CAPABILITIES:
        level = (
            profileos_support(capability)
            if package is None
            else package.level(capability.id)
        )
        counts[level] += 1
    return {str(level): count for level, count in counts.items()}


def matrix() -> list[dict[str, object]]:
    """The whole comparison as flat rows, ready for a table or a web page."""
    rows: list[dict[str, object]] = []
    for capability in CAPABILITIES:
        row: dict[str, object] = {
            "id": capability.id,
            "area": str(capability.area),
            "name_en": capability.name_en,
            "name_he": capability.name_he,
            "detail": capability.detail,
            "differentiator": capability.differentiator,
            "profileos": str(profileos_support(capability)),
        }
        for package in PACKAGES:
            row[package.id] = str(package.level(capability.id))
        rows.append(row)
    return rows


def not_documented_elsewhere() -> list[Capability]:
    """Capabilities ProfileOS has that no compared package documents.

    A capability only qualifies if at least one package was actually looked
    into and came back :attr:`Support.NOT_DOCUMENTED`. Without that condition
    the list would fill up with things nobody checked — ordinary capabilities
    that every package almost certainly has — and calling those a distinction
    would be flattering the wrong way.
    """
    qualifying: list[Capability] = []
    for capability in CAPABILITIES:
        if not capability.implemented:
            continue
        levels = [package.level(capability.id) for package in PACKAGES]
        if any(level in (Support.FULL, Support.PARTIAL) for level in levels):
            continue
        if Support.NOT_DOCUMENTED not in levels:
            continue  # nobody checked; silence is not evidence
        qualifying.append(capability)
    return qualifying


def missing_from_profileos() -> list[Capability]:
    """Capabilities some compared package documents and ProfileOS lacks."""
    return [
        capability
        for capability in CAPABILITIES
        if not capability.implemented
        and any(
            package.level(capability.id) in (Support.FULL, Support.PARTIAL)
            for package in PACKAGES
        )
    ]


def summary() -> dict[str, object]:
    return {
        "capabilities": len(CAPABILITIES),
        "packages_compared": len(PACKAGES),
        "profileos_implemented": sum(1 for c in CAPABILITIES if c.implemented),
        "profileos_gaps": len(missing_from_profileos()),
        "not_documented_elsewhere": len(not_documented_elsewhere()),
        "claims_verified": not verify_claims(),
        "standing_limitations": len(STANDING_LIMITATIONS),
    }


__all__ = [
    "Support",
    "Area",
    "Capability",
    "Package",
    "CAPABILITIES",
    "CAPABILITY_BY_ID",
    "PACKAGES",
    "STANDING_LIMITATIONS",
    "resolve_probe",
    "verify_claims",
    "profileos_support",
    "coverage",
    "matrix",
    "not_documented_elsewhere",
    "missing_from_profileos",
    "summary",
]
