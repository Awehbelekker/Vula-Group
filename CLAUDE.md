# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout & working directory

The repo root is `C:\Users\Ian\Vula-Group\` (this file's directory). It is a monorepo with two git submodules.

```
vula_mind/        Python AI backend — FastAPI API, HRM orchestrator, skills, commerce/invoicing/
                  bookkeeping, verification layer, ingestion pipeline, integrations, migrations/
vula_dashboard/   React 18 + Vite tenant + master-admin PWA (white-labeled per tenant), incl. a
                  Puck-based drag-and-drop page builder. Deploys to Vercel.
vula_storefront/  (submodule) multi-tenant storefront hosting app
off_the_hook/     (submodule) Next.js storefront for the Off the Hook tenant
vula_mobile/      Expo / React Native thin client
n8n_workflows/, infrastructure/, kelp/   automation + infra (kelp has its own CLAUDE.md)
```

**Gotcha — nested working directory.** Claude Code sessions for this project are configured with primary working directory `C:\Users\Ian\Vula-Group\Vula-Group\` — a git-untracked scratch folder *inside* the repo, not the code. All real work is one level up. Use absolute paths under `C:\Users\Ian\Vula-Group\` (or `cd` into `vula_mind` / `vula_dashboard`). This CLAUDE.md at the repo root is auto-loaded from the nested dir via parent-directory walk.

**Gotcha — concurrent edits.** Ian keeps VSCode buffers open on files in `vula_mind/`; an unsaved buffer can silently revert an agent edit on save. After editing, re-check the file on disk (Grep/Read) before relying on it.

## Common commands

All backend commands run from `vula_mind/`:

```bash
cd vula_mind
pip install -r requirements.txt

# Run the API (dev)
uvicorn vula.api.server:app --reload --port 7438
#   needs env: SUPABASE_URL, SUPABASE_SERVICE_KEY, QDRANT_BASE, OLLAMA_BASE (or OPENROUTER_API_KEY)
#   config comes from vula_mind/.env via config.py (pydantic-settings)

# Tests (pytest-asyncio; markers are explicit — there is no pytest.ini)
pip install pytest pytest-asyncio
pytest tests/                              # full suite (~196 test files)
pytest tests/test_llm_router.py            # one file
pytest tests/test_llm_router.py::test_name # one test
#   conftest.py forces test doubles (localhost Ollama/Qdrant), isolates SQLite stores in a
#   temp dir, and stubs WhatsApp creds so no test can hit the real Meta API.

# Lint — CI only checks real errors (undefined names / unused imports), not style:
ruff check vula/ core/ --select=F --exclude scripts,tests

# Every table created in migrations/*.sql must get RLS enabled — CI enforces this:
python tools/check_migrations_rls.py
```

Dashboard (`vula_dashboard/`): `npm install && npm run dev` (Vite), `npm run build` (must pass in CI).

**Deploy:** backend → Railway (`railway up`; Dockerfile build, health check `/status`). Dashboard + storefronts → Vercel. **After changing Railway env vars you must `railway up` again** — env changes alone redeploy stale code.

**Migrations:** SQL files in `vula_mind/migrations/` (numbered, idempotent, currently ~157). Applied **manually** in the Supabase SQL editor — there is no migration runner. `vula_mind/db/migrations/` is a near-empty legacy path; ignore it.

## Architecture — the big picture

Vula is **one multi-tenant AI backend** serving many SME businesses. Every business ("tenant") talks to Vula on its own WhatsApp number and manages everything in a branded web dashboard. The same engine serves very different tenants (commerce: Off the Hook seafood; professional/knowledge: DIGG architecture).

### Multi-tenancy is structural
- `tenant_id` on every row + Supabase **RLS** on every table (CI-guarded).
- Per-tenant Qdrant collection (`vula_{tenant_id}`), per-tenant WhatsApp number, theme, branding, and API credentials (stored in the DB, Fernet-encrypted for the sensitive tables — never in the repo/frontend).
- `accent_color` on the tenant drives the whole dashboard via CSS variables.

### Request flow (WhatsApp is the primary surface)
`POST /v1/whatsapp/webhook` → `vula/api/whatsapp.py::receive_message` → `_handle_message` dispatches by message type and sender role:
- **Media / voice notes** → download, transcribe (local-first Whisper), scan (vision), or ingest as a document (`_handle_document_ingest`, `commit_inbound_document`).
- **Merchant/staff & sales-rep messages** → dedicated *role-gated* path that calls `get_skill("commerce_admin")` directly (NOT the keyword router). Confirm-before-send guard on anything that spends money or messages customers; state-changing actions are **read back** against the DB before being reported done.
- **Customer commerce messages** → `get_skill("commerce_assistant")`.
- **Everything else (knowledge questions)** → `_rag_reply` → `core/agent_runner.py::AgentRunner`.

`vula/api/chat.py` (dashboard chat) reuses `_rag_reply`, adding persistent history.

### The agent runner / HRM
`AgentRunner.run()` → `HRMOrchestrator.plan()` (`core/hrm/orchestrator.py`) picks:
- **complexity** (1–3) from keyword heuristics (or a local-LLM score),
- **skill** from `SKILL_KEYWORDS` — an *ordered* dict of substring matches. Order matters and is heavily commented (e.g. `finance_admin` before `calculations`). A keyword miss triggers one cheap local-LLM classification pass (`skill_llm_fallback_enabled`), then falls back to `reasoning`.
- **model tier**, **branch count**, **merge strategy**.

Branches run in parallel; `core/thinkmesh/merger.py` merges; `core/memory/` (SQLite `reflection.db`) logs the outcome for future routing hints. WhatsApp calls cap at `max_branches=1` for cost.

**`core/skills/registry.json` is NOT consulted for routing** (loaded but never read). `core/hrm/orchestrator.py::SKILL_KEYWORDS` + `core/skills/loader.py::_SKILLS` are the real source of truth. `CONTRIBUTING.md`'s skill example is also stale (old `execute()` signature).

### Skills (`core/skills/`)
Every skill subclasses `BaseSkill` (`core/skills/base.py`): `async def run(inp: SkillInput) -> SkillOutput`. `BaseSkill.__call__` times it, catches exceptions into `SkillOutput.error`, then runs the verification hook.

Shared behaviour policy ("the Soul") lives in `base.py`: `behaviour_preamble(persona, agentic, preferred_language)` composes ethics/honesty/reasoning/conversation/untrusted-content rules that are prepended to every answering skill. Pass `agentic=True` for skills with a tool-calling loop. `base.py` also holds deterministic backstops every tool-calling skill should use: `tool_source()`, `unverified_prices()`, `wrong_arithmetic()`, `looks_like_tenant_data_question()`, `need_info_message()`.

Implemented skills: `reasoning` (fallback), `commerce_admin` (~3.2k lines, 22-tool admin + sales-rep agent), `commerce_assistant`, `finance_admin`, `architecture_planning`, `calculations` (deterministic, self-verifies), `standards_lookup`, `email_admin`, `draft_admin`, `clickup_admin`, `google_admin`, `microsoft_admin`, `file_parse`, `memory_recall`, `web_search`.

### LLM routing (`core/llm_router.py`) — local-first, auditable
`resolve_generation_route()` decides Ollama (local, on an SA GPU box reached via a Cloudflare-Access-secured tunnel) vs OpenRouter (cloud). Cloud is used **only for a logged reason**: (a) local unreachable, (b) unreliable local response (`looks_unreliable()` / logprob confidence, handled by the caller via `escalate_to_cloud()`), (c) genuine complexity (frontier task type or prompt over `local_complexity_token_cap`), or the explicit `prefer_cloud_llm` operator override. Every decision goes to `_log_decision()` → `core/reasoning_telemetry.py` (migration 045 sink) for "why did tenant data leave South Africa" auditing. Embeddings are deliberately not routed here (fixed vector size per Qdrant collection).

### Verification layer (`core/verification.py`)
Per-skill policy `none | deterministic | adversarial`, resolvable at runtime via the `VERIFICATION_POLICY_OVERRIDES` env JSON (no redeploy). `adversarial` runs one checker-framed LLM pass (distinct from the answering model) that finds defects → drops confidence + appends a caveat; it never blocks the answer. Fail-open everywhere. `strip_caveat()` must be called before persisting a reply to conversation history (the caveat is for the human, not the model's next turn). Admin mutating tools additionally have a DB read-back gate (`readback_verify_enabled`).

### RAG / ingestion (`vula/ingestion/pipeline.py`)
Document → text extraction → semantic chunk → embed → per-tenant Qdrant collection → authority-tagged retrieval. PDF text extraction tries fitz (PyMuPDF) first — exact and ~10x faster than pdfplumber, which chokes on malformed streams common in bank-generated PDFs — then falls back to pdfplumber, then poppler+OCR for image-only pages. Embed model is chosen by *name*: `bge-m3` (1024-dim, local/tunnel) vs `text-embedding-3-small` (1536-dim, cloud). A collection's vector size is fixed, so `MODEL_EMBED` must stay consistent per environment.

Money-document extraction in `_analyze_document` (`vula/api/whatsapp.py`) layers three tiers before booking anything, cheapest/most-certain first: (1) `vula/ingestion/payment_notice.py` — deterministic positional parse for rigid machine-generated layouts (FNB payment notices; add a matcher per bank as samples come in), confidence 1.0, no LLM; (2) the LLM path, verified by `extraction_quality.scan_quality_ok` (line items reconcile with the total) and `ungrounded_figures` (every money figure actually appears in the source text) — either check failing escalates cheap→cloud; (3) if the cloud pass still fails, one retry against `vula/ingestion/docling_extract.py` — Docling (self-hosted, MIT, CPU; `requirements-docling.txt`, not `requirements.txt`) re-renders the PDF as reading-order-correct Markdown for the minority of documents whose multi-column/merged-cell tables fitz's raw text-join scrambles. A figure that's still ungrounded after all three is never auto-booked — it's staged (`fields["_unverified_figures"]`) for owner review instead.

## Engineering non-negotiables

- **Tenant isolation** — explicit `tenant_id` scoping + RLS on every query and every new table.
- **Financial integrity** — money is always **integer cents**, computed server-side, never by the LLM. Use the `calculate` tool / deterministic recompute for any arithmetic shown to a user.
- **POPIA** — implied opt-in on first contact; honour opt-out/suppression; local-first generation; telemetry carries type labels/hashes, never raw prompt or customer content.
- **Verified, not reported** — state-changing actions are read back and confirmed, not assumed. Never claim an action succeeded without a tool call that performed it.
- **WhatsApp constraints** — Meta only allows free-form text within 24h of the user's last inbound message; proactive sends need a Meta-approved template (`whatsapp_notify_template`). Paste raw URLs (no markdown link syntax) — WhatsApp doesn't render markdown.

## Config

`vula_mind/config.py` — single `Settings` (pydantic-settings), loaded from `vula_mind/.env`. Import as `from config import settings`. `.env.example` files at repo root and in `vula_mind/` list expected vars.
