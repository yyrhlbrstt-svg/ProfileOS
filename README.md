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

---

## Quick start

```bash
pip install -r requirements.txt
python tools/generate_sample_dxf.py     # build the sample drawings
python -m profileos.cli demo            # run the whole chain end to end
```

Then explore:

```bash
profileos section analyse data/samples/mullion_mb70.dxf
profileos element build 2400 1800 --mullions 800,1600 --sash 1,0 --sash-type tilt_turn
profileos pipe size 1.2 45 --lift 12 --available 250
profileos cnc drivers
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
| HTTP API | `profileos serve` → `/docs` (14 endpoints) |
| Desktop | `profileos ui` |

The desktop application is a six-page workspace following the order work moves
through a shop: Profile → Element → Nesting → Machining → Quotation → Shop
floor. Dark and light themes (`Ctrl+T`); `Ctrl+1`…`Ctrl+6` jump between pages.

---

## Testing

```bash
pytest                                  # 343 tests
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
(checked against ezdxf), Code 128 module counts (the spec formula).

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

**Licensing.** The WebAuthn/FIDO2 hardware licensing described in the original
specification is not implemented in this revision. The security module's
exception types exist; the engine does not.

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
  nesting/     kerf/mitre, heuristics, column generation, inventory
  cnc/         IR, macros, clamps, toolpaths, ten machine drivers
  plumbing/    hydraulics, pipe catalogues, network analysis
  quoting/     bill of materials, suppliers, pricing
  mes/         barcodes, production tracking, job cards
  api/         FastAPI service
  ui/          PySide6 desktop application
tools/         sample DXF generator
examples/      plugin examples
tests/         343 tests
```
