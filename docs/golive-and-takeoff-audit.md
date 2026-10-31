# Go-live and takeoff audit

Written 2026-10-31 on branch `ccr-f7a53584-worzz4` (base `c95915e`, PR #253). This is the audit
**before** building anything (RULE 0). Every line is DONE / PARTIAL / MISSING with file:line
evidence. Paths are relative to `vula_mind/` unless they start with `docs/`, `vula_dashboard/`
or `migrations/`. Status lines are updated at the end of each phase (see "Final status").

Legend: **DONE** = in code and covered by a test. **PARTIAL** = in code, but a gap or no test.
**MISSING** = not in the repo. **NEW** = a defect found during this audit that wasn't in the brief.

---

## Phase 0 — test suite

`pytest tests/` on Python 3.11 (the same version CI uses, `.github/workflows/ci.yml`), with
`requirements-dev.txt` installed:

**6,741 passed, 2 skipped, 0 failed** (183 s). The other CI checks also pass locally:
`ruff check vula/ core/`, `tools/check_migrations_rls.py`, `tools/check_migration_sentinels.py`.

None of these tests measure takeoff accuracy (see Phase 4). A green suite says nothing about
whether a room area is right.

Note: `pip install -r requirements.txt` **fails on Python 3.13**. `http-ece` (pulled in by
`pywebpush`) does not build (`AttributeError: install_layout`). CI and the Dockerfile use 3.11,
so production isn't affected. Anyone setting up locally on 3.13 will hit it.

## Phase 0 — the Sept-11 blockers, re-checked

No Sept-11 blocker document exists in the repo. The items live in code comments
(`vula/api/server.py:129`, `vula/log_streams.py:1-7`) and commit `9e6ccd3` (#231, "Launch
hardening").

| # | Item | Status | Evidence | Tests |
|---|---|---|---|---|
| 1 | Log mislabeling (Railway labels stderr as "error") | **DONE** | `vula/log_streams.py:77` (`BelowError`), `:82` (`handlers()`); `vula/api/server.py:125-145`; uvicorn via `log_config.json:17,25,31` + `start.py:15-17`; JSON `level` field `log_streams.py:53,63` | `test_logging_stream.py::test_routine_lines_never_reach_stderr`, `test_audit_quick_fixes_1020.py::test_info_to_stdout_and_errors_to_stderr`, `test_json_logs.py::test_levels_are_the_ones_railway_reads` |
| 2 | Human-handoff ghosting | **DONE**, 2 caveats | `vula/handoff_watch.py:23` (remind after 30 min), `:24` (resume after 2 h), `:27` (customer apology); loop `server.py:1169`, started `:1612`; `commerce/service.py:1459` | `test_handoff_watch.py` (6 tests incl. `test_resume_happens_even_if_nothing_can_be_sent`), `test_stale_handoff.py` |
| | Caveat a | PARTIAL | The "already reminded" set is held in memory (`handoff_watch.py:33`). A restart can repeat one reminder. Harmless (a duplicate nudge, never a ghost). | — |
| | Caveat b | PARTIAL | The apology can't be sent outside Meta's 24 h window. The bot still resumes. | — |
| 3 | Rate scraper end to end | **PARTIAL** | Fetch `takeoff/construction_rates_scraper.py:114-146`, parse `:198,:330`, store SQLite + Supabase `vula_material_rates` `:361-447`, weekly loop `server.py:226-239`. **But scraped rates never reach a BOQ line.** See NEW-T1. | `test_rates.py` (offline, mocked). No test proves that a scraped rate prices a BOQ line. |
| 4 | Payment-link / email env gaps | **PARTIAL** | `vula/readiness.py:26-46` checks platform env. Tenant payments are checked from DB rows (`startup_checks.py:218-239`). **Not checked by /ready:** `PUBLIC_BASE_URL` (builds every pay link; its default is the prod Railway URL, so a staging box would mint prod links), `RESEND_API_KEY`/`FROM_EMAIL`/`TEAM_EMAIL` (`vula/api/email.py:32,37,412`), `PAYFAST_MERCHANT_*` (subscription link, `vula/api/onboarding.py:114-151`), `YOCO_*` platform fallback (`vula/api/yoco.py:245-249`). PayFast/Resend being off is a documented 2026-09-11 decision (`config.py:474-493`), so the fix is to *report* them in /ready as "off by design", not to fail it. `takeoff/order_manager.py:379` reads `RESEND_API_KEY` straight from `os.environ`, bypassing settings. | `test_readiness.py` (3), `test_config_warn_missing.py` |
| 5 | Reply deadline | **DONE** (not redone) | `config.py:57-58`; `vula/api/whatsapp.py:8697` `_run_with_holding_message`, `:8725-8749` | `test_audit_quick_fixes_1020.py::test_a_stuck_turn_is_stopped_and_the_person_told` +3 |
| 6 | Escalation languages zu/xh/st | **DONE** (not redone) | `vula/escalation.py:40-42, 95-97, 117-119`; AI check for other SA languages `:133-144` | `test_escalation.py::test_detects_complaint_words_in_isizulu_isixhosa_sesotho`, `test_escalation_languages.py` |
| 7 | `ENFORCE_TENANT_AUTH` | **DONE in code; production value UNVERIFIED** | `config.py:348` (default `False`), enforced `vula/api/tenant_auth.py:73` + `server.py:1904`, error logged on every boot while off `server.py:1713-1717`, `/ready` reports a gap while off `readiness.py:37-38` | `test_readiness.py::test_platform_gaps_make_it_not_ready`, `test_route_auth_sweep.py` |

Not redone, per the brief: Jev benchmarking (#253), reply deadline, escalation languages,
readiness report, go-live eval. Takeoff tenant scoping is **sound**: `takeoff/api.py:251`
`_job_for` + `authorized_tenant`, and the upload form's `tenant_id` is checked by
`require_auth` → `_request_tenant` (`vula/api/master_auth.py:151-171`). Only a regression test
is to be added.

---

## Phase 1 — staging

| Item | Status | Evidence |
|---|---|---|
| Staging environment | **MISSING** | `docs/staging.md:4` "Vula has no staging environment". The guide exists; nothing is created. |
| Supabase branch / Railway `staging` env / Meta test number / Yoco test keys / PayFast sandbox / Vercel preview env var | **MISSING** | All manual steps in `docs/staging.md` §1-4 |
| Migration runner | **MISSING** (detection only) | No `schema_migrations` table. Detection: `vula/startup_checks.py:30` `_SENTINELS` (~117 probes), boot drift log `server.py:1685-1695`, Master › Health `GET /v1/master/migrations/pending-sql` `vula/api/master.py:723-735` (builds a paste-able script, doesn't run it). `tools/check_migration_sentinels.py` (CI) makes every migration ≥175 declare a probe. |
| Smoke test against a deployed env | **MISSING** | Only the manual checklist `docs/staging.md:82-100`; `/v1/master/whoami` (`master.py:52`) exists but nothing calls it |
| Pre-deploy migration hook | **MISSING** | `railway.toml` has no `preDeployCommand`; `start.py` only execs uvicorn |

**Proposed (reuse, not duplicate):** `tools/migrate.py` reads the same `migrations/NNN_*.sql`
glob that `master.py:_migration_files` uses (shared helper, not a copy). It records applied
files in a new `vula_schema_migrations` table (RLS on, no policies, service key only), using
the migration's file hash. Default is `--dry-run`. `--baseline` marks everything the sentinel
probes already see as applied, so a first run against production never re-runs 239 files.
It connects with `DATABASE_URL` (Postgres connection string), because PostgREST can't run DDL.
**It will not be run against production without your OK.**
`tools/smoke.py` hits `/status`, `/ready`, `/v1/whatsapp/webhook` (verify handshake with a wrong
token must 403), an unauthenticated tenant route (must 401), and optionally a signed-in
`/v1/master/whoami`. It targets `--base-url`.

## Phase 2 — security cleanup (nothing changed yet; each needs your OK)

| Item | Status | Evidence / proposal |
|---|---|---|
| `ops_wa_token_backup_20261022`, other `ops_*_backup_*`, `cleanup_log` | **Not in the repo** | No migration creates them and no code reads them. They were made by hand in the SQL editor. I will list them from the live DB (read-only), confirm the live WhatsApp token works (a `GET /{phone_number_id}` with the stored token, printing only the HTTP status), then show you the exact `drop table` statements. |
| `search_path` on `kb_ledger_append_only`, `kb_tax_invoice_immutable`, `kb_audit_append_only` | **MISSING** | Defined without `SET search_path`: `migrations/199_tap_to_pay.sql:118-134`, `205_tap_tax_invoices.sql:28-34`, `232_tap_audit.sql:23-29`. Fix pattern already in repo: `173_security_advisor_hardening.sql:53-56`. Proposal: new migration `240_trigger_search_path.sql` with three `alter function … set search_path = public, pg_temp;`. This is metadata only and doesn't change ledger behaviour. |
| `EXECUTE` on `public.vula_storage_can_write` for `authenticated` | **Needed: recommend keeping** | `193_storage_tenant_writes.sql:10-21`: `security definer`, `grant execute … to authenticated`. It is called by storage.objects policies for **authenticated** users (`:23-47`), and the dashboard uploads with the user's session: `vula_dashboard/src/lib/brandUpload.js:39`, `VulaInvoices.jsx:1298,1316,1337`, `VulaProjectBoard.jsx:126`. Revoking it breaks logo, signature and product-image uploads. What it leaks is only "am I a member of the tenant in this folder" for the caller itself. Proposal: keep it, and document why the advisor warning is accepted. |
| Leaked-password protection | Dashboard setting | On your checklist (Supabase › Auth › Policies). |
| 54 "RLS enabled, no policy" tables | **PARTIAL docs** | By design (service key only). Explained only in migration comments (`173_security_advisor_hardening.sql:4-11`, and 146, 167, 178, 212-239). No central doc. Proposal: `docs/security-model.md` section. Note: migration 173 added explicit `service_role` policies, but 212-239 went back to none. Both are safe; the doc will say so. |
| `ENFORCE_TENANT_AUTH=true` in production | **UNVERIFIED** | Default is `False` (`config.py:348`). I'll confirm through `GET /ready` on production or the Railway variable *name/value for that single key only*. |

## Phase 3 — Meta / WhatsApp

| Item | Status | Evidence |
|---|---|---|
| Embedded Signup v4 (Configuration ID) | **DONE** | `vula_dashboard/src/components/settings/connect/useWhatsAppConnect.js:181-191` `config_id: FB_CONFIG_ID`, `response_type: 'code'`, `override_default_response_type`, `extras.setup`, `featureType` (coexistence), `sessionInfoVersion: '3'`. No `version` in extras (the v4 shape). `VITE_FB_CONFIG_ID` read at `:19`, connect blocked without it at `:123-126`, documented in `vula_dashboard/.env.example:22`. |
| **NEW-M1: Graph API pinned to `v19.0`** | **HIGH** | Meta lists Graph API v19.0 as available until **21 May 2026**. Calls to a removed version are served by the oldest live version, so behaviour can change under us. About 25 hard-coded copies: `config.py:146`, `vula/api/whatsapp_connect.py:40`, `whatsapp_coexist.py:34`, `whatsapp.py` (14 sites), `wa_login.py:479`, `commerce.py:6283`, `yoco.py:118`, `commerce/wa_templates.py:25`, `wa_profile.py:22`, `onboarding.py:274`, `meta_catalog.py:27`, `takeoff/order_manager.py:457`, `integrations/platform_support.py:52`, and the FB SDK init `useWhatsAppConnect.js:113`. Proposal: one `settings.graph_api_version` (and `VITE_FB_GRAPH_VERSION`), every URL built from it, plus a test that greps for stray `v\d+\.0` literals. |
| Webhook fields `messages`, `statuses` | **DONE** | `whatsapp.py:761`, `:1097-1122` |
| `smb_message_echoes`, `history`, `smb_app_state_sync` | **DONE** | `whatsapp.py:757-761` → `whatsapp_coexist.py:101-106` (`_echoes :110`, `_history :130`, `_contacts :164`) |
| `account_update`, `message_template_status_update`, `phone_number_quality_update` | **MISSING** | Not referenced anywhere. A rejected template or quality drop goes unnoticed. Proposal: log and raise an operator alert (reuse `docs/operator-alerts.md` path), no behaviour change. |
| Signature + verify handshake | **DONE** | `whatsapp.py:243-261`, `:699-734` |
| 24 h template fallback | **DONE** (inert until `WHATSAPP_NOTIFY_TEMPLATE` set) | `whatsapp.py:8186-8224`, triggered on 131047 `:8570-8575` |
| Doc vs code: "disconnect → token revoked" | **CONTRADICTED** | `whatsapp_connect.py:366-398` only nulls the token and keeps the row. It never calls Meta. Either fix the doc or add `DELETE /{waba}/subscribed_apps`. |
| Doc vs code: "Vula registers it" | PARTIAL | Only with `WHATSAPP_REGISTER_ON_CONNECT=true` + PIN (`config.py:460-461`, default off). |
| Number discovery fallback | Risk | `whatsapp_connect.py:160-187` picks the *first* number on any of the owner's businesses when Meta omits ids. That number could be wrong. |
| Stale comment | Trivial | `whatsapp_connect.py:69` says sessionInfoVersion 2; frontend sends 3. |
| Backend `.env.example` | PARTIAL | Missing `VULA_FB_APP_ID`, `VULA_FB_APP_SECRET`, `WHATSAPP_NOTIFY_TEMPLATE`. |
| Go-live eval per tenant on staging | **BLOCKED** | Needs staging (Phase 1) and the local/cloud model. `evals/golive.py` (85% pass mark) exists. |

---

## Phase 4 — takeoff accuracy (the gaps named in the brief, all confirmed)

| Gap | Status | Evidence |
|---|---|---|
| Scale is only a title-block string | **CONFIRMED** | `takeoff/plan_reader.py:62` `scale: str`; regex `:264`; **never read by any calculation** (no `.scale` use anywhere in `vula/` or `core/`). |
| Confidence is a hardcoded constant per method | **CONFIRMED** | Sheet: `plan_reader.py:173` (0.70 OCR), `:176` (0.90 text), `:242` (0.65), `:645` (0.85 DXF), `:656` (0.70 image). BOQ line: literal per line in `boq_generator.py:285-415` (85, 90, 92…). The only "measured" signal is halving on a failed reconcile, `:678`. |
| No accuracy benchmark | **CONFIRMED MISSING** | No takeoff cases in `evals/cases/`; tests in `tests/test_takeoff.py` cover pricing arithmetic only, never measurement against ground truth. |
| `geometry_reconciled()` only warns | **CONFIRMED** | `plan_reader.py:583` "never blocks anything"; `takeoff/api.py:98-122` runs read → BOQ → orders straight through. |
| No propose-confirm gate in takeoff | **CONFIRMED** | Same pipeline. **Note on migration 137:** `commerce_automation_firings` has `automation_id uuid not null references commerce_automations(id)`, so its table can't hold a takeoff review without a fake automation. Proposal (no duplicate table): reuse its *pattern* (`pending → approved/rejected`, `decided_at`, who) on the takeoff job row we already persist (`vula_takeoff_jobs`, migration 180, `state` jsonb). The job stops at `needs_review`, and pricing runs only after `POST /takeoff/{id}/review` approves it. **Your call, see Q3.** |
| Flooring is area × rate only | **CONFIRMED** | `boq_generator.py:333-361` `finish_map`, room area × `lvt_vinyl` rate. No roll width, lanes, seams or waste. A waste pattern already exists to reuse: `vula/commerce/costing.py:150,376` (`waste_pct`, 0-100 validated). |

### NEW defects found in takeoff (not in the brief)

| ID | Severity | Defect | Evidence |
|---|---|---|---|
| NEW-T1 | **HIGH** | **DXF ignores drawing units.** Shoelace area is taken in raw drawing units and reported as m². A plan drawn in mm (the SA norm) gives areas 1,000,000× too big. `$INSUNITS` is never read and the sheet is hard-labelled `1:100`. | `plan_reader.py:295-301` (polylines), `:331-365` (area/perimeter), `:639-645` |
| NEW-T2 | **HIGH** | **Scraped market rates never price a BOQ line.** Scraper keys come from labels (`porcelain_tiles_600x600__laid_`, `construction_rates_scraper.py:342`). BOQ looks up `LIKE '%tiles_porcelain_floor%'` (`boq_generator.py:171-185`), which never matches, so every line uses the built-in `FALLBACK` table. When a loose `LIKE` *does* match, the unit isn't checked. | as cited |
| NEW-T3 | **HIGH** | **Mezzanine slab priced per m² at a per-m³ rate.** `"m²", mez_area, "concrete_30mpa"`; the rate is R2,100-2,600 **per m³**. A 150 mm slab is 0.15 m³/m², so the line is about 6.7× too high. | `boq_generator.py:297` vs `:129` |
| NEW-T4 | MED | BOQ money is float rands (`mid * qty`), not integer cents (a CLAUDE.md non-negotiable). `boq_online.py` already does it right (`amount_cents`, half-up). | `boq_generator.py:262-264`; `commerce/boq_online.py:64` |
| NEW-T5 | MED | Two pricing paths: takeoff's `RateLookup`/`FALLBACK` vs `boq_template.library()` (tenant rates + learned rates) that `boq_online` uses. Takeoff output can't be opened as an online BOQ draft. Proposal: the reviewed takeoff becomes a `vula_boq_drafts` draft (migration 227) instead of extending the parallel generator. | `takeoff/api.py`, `commerce/boq_online.py:154` |
| NEW-T6 | MED (POPIA) | Uploaded plans are kept forever. `takeoff/api.py:222` writes `UPLOAD_DIR/{job}_{filename}` and nothing deletes it. `vula_takeoff_jobs` rows also have no retention. Filename is used unsanitised in the path. | `takeoff/api.py:222`; `config.py:94` |
| NEW-T7 | LOW | DXF door/window detection: any block name containing `DR` / `WD` (e.g. `DRAIN`, `WDB`) is counted as a door/window of a fixed 0.9×2.1 / 1.2×1.2 size. | `plan_reader.py:317-320` |
| NEW-T8 | LOW | GFA-driven lines (sprinklers, electrical, paint) are priced even when GFA came only from the LLM and the room schedule disagrees. 4d's gate covers this. | `boq_generator.py:282` |

### Plan for Phase 4 (pending your answers below)

- **4a benchmark:** `evals/takeoff_bench.py` (same shape as `evals/golive.py`: cases → score →
  report, 2% area tolerance, separate length/count tolerances). Fixtures go in
  `evals/takeoff_plans/<case>/plan.{pdf,dxf}` + `truth.json`. CI runs the **deterministic** cases
  (synthetic DXF in mm/m, a text PDF with a room schedule) on every push. Real anonymised plans run
  the same harness with `--live` (needs the model). I'll generate synthetic fixtures now; **I need
  your real plans and hand measurements** for the meaningful numbers.
- **4b calibration:** `DrawingSheet` gains `scale_ratio`, `scale_method`
  (`dxf_units | title_block | user_two_point | dimension_check`), `scale_verified_by`. DXF uses
  `$INSUNITS` (the default 1:1 when unitless is flagged, never assumed). Stored in the job state
  (no new table).
- **4c measured confidence:** confidence = agreement between independent readings (LLM room
  schedule vs DXF geometry vs GFA), plus that tenant/plan-type's benchmark history. The literals at
  `plan_reader.py:173,176,242,645,656` go.
- **4d blocking review:** fails/out-of-tolerance → job status `needs_review`; nothing reaches
  `BOQGenerator.generate()` until approved; approval is recorded (who, when, the quantities they
  accepted or edited).
- **4e sidecar decision:** after 4a numbers exist. No OpenTakeoff code is pulled until then.
- **4f flooring:** only if a tenant uses it (Q4).
- **4g POPIA:** retention setting (`takeoff_retention_days`), daily purge of files + job state,
  a delete endpoint, and a sanitised filename.
- Fix NEW-T1..T4 first, each with a failing-first test.

---

## Questions I need answered before building

1. **Production access:** may I make **read-only** calls to production? That means Supabase
   `list_tables`/`get_advisors`/`select` on the backup tables, Railway reading the single
   `ENFORCE_TENANT_AUTH` variable, and `GET /ready`. Nothing written.
2. **Migration runner:** OK to add `vula_schema_migrations` (new migration 240/241) and a
   `DATABASE_URL`-based runner, dry-run by default, never auto-run on deploy until you say so?
3. **Takeoff review gate:** reuse the job row (`vula_takeoff_jobs.state`) with a 137-style
   `pending/approved/rejected` status (my recommendation, no new table), or do you want a
   general `review_queue` table that 137's firings would later move to?
4. **Flooring (4f):** Gerflor's tenant is set up as a sales-rep CRM (`migrations/127_seed_gerflor_tenant.sql`:
   modules `crm, followups`, no takeoff). Will Gerflor or anyone else price sheet vinyl/LVT
   through takeoff? If yes, send one real Gerflor job (plan + their cut list / roll order).
5. **Benchmark plans:** please send 3-10 anonymised plans (ideally: one DXF, one CAD-exported
   PDF, one scanned PDF) with your hand measurements (room areas m², wall lengths m, door/window
   counts). Until then I'll build and CI-run the harness on synthetic fixtures only.
6. **NEW-M1 (Graph v19.0):** OK to centralise and bump to the current Graph version? It touches
   every WhatsApp call, so it should go through staging first.

## Final status (2026-10-31, end of this pass)

Branch `ccr-f7a53584-worzz4`. Full suite **6,853 passed, 2 skipped, 0 failed** (start of day: 6,741 / 2 /
0). Also green: `ruff check vula/ core/`, import check, `check_migrations_rls.py`,
`check_migration_sentinels.py`, `check_client_secrets.py`, `python -m evals.takeoff_bench` (4/4),
dashboard `npm run build` + `npm run test:unit` (38/38) + size and token checks. **Nothing in
production was changed.** Production was only read (Supabase SQL selects and advisors, Railway
logs).

### Verified against production (read-only)

| Item | Result | How |
|---|---|---|
| `ENFORCE_TENANT_AUTH` on in production | **VERIFIED ON** | Production booted ≥6 times on 31 Oct (Railway logs "Application startup complete"). The boot error `ENFORCE_TENANT_AUTH is off…` (`vula/api/server.py:1732`) appears **zero** times since 24 Oct. |
| Live WhatsApp token works | **VERIFIED** | Meta's recent error responses are `132001 Template name does not exist`. That is a template error after the token authenticated. There are no 190 (invalid token) errors since 28 Oct. The 4 connected numbers in `vula_whatsapp_accounts` carry no per-tenant token (they use the platform token). The 3 rows in `ops_wa_token_backup_20261022` are old per-tenant **user** tokens, stored in plain text. |
| **NEW-P1:** two scheduled templates fail twice a day | **OPEN** | `seedleme_delivery_briefing` (~04:30 UTC) and `seedleme_sales_summary` (~16:00 UTC) fail every day since at least 28 Oct with 132001. The templates don't exist (or aren't approved) on that WABA. Owner messages aren't arriving. |
| Advisors | as audited | 54 × `rls_enabled_no_policy` (INFO), 3 × `function_search_path_mutable`, 1 × security-definer `vula_storage_can_write`, leaked-password protection off. |
| **NEW-P2:** test rows in production's rate table | **OPEN** | `vula_material_rates` has 10 rows with an empty `source` ("Item 0".."Item 4", "OK Item", "Test Tile", "Porcelain Tiles"…). Test data reached production at some point. The takeoff now ignores them. |
| **NEW-P3:** the scraped rates are supply prices | **FINDING** | Every scraped row is a retail supply price (or an AECOM building-type cost per m²). None is a laid/installed trade rate, so no BOQ line can be priced from them. Since `1feefaa` the takeoff refuses to use them, uses the reference table, and labels the line with that source. Pricing BOQ lines from market data needs installed-rate sources (or a materials + labour split). That is a product decision, not a bug fix. |

### Phase-by-phase

| Item | Final | Evidence |
|---|---|---|
| **P0** suite + blockers | DONE | as above; blockers table at the top stands, plus the production checks |
| **P1** migration runner | **DONE (code)** | `tools/migrate.py`, `vula/migrations_files.py`, migration 240, `tests/test_migrate_tool.py` (19). End-to-end run on a throwaway Postgres 16. Not run against any Supabase project. |
| **P1** smoke test | **DONE (code)** | `tools/smoke.py`, `tests/test_smoke_tool.py` (8). Can't reach production from this sandbox (egress blocked). Run it yourself, see the checklist. |
| **P1** readiness gaps | **DONE** | `/ready` "settings" list: PUBLIC_BASE_URL, email, PayFast, Yoco (never failing). Fails staging on prod PUBLIC_BASE_URL. `tests/test_readiness.py` +3. |
| **P1** staging env itself | **NOT CREATED** | No Supabase branch exists (`list_branches` → none). Creating one is billed hourly, so it's on your checklist with exact steps (`docs/staging.md`). |
| **P2** search_path | **DONE (migration written, not applied)** | `migrations/241_trigger_search_path.sql` |
| **P2** `vula_storage_can_write` | **KEEP: documented** | `docs/security-model.md` |
| **P2** backup/cleanup tables | **PROPOSED, needs your OK** | SQL below |
| **P2** RLS-no-policy doc | **DONE** | `docs/security-model.md` |
| **P3** Graph API version | **DONE** | `vula/meta_graph.py`, `GRAPH_API_VERSION` default v25.0, `tests/test_graph_api_version.py` |
| **P3** account-health webhooks | **DONE** | `vula/api/whatsapp_account_events.py`, `tests/test_whatsapp_account_events.py` (22) |
| **P3** disconnect | **DONE** | unsubscribes the WABA when its last number goes. Doc corrected. |
| **P3** Embedded Signup v4 | **DONE (already)** | Config ID flow, see table above |
| **P3** go-live eval per tenant | **BLOCKED** | Needs staging (P1) and a model. Not run. Command in the checklist. |
| **4a** benchmark | **DONE, synthetic only** | `evals/takeoff_bench.py`, CI step, `tests/test_takeoff_bench.py`. **No real plans yet**, so there is no real error % to report. |
| **4b** calibration | **DONE** | `DrawingSheet.scale_ratio / scale_method / scale_verified_by`. DXF uses `$INSUNITS`. Review can record "dimension checked". |
| **4c** measured confidence | **DONE** | `vula/takeoff/accuracy.py`. Constants removed (test asserts it). |
| **4d** review gate | **DONE** | `needs_review` → `POST /takeoff/{id}/review`. `BOQGenerator` raises `ReviewRequired`. Dashboard review card. |
| **4e** sidecar decision | **NOT YET DECIDED: skipped for now** | See below |
| **4f** flooring | **NOT BUILT** | Waiting for your answer: no tenant uses takeoff for vinyl/LVT yet (Gerflor is CRM-only) |
| **4g** POPIA | **DONE** | 90-day retention (daily loop), `DELETE /takeoff/{id}`, safe file names |
| NEW-T1 DXF units | **FIXED** | `test_takeoff_measure_fixes.py` |
| NEW-T9 LWPOLYLINE read as 0 m², so **no DXF ever produced a room** | **FIXED** | same |
| NEW-T2 scraped rates never matched | **FIXED** (match by label + unit + band + "installed") | same, using real production labels |
| NEW-T3 slab m² × m³ rate | **FIXED** | same |
| NEW-T4 float money | **FIXED** (integer cents, half-up) | same |
| NEW-T5 two pricing paths | OPEN | takeoff → `vula_boq_drafts` hand-off not built (next step) |
| NEW-T6 plans kept forever | **FIXED** (4g) | |
| NEW-T7 DRAIN = door | **FIXED** | |
| NEW-T8 GFA lines priced unchecked | **FIXED** by 4d | |
| DXF rooms with no finishes priced without flooring | **FIXED** | held for review; finishes set on the review card |

**Correction to the audit (RULE 0).** Migration 013 `vula_approvals` (`vula/commerce/approvals.py`) is
an existing generic approval engine, and I missed it in the first pass. It is WhatsApp/phone-based
multi-approver (`approver_phone` required). The takeoff review is a dashboard check that *edits*
quantities, so the gate stays on the job row, as proposed. If you want takeoff approvals to reach a
QS's WhatsApp too, the job can raise a `vula_approvals` row as well.

### 4e — sidecar decision

**Don't add it yet.** The benchmark exists, but it has only synthetic plans. The DXF reader and the PDF
room-schedule reader measure them exactly (0.00% error), but synthetic plans can't tell us how far
the **AI reader** is off on real drawings. That is the number your rule depends on. Once you send
real plans + hand measurements, run `python -m evals.takeoff_bench --dir evals/takeoff_plans/real
--live`. If `pdf_text`/`pdf_scanned` mean area error is within 2%, skip the sidecar. If it isn't, add it.

Another session ("Vula takeoff accuracy audit", branch `ccr-d6954993-0a7b3b`, commits `574e6b4`,
`76d1ace`) has already built an OpenTakeoff sidecar pinned to `apache-2.0-final` (84c7f48e), with
LICENSE/NOTICE kept. Its bridge and Docker image were **never executed**, and it is **not merged or wired
in** here. If 4e says yes, that branch is the starting point (a three-line hook in
`PlanReader.read`). **A lawyer should confirm the licence position** before it ships: Apache-2.0 at that
tag, AGPL after it, and no later commits may be pulled.

### Production changes awaiting your OK (exact; none run)

1. **Drop the hand-made backup tables** (0 dependents, no code reads them; checked 31 Oct):
   ```sql
   -- 3 rows: old per-tenant WhatsApp USER tokens in plain text (22 Oct). Live numbers use the platform token.
   drop table if exists public.ops_wa_token_backup_20261022;
   -- 45 rows: restore point for the 26 Oct DIGG self-supplier cleanup. Drop once DIGG's suppliers look right.
   drop table if exists public.ops_digg_self_supplier_backup_20261026;
   -- 1,512 rows (id, field, old_value, new_value, at, tbl): undo log of the 2 Oct data cleanup.
   -- Export it first if you might ever need to undo that cleanup.
   drop table if exists public.vula_cleanup_log_20261002;
   ```
2. **Delete the test rows** the suite wrote to production before `tests/conftest.py` was fixed:
   `delete from public.vula_material_rates where coalesce(source,'') = '';` (10 rows) and
   `delete from public.vula_takeoff_jobs where id in ('j1','4aaf1e62-6b3');` (2 rows)
3. **Record migrations, then apply 240 + 241** with the runner (commands in the checklist). 241 only sets
   `search_path`. It changes no ledger behaviour.
4. **Railway:** nothing is required. Optional: `GRAPH_API_VERSION` (default v25.0), `TAKEOFF_RETENTION_DAYS`
   (default 90). `WHATSAPP_API_URL` is no longer read. After any change, run `railway up`.

## No double work: what other sessions built (checked 31 Oct, end of day)

| Session / branch | What it built | Overlap | Action taken |
|---|---|---|---|
| "Vula takeoff accuracy audit", `ccr-d6954993-0a7b3b` (6 commits) | OpenTakeoff sidecar + client; a **second** takeoff corpus in the same `evals/takeoff_plans/` folder with its own truth format and scorer | Benchmark duplicated mine | **Merged into one:** its 6 real public plans and its truth format now feed `evals/takeoff_bench.py`. The sidecar, its client and its scorer stay on that branch for 4e. That session said it has stopped takeoff work. |
| "Vula system architecture review" (no commits) | A handoff note: Ian gives the takeoff build to the takeoff-audit session, and this session drops Phase 4. Plus 10 defects and v1 scope | **Ownership conflict:** written 19:20, but the takeoff-audit session handed the work here at 19:25, probably without seeing the note | **Paused takeoff building here until you confirm the owner.** The 10 defects are listed below for whoever owns it. |
| `ccr-d78dd68e`, `ccr-6f483bd1` | already squash-merged to master (#237, #217, #212) | none | none |
| other `claude/*`, `ccr-ee325c00`, `ccr-e31cb0f5` | old, unrelated | none | none |

**Retention.** Nothing else in the codebase deletes data on a schedule, so the takeoff purge duplicates
nothing. Retention and purge are one mechanism: `vula/retention.py` (one daily sweep, one `Rule` per
kind of data) replaced the takeoff-only loop, so the next kind of expiring data adds a rule, not a
41st loop. Candidates waiting for your decision are in `docs/security-model.md` › Retention.

**Takeoff defects from the architecture-review note, still open (for whoever owns takeoff):**
1. `plan_reader._llm_extract` calls Ollama without Cloudflare Access headers, a task label or a cloud
   fallback, so in production the AI reader probably gets `{}` (no rooms, GFA 0). Since `3ddc00c` such a
   job is held for review instead of producing a ~R50k BOQ from fixed minimums, but the reader still
   needs to go through `core/llm_router`.
2. Every scheduled plumbing fixture is priced as a full bathroom (~R60k) (`boq_generator.py`, plumbing).
3. Windows are priced as solid doors; "glass" is a count priced per m².
4. Counted twice: wet-room tiles in flooring and in tiling. Σ perimeter × height counts shared walls
   twice. Ceilings are priced over full GFA even for "exposed".
5. `BOQ.to_dict` drops rate range, source and by-trade, so the Excel Source column and the Trade Summary are empty.
6. PO numbers restart per job (`PO-YYYYMM-001`) and are the SQLite key.
7. Suppliers keyed by id only, and the `/suppliers` routes take an unchecked `tenant_id` (default "default").
8. Concurrent jobs share `/tmp/vula_sheet_{i}.png`.
9. RFQ sender hard-coded to one client. `/send` is a stub.
10. Test rows in production `vula_takeoff_jobs`: **cause fixed** (`tests/conftest.py`, the suite can no
    longer reach Supabase). Rows to delete: `delete from public.vula_takeoff_jobs where id in ('j1','4aaf1e62-6b3');`

## What Richard must do manually

**This week, before go-live**
- [ ] Review and merge this branch (`ccr-f7a53584-worzz4`). Deploy, then `railway up`.
- [ ] Say OK (or not) to each production change above. I'll run the SQL once you approve.
- [ ] Fix the two failing scheduled templates: create and get approval for `seedleme_delivery_briefing` and
      `seedleme_sales_summary` in WhatsApp Manager on that tenant's WABA (or switch those jobs off).
- [ ] Supabase › Authentication › Policies: **turn on leaked-password protection**.
- [ ] After deploy, from `vula_mind/`:
      `PROD_API_KEY=… python tools/smoke.py --base-url https://vula-group-production.up.railway.app --api-key-env PROD_API_KEY`

**Migrations (once, then the runner replaces pasting SQL)**
- [ ] Get the production Postgres URI (Supabase › Connect › Session pooler, port 5432). Then:
      `export DATABASE_URL=…`
      `python tools/migrate.py baseline --target production --upto 239 --yes --i-mean-production`
      `python tools/migrate.py apply --target production` (dry run, should list 240, 241)
      `python tools/migrate.py apply --target production --yes --i-mean-production`

**Staging (docs/staging.md)**
- [ ] Supabase: create branch `staging` on `vula-production` (billed per hour). Run `migrate.py apply --target staging --yes`.
- [ ] Railway: new environment `staging` (duplicate the service). Set the branch's Supabase keys, a different `API_KEY`,
      `VULA_ENV=staging`, staging's own `PUBLIC_BASE_URL`, Yoco **test** keys, PayFast **sandbox**, `ENFORCE_TENANT_AUTH=true`. Then `railway up`.
- [ ] Meta: add the free test number, add the team's phones as recipients, point its webhook at the staging URL.
- [ ] Vercel: Preview env var `VITE_API_URL` = staging URL (scoped to the staging branch).
- [ ] Run `tools/smoke.py` against staging. Then run the go-live eval per tenant (`digg-demo`, `off-the-hook`, `awake-sa`)
      from Master › Go-live and check each is **≥ 85%**.

**Meta portal (docs/META_WHATSAPP_LAUNCH.md)**
- [ ] Business Verification (Security Centre).
- [ ] App Review: Advanced Access for `whatsapp_business_management` and `whatsapp_business_messaging`, with the screencast.
- [ ] Facebook Login for Business › Configurations: the v4 Embedded Signup configuration. Copy its ID to Vercel
      `VITE_FB_CONFIG_ID` and redeploy.
- [ ] Webhook fields: `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync`, and now also `account_update`,
      `message_template_status_update`, `phone_number_quality_update`, `template_category_update`.
- [ ] Railway has `VULA_FB_APP_ID` and `VULA_FB_APP_SECRET` (webhook signatures need the secret).

**Takeoff**
- [ ] Send 3–10 anonymised plans (DXF, CAD PDF, scanned PDF) with hand measurements. They go in `evals/takeoff_plans/real/`.
      That gives the real error % and decides 4e.
- [ ] Tell me whether any tenant will price sheet vinyl/LVT through takeoff (4f). If so, send one real Gerflor job.
- [ ] If 4e ends up needing the OpenTakeoff sidecar: get a lawyer to confirm the Apache-2.0 position first.
