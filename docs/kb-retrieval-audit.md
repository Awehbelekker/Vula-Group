# KB retrieval audit (Phase 0/1, session 1)

2026-10-16. Step A of the KB-retrieval project: what already exists, so nothing is built twice.
Read from the repo at `0a6bb80` (master) — not from the Supabase connector. Line numbers are as
of that commit; the Step C changes in this branch move some of them.

## The query path, as it actually is

`whatsapp._rag_reply` (`vula/api/whatsapp.py:5875`) → deterministic handlers first (supplier
alias `:5795`, project register `:5922`, project list `:5983`, stock sheet, programme) →
`AgentRunner` → `HRMOrchestrator` picks a skill (mostly `reasoning`) →
`VulaIngestionPipeline.query()` (`vula/ingestion/pipeline.py:1569`) →
`format_kb_chunks` (`core/skills/base.py:842`) → prompt. Customer messages skip all of that and go
to `commerce_assistant`, whose `_retrieve_kb` (`core/skills/commerce_assistant.py:1380`) calls the
same `query()` with no filter.

## Audit table

| item | status | evidence (file:line) | decision |
|---|---|---|---|
| **Step B — eval** | | | |
| Eval framework (routing, tool choice) | PRESENT | `evals/harness.py`, `evals/run.py`, `tests/test_evals.py` | reuse: new `retrieval` layer is a sibling module run from the same CLI |
| Live read-only benchmark on real tenants + stored reports | PRESENT | `evals/benchmark.py:470` `run()`, `vula/api/master.py:1198` `POST /v1/master/benchmark`, `vula_eval_reports` (migration 178) | extend: `benchmark.run()` now adds retrieval rows + a `retrieval` metrics block |
| Replay corpus discipline (no phone numbers, no tenant figures in a public repo) | PRESENT | `evals/replay.py:16-19` | reuse the rule for retrieval cases |
| Retrieval metrics (recall@k, MRR, nDCG) | MISSING | no hit for recall/mrr/ndcg/golden/expected_docs anywhere in `vula_mind/`, `docs/`, `_archive/` | build `evals/retrieval.py` — harness.py scores *routing/tool choice* rows, benchmark.py scores *answers*; neither has a ranked-list scorer, a fixture KB or an embed stub to host. One flat module next to them, same style as `replay.py`. |
| Retrieval case file | MISSING | `evals/cases/` has benchmark/replay/routing/tool_choice only | build `evals/cases/retrieval.yaml` (+ fixture corpus `retrieval_fixture.yaml`) |
| Spec / Gerflor / DIGG failure seeds | PARTIAL (as comments + unit tests, not as eval cases) | `pipeline.py:754-771`, `:1580-1592`, `:855-861`; `reasoning.py:165-170` (fireplace); `reasoning.py:24-31` (R70,400); `tests/test_query_expansion.py` | reuse their wording as seed cases |
| Synthetic question generator | MISSING | — | build (`python -m evals.run retrieval-synth`), writes to git-ignored `evals/reports/` because generated questions carry tenant text |
| **Step C — defects** | | | |
| C1 `[:900]` grounding cut | PRESENT (defect) | `core/skills/base.py:876` (format_kb_chunks), `reasoning.py:84,152,156`, `architecture_planning.py:71,82,87`, `finance_admin.py:573`, `standards_lookup.py:57` (+ `[:1800]` at `:55`). Also `commerce_assistant.py:1391,1395` (`[:400]`), `:2282` (`[:600]`), `:2431` (`[:500]`) — not in the brief, same defect | build one helper `fit_kb_excerpts` in `base.py`, replace all grounding cuts. `menu_page.py:98`, `merchants.py:154`, `doc_titles.py:109`, `reasoning.py:184` (web text) are not KB grounding → skip |
| C1 sources `text` cut | PRESENT (defect) | `verification.py:274` grounds the adversarial checker on `sources[].text`, which every skill cut to 900 chars | the sources text now carries the same excerpt the model saw (not display-only) |
| Chunk size | PRESENT | `config.py:84-85` 512/64; `SemanticChunker` counts **words** (`pipeline.py:637`) → ~3,000–3,500 chars per full chunk, so the 900-char cut dropped ~70% of a full chunk | measure per tenant with `tools/kb_stats.py` (needs prod Qdrant) |
| C2 point ids | PRESENT (defect) | `pipeline.py:1104` `int(md5(chunk_id)[:8], 16)` — 32 bits | extend `upsert_chunks`: uuid5(`tenant:chunk_id`), legacy-id clean-up so re-ingest stays idempotent |
| Collision detector | MISSING | — | build `tools/kb_stats.py` (read-only) |
| Content-hash `doc_id` + supersede | PRESENT | `pipeline.py:1758` `_doc_id`, `:1784` `_supersede_older_versions`, `tests/test_reingest_replaces.py` | reuse, untouched |
| C3 Qdrant pin | PRESENT (defect) | `docker-compose.yml:11` `qdrant/qdrant:latest`; `DEPLOYMENT.md:96,109` bare `qdrant/qdrant` | pin |
| C4 thresholds | PRESENT (hard-coded) | `pipeline.py:1596` `0.35 if authoritative_only else 0.3`; `QdrantStore.search` default `0.3` (`:1135`) | move to `config.py`, same defaults |
| C5 audience / visibility | MISSING | no `audience`/`visibility`/`staff_only` in any chunk payload (`pipeline.py:1106-1116`); the hits in `commerce.py`/`promo.py` are broadcast audiences, unrelated | build (see finding below) |
| **Later phases (facts only, nothing built)** | | | |
| Reranker / cross-encoder | MISSING | no hit | later phase |
| BM25 / sparse / hybrid / RRF / Query API | MISSING | only `points/search` + `points/scroll` (`pipeline.py:1170,1245`); "hybrid" hits are the LLM router | later; the literal keyword pass (`keyword_search`, `pipeline.py:1193`) with full-text index on `text` is the existing partial substitute — extend, don't duplicate |
| Contextual chunks (heading path, table header, small-to-big) | MISSING | chunks embedded as bare text (`pipeline.py:1460`); filename/title not in the vector; table rows rely on the chunker keeping paragraphs | later |
| Facts table | PARTIAL | `vula_filed_documents` with extracted `fields`; `service.find_filed_document` (`service.py:3962`); `filed_amounts_by_filename` (`service.py:4554`) already stitched into `format_kb_chunks` | later: extend these, no new `kb_facts` until reviewed |
| Entities / aliases / graph | PARTIAL | supplier aliases `whatsapp.py:5795`; project register + `project_resolver`; no graph/triples | later: extend aliases + register |
| Document titles | PRESENT | `vula/commerce/doc_titles.py:25` `titles_for` (used by `format_kb_chunks` and `spec_product_missing`) | reuse |
| Abstention / grounding | PARTIAL | `looks_like_tenant_data_question` (`base.py:590`), `SPEC_NOT_FOUND` + `spec_product_missing` (`base.py:768`), `verification.py` adversarial checker, `escalation.should_escalate` (`escalation.py:168`), `extraction_quality.ungrounded_figures`, `benchmark.ungrounded_amounts` | reuse; the retrieval eval measures abstention against the same deterministic guards |
| Answer-time citation check | MISSING (as a gate) | adversarial checker only appends a caveat | later |
| Conflict / contradiction detection | MISSING | only the checker prompt's word "contradictions" (`verification.py:46`) | later |
| Qdrant backups | PRESENT | `vula/integrations/qdrant_backup.py`, `docs/dr.md` | reuse for the C2 migration plan (snapshot before switch) |

## Reuse, do not rewrite

`VulaIngestionPipeline.query()` (expansion, synonym variant, grounded rewrite check, keyword
pass, placeholder stripping), `QdrantStore.keyword_search`, `_doc_id` / `_supersede_older_versions`,
`format_kb_chunks` (filed amounts + titles), `looks_like_spec_question` / `spec_product_missing`,
`looks_like_tenant_data_question`, `core/dry_run.py`, `evals/harness.py`, `evals/benchmark.py` and
`vula_eval_reports`, `qdrant_backup.py`.

## "Things to verify" — results

| claim | verdict |
|---|---|
| Query path `_rag_reply → AgentRunner → skill → query()` | correct, but customers bypass it (`commerce_assistant` calls `query()` directly) and several deterministic handlers answer before any skill |
| bge-m3 / text-embedding-3-small, one unnamed dense Cosine vector per `vula_{tenant_id}` | correct (`pipeline.py:681-735`, `:1073`). Collection name also replaces `-` with `_` (`:1064`) |
| Original + synonym variant + ≤2 grounded rewrites; 0.30 / 0.35; reserved keyword slots; no BM25/fusion/reranker | correct. Additionally: the keyword pass ignores `exclude_source_types`, so `authoritative_only=True` can still return `conversation`/`learned` chunks through it (not fixed here — flagged) |
| `SemanticChunker` 512/64 words, bare text embedded | correct |
| `[:900]` in base.py ~876, reasoning ~152, architecture ~71/82/87, finance ~573, standards ~57; `answer()` doesn't truncate | correct, and incomplete: reasoning also at 84/156, standards uses `[:1800]` for the prompt and `[:900]` for the verifier's sources, and commerce_assistant cuts to 400/500/600 |
| Point ids `int(md5(chunk_id)[:8],16)` | correct (32 bits). Birthday bound: ~1% chance of at least one collision in a collection at ~9,300 chunks, ~50% at ~77,000 |
| `docker-compose.yml` uses `qdrant/qdrant:latest` | correct; `DEPLOYMENT.md` uses the untagged image (same thing) |
| No audience field | correct |
| Latest migration 213 | correct (`213_website_leads.sql`) — this session adds no migration |

## C5 finding — customers can retrieve staff/owner documents

Yes. `commerce_assistant` (customer WhatsApp, `whatsapp.py:8167`) runs
`VulaIngestionPipeline.query(question, top_k=4)` (`commerce_assistant.py:1385`, also `:2281`,
`:2430`) on the whole tenant collection, with no filter at all — not even `authoritative_only`. The
same collection receives:

* every document the owner or staff send on WhatsApp (`whatsapp.py:2348`, "always adds to KB"),
  including supplier invoices with cost prices;
* bank statements (`whatsapp.py:2171`);
* email attachments (`email_imap/sync.py:756`), Google/Microsoft files, ClickUp tasks;
* scan commits with supplier, totals and line items (`commerce/service.py:2821`);
* `learned` chat exchanges (`whatsapp.py:1793`).

So a customer asking a question that happens to embed near a supplier invoice or statement can
get it as grounding. Fix in this branch: `audience` payload field at ingest (`public` / `staff` /
`owner`, default `staff`), keyword index on it, and a mandatory audience filter on every
customer-facing `query()` — including the keyword pass, and failing closed if the filter is
refused. **Existing points carry no `audience` and need a backfill** (below). Until then the
default customer filter excludes only points explicitly tagged `staff`/`owner`; flipping
`KB_CUSTOMER_AUDIENCE_STRICT=true` after the backfill makes it "public only".

Public by default: `website` (site import, Shopify, WooCommerce), `starter`,
`learned_answer_approved`, owner-taught Q&A (`commerce.py:2243`), recipes (`commerce.py:5950`).
Owner: bank statements. Everything else: staff.

## C2 migration plan (next phase — not done in this session)

1. `python tools/kb_stats.py` on the server: points vs distinct chunk ids per tenant, and missing
   `chunk_index` gaps per document (an overwritten point leaves a gap). Confirms whether any
   collision has actually happened.
2. Snapshot every collection (`qdrant_backup.backup_all_tenants()`).
3. Create `vula_{tenant}_v2` with the same vector config + payload indexes (`source_type`,
   `doc_id`, `audience`, full-text `text`).
4. Backfill: scroll v1, write each point to v2 under `uuid5(NAMESPACE, f"{tenant}:{chunk_id}")`
   with its stored vector (no re-embedding), setting `audience` by `source_type`/filename rules
   and the `vula_filed_documents` category. Documents with chunk gaps are re-ingested from
   storage instead.
5. Switch: `_collection_name` reads a per-tenant "collection version" (or a Qdrant alias
   `vula_{tenant}` → v2 via `POST /collections/aliases`, which needs no code change).
6. Rollback: point the alias back at v1 (kept read-only for 14 days), then delete.

New ingests already use uuid5 ids in this branch (`QDRANT_UUID_POINT_IDS=false` reverts), and
`upsert_chunks` deletes the legacy int-id point of the same `chunk_id` so an identical re-upload
still overwrites instead of duplicating.

## Left open by this session (decisions for Ian)

* **Audience backfill** for existing points (none carry `audience`). Rule of thumb: `website`,
  `starter`, `learned_answer_approved` → public; filenames filed in `vula_filed_documents` under a
  money category (invoice, statement, payslip…) → owner/staff; everything else staff unless the
  owner marks it public. Then set `KB_CUSTOMER_AUDIENCE_STRICT=true`. Part of the C2 v2 backfill.
* **Dashboard upload** now accepts `audience=public`, but the dashboard doesn't send it yet. Until
  it does, a menu or policy uploaded from the dashboard is staff-only for customers.
* Invoice catalogue items (`commerce.py:4226`) and the business profile (`business_profile.py`)
  stay staff by default — say if either should be customer-visible.
* The keyword pass ignores `exclude_source_types`, so `authoritative_only=True` can still return
  `conversation`/`learned` chunks through it. Not fixed (out of scope); small fix next phase.
* Real chunk-length distribution per tenant, collision count and the production Qdrant version:
  run `python tools/kb_stats.py` on the server.

---

## Session 1b — audience safety end to end (2026-10-17)

Brief: `docs/vula-accuracy-program.md` (saved this session). Same branch as Session 1.

| item | status before | evidence (file:line at start of 1b) | decision |
|---|---|---|---|
| Audience backfill for existing chunks | MISSING | no set-payload anywhere; `kb_stats.py` only counts | build `tools/kb_audience_backfill.py` (a WRITE tool — kb_stats stays read-only) |
| Customer-view test (vector + keyword, per pilot tenant) | PARTIAL | `tests/test_kb_audience.py` (one tenant, one question); eval `sensitive` cases | extend `evals/retrieval.py` (`customer_view`), CI test + live CLI + benchmark rows |
| Past-exposure evidence | MISSING | retrieved chunks are not logged: `vula_turns` (migration 195) holds steps and full replies only | build `tools/kb_exposure_check.py` (reconstruction; read-only) |
| `page_copy.py` (public web page) | **leaked** | `page_copy.py:123` `query(…, authoritative_only=True)`, no audience | fixed: `audience="public"` |
| Broadcast draft brand voice (`api/commerce.py`) | **leaked** | `commerce.py:5264` `query(…, top_k=2)`, no audience | fixed: `audience="public"`, plus the C1 budget instead of `[:400]` |
| Outsider on a knowledge-mode line | **leaked — not in the brief** | `whatsapp.py:904` `_caller_identity` → (None, None) for any non-team number; `_rag_reply` → `AgentRunner` → every skill with full KB; fallback `whatsapp.py:6107` | fixed: `_rag_reply` runs a non-team sender's turn under `kb_audience("public")`, honoured by every `query()` (`KB_OUTSIDERS_PUBLIC_ONLY`, default on) |
| Storefront / site copy | PRESENT (no KB read) | `vula_storefront`, `off_the_hook` are submodules not checked out here; backend endpoints they call that read the KB: none besides commerce_assistant | skip — re-check when the submodules are present |
| `draft.py`, `/query`, `commerce_admin`, `service.find_filed_document` | staff-only callers | authenticated dashboard / role-gated admin | skip |
| Keyword pass ignores `authoritative_only` | PRESENT (defect) | `pipeline.py:1193-1270` | fixed in Qdrant filter + result check |
| Upload toggle / badge / customer preview | MISSING | `VulaDocuments.jsx` upload sends tenant + files only | built (dashboard + 3 endpoints) |
| Owner choice survives re-upload / re-sync | MISSING | ingest re-applies the default | built (`QdrantStore.owner_audience`) |
| Business profile public/private | MISSING | `business_profile.py:130` one document, no source_type → one audience | built: two documents, owner list (`public_keys`, migration 214) |
| Invoice-derived items | PRESENT | `commerce.py:4226` (`invoice_item_*`) and `service.py:2821` (scan commits) both ingest with default → staff | keep staff. Correction: `invoice_item_*` are the tenant's own invoicing catalogue (its SELLING prices), not supplier costs; the supplier-invoice text comes from scan commits. Customers get prices from `service.list_products`. |

Shared Vula collections (`vula_training`, `business_basics`, `vula_network`, `sector_*`) are
public by collection (`pipeline.is_shared_collection`), so an outsider still gets general SA
guidance.
