# Vula — AI that runs your business

> **An AI business operating system for South African SMEs.**
> Each business gets a WhatsApp-native AI team member and a branded web dashboard that
> runs their day-to-day — orders, invoices, documents, finances, clients and projects.
> ZAR-native · POPIA-compliant · white-labeled · built in Cape Town.

Vula is **one multi-tenant AI backend** serving many businesses. A tenant talks to Vula where
they already work — **WhatsApp** — and manages everything in a **branded portal** that looks like
*their* system ("Powered by Vula"). The same engine adapts to very different businesses:

- **Commerce tenants** (e.g. *Off the Hook*, a Cape Town seafood delivery): WhatsApp ordering,
  Yoco payments, delivery scheduling, product catalogue, POPIA-compliant broadcasts.
- **Professional/knowledge tenants** (e.g. *DIGG*, an architecture practice): document intelligence,
  project workspaces, QS cost estimating, invoicing, finance reconciliation.

---

## What Vula does

**Talk to it on WhatsApp.** Every tenant gets their own WhatsApp number. Customers order; owners
run the shop ("how much did we make today?", "mark order 142 packed"); professionals ask their
knowledge base. Inbound messages route to the right tenant + mode automatically, with persistent
per-conversation memory.

**Commerce.** Products, cart and orders; **Yoco** card payments; delivery slots; a customer/CRM
directory with full interaction history; POPIA consent + suppression-aware broadcasts; sales
**reports** (trend + product performance).

**Invoicing & billing.** Multi-template tax-invoice PDFs (incl. a logo-forward *Branded* template),
correct **VAT** handling (registered / inclusive), EFT details, **saved clients/suppliers**,
**recurring invoices**, **Yoco pay-links** ("Pay now" → auto-marked paid), and **credit notes** —
all server-computed in integer cents.

**Knowledge & documents.** Upload or WhatsApp a document → it's parsed, classified, filed to the
right project, and embedded into a **per-tenant vector knowledge base** with authority-tagged
retrieval (no chat-noise contamination). A **Smart Scanner** reads receipts/bills with vision.

**Project Workspaces ("Claude Projects" for a practice).** Pick a project → scoped chat threads
that already know its documents, finances, team and to-dos; an editable project brief; and
**to-dos that two-way sync to ClickUp**.

**Email as a teammate.** Connect any mailbox (IMAP/SMTP, Gmail/Outlook OAuth). Vula auto-syncs,
builds a **contacts/suppliers directory**, files genuine attachments to the KB, flags emails that
**need a reply**, and drafts responses in your voice.

**Finance & reconciliation.** A per-project money-in/out ledger; payments reconciled to invoices
by amount + **bank account**; budgets vs actual.

**Team, notifications & learning.** Per-user WhatsApp notifications + access scopes (RBAC); Vula
**learns from corrections** (a one-time "which project?" answer becomes a rule); per-tenant **COGS
metering** so cost-to-serve is visible.

---

## Architecture

```
WhatsApp (Cloud API)              Branded web dashboard (PWA)
 per-tenant numbers                 digg.vula-ai.com, etc.
        │                                   │
        └───────────────┬───────────────────┘
                        ▼
              vula_mind  —  FastAPI backend (Railway)
                        │
        HRM orchestrator → keyword-routed, tool-calling skills
   (reasoning · commerce · finance · email · standards · scanner · …)
                        │
        LLM router  ──►  cloud-first (OpenRouter Llama-3.3-70B for answers;
                         Gemini-2.5-Flash cheap tier for doc analysis/classification
                         with validate-and-escalate); local Ollama fallback (optional)
                        │
   ┌────────────────────┼─────────────────────────────┐
   ▼                    ▼                              ▼
Supabase            Qdrant Cloud                  Yoco / Meta
Postgres + Storage  per-tenant vector KB          payments + WhatsApp
+ Auth (RLS)        (vula_{tenant})
```

- **Multi-tenant by construction:** `tenant_id` on every row + Supabase **RLS**; per-tenant Qdrant
  collection; per-tenant WhatsApp number, theme and branding.
- **White-label theming:** a tenant's `accent_color` drives the entire dashboard (CSS variables),
  not just the invoice PDF.
- **Embeddings:** `text-embedding-3-small` (pinned per collection).
- **Frontend:** React/Vite PWA on Vercel; design tokens (Cormorant headings, Source Code Pro for
  money) + reusable UI primitives.

### Engineering non-negotiables
- **Tenant isolation** — explicit `tenant_id` scoping + RLS on every query.
- **Financial integrity** — money is always **integer cents**, computed server-side.
- **Secret safety** — no secrets in the frontend or the repo; per-tenant API credentials in the DB.
- **POPIA** — implied opt-in on first contact, honoured opt-out/suppression, data-deletion on request.

---

## Repo structure

```
vula_mind/        Python AI backend — FastAPI API, HRM orchestrator, skills,
                  commerce + invoicing, ingestion pipeline, integrations, migrations/
vula_dashboard/   React/Vite tenant + master admin (PWA), white-labeled per tenant
vula_mobile/      Expo / React Native thin client
off_the_hook/     Next.js storefront for the Off the Hook tenant (Yoco + WhatsApp ordering)
infrastructure/   Shared infra config
n8n_workflows/    Workflow automation (briefings, follow-ups)
```

Database migrations live in `vula_mind/migrations/` and are applied manually in the Supabase SQL
editor (numbered, idempotent).

---

## Tenants

| Tenant id | Type | Brand |
|---|---|---|
| `digg-demo` | knowledge / professional | DIGG Architecture (`digg.vula-ai.com`) |
| `off-the-hook` | commerce | Off the Hook — Cape Town seafood (`offthehook.co.za`) |
| `awake-sa` | commerce | Awake South Africa |

Adding a tenant = a `vula_tenants` row + a Qdrant collection + (optionally) a WhatsApp number,
theme and storefront.

---

## Plans

| Plan | Price | For |
|---|---|---|
| **Starter** | R1,500/mo | Sole traders & small practices (core AI + invoicing) |
| **Growth** | R3,500/mo | Growing SMEs (full AI suite, CRM, custom persona) |
| **Business** | R7,500/mo | Established businesses (unlimited users, white-label) |

---

## Local development

```bash
# Backend (FastAPI)
cd vula_mind && pip install -r requirements.txt
uvicorn vula.api.server:app --reload --port 7438
#   needs: SUPABASE_URL, SUPABASE_SERVICE_KEY, QDRANT_BASE/API_KEY, OPENROUTER_API_KEY

# Dashboard (React/Vite PWA)
cd vula_dashboard && npm install && npm run dev
```

Production: backend on **Railway** (`railway up`), dashboard + storefront on **Vercel**.

---

## Built by

**Vula Group (Pty) Ltd** — Cape Town, South Africa
GitHub: [@Awehbelekker](https://github.com/Awehbelekker/Vula-Group)

© 2026 Vula Group (Pty) Ltd. See [LICENSE](LICENSE).
