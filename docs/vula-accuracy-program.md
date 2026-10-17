# Vula Accuracy Program: master brief for Claude Code

Save this file in the repo as `docs/vula-accuracy-program.md`. It is the single source of truth for a multi-session program. Each session starts with a short kickoff prompt (section 4) that tells Claude Code to read this file and execute only that session.

Work from `vula_mind/` (the real code is one level above the nested scratch working directory). Read `CLAUDE.md` first and obey its non-negotiables: tenant isolation and RLS on every table, local-first generation with logged cloud reasons, verified-not-reported, POPIA (no raw tenant text, questions or answers in telemetry, only labels, counts, scores, hashes), money as integer cents computed server-side.

---

## 0. Operating rules (apply to every session)

1. **No-double-build protocol, before any code.**
   - Run `git status`, `git branch -a`, `git log --oneline -30`, `git stash list`. If files you plan to touch have uncommitted changes (Ian keeps VSCode buffers open; unsaved buffers silently revert edits), stop and report. Re-read each file from disk after editing it.
   - Grep case-insensitively across `vula_mind/`, `docs/`, `evals/`, `tests/`, `migrations/`, `_archive/` and TODO/FIXME comments for every concept in your session's scope, plus its likely synonyms.
   - Read the existing modules in scope before deciding anything.
   - Append your findings to `docs/kb-retrieval-audit.md` as a table: `item | PRESENT / PARTIAL / MISSING | evidence file:line | decision (reuse / extend / build / skip)`.
   - PRESENT: do not touch (add a test only if untested). PARTIAL: extend in place, same module, same naming. MISSING: build. If a new thing overlaps an existing one, extend the existing one. A new module needs a one-line justification of why no existing module could host it.
   - Re-grep for a name and purpose just before creating each new file or function (another session may have added it).
   - The repo wins over this document. Where a claim here is wrong or already fixed, say so in your report.
2. **Measure before and after.** Every change ships with a test and an eval number. Numbers go in `evals/RESULTS.md` (one table per workstream, dated). No change merges on "it looks better".
3. **Everything behind a setting** in `config.py`, default OFF until its eval gate passes, then enabled per tenant (pilot tenants first), then default on. Reuse the `VERIFICATION_POLICY_OVERRIDES` env-override pattern where a runtime switch is needed.
4. **Branch per session**, `accuracy/<workstream>-<short-name>`, small commits, never push to main.
5. **Quality gates** before finishing any session: `pytest tests/` (do not reduce or skip tests), `ruff check vula/ core/ --select=F --exclude scripts,tests`, `python tools/check_migrations_rls.py`.
6. **Migrations**: new tables get RLS enabled, idempotent SQL, number after the highest existing file (it was `213_website_leads.sql`; re-check). Migrations are applied manually in the Supabase SQL editor, so print the exact SQL and say it needs running. Do not use the Supabase MCP connector for inspection (it returns the wrong organization); read `migrations/` instead.
7. **Destructive or bulk operations on tenant data** (reindexing, re-reading filed documents, cleanups): preview first, apply only on request, resumable, idempotent, with rollback. Never reindex in place; use versioned collections.
8. **Style**: dated comments (`# 2026-10-17: ...`) explaining why, including the real failure that motivated the change, as the repo already does.
9. **Deployment gotchas**: after changing Railway env vars you must `railway up` again; risky changes go through staging first (`docs/staging.md`; staging is not set up yet, so flag it as a prerequisite rather than skipping it).
10. **Final report per session** (one short message): audit result (reused / built / skipped and why), before-and-after numbers, claims in this brief that were wrong, blockers or decisions needed from Ian, exact commands or SQL Ian must run by hand. Then stop.

## 1. North-star metrics (what "best in the business" means, measured)

| Area | Metric |
|---|---|
| Retrieval | recall@5, recall@20, MRR, nDCG@10 per tenant and question type |
| Honesty | correct-abstention rate on unanswerable questions; unsupported-claim rate; numeric groundedness |
| Extraction | field-level accuracy (supplier, doc number, date, VAT, total, line items); hallucination rate; auto-filed-correctly rate |
| Filing | catch-all category rate; invoices missing supplier or total; documents with no project; names not on the register |
| Routing | routing accuracy on labelled messages (keyword layer and fallback separately) |
| Language | the above, split by language (English, Afrikaans, isiZulu, isiXhosa, Sesotho, mixed) |
| Cost and speed | p50 and p95 reply latency; tokens and cloud calls per reply; share of cloud escalations with a logged reason |

Targets are set after the baselines exist. Do not invent targets in advance.

## 2. What the code review found (verified 2026-10-17 on a shallow clone; confirm each)

**Retrieval** (`vula/ingestion/pipeline.py`, `core/skills/base.py`, `core/skills/reasoning.py`):
- Query = original + synonym variant + up to two grounded LLM rewrites, dense vector search per variant (threshold 0.30, 0.35 when `authoritative_only`), plus a literal keyword pass with hand-tuned reserved slots. No BM25 or sparse vectors, no fusion, no reranker.
- Chunking: `SemanticChunker`, 512 words, overlap 64 words; chunks embedded as bare text (no document, section or table-header context).
- `format_kb_chunks` (about `core/skills/base.py:876`) cuts each chunk to 900 **characters**; the same cut appears in `reasoning.py`, `architecture_planning.py`, `finance_admin.py`, `standards_lookup.py`. A 512-word chunk is roughly 3,000+ characters.
- Point IDs are `int(md5(chunk_id)[:8], 16)`: 32 bits, collisions overwrite silently.
- `docker-compose.yml` uses `qdrant/qdrant:latest`. Single unnamed dense vector, Cosine.
- No `audience` field in chunk payloads; no entity or graph tables.

**Documents and filing**:
- OCR chain is already careful: PyMuPDF text (in a child process) -> pdfplumber -> local GLM-OCR -> cloud vision (with hallucination checks) -> Docling / Tesseract. Money extraction verifies arithmetic and that every figure exists in the source (`vula/commerce/extraction_quality.py`). `vula/commerce/reread.py` re-analyses documents whose first read was empty.
- Two classification systems exist: `_classify_document` (`vula/api/whatsapp.py` about line 4449, single caller about line 2627) uses filename keywords and reads text only for `.txt/.csv/.md`; the model-based analysis (`_analyze_document`) assigns its own category and summary. `vula/commerce/doc_quality.py::category_from_summary` patches catch-alls afterwards.
- `filed_amounts_by_filename` (`vula/commerce/service.py` about line 4554) joins KB chunks to filed documents by **filename** and keys its result by filename, so two different documents with the same name collide and a chunk can get the wrong verified figure.
- Production snapshot in the `doc_quality.py` docstring (2026-10-02, may be outdated; call its `health` function for current numbers): digg-demo 759 documents, 214 in a catch-all category, 66 of 223 invoices with no supplier, 63 with no total, 347 waiting on "which project?", 192 filed under names not on the project register; off-the-hook 273 documents, 101 catch-all. `cross_check.py` (2026-09-28) recorded bank lines matched to anything: 3 of 372 and 2 of 742.
- `ingest_file` is called from email sync, Microsoft sync, onboarding and WhatsApp document paths. Whether transactional documents (receipts, bank notices) are being chunked into the general KB alongside policies and specs is **unverified**.

**Vula Mind**:
- Routing = `core/hrm/orchestrator.py::SKILL_KEYWORDS`, an ordered dict of substring matches (order matters); an LLM classification only on a keyword miss; fallback `reasoning`. `core/skills/registry.json` is loaded but never used for routing.
- `evals/cases/routing.yaml` has about 36 deterministic cases with the LLM fallback disabled; there is no eval of the LLM fallback or any learned router.
- `ReflectionAgent.get_routing_hints` is consulted in `core/agent_runner.py` (about line 87); whether it measurably changes outcomes is **unverified**.
- WhatsApp calls are capped at `max_branches=1`, so multi-branch ThinkMesh reasoning rarely runs there.
- Voice notes: only English and Afrikaans speech is accepted; other languages are asked to type (`core/lang.is_voice_supported`). The go-live audit also flagged English/Afrikaans-only escalation detection.

## 3. Workstreams

### WS1: Eval foundations (gate for everything)
Extend the existing eval framework (`evals/harness.py`, `run.py`, `benchmark.py`, `replay.py`, `cases/*.yaml`); create `evals/retrieval/`, `evals/extraction/`, `evals/filing/` only where the existing harness cannot host them.
- **Retrieval eval**: YAML cases with `tenant, question, type (lookup|spec|price|policy|multi_hop|follow_up|sensitive|unanswerable), expected_docs, must_contain, language, synthetic`. Seed from real failures in code comments and tests: Gerflor "Which importer does the creation range", "How stocks creations", "Who imports the Creation range?"; "what is the slip rate on the Affinity?"; the DIGG cast-iron-fireplace colour question wrongly answered from a flue-material rule; the R70,400 "logged" fabrication class; plus terse, misspelled, Afrikaans and mixed-language variants. Add synthetic questions per document (5 to 10, hand-check a sample) and 15 to 25 unanswerable questions per tenant. Keep a **holdout slice** never used for tuning.
- **Extraction eval**: ground truth from owner-confirmed fields, invoices whose line items reconcile to the total, and hand-labelled samples; field-level accuracy, hallucination rate, latency per engine.
- **Filing eval**: extend the benchmark's existing filing-accuracy measurement with category correctness, project resolution correctness and catch-all rate, run read-only on real tenants.
- **Routing eval**: grow `routing.yaml` to hundreds of cases from real traffic, in two suites: the deterministic keyword layer (CI) and the LLM/learned layer (live, since it needs a model).
- Two run modes: deterministic fixtures in CI (stubbed embedder, no network), and live read-only runs through the Master benchmark route. JSON report per run, trend table in `evals/RESULTS.md`, always with latency and token columns.

### WS2: Known defects (low risk, immediate)
- Chunk truncation: measure the real chunk-length distribution per tenant; replace every grounding `[:900]` with one shared token-budget helper (highest-ranked first, never cut mid table row or sentence, keep the title tag); test that a fact at the end of a long chunk reaches the prompt; keep 900 only for display previews and say so in a comment.
- Point IDs: collision detector per tenant; deterministic 128-bit IDs (UUID5 of `tenant_id:chunk_id`) for new ingests; plan the rebuild via versioned collections (WS3).
- Pin Qdrant in `docker-compose.yml` and `DEPLOYMENT.md`; find and report the version production runs; choose a pin at or above the highest version the plan needs (Query API with RRF v1.10.0+, server-side BM25 v1.15.3+, weighted RRF v1.17.0+, adding a sparse vector to an existing collection v1.18). Do not guess the production version.
- Thresholds 0.30 and 0.35 into settings (per embed model if cheap), no behaviour change yet.
- Audience safety: determine whether customer-facing paths can retrieve staff-only or owner-only chunks; if yes add an `audience` payload field (conservative default), a keyword index, and a mandatory filter on customer-facing queries, with tests and a backfill plan.
- Join by `doc_id`, not filename, in `filed_amounts_by_filename` and any similar helper; handle same-name documents; add a collision test.

### WS3: Retrieval upgrade
1. **Contextual chunks, deterministic first**: prepend (embed, store separately as `context`) document title (`doc_titles`), filename, page, heading path, table header row and the row's first-column item. Present context separately from chunk text in prompts. Optional LLM-written 50 to 100 token context per chunk, routed through `core/llm_router.py` (local default, cloud only with a logged reason: this is tenant text), per-tenant flag, kept only if the eval says so.
2. **Structure-aware chunking**: heading hierarchy, token-based sizes, tables kept whole or split by row groups with repeated headers, one row per chunk for price and spec sheets, small-to-big (index small, return parent section). Use the existing optional Docling path where fitz scrambles layouts.
3. **Safe reindexing**: versioned collections (`vula_{tenant}_v2`), resumable backfill per tenant, switch by setting or alias, instant rollback, preserve `_supersede_older_versions` and content-hash `doc_id` idempotence.
4. **Hybrid retrieval**: dense + sparse BM25 (IDF modifier) fused with RRF through the Qdrant Query API (`prefetch` per retriever); replace the scroll-based keyword pass and reserved-slot heuristic; keep the synonym map and `_spec_expansion` as query-time expansion; test weighted RRF and DBSF; raise per-retriever prefetch limits (start 50 to 100). Verify whether BGE-M3 sparse or multi-vector outputs are obtainable through Ollama; if not, use server-side `Qdrant/bm25`. Check BM25 behaviour on Afrikaans.
5. **Reranking**: retrieve wide, rerank with a multilingual cross-encoder behind a `Reranker` interface (local service on the GPU box behind the Cloudflare tunnel; cloud provider with a logged reason; fail open to fused order; cache; latency budget). Verify how bge-reranker-v2-m3 can actually be served; do not assume Ollama exposes a rerank endpoint.
6. **How many chunks to pass**: test N = 5, 8, 12, 20 under a token budget, per skill and per model; pick by measured accuracy.
7. **Query understanding**: classify lookup | spec | price | policy | multi_hop | follow_up | conversational; rewrite follow-ups into standalone queries using conversation history, keeping `_grounded_rewrite`; apply metadata filters (category, source_type, audience, recency).
8. **Freshness**: carry `valid_from` / `valid_until` or document date; prefer newest versions; flag stale answers.

### WS4: Document intelligence (OCR, classification, filing)
1. **Document registry as the spine**: one row per document (`doc_id`, `content_hash`, type, parties, dates, amounts, project, status, per-field confidence, source), with KB chunks, facts and filed rows all carrying `doc_id`. Reuse `vula_filed_documents` and `doc_id` if they already serve; extend rather than add a parallel table.
2. **Intake quality gate**: blur, skew, glare and crop checks before OCR; ask for a retake on WhatsApp when unreadable; reuse `image_prep.py`.
3. **OCR model selection by eval, not opinion**: on the extraction eval compare current GLM-OCR, a Qwen2.5-VL-class model, other local candidates (for example DeepSeek-OCR 2, dots.mocr) and the cloud route, on the local GPU box. Report field accuracy, hallucination rate, latency, cost. Switch only on evidence. Keep the existing hallucination checks, child-process isolation and escalation chain.
4. **Content-based classification**: one classifier that uses extracted text and layout with a fixed taxonomy, a confidence, and an explicit "unknown, ask" outcome; deterministic parsers stay as fast paths for rigid layouts (payment notices, bank statements); merge `_classify_document` into it (single source of category truth) and fix the PDF/image-by-filename gap.
5. **Field validation and calibrated confidence**: rule checks per field (VAT number format, date plausibility, amount versus supplier history, supplier against the known party list); two-model agreement for high-value documents; thresholds per document type and field; low-confidence items go to the owner review queue; owner corrections are stored as calibration data and recalibrate thresholds. Raw model self-reported confidence is not trusted.
6. **Project and party resolution**: auto-assign from supplier history, alias registry, site address, dates, email thread, programme and ClickUp; propose with one-tap confirm over WhatsApp using the existing propose-confirm gate (migration 137); reduce "which project?" and off-register names; add a canonicalisation job (preview then apply).
7. **Ingest policy (what goes where)**: knowledge documents (policies, specs, price lists, procedures) go to KB chunks and facts; transactional documents (invoices, receipts, bank notices) go to structured records plus a searchable summary, not general retrieval, unless the audit shows a reason. Document the policy and enforce it at the ingest call sites (email sync, Microsoft sync, onboarding, WhatsApp).
8. **Multi-document files**: detect PDFs containing several invoices or statements and split them (audit first; build only if the data shows it happens).
9. **Backfill with preview**: extend `reread.py` to re-run improved extraction and classification over existing documents; only changes that add information replace what is filed; preview first, apply on request; never auto-book into the books.
10. **KPIs**: catch-all rate, missing supplier and total rates, project-unresolved rate, auto-filed-correctly rate, all from `doc_quality.health`, tracked weekly.

### WS5: Facts layer, conflicts, graph-lite
1. **Facts table** (`kb_facts`, RLS): `tenant_id, entity, attribute, value, unit, value_numeric (integer cents for money), source_doc_id, page, valid_from, valid_until, confidence, verified_by`; indexes on `(tenant_id, entity, attribute)`. Extract at ingest for tabular and spec documents with the existing tiered pattern (deterministic first, LLM second, grounding check, stage unverified facts for owner review). Retrieval router queries facts first for lookup, spec and price questions, answers deterministically with the source, falls back to chunk retrieval.
2. **Conflict detection on structured facts** (not by asking an LLM to compare two texts): same entity and attribute with different values across documents or dates; surface to the owner ("price list says R450, last invoice R480"), with resolve actions (supersede, mark exception, ask supplier). Research shows contradiction detection across retrieved documents is hard for LLMs, so this stays rule-based.
3. **Bitemporal answers**: "what did we quote on date X" from `valid_from` / `valid_until`.
4. **Entity and alias layer (graph-lite), gated**: `kg_entities` and `kg_edges` (RLS) seeded from the existing supplier-alias and project-register data; extraction stores source chunk IDs; use only for entity linking, alias resolution and capped 1 to 2 hop expansion. Enable per tenant only if multi-hop questions improve meaningfully with no loss on lookups and prompt size stays in budget. A full Microsoft-style GraphRAG pipeline is out of scope (heavy indexing, very long prompts, depends on larger LLMs than the local models). Consider HippoRAG-style PageRank retrieval over this graph only if the eval justifies it.

### WS6: Answer-time grounding and safety
- **Evidence gate before generation**: retrieval-confidence signal (top rerank score, margin, coverage of the question's entities and numbers); below threshold ask one clarifying question, state what was and was not found, or escalate (reuse `should_escalate` and the learned-answer flow).
- **Cite or drop**: structured output naming the chunk or fact ID behind each claim; deterministic check that every number, unit, code and date in the answer appears in the cited evidence (generalise `ungrounded_figures` / `ungrounded_amounts` beyond money); remove unsupported sentences or retry once, then abstain. Keep the adversarial verification pass for high-stakes tenants (fail-open on infrastructure errors; `strip_caveat()` before persisting history).
- **Disagreement means abstain (experimental)**: for money and spec answers, generate with two models or branches; if the figures differ, escalate to the owner. Evaluate cost versus accuracy before enabling.
- **Per-tenant pillars and guidelines**: a rules layer separate from documents (allowed topics, tone, mandatory handling of sensitive topics). Reference case: a church tenant where addiction and self-harm topics route to the care team with a fixed, safe response, never an improvised one. Build the guided setup so every tenant can feed the KB and define pillars; enforce before generation; eval cases (including adversarial phrasings and Afrikaans) must pass.
- Keep: the "a related document is not an answer" rule, spec-not-found refusal, placeholder stripping, `fence()` prompt-injection hardening.

### WS7: Vula Mind (routing, learning, reasoning)
- **Routing**: keep deterministic keyword routes for safety-critical paths; add a learned or LLM router for the rest, with a confidence value and a clarifying question when ambiguous; measure on the grown routing eval (both suites); resolve or delete the unused `registry.json`; document the real routing order in one place.
- **Prove the learning loop**: confirm `get_routing_hints` changes routing or tier decisions; A/B on the eval; if no measurable effect, simplify or fix it rather than keep it as decoration.
- **Multi-branch experiment**: on money and spec answers only, compare 1 branch versus 2 on accuracy, latency and cost under the WhatsApp cap; enable only if worthwhile.
- **Per-turn traces**: route, retrieved evidence IDs, tool calls, verification result, cost; labels and hashes only (POPIA); every production failure becomes a replay case (`replay.py`).
- **Tool-choice evals**: grow `cases/tool_choice.yaml` per skill, especially tools that spend money or message customers (confirm-before-send stays).

### WS8: Language and voice
- SA-language eval set (isiZulu, isiXhosa, Sesotho, Afrikaans, English, code-switched) for retrieval, extraction and escalation detection; fix English/Afrikaans-only escalation detection (go-live blocker).
- Benchmark voice-note transcription on real notes: current local Whisper versus Lelapa AI's Vulavula API (lists isiZulu, Sesotho and Afrikaans speech-to-text) and any other candidates; spoken-number and currency normalisation ("two hundred and fifty rand"). Check any cloud use against POPIA and the local-first rule.
- Note the naming proximity between Lelapa's product "Vulavula" and "Vula"; flag it to Ian rather than act on it.

### WS9: Differentiators (visible to tenants)
- **Accuracy scorecard per tenant**: nightly synthetic-customer self-test over the tenant's own KB, accuracy and abstention trend, coverage score, alerts when a new upload breaks previously correct answers.
- **KB health view**: documents never retrieved, unanswered questions (owner queue feeding learned answers), stale or duplicate documents, facts awaiting verification, conflicts.
- **Owner-correction flywheel**: every correction becomes a verified fact with provenance, a calibration data point, and, where appropriate, a routing or retrieval eval case.
- **Document intelligence** (WS4) results shown as tenant-facing numbers (auto-filed correctly, awaiting review).

### WS10: Tuning, then (only if justified) fine-tuning
- **Tune first**: controlled sweeps, one variable at a time, on the eval with the holdout slice: thresholds, prefetch limits, fusion weights, chunk size and overlap, chunks passed, reranker cutoff, abstain threshold, prompt wording, synonym map from real owner questions. Report per tenant and latency, not only averages.
- **Fine-tune only after tuning plateaus below target**: an embedding model or reranker on (question, correct chunk, hard negative) triples built from owner corrections and verified Q&A; not the answering model for facts (facts come from retrieval and citations). At most consider a small local model for format and behaviour (cite, abstain, WhatsApp style).
- **Data governance**: tenant documents and questions are personal information under POPIA; use only opted-in data or per-tenant adapters; no default pooling; get consent first. Build the collection and consent mechanism before any training.

## 4. Session plan and kickoff prompts

Run sessions in order; each ends with a report and waits for Ian's review. Paste the kickoff text into Claude Code.

- **S1 (already issued as the "Session 1 prompt")**: audit protocol, WS1 retrieval eval baseline, WS2 defects. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 1 only: section 0 rules, WS1 retrieval eval baseline, and all of WS2. Do not start any other workstream. Stop and report.`
- **S2**: WS1 extension, extraction, filing and routing evals, plus the WS4 audit and registry design (no behaviour changes yet). Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 2 only: WS1 extraction, filing and routing evals with baselines, and the WS4 audit (current filing KPIs via doc_quality.health, classifier consolidation plan, ingest-policy audit at every ingest_file call site, document registry design). No behaviour changes. Stop and report.`
- **S3**: WS3 retrieval upgrade, in the order listed, each step gated by the retrieval eval. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 3 only: WS3 steps 1 to 8 in order, each behind a setting and gated by the retrieval eval, with versioned reindexing and rollback. Stop after each step if the eval does not improve. Report.`
- **S4**: WS4 document intelligence build. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 4 only: WS4 items 1 to 10 using the Session 2 audit and baselines. Preview-then-apply for any change to existing documents. Report KPIs before and after.`
- **S5**: WS5 facts layer and conflict detection first; the graph-lite layer only if the eval gate is met. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 5 only: WS5 items 1 to 3, then item 4 only if multi-hop eval cases show a gain without lookup regressions. Report.`
- **S6**: WS6 grounding, abstention, pillars and guidelines. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 6 only: WS6, including the pillars and guidelines setup and its eval cases. Report.`
- **S7**: WS7 Vula Mind. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 7 only: WS7, measuring before changing anything; delete or wire registry.json; report whether routing hints change outcomes. Report.`
- **S8**: WS8 language and voice. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 8 only: WS8, starting with the SA-language eval set and the escalation-detection gap. Report.`
- **S9**: WS9 scorecard, KB health and the correction flywheel. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 9 only: WS9. Report.`
- **S10**: WS10 tuning sweeps; fine-tuning only if the plateau condition holds and consent exists. Kickoff: `Read docs/vula-accuracy-program.md. Execute Session 10 only: WS10 tuning sweeps with the holdout slice; do not fine-tune unless the plateau and consent conditions in the brief are met. Report.`

Dependencies: S1 before everything; S2 before S4; S3 before S6 (the evidence gate needs rerank scores); S5 before the answer-from-facts routing in S6; S7 and S8 can run in parallel with S3 to S6 once S1 and S2 exist.

## 5. Definition of done

- Baselines recorded before every workstream, and a dated results table after each.
- The audit table covers every item, with PRESENT items reused and nothing built twice.
- Retrieval, extraction, filing, routing and language evals live in CI (deterministic subset) and in the Master benchmark (live), with a weekly scheduled run and a trend line.
- Hybrid retrieval, reranking, contextual chunks, evidence gate and cite-or-drop are enabled per tenant only after passing their gates.
- Filing KPIs (catch-all rate, missing supplier and total, project-unresolved rate) improved against the Session 2 baseline.
- `CLAUDE.md` updated: RAG and ingestion section, routing section, the document registry and ingest policy.
- No regression in the existing test suite; all quality gates pass.

## 6. Decisions needed from Ian before or during the program

1. Which Qdrant version does production run, and is a staging environment (Supabase branch plus Railway `staging`) set up before risky changes?
2. Pilot tenants for each gate (the code and comments mention gerflor, digg-demo, off-the-hook); which tenants may use cloud reranking, cloud contextualisation or cloud vision, given POPIA?
3. Budget ceiling for cloud calls (rerank, vision, contextual chunks) and the p95 latency target for WhatsApp replies.
4. Who confirms ground truth for extraction and filing evals (owners via WhatsApp confirm, or Ian)?
5. Consent and data policy for any training use of tenant data.
6. Whether Vulavula (Lelapa AI) is acceptable as a cloud voice option for opted-in tenants.

## 7. Evidence and what is not verified

- Contextual retrieval (Anthropic, own benchmark): contextual embeddings cut top-20 retrieval failures 35%, with contextual BM25 49%, with reranking 67% (5.7% to 1.9%); reranking took the top 150 and kept 20; passing 20 chunks beat 5 or 10; skip RAG for knowledge bases under about 200k tokens. https://anthropic.com/news/contextual-retrieval
- Qdrant hybrid search, RRF and DBSF, version requirements, multi-stage queries. https://qdrant.tech/documentation/concepts/hybrid-search/ and https://qdrant.tech/documentation/search-tuning/hybrid-search/
- BGE-M3 (dense, sparse, multi-vector; 8192 tokens; 100+ languages). https://huggingface.co/BAAI/bge-m3
- Graphs in RAG (ICLR 2026, GraphRAG-Bench on medical guidelines and novels, not SME data): graphs help multi-hop reasoning and summarisation; plain RAG matches or beats them on simple lookups; graph prompts and indexing are far heavier. https://arxiv.org/pdf/2506.05690
- Contradiction detection across retrieved documents is hard for LLMs. https://arxiv.org/html/2504.00180v1
- Visual document retrieval (ColPali/ColQwen) needs about 700 to 1,000 vectors per page and a pooled two-stage index to be practical. https://qdrant.tech/documentation/tutorials-search-engineering/pdf-retrieval-at-scale/
- OCR model landscape and confidence-scoring guidance are from vendor-authored articles with no hard benchmark numbers, so they only suggest candidates; the extraction eval decides. https://unstract.com/blog/best-opensource-ocr-tools/ and https://www.extend.ai/resources/best-confidence-scoring-systems-document-processing
- Lelapa AI Vulavula languages and services. https://docs.lelapa.ai/overview/introduction
- **Not verified, check before relying**: how to serve a multilingual reranker; whether BGE-M3 sparse or multi-vector outputs are available through Ollama; BM25 behaviour on Afrikaans; the Qdrant version in production; today's filing KPIs; whether the routing hints change outcomes; whether transactional documents are chunked into the general KB; any specific benefit of fine-tuning on this data.
