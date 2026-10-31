# Takeoff accuracy audit (Rule 0)

31 Oct 2026. Read before building anything: `vula_mind/vula/takeoff/*`, `vula/commerce/boq_online.py`,
`boq_template.py`, migrations 056 / 129 / 137 / 227 (plus 013 and 180, which turned out to matter),
`tests/test_takeoff*.py`, `tests/test_boq*.py`, `tests/test_id_route_scoping.py`,
`vula_dashboard/src/components/estimating/TakeoffV2.jsx` + `useTakeoff.js`.

Paths below are relative to `vula_mind/` unless they start with `vula_dashboard/`.

## How the takeoff works today

`POST /takeoff/upload` → `api.py:92 _process_plans` → `PlanReader.read` (`plan_reader.py:625`) →
`BOQGenerator.generate` (`boq_generator.py:274`) → `OrderManager.build_orders` (`api.py:121`), all in one
background task with nothing stopping it in between. Job state is kept in memory and copied to
`vula_takeoff_jobs` (migration 180, `api.py:47-70`).

- **PDF:** pdfplumber text and tables, or GLM-OCR when a page has under 50 chars (`plan_reader.py:149-194`).
  Then five LLM calls pull rooms, openings, MEP, GFA/NFA and the title block out of that text
  (`plan_reader.py:381-414`). **No geometry is measured from a PDF.** Every area is a number the LLM read
  or guessed from the text.
- **DXF:** ezdxf takes closed polylines as rooms and computes area and perimeter with the shoelace formula
  (`plan_reader.py:278-364`). Doors and windows come from block names at fixed sizes, 0.9×2.1 and 1.2×1.2
  (`:315-320`). DWG is listed in the docstring and the upload allow-list, but `read()` has no DWG branch
  (`:659-661`).
- **BOQ:** quantities come from GFA and room areas through fixed factors, priced with the tenant's own
  rate, then the scraped DB, then the AECOM fallback (`boq_generator.py:216-272`). Money is **float rands**
  (`BOQItem`, `:46-61`; `to_dict`, `:94-116`), not integer cents.
- **Online BOQ / QS view:** `boq_online.py` holds a separate per-project bill in integer cents
  (`vula_boq_drafts`, migration 227). It is seeded from a *filed* QS bill (`boq_online.py:170`) or typed in.
  **Nothing moves a takeoff BOQ into it.** The QS share view (`project_tracker.py:308 client_view`,
  `_QS_FIELDS`) shows contract, certified and spend figures. It shows no measurement or verification data.

## Item-by-item

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | Measured geometry for PDFs | **MISSING** | `plan_reader.py:149-194`: PDF path is text/OCR only. Areas come from LLM extraction (`:416-445`, `:507-518`). DXF is the only measured path (`:285-329`). `ExtractedProject` (`:118-134`) has no field saying where a figure came from (measured or read). |
| 2 | Calibrated scale per sheet | **MISSING** | `DrawingSheet.scale: str` (`plan_reader.py:62`), regex-parsed from "SCALE 1:100" text (`:264-266`), default `"unknown"` (`:181`). The DXF sheet is hard-coded `"1:100"` (`:643`). The scale is **never used** in any calculation, and no method or verifier is recorded. Dashboard only displays it (`TakeoffV2.jsx:141`). |
| 3 | Benchmark (10+ plans, error % per type, CI) | **MISSING** | No takeoff fixtures or truth set. `scripts/benchmarks/` is the chat benchmark only, live-model and not in CI. `tests/test_takeoff.py` checks BOQ arithmetic and the `geometry_reconciled` thresholds (`:382-420`), not accuracy against real plans. CI already runs `pytest tests/` (`.github/workflows/ci.yml:49`), so a pytest benchmark would run in CI with no workflow change. |
| 4 | Measured confidence | **MISSING** (one coarse signal) | Hard-coded: 0.90 text, 0.70 OCR (`plan_reader.py:173,176`), 0.65 full-OCR (`:242`), 0.85 DXF (`:645`), 0.70 image (`:656`). Project score = mean of these (`:413`). The only real signal: halved when `geometry_reconciled` fails (`:672-678`). Per-line confidences are constants per trade (`boq_generator.py:286-432`, e.g. `finish_map :338-347`). |
| 5 | Blocking review before pricing/ordering | **MISSING** | `geometry_reconciled` "never blocks anything" (`plan_reader.py:583-604`). Its warnings only go into `extraction_notes` (`:674`). `_process_plans` prices and builds orders straight after (`api.py:113-142`). No approve UI in `TakeoffV2.jsx`, which shows only a legend (`:72`) and a per-line chip (`:112`). **Problem with the brief:** migration 137 is not a generic gate. `commerce_automation_firings.automation_id uuid NOT NULL REFERENCES commerce_automations` (`137_automation_firings.sql:15`), so it cannot hold a takeoff review row without a schema change. The generic propose-confirm engine is **migration 013** `vula_approvals` (`entity_type` free text, `013_approvals.sql:10`) with `vula/commerce/approvals.py:75 create_approval` / `:129 record_decision` / `:248 _on_approved`. |
| 6 | Flooring roll/lane/seam/waste (LVT, sheet vinyl) | **MISSING** | Flooring = summed room area × rate, no waste (`boq_generator.py:333-356`). No sheet-vinyl key in `finish_map` (`:338-347`) or in the LLM schema (`plan_reader.py:422`). `lvt_vinyl` is a single rate (`boq_generator.py:139`). Per-tenant `waste_pct` exists only for manufacturing BOMs (migration 211), not takeoff. |
| 7 | `_job_for()` tenant scoping | **PARTIAL**: not exploitable today, but fails open | `api.py:244-251`: `if not job or (tenant_id and job.tenant_id != tenant_id)`, so with a falsy `tenant_id` **any** job is returned. Callers pass `authorized_tenant(request) or tenant_id` (`api.py:256,270,280,295,314`). `authorized_tenant` is non-empty for every tenant *member* (`master_auth.py:129-136,143-148`). It is empty only for an API-key or master caller (meant to see every tenant) or in dev mode with no `API_KEY` (open anyway). A member who sends no tenant at all gets 401 from `require_auth` (`master_auth.py:129,137`). Covered by `tests/test_id_route_scoping.py:40`. The weakness: safety rests on an implicit rule outside the function. Any new caller that passes `None` for a member request leaks across tenants. |

## Found during the audit (outside the 7 items, affects accuracy)

- **A. DXF units are ignored.** `$INSUNITS` is never read, and the shoelace area is used as m² (`plan_reader.py:297-302`).
  A DXF drawn in mm (the SA norm) gives areas 10⁶× too big. The "area > 2" filter (`:298`) is in drawing
  units, so it filters nothing in mm. This is the most accurate path today, and it is wrong for mm drawings.
- **B. DXF rooms have no finishes.** They come out as `Room_N` with `floor_finish=""`. That replaces the
  LLM's named rooms wholesale (`:667-668`), and then every flooring line is dropped (`boq_generator.py:350`, `not finish`).
- **C. Takeoff money is float rands.** This conflicts with the integer-cents rule. Any takeoff→`boq_online`
  hand-off has to convert at the boundary (`boq_online.amount_cents`, `boq_online.py:64`).
- **D. Dashboard drawing register shows mock sheets when a job has 0 sheets.** `job.project.sheets` is a count,
  and `0` is falsy, so it falls back to `MOCK_TAKEOFF.drawings` (`TakeoffV2.jsx:64`).

## Proposed build order

Each phase is a small commit with its tests and a diff summary. Nothing gets a second BOQ, rates, export
or QS-view path.

1. **Item 7 (fail closed).** `_job_for` takes an explicit `privileged: bool` (true only when `require_auth`
   passed on the API key or a master login). With no tenant and no privilege it returns None. Add a test
   that calls it directly with `None` for a member-shaped request.
2. **Fix A + B** (small, unblocks honest DXF numbers for the benchmark): honour `$INSUNITS`, and merge
   vector geometry into the LLM's named rooms instead of replacing them.
3. **Item 2: calibration.** Add `Calibration{units_per_m, method: title_block|dimension|two_point|dxf_units,
   verified_by, verified_at}` on `DrawingSheet`, keeping `scale` as the display string. Persist it in the
   job state. Add `POST /takeoff/{job_id}/sheets/{n}/calibrate`.
4. **Item 1: OpenTakeoff sidecar.** It runs as a separate container on HTTP, pinned to tag
   `apache-2.0-final` @ `84c7f48e`, vendored only as a Docker build context with its LICENSE and NOTICE,
   and never merged with later commits. A thin client turns its measurements (via the item-2 calibration)
   into `Room.area/perimeter`, `DoorWindow` counts and `ExtractedProject` fields. A new `source` field on
   `Room` records measured or read. Downstream code is unchanged. If the sidecar is down, it falls back to today's path.
5. **Item 4: confidence.** Score = agreement between the LLM-read and engine-measured figures (per room
   and GFA), scaled by that plan type's benchmark error history. This replaces the constants in
   `plan_reader.py` and feeds `BOQItem.confidence`.
6. **Item 5: review gate.** When `geometry_reconciled` fails, or AI and measured figures disagree beyond
   tolerance, `_process_plans` stops at `status="awaiting_review"` and opens an approval. Pricing and orders
   only run from `_on_approved`. The dashboard gets an approve/reject card on the takeoff.
7. **Item 6: flooring.** Add `sheet_vinyl` / `lvt` finishes with roll width, lane direction, seams and
   pattern repeat in `BOQGenerator`. Waste % is per tenant (with defaults), and the output is m² laid plus
   m² to order plus roll count.
8. **Item 3: benchmark.** Add `tests/benchmarks/takeoff/` fixtures and a pytest that reports area,
   length and count error % per plan type and fails above tolerance.

## Who builds what (31 Oct 2026, so the work isn't done twice)

A second session ("Vula-Group go-live and takeoff", branch `ccr-f7a53584-worzz4`,
`docs/golive-and-takeoff-audit.md` Phase 4) audited the same takeoff items and is building them.
To avoid two builds of the same thing:

| Work | Owner |
|---|---|
| Items 2-7, fixes A-D (DXF units, rates matching, cents, calibration, confidence, benchmark, review gate, flooring BOQ, `_job_for`, dashboard): `plan_reader.py`, `boq_generator.py`, `api.py`, `TakeoffV2.jsx` | **go-live session** (`ccr-f7a53584-worzz4`) |
| Item 1, OpenTakeoff sidecar: `services/opentakeoff/`, `vula/takeoff/opentakeoff_client.py` (new files only) | **this session** (`ccr-d6954993-0a7b3b`), **done** |
| Wiring the sidecar into `PlanReader.read` (a ~3-line call) | go-live session, next to its calibration/confidence code |

Findings 1-5 from this audit (fail-open `_job_for`, DXF rooms replacing named rooms, the mock-drawings
bug, migration 013 as a gate option, OpenTakeoff's own roll/waste/scale provenance) were sent to that
session.

### Item 1: what was built

- `services/opentakeoff/Dockerfile` builds OpenTakeoff at tag `apache-2.0-final` = `84c7f48e…`
  (verified on GitHub: the LICENSE at that commit is Apache-2.0 and the NOTICE file is present).
  The build re-checks both. Upstream LICENSE, NOTICE and THIRD-PARTY-NOTICES ship unchanged in the
  image, and no upstream source is in the repo. `tests/test_opentakeoff_sidecar_pin.py` fails CI if
  the pin moves.
- `services/opentakeoff/bridge.mjs` is Vula's HTTP wrapper around the engine's MCP stdio server
  (`POST /measure`). It starts a fresh engine per plan, takes the scale from an override or the
  drawn note (unscaled sheets are not measured), runs `detect_rooms`, and returns the engine's
  units. It is locked without `OPENTAKEOFF_API_KEY`.
- `vula/takeoff/opentakeoff_client.py`: `measure_plan()` (fail-open), conversion to metres, and
  `apply_measurements()`, which fills the existing `ExtractedProject` and returns AI-vs-measured
  `agreements`. Those agreements are the input for measured confidence (item 4) and the review gate (item 5).
- **Run end to end in CI** (`.github/workflows/takeoff-sidecar.yml`: builds the pinned image,
  starts it, checks the API-key lock, then scores it against `vula_mind/evals/takeoff_plans`).

### Item 1: measured results (CI runs 1-2, 31 Oct 2026)

| Case | Result |
|---|---|
| synthetic four-room (analytic truth) | **pass**, every room +1.53% (2% tolerance) |
| VA Roseburg A-03a, not to scale | **pass**: correctly left unmeasured |
| VA St Cloud AF101, real finish plan | **fail**: 4 rooms 20-36% *under*, 1 missed. The engine withheld 20 seeds (12 "unowned", 8 label bubbles) |
| Porterville ADU A1-101, real residential | **fail**: nothing measured. 12 seeds withheld (8 unowned, 3 duplicate, 1 bubble) |
| VA Dublin A-601 counts / VA Shreveport C-300 lengths | not covered: the bridge measures rooms only |

The engine's flood sensitivity at 0.8 gave identical results.

**What this means:**
- OpenTakeoff's *measuring* is exact, but its *automatic room finding* (`detect_rooms`, which
  upstream gates off by default for exactly this reason) is not usable on real plans yet.
  Feeding it into pricing would make the takeoff worse.
- So the sidecar stays **not wired** into `PlanReader.read`. This agrees with the go-live session's
  rule: wire it only when the benchmark justifies it.
- CI enforces only what is proven (synthetic + NTS refusal) and reports the real-plan cases on
  every run. A case moves into `--require` once it passes on merit.
- Where it can still earn its place: measuring outlines a person draws or confirms in the dashboard.
  Upstream's own known-answer runs put supplied rings within 0.75% (`measure_polygon`), so the
  engine's maths can be trusted once a person confirms the boundary. That fits the item-5 review
  gate, not automatic pricing.

## Decisions I need from you before building

1. **Gate table (item 5).** Migration 137 cannot hold a takeoff review without a schema change.
   Should I use the generic migration-013 `vula_approvals` engine (`entity_type="takeoff_review"`)?
   I recommend it, since it needs no new table. The alternative is a new nullable column on 137.
2. **Benchmark plans (item 3).** The repo has no DIGG/Gerflor plans and no hand-measured truth, so I
   can't produce them. Can you supply 10+ anonymised PDFs/DXFs with measured room areas, wall lengths and
   opening counts? Until then I'd ship the harness with 2–3 synthetic DXF/PDF fixtures whose geometry is
   known exactly. Is the 2% area tolerance agreed? I'd suggest 2% for length and exact match for counts.
3. **OpenTakeoff.** I'll check, before vendoring anything, that tag `apache-2.0-final` resolves to `84c7f48e`
   and that the LICENSE at that commit is Apache-2.0. What is the upstream repo URL?
4. **Flooring defaults (item 6).** What are the Gerflor roll widths (e.g. 2 m / 4 m sheet) and the default
   waste % for LVT and sheet? I'll make them per-tenant settings, but they need sensible defaults.
5. **Fixes A–D.** These are not in your list. Should I fold A and B into phase 2 as proposed, and D into
   the item 5 dashboard work?
