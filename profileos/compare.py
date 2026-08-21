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
        "type_library", Area.ELEMENTS,
        "Spoken type library", "ספריית טיפוסים בדיבור",
        "Every opening type the trade makes, at any size, leaf count and "
        "series — found by saying it: \"הזזה 4 כנפיים 6000/2200 קליל 9000\". "
        "Sizes are generated, not stored, so nothing is missing from the list, "
        "and centimetres, metres and millimetres are all understood.",
        probe="profileos.library:search_openings",
        differentiator=True,
    ),
    Capability(
        "shutters", Area.ELEMENTS,
        "Rolling shutters sized from the roll", "תריסים לפי גליל אמיתי",
        "The box comes from the wound coil diameter, not a rule of thumb: the "
        "shaft, the motor torque, the curtain weight and the structural "
        "opening the builder has to leave all fall out of the same "
        "calculation, and they are quoted with the window rather than on a "
        "separate sheet.",
        probe="profileos.accessories.shutters:size_shutter",
        differentiator=True,
    ),
    Capability(
        "screens_sills", Area.ELEMENTS,
        "Screens, sills and trims", "רשתות, אדנים ומסגרות",
        "Insect screens split into leaves that still slide, sills checked for "
        "fall and projection, and perimeter trims — each sized from the "
        "opening and each on the bill of materials.",
        probe="profileos.accessories.screens:size_screen",
        differentiator=True,
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
    Capability(
        "whole_window_u", Area.ELEMENTS,
        "Whole-window U-value", "⁦U_w⁩ של הפתח כולו",
        "The frame, the glass and the edge weighted by the areas of the "
        "element that was actually drawn, per EN ISO 10077-1 — so six small "
        "panes correctly report worse than one large one, which is the "
        "trade-off the divisions are making.",
        probe="profileos.compliance.thermal:window_u_value",
        differentiator=True,
    ),
    Capability(
        "acoustic_estimate", Area.ELEMENTS,
        "Sound reduction estimate", "אומדן ⁦R_w⁩ מותקן",
        "The glazing by mass law with the coincidence and lamination "
        "corrections, then penalised for how the window opens, how it seals "
        "and whether a shutter box sits above it — the installed figure, not "
        "the glass laboratory's.",
        probe="profileos.compliance.acoustic:estimate_acoustic",
        differentiator=True,
    ),
    Capability(
        "wind_and_classes", Area.ELEMENTS,
        "Wind case and required classes", "לחץ רוח וסיווגים נדרשים",
        "Design pressure from velocity, terrain, height and facade zone — a "
        "corner takes far more suction than the middle of the same wall — and "
        "the air, water and wind classes to demand of the supplier. The basic "
        "velocity is never invented: without a recorded source the pressure "
        "is reported as unverified.",
        probe="profileos.compliance.wind:design_pressure",
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
        "plumbing_fixtures", Area.ADJACENT,
        "Fixture units and simultaneous demand", "יחידות עומס וספיקה בו-זמנית",
        "Count what is connected and size the main from it: loading units "
        "through the Hunter demand curve for the supply, drainage fixture "
        "units for the waste, with cistern and flush-valve buildings on "
        "separate curves.",
        probe="profileos.plumbing.fixtures:FixtureSchedule",
        differentiator=True,
    ),
    Capability(
        "plumbing_drainage", Area.ADJACENT,
        "Drainage and vent sizing", "תכנון דלוחין ואוורור",
        "Branch, stack, vent and house drain sized from fixture units against "
        "fall, with the rules that beat the tables enforced: never reduce "
        "downstream, never below the largest trap, and a WC gets 100 mm.",
        probe="profileos.plumbing.drainage:design_drainage",
        differentiator=True,
    ),
    Capability(
        "plumbing_hot_water", Area.ADJACENT,
        "Hot water circulation and dead legs", "מחזור מים חמים וזנבות",
        "Heat loss through the insulation, the circulation flow that carries "
        "it, the return pipe and pump duty that follow, and the uncirculated "
        "tail checked against how long somebody waits at the tap.",
        probe="profileos.plumbing.hotwater:design_circulation",
        differentiator=True,
    ),
    Capability(
        "plumbing_takeoff", Area.ADJACENT,
        "Plumbing materials take-off", "כתב כמויות לאינסטלציה",
        "Pipe counted in stock lengths rather than metres, insulation counted "
        "per run, fittings and valves gathered by size, and the waste "
        "allowance stated as its own line rather than folded into a quantity.",
        probe="profileos.plumbing.takeoff:take_off",
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
        "israeli_tax_documents", Area.COMMERCIAL,
        "Israeli tax documents", "מסמכי מס ישראליים",
        "חשבונית מס, תעודת משלוח, קבלה and credit notes on the shop's own "
        "paper, with the עוסק מורשה number, the שוטף+ terms calculated from "
        "the end of the month of invoice, and the Tax Authority allocation "
        "number checked before the document is issued rather than after the "
        "customer refuses to pay it.",
        probe="profileos.erp.israel:render_document",
        differentiator=True,
    ),
    Capability(
        "hebrew_calendar", Area.PLATFORM,
        "The Israeli working year", "לוח השנה העברי בתכנון",
        "The Hebrew calendar computed rather than typed in: the working week "
        "runs Sunday to Thursday, festival eves are short days, חול המועד is "
        "a thin one, and no schedule ever promises a delivery on Yom Kippur "
        "or counts the fortnight of Tishrei as ordinary weeks.",
        probe="profileos.hebrew_calendar:holidays_for_hebrew_year",
        differentiator=True,
    ),
    Capability(
        "service_register", Area.COMMERCIAL,
        "Service calls and warranty", "קריאות שירות ואחריות",
        "The window a year later: what came back, whether it is still under "
        "warranty counted from handover, and — because every call records a "
        "cause — which faults recur often enough to be a message to the "
        "workshop rather than to the fitter.",
        probe="profileos.service.register:ServiceRegister",
        differentiator=True,
    ),
    Capability(
        "cheque_book", Area.COMMERCIAL,
        "Post-dated cheque register", "ספר צ׳קים דחויים",
        "How customers here actually pay: five cheques over five months, "
        "which can be banked and which cannot, what clears each week, and "
        "whose cheques come back. Never posted as revenue, because a promise "
        "booked as revenue hides a bad debtor.",
        probe="profileos.erp.collection:ChequeBook",
        differentiator=True,
    ),
    Capability(
        "job_costing", Area.COMMERCIAL,
        "Live job profitability", "רווחיות בזמן אמת",
        "One job read from four sides at once — quoted, committed, invoiced "
        "and returned to — while it is still open and something can be done "
        "about it. A side with no data is declared rather than assumed to be "
        "zero.",
        probe="profileos.projects.costing:cost_job",
        differentiator=True,
    ),
    Capability(
        "coating_area", Area.CATALOGUE,
        "Coating area from the section", "שטח צביעה מהחתך עצמו",
        "Anodising and paint are charged by the square metre, and the square "
        "metre is measured off the outside of the imported section — not from "
        "a factor, and not from the wetted perimeter, which on a thermally "
        "broken profile is more than double and includes chambers no bath "
        "reaches.",
        probe="profileos.finishing:coating_area_per_metre",
        differentiator=True,
    ),
    Capability(
        "packing_list", Area.SHOPFLOOR,
        "Loading list in fitting order", "רשימת העמסה בסדר ההרכבה",
        "The lorry loaded so the first unit off is the first one wanted: by "
        "floor, then by the site's own order, heaviest last on so it comes "
        "off first — with the carry each unit needs and the load split "
        "against the vehicle's real payload.",
        probe="profileos.delivery.packing:pack",
        differentiator=True,
    ),
    Capability(
        "installation_plan", Area.SHOPFLOOR,
        "Installation planning", "תכנון ימי הרכבה",
        "Fitting time per unit by site condition and access — a renovation "
        "with stairs is not a new build with a lift — laid on real working "
        "days, so a short Friday is never given eight hours of work and a "
        "crew too small for a lift-slide is caught before the van leaves.",
        probe="profileos.delivery.installation:plan_installation",
        differentiator=True,
    ),
    Capability(
        "job_attachments", Area.COMMERCIAL,
        "Photographs and papers on the job", "צילומים ומסמכים בתיק",
        "The photograph that settles who pays, kept in the job rather than on "
        "a fitter's phone: copied into the job's own folder as ordinary "
        "files, checksummed so a signed document replaced after filing is "
        "visible, and refused deletion when it is evidence.",
        probe="profileos.projects.attachments:AttachmentStore",
    ),
    Capability(
        "readiness_report", Area.PLATFORM,
        "Says what it cannot do yet", "אומרת מה היא עדיין לא יכולה",
        "Every package in this trade is complete only once the shop's own "
        "facts are in it. This one says which of them are missing, what each "
        "gap blocks, and how to close it — so a fresh installation reports "
        "that no bar may be cut from it yet instead of looking finished and "
        "quietly pricing on stand-in figures.",
        probe="profileos.readiness:readiness",
        differentiator=True,
    ),
    Capability(
        "series_confirmation", Area.CATALOGUE,
        "Eleven numbers to make a series cuttable", "אישור סדרה בהזנה אחת",
        "The gap between quoting and cutting is the supplier's own deductions, "
        "and no software can invent them. This one asks for exactly eleven "
        "figures in catalogue order, each with where to find it, checks them "
        "against what is physically possible, catches a transposed pair, and "
        "records the source — after which the series is cuttable and the "
        "not-for-production banner comes off its sheets.",
        probe="profileos.systems.confirmation:Confirmation",
        differentiator=True,
    ),
    Capability(
        "hardware_by_load", Area.ELEMENTS,
        "Hardware chosen by sash weight", "פרזול לפי משקל הכנף",
        "The commonest warranty call in the trade is a hinge one size down. "
        "Parts here carry a rating, a leaf size and a source; selection takes "
        "the lightest that carries the leaf, refuses anything unrated, and "
        "when nothing fits says whether it was the weight or the width — "
        "rather than returning the largest thing on the shelf.",
        probe="profileos.hardware.library:HardwareLibrary",
        differentiator=True,
    ),
    Capability(
        "migration_import", Area.PLATFORM,
        "Import from what the shop uses now", "ייבוא ממה שיש היום",
        "Nobody retypes four hundred customers. Exports come across as CSV — "
        "including the windows-1255 Excel on a Hebrew Windows actually "
        "writes, the title lines above the header, and column names spelled "
        "three different ways — and every import shows what it would do, "
        "which column fed which field and which rows it will skip and why, "
        "before it writes anything.",
        probe="profileos.migration.importers:plan_customers",
        differentiator=True,
    ),
    Capability(
        "shared_folder", Area.PLATFORM,
        "Two people, one folder", "שני מחשבים, תיקייה אחת",
        "The data stays as files the shop can copy and back up with anything, "
        "and a lock beside them stops two estimators' saves overlapping. A "
        "lock left by a machine that went away expires rather than holding "
        "the shop up, and a write built on data somebody else has since "
        "changed is refused instead of silently erasing their work.",
        probe="profileos.core.sharing:guarded",
    ),
    Capability(
        "machine_proving", Area.CNC,
        "Proving record for posted programs", "רישום הוכחת קוד מכונה",
        "No program from here has been cut on a real machine, and that is "
        "said on every posted file. It stops being said one pair at a time: "
        "a named person cuts on scrap, measures, and records what they "
        "found — and the banner comes off for that post-processor on that "
        "machine only, because proving the Elumatec says nothing about the "
        "Emmegi beside it.",
        probe="profileos.cnc.proving:ProvingRecord",
        differentiator=True,
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

#: The same caveats, for the Hebrew interface. Kept in the same order as
#: :data:`STANDING_LIMITATIONS` — one is a translation of the other, and a
#: caveat that exists in only one language is a bug.
STANDING_LIMITATIONS_HE: tuple[str, ...] = (
    "קטלוגי הפרופילים המצורפים הם דוגמאות. LogiKal מגיעה עם קטלוגים של "
    "כ-700 ספקים, מאומתים ומתוחזקים לאורך עשורים; מנוע הקליטה כאן מאפשר "
    "למפעל לבנות ספרייה משלו ממה שהספקים שלו מפרסמים, אבל את הספרייה "
    "עדיין צריך לבנות.",
    "פורמטי ה-CNC הקנייניים — Elumatec NCX/ECX/NCW/DGX, Schüco MCO, "
    "Kaban KBN — מעולם לא נחתכו על מכונה פיזית מהתוכנה הזאת. הוכיחו "
    "אותם על פסולת לפני ייצור.",
    "ספר החשבונות הוא הנהלת חשבונות, לא הנהלת חשבונות מאושרת. החוק "
    "הישראלי קובע דרישות לניהול ספרים ממוחשב ולהפקת חשבוניות מס, ושום "
    "דבר כאן לא הוגש לאישור מולן. התייחסו אליו כאל חשבונאות ניהולית של "
    "המפעל והמשיכו להפיק מסמכים סטטוטוריים ממה שכבר מאושר, עד שיאושר.",
    "זמני התקן שהמתזמן עובד לפיהם הם ערכי התחלה, לא מדידות של המפעל "
    "שלכם. תאריך שהובטח טוב רק כמו הדקות-לחיתוך שמאחוריו, אז מדדו כמה "
    "עבודות אמיתיות וקבעו אותם.",
    "שום דבר בהשוואה הזאת לא נבחן מול התקנה של מתחרה. שורות המתחרים "
    "מתעדות תיעוד פומבי בלבד, ו\"לא מתועד\" לעולם אינו \"לא קיים\".",
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
    "STANDING_LIMITATIONS_HE",
    "resolve_probe",
    "verify_claims",
    "profileos_support",
    "coverage",
    "matrix",
    "not_documented_elsewhere",
    "missing_from_profileos",
    "summary",
]
