# ProfileOS

Integrated CAD/CAM, structural analysis, cutting optimisation, CNC
post-processing, glazing, costing and shop-floor execution for architectural
aluminium — windows, doors and curtain walling — with multi-trade extension into
pipework.

```
DXF  →  section properties  →  element design  →  cut list  →  nesting
                                    ↓                            ↓
                              glass + hardware              machine code
                                    ↓                            ↓
                                 costing                    shop floor
```

Everything runs from one library, one CLI, one HTTP API and one desktop
application.

Configured for **דאדי בע"מ**, Beit El. Overview and capability comparison:
<https://profile-os-one.vercel.app>

---

## Installing on a Windows machine (the way it is delivered)

1. Install **Python 3.11 or newer** from <https://www.python.org/downloads> —
   tick **Add python.exe to PATH** on the first screen of the installer. This
   is the one box that matters; nothing works without it.
2. Double-click `install\התקנה.bat`. It checks Python, installs every
   component, seeds a starting order book and puts a shortcut on the desktop.
3. Double-click `ProfileOS.bat` — or the desktop shortcut — to run.

To use the shop-floor terminal from a phone on the same Wi-Fi, double-click
`install\טרמינל-לטלפון.bat` and type the six-digit code it prints into the
phone's browser.

---

## Quick start (from a shell)

```bash
pip install -r requirements.txt
python tools/generate_sample_dxf.py     # build the sample drawings
python -m profileos.cli demo            # run the whole chain end to end
```

Then explore:

```bash
profileos section analyse profileos/data/samples/mullion_mb70.dxf
profileos element build 2400 1800 --mullions 800,1600 --sash 1,0 --sash-type tilt_turn
profileos pipe size 1.2 45 --lift 12 --available 250
profileos cnc drivers
profileos seed                          # a starting order book
profileos jobs list                     # the order book
profileos jobs pack J-2026-0001         # the printable job pack
profileos pipe fixtures -d 8            # fixture units and demand
profileos pipe drainage -d 8 -f 4       # branch, stack, vent, house drain
profileos ui                            # desktop application
profileos serve                         # HTTP API, docs at /docs
```

---

## What it does

### Geometry — `profileos.geometry`
Reads DXF cross-sections and reconstructs them into topology. Handles the mess
real drawings arrive in: outlines split into dozens of loose `LINE` and `ARC`
entities, endpoints that meet only to within drawing tolerance, nested blocks,
and annotation layers that must not be counted as material.

- Contour chaining via a spatial hash, with bounded gap repair.
- Nesting-depth topology: even depth is material, odd is void — which resolves
  chambers, screw-port bosses inside chambers, and the bores inside those.
- Ray-cast wall-thickness scan along inward normals.
- `$INSUNITS` handling and envelope plausibility checks that catch unit errors.

### Structural — `profileos.structural`
Exact section properties by Green's theorem, verified against closed-form
solutions:

| Section | Checked against |
|---|---|
| Rectangle | A, Iₓ, I_y, Sₓ, Zₓ, rₓ exact; shape factor exactly 1.5 |
| Hollow tube | Iₓ/I_y exact; J within 5 % above Bredt; shear centre at the centroid |
| Circle | shape factor matches 16/(3π) |
| Rotated sections | principal angle tracks rotation; I₁, I₂ and the trace invariant |

Plus plastic neutral axis and moduli by equal-area bisection, Tri6
finite-element torsion and warping constants via `sectionproperties`, a
thin-walled Bredt fallback that labels itself as an estimate, and EN 1999-1-1
bending, shear, deflection and buckling checks with a maximum-span solver.

### Elements — `profileos.elements`
Turns an opening into a production package: frame and sash cut lengths, glass
sizes, gasket runs and hardware. Per-system deductions, overlaps and clearances
live in hot-reloadable rule sets, because those numbers are exactly what differs
between one profile system and another.

Safety-glass regulation is enforced: door leaves, low sills and large panes are
flagged when the specified build-up is not safety glass.

### Glazing — `profileos.glazing`
EN 673 centre-pane U-values — radiative plus gas conductance per cavity, so a
low-E coating shows its real effect. Verified against published figures:

| Build-up | Computed | Published |
|---|---|---|
| 6 mm monolithic | 5.68 | ~5.7 |
| 6/16 air/6 uncoated | 2.70 | ~2.7 |
| 6/16 argon/4 low-E | 1.09 | ~1.1 |
| 4/14/4/14/4 triple, two low-E | 0.62 | ~0.6 |

Plus EN ISO 10077-1 whole-window `U_w` including the spacer ψ-value.

### Nesting — `profileos.nesting`
1D cutting-stock optimisation by Gilmore-Gomory column generation: GLOP solves
the restricted master, CP-SAT prices new patterns as a bounded knapsack, SCIP
solves the integer master. Greedy heuristics provide the initial columns, a
quality floor and a fallback.

Mitre allowance is computed from the profile depth with a **selectable length
reference** (centreline / outer / inner), because where a nominal length is
measured genuinely differs between shops. Remnants are tracked in a persistent
store and offered before fresh stock.

**2D sheet nesting** for glass and infill panels is a different problem, and is
solved as one. A glass table scores edge to edge and snaps, so every cut splits
a rectangle into exactly two: a layout that cannot be decomposed into such cuts
is scrap on the table however good its area utilisation looks. Every sheet the
engine returns is checked by an independent verifier that searches for a real
cutting sequence and reports how many stages it needs — it rejects the classic
pinwheel, which tiles perfectly and cannot be cut. A CP-SAT model solves the
two-stage problem exactly where the instance is small enough to close, and the
result reports optimality only as strongly as it was proved, separating a
global proof from one that holds within the machine's stage limit.

### Catalogue ingestion — `profileos.catalogue`
A profile library is the thing a fabricator cannot buy their way out of, and
the established packages sell it as a subscription. This reads the supplier's
own published table (PDF, CSV or TSV) and drawing pack, measures every drawing
through the geometry and structural engines, and sets each published figure
against the one it measured.

That cross-check is the point. Agreement is evidence; a disagreement is
reported rather than resolved, and the article stays out of the library until
somebody decides which figure is right. Two parsing problems get real
solutions: the decimal convention is settled once per document, because
`1,842` is 1842 in London and 1.842 in Milan and nothing inside the token can
tell them apart; and data columns are read from the run at the end of a line,
because a description like "Mullion 70/100" otherwise shifts every figure one
place left. Units are part of the column definition and never inferred — cm⁴
to mm⁴ is a factor of ten thousand on a second moment.

### CNC — `profileos.cnc`
A machine-neutral intermediate representation drives ten drivers:

| Driver | Format |
|---|---|
| `elumatec.ncx` / `.ecx` / `.ncw` / `.dgx` | Elumatec SBZ, eluCad exchange, legacy SBZ, DG saw |
| `schueco.mco` | Schüco MCO / XML |
| `kaban.kbn` | Kaban |
| `emmegi.campro` | Emmegi CamPro / FpPro |
| `fom.cam` | FOM Industrie |
| `iso.gcode` / `iso.gcode.siemens` | Fanuc and Sinumerik dialects |

With ten parametric hardware macros, automatic tool selection, toolpath
generation with cutter-radius compensation, and clamp interference detection
with greedy repositioning plus bar-support checks.

Drivers **refuse** jobs containing operations the control cannot execute rather
than silently dropping them.

### Plumbing — `profileos.plumbing`
Darcy-Weisbach with Colebrook-White (fixed-point, Swamee-Jain seeded), exact
`64/Re` in the laminar range, Hazen-Williams for codes that specify it, fitting
K-values, copper/PPR/steel catalogues, constraint-based sizing, and Hardy Cross
network balancing.

### Costing — `profileos.quoting`
Bill of materials aggregated by category, code and unit, with profile
quantities taken from **bars actually consumed** when a nesting report is
supplied. Hot-reloadable supplier price lists with quantity breaks, minimums,
discounts and validity windows. Cost and price kept separate so margin stays
visible; unpriced codes are reported, never silently zero-costed.

### Shop floor — `profileos.mes`
Code 128 (implemented directly) and QR labels, a validated production stage
machine with append-only history, bottleneck detection, and self-contained
tablet job cards with assembly sequences derived from the element's own content.

### Site measurement — `profileos.delivery.survey`
Three widths, three heights and both diagonals per opening — the measurements
the trade actually takes — with the frame size derived from the **smallest**
dimension less the system's own fitting clearance. An opening out of square,
not parallel, or measured off an unfinished floor is named and left out of
production rather than rounded into the batch. No clearance from the
catalogue means no frame size is offered at all.

### Documents the shop sends — `profileos.erp`, `profileos.glazing.order`
Order confirmations that carry what the shop is still waiting on from the
customer, each with a date, and a delivery date counted in working days from
when the last blocker clears — printed as provisional while anything is
outstanding. Glass orders generated from the same panes the machining came
from, with the sizes left blank where the series' rebate is unconfirmed,
because an insulating unit cannot be recut. Handover packs with care
instructions for the finishes actually on the job and a warranty per part of
the work whose clock starts on a stated date.

### The office — `profileos.reports`, `profileos.erp.stocktake`, `profileos.erp.timesheets`
Quoted against ordered by month, win rate by count and by money, margin by
customer, the live pipeline and what is late — every percentage carrying the
number of records behind it. Stocktake sheets that go to the racks without the
expected figure printed on them and post only the lines somebody counted.
Hours booked by person, job and operation, with rework separated, feeding the
job costing so margin is measured rather than repeated.

### Keeping the shop — `profileos.core.backup`, `profileos.core.audit`
One dated archive of everything, with a restore that says what it would
replace and moves the current folder aside rather than deleting it. A
hash-chained audit trail where each entry carries a digest of the one before
it, so a line removed from the middle or a figure edited afterwards breaks the
chain and the check names the line.

### Continuous updates — `profileos.core`
Plugins load and reload without restarting. Python plugins are statically
validated with `ast` **before execution** — `eval`, `exec`, `subprocess`,
dynamic imports and filesystem deletion are refused. JSON/XML plugins validate
against pydantic schemas. A polling watcher (mtime + SHA-256) picks up changes.

```bash
profileos plugin validate examples/plugins/custom_macros.py
profileos plugin watch
profileos plugin list
```

See `examples/plugins/` for a macro plugin and a system-rules document.

---

## Interfaces

| Interface | Entry point |
|---|---|
| Library | `import profileos` |
| CLI | `profileos --help` |
| HTTP API | `profileos serve` → `/docs` (18 endpoints) |
| Desktop | `profileos ui` |

The desktop application is a nine-page workspace following the order work moves
through a shop: Profile → Element → Nesting → Glass → Machining → Quotation →
Shop floor, with Catalogue and System alongside. Dark and light themes
(`Ctrl+T`). Nothing in the suite is CLI-only.

`profileos compare` prints the capability matrix and `--verify` checks that
every claim in it resolves to a symbol in this codebase. The same matrix drives
the public site, built by `tools/build_site.py`, which refuses to build if a
claim no longer resolves.

---

## Testing

```bash
pytest                                  # the whole suite
QT_QPA_PLATFORM=offscreen pytest        # includes the UI suite
```

Tests verify against closed-form solutions and published reference values
wherever one exists, rather than against recorded output. The UI suite drives a
real window headlessly through the entire workflow and grabs every page to catch
paint-time failures.

---

## Status and limitations

Read this before putting it near a machine.

**Verified against independent references.** Section properties (closed-form),
glazing U-values (published figures), friction factors (Moody chart and the
Colebrook residual), nesting optimality (LP lower bound), DXF bulge arcs
(checked against ezdxf), Code 128 module counts (the spec formula), guillotine
cuttability (the pinwheel counter-example and seven hand-derived stage counts),
and catalogue ingestion (a supplier figure deliberately falsified by 27 %,
which the cross-check finds without being told where to look).

**Not yet validated against physical machines.** The proprietary CNC formats —
NCX, ECX, NCW, DGX, MCO, KBN, CamPro, FOM — are written from documented and
observed structure. They produce well-formed, internally consistent, complete
programs, but no output has been cut on a real machine. **Before production
use: post a known part, diff it against a file the vendor's own software
produced for that part, and correct the field mapping.** The neutral IR is
designed so any such correction is confined to one driver module.

**Illustrative data.** The bundled system rules, hardware lists, materials and
supplier prices are plausible placeholders, not any manufacturer's catalogue.
Replace them with figures from the system supplier's technical manual. The
plugin mechanism exists precisely so this is a data change, not a code change.

**Engineering judgement is not replaced.** Design checks implement EN 1999-1-1
and EN 673 as documented, with stated simplifications (shear area taken as 50 %
of the gross area; composite sections assume full shear transfer). A qualified
engineer must review any structural result before it is relied upon.

**No ERP, no 3D, no capacity planning.** There are no purchase orders, no stock
movements and no ledger; quotations and supplier enquiries stop at the
document. Elements are drawn in elevation and section only, and the shop's
schedule is not managed here. These gaps are recorded by name in
`profileos.compare` and shown on the site rather than left to be discovered.

**The comparison is public documentation only.** Nothing in it has been tested
against a competitor's installation, and "not documented" never means "absent".

---

## Layout

```
profileos/
  core/        configuration, logging, events, registries, hot reload
  models/      materials, profiles, machines, orders, results
  geometry/    DXF import, contour chaining, topology, validation
  structural/  Green's theorem, plastic moduli, torsion, design checks
  elements/    openings, system rules, cut lists, glass, hardware
  glazing/     build-ups, U-values, safety compliance
  nesting/     kerf/mitre, column generation, inventory, 2D sheet nesting
  cnc/         IR, macros, clamps, toolpaths, ten machine drivers
  plumbing/    hydraulics, pipe catalogues, network analysis
  catalogue/   supplier table parsing, drawing ingestion, cross-verification
  quoting/     bill of materials, suppliers, pricing
  mes/         barcodes, production tracking, job cards, piece labels
  delivery/    site measurement, loading, installation days, handover packs
  projects/    job files, costing, attachments, saved templates
  reports.py   sales, win rate, margin by customer, the pipeline
  api/         FastAPI service
  ui/          PySide6 desktop application
  updates/     signed manifests, atomic install, rollback
  security/    hardware fingerprint, offline licences, WebAuthn/FIDO2
  compare.py   the capability matrix, bound to real symbols
tools/         sample DXF generator, site build
site/          the public comparison page
examples/      plugin examples and a sample elevation set
tests/         the suite
```
