# Vula — AI that runs your business

> **An AI business operating system for South African SMEs.**
> Each business gets a WhatsApp-native AI team member — for customers *and* for the
> owner — plus a branded web dashboard that runs their day-to-day: orders, invoicing,
> bookkeeping, documents, projects and staff.
> ZAR-native · POPIA-compliant · local-first · white-labeled · built in Cape Town.

Vula is **one multi-tenant AI backend** serving many businesses. A tenant talks to Vula where
they already work — **WhatsApp** — and manages everything in a **branded portal** that looks like
*their* system ("Powered by Vula"). The same engine adapts to very different businesses:

- **Commerce tenants** (e.g. *Off the Hook*, a Cape Town seafood delivery): WhatsApp ordering,
  Yoco payments, delivery scheduling, bookings, bank reconciliation, an owner who runs the whole
  shop by chatting to the agent.
- **Professional/knowledge tenants** (e.g. *DIGG*, an architecture practice): document intelligence,
  project workspaces, QS cost estimating, invoicing, finance reconciliation.

---

## What Vula does

**Talk to it on WhatsApp — in your own language.** Every tenant gets their own WhatsApp number.
Customers order by text *or voice note* (transcribed, local-first Whisper); owners run the shop
("how much did we make today?", "mark order 142 dispatched"); professionals ask their knowledge
base. Vula replies in whichever South African language the person is using — English, Afrikaans,
isiZulu, isiXhosa, Sesotho and more — mirrored automatically, with persistent per-conversation
memory. A **team inbox** lets staff take over a conversation, leave internal notes, tag, and use
canned replies, closing the gap vs a dedicated CS platform.

**The AI business assistant.** The owner doesn't use a dashboard to run day-to-day ops — they
just message the agent. A 22-tool conversational admin agent handles sales summaries, order
status, stock, invoices/quotes, expenses, products, bookings, subscriptions, customer lookup,
finance insights and broadcasts (with a confirm-before-send guard on anything that spends money
or messages customers). Every state-changing action is **read-back verified** before it's
reported as done — Vula re-checks the database, not just its own claim.

**Commerce.** Products, cart and orders; **Yoco** card payments; delivery slots; **bookings &
appointments**; **recurring subscriptions**; a customer/CRM directory with full interaction
history; POPIA consent + suppression-aware broadcasts and segments; sales reports.

**Invoicing & billing.** Multi-template tax-invoice PDFs (incl. a logo-forward *Branded*
template), correct **VAT** handling (registered / inclusive), discounts & deposits, EFT details,
**saved clients/suppliers**, **recurring invoices**, **Yoco pay-links** ("Pay now" → auto-marked
paid), and **credit notes** — all server-computed in integer cents.

**Bookkeeping, without an accounting package.** Read an emailed bank statement (even a
password-protected PDF), extract every transaction, and reconcile it — credits matched to
invoices, debits to expenses — with anything uncertain left for one-tap human review, never
auto-guessed. On top of the reconciled ledger: a real **chart-of-accounts + VAT + P&L/GL** layer,
**WhatsApp-photo expense claims** (who paid, reimbursable or not, which project), **company-card
transaction attribution**, and a **casual-labour register** that recognises worker payments
straight off the bank statement. Built as the practical off-Xero path for SMEs, not a
Xero-replacement product.

**Historical onboarding.** New tenants don't start from zero — Vula reconstructs past orders and
contacts from WhatsApp exports or email history (extract → owner reviews → commit), so the
picture is complete from day one.

**Knowledge & documents.** Upload or WhatsApp a document → it's parsed, classified, filed to the
right project, and embedded into a **per-tenant vector knowledge base** with authority-tagged
retrieval (no chat-noise contamination). A **Smart Scanner** reads receipts/bills with vision.

**Project Workspaces ("Claude Projects" for a practice).** Pick a project → scoped chat threads
that already know its documents, finances, team and to-dos; an editable project brief; project
BoQ/costing; and **to-dos that two-way sync to ClickUp**.

**Email as a teammate.** Connect any mailbox (IMAP/SMTP, Gmail/Outlook OAuth). Vula auto-syncs,
builds a **contacts/suppliers directory**, files genuine attachments to the KB, flags emails that
**need a reply**, and drafts responses in your voice.

**Website, no developer needed.** A drag-and-drop page builder (Puck) lets a tenant build and
publish their own branded site pages.

**Team, notifications & learning.** Per-user WhatsApp notifications + access scopes (RBAC); Vula
**learns from corrections** (a one-time "which project?" answer becomes a rule, the same pattern
that drives learned categorisation in bookkeeping); per-tenant **COGS metering** so cost-to-serve
is visible.

**Verified, not just reported.** A per-skill verification layer sits between generation and
"done": deterministic checks (e.g. every QS calculation is anchored to an exact evaluator, never
just an LLM's arithmetic) and, for the highest-risk skills, an adversarial checker pass distinct
from the answering model — flipped on per skill based on measured production reliability, not
guesswork. Combined with the read-back gate on admin actions, "the agent said it worked" and
"it actually happened" are two separately verified things.

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
 (reasoning · commerce · admin agent · finance/bookkeeping · email
  · standards · scanner · bookings · voice · language · …)
                        │
        Verification layer  ──►  per-skill deterministic / adversarial
                                  checks + read-back completion gate
                        │
        LLM router  ──►  local-first (Ollama on an SA GPU box, reached via a
                         Cloudflare-Access-secured tunnel); cloud (OpenRouter)
                         only for a sanctioned, logged reason — local
                         unreachable, an unreliable local response, or
                         genuine task complexity. Every decision is auditable.
                        │
   ┌────────────────────┼─────────────────────────────┐
   ▼                    ▼                              ▼
Supabase            Qdrant Cloud                  Yoco / Meta
Postgres + Storage  per-tenant vector KB          payments + WhatsApp
+ Auth (RLS)        (vula_{tenant})
```

- **Multi-tenant by construction:** `tenant_id` on every row + Supabase **RLS**; per-tenant Qdrant
  collection; per-tenant WhatsApp number, theme and branding.
- **Local-first, POPIA by architecture:** generation defaults to a GPU box on South African soil;
  cloud is the fallback, never the default, and every routing decision is logged with its reason.
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
- **Verified, not reported** — state-changing actions are read back and confirmed, not assumed.

---

## Repo structure

```
vula_mind/        Python AI backend — FastAPI API, HRM orchestrator, skills,
                  commerce + invoicing + bookkeeping, verification layer,
                  ingestion pipeline, integrations, migrations/
vula_dashboard/   React/Vite tenant + master admin (PWA), white-labeled per tenant,
                  incl. a Puck-based drag-and-drop page builder
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

WhatsApp is confirmed **live in production** for Off the Hook and DIGG — not a dev-mode demo.

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
#   needs: SUPABASE_URL, SUPABASE_SERVICE_KEY, QDRANT_BASE/API_KEY, OLLAMA_BASE (or OPENROUTER_API_KEY)

# Dashboard (React/Vite PWA)
cd vula_dashboard && npm install && npm run dev
```

Production: backend on **Railway** (`railway up`), dashboard + storefront on **Vercel**.

---

## Built by

**Vula Group (Pty) Ltd** — Cape Town, South Africa
GitHub: [@Awehbelekker](https://github.com/Awehbelekker/Vula-Group)

© 2026 Vula Group (Pty) Ltd. See [LICENSE](LICENSE).
