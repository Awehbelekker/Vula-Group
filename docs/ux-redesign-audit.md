# UX redesign: no-double-build audit (Phase A)

2026-10-17, branch `ccr-d78dd68e-izdba0`. Read-only audit before any redesign code. Status:
**PRESENT** = don't rebuild · **PARTIAL** = extend in place, same naming · **MISSING** = build.
Paths: `FE` = `vula_dashboard/src`, `BE` = `vula_mind`. Line numbers are as of commit `0a6bb80`.

Agreed changes to the brief (from the review before Phase A):
1. v2 token values are scoped under `[data-ui="v2"]` on `<html>`, set by the same flag as the new shell, so the old shell keeps today's colours.
2. The hard-coded hex colour cleanup ratchets per screen through the existing `check:tokens` baseline. There is no single sweep across all 86 components.
3. The per-tenant manifest must be same-origin. Serve it through the existing Vercel `/api/*` rewrite, not from Railway directly.
4. The All activity feed (E2) runs as its own backend track. It merges existing tables at query time with a `(created_at, source, id)` cursor. There is no new events table.

---

## 0. Repository state

| Check | Result |
|---|---|
| `git status` at start | clean on `ccr-d78dd68e-izdba0` (= `master` @ `0a6bb80`), no stashes |
| Submodules | `vula_storefront` and `off_the_hook` are **not checked out** in this container (`git submodule status` shows `-`). Phase H for those two can't be audited here. |
| Migrations | highest is **213** (`213_website_leads.sql`), not ~157. Next free number: **214**. `tools/check_migrations_rls.py` runs in CI (`.github/workflows/ci.yml:54`). |
| Dashboard CI | `npm ci`, `npm run build`, `npm run check:tokens` (`ci.yml:89-98`). No browser tests. |

## 1. ⚠ Security findings (not redesign work; report only, nothing fixed)

These came up while auditing E2's requirement that roles be enforced on the server. They block that requirement, because there is no server-side role or section enforcement to build on.

| # | Finding | Evidence | Severity |
|---|---|---|---|
| S1 | Tenant guard is off unless `ENFORCE_TENANT_AUTH=true`. **Check the Railway value.** | `BE/config.py:248` (default `False`), `BE/vula/api/server.py:1774`, `.env.example:100` | High if off in prod |
| S2 | `GET /v1/team/{t}/me?email=` trusts the email query parameter. An unknown email gets `role: owner, full: true`, which drives all dashboard gating. | `BE/vula/api/team.py:144-153` | High |
| S3 | Section access (Money, Team…) is enforced **only in the browser** (`merchantVisible`). Any member, including a sales rep, can call `/admin/invoices`, `/admin/bank/*`, etc. directly. | `FE/navConfig.jsx:204-219`; no `require_section` in `BE/vula/api/commerce.py`; guard checks membership only (`server.py:1772-1813`, `tenant_auth.py:30`) | High |
| S4 | Any tenant member can PATCH another member's role/access (only `require_tenant_actor`, i.e. "is a member"). | `BE/vula/api/team.py:107-109` | High |
| S5 | Email follow-up PATCH updates by `id` with **no tenant filter**, so a cross-tenant write by id is possible. | `BE/vula/api/email_connect.py:224-227` | Medium |
| S6 | `cs-metrics` "local %" reads routing telemetry with no `tenant_id` filter (platform-wide figure shown as the tenant's). | `BE/vula/api/commerce.py:7826-7828` | Low (no content leak, wrong number) |
| S7 | Count caps: `admin_stats.open_escalations` uses `limit(10)`; escalations `open_count = len(rows)` is capped by `limit` (default 50). Badges under-count. | `commerce.py:3981-4112`, `commerce.py:7615` | Low |

Recommendation: fix S1–S5 as a separate security PR **before** E2. A role-filtered activity feed means nothing while `/me` can be spoofed and money endpoints are open to any member. `tests/test_id_route_scoping.py` and `tests/test_route_auth_sweep.py` are the patterns to copy.

## 2. Things to verify (from the 2026-10-17 read-only review)

| Claim | Verdict | Evidence |
|---|---|---|
| Tokens in `index.css`: `--bg #F7F4EE`, `--surface`, `--accent: var(--brand-accent, #2C5545)`, `--ink`, `--ok/warn/danger/info`, `--r-card 12px`, `--r-pill 999px`, Cormorant / Inter / Source Code Pro | ✅ correct | `FE/index.css:7-62` (`--accent` :10, `--bg` :17, `--r-card` :45, `--r-pill` :47, fonts :39-41) |
| A theme/tokens module with light/dark/auto | ✅ correct | `FE/theme/tokens.js:99-115` (`DARK_MODE_READY`, `getThemeMode`, `setThemeMode`, `applyThemeMode`); toggle `FE/components/VulaShell.jsx:13-21` |
| ~85 of 86 components use inline styles | ✅ correct | 85/86 `.jsx` in `components/`; 3,647 `style={{` across `src` |
| `#C4922A` ~19×, `#F0EDE8`, `#2A2A2A`, `#0D0D0D` hard-coded | ⚠ partly wrong: `#C4922A` 25×, `#F0EDE8` 16×, `#2A2A2A` 11×, `#0D0D0D` 6×, **all in one file** (`VulaOnboarding.jsx`) | baseline total 580 values / 58 files (`scripts/design-token-baseline.json`) |
| ~219 uses of `var(--accent)` | ⚠ 206 in 69 files (+7 `T.accent`) | grep |
| Nav icons are emoji | ✅ correct | `FE/navConfig.jsx:21-169` |
| One breakpoint at 860px, bottom-left burger, sideways tab strips, 16px inputs | ✅ correct | `index.css:125-131`, `:186-196` (burger `position:fixed; bottom:16px; left:16px`) |
| ~10 merchant sections gated by `merchantVisible` | ✅ correct (12 entries: Home, Inbox, 9 sections, Storefront, Team, Settings, My Work) | `navConfig.jsx:19-119`, `:204-234` |
| "Powered by Vula" repeated as footers | ✅ correct: 10 in the dashboard + receipt | §5 below |
| Manifest SVG-only icons, `orientation: portrait`, two shortcuts | ✅ correct | `vula_dashboard/public/manifest.webmanifest` |
| `sw.js` is a 29-line network-first cache of **every** GET with `skipWaiting` | ⚠ partly wrong: it **skips** `/v1/` and supabase URLs (no API data cached), but caches non-OK and cross-origin responses | `public/sw.js:5,19,21-25` |
| `index.html` uses `/icon.svg` as apple-touch-icon | ✅ correct (iOS ignores SVG) | `index.html:16` |
| `App.jsx` swaps per-tenant apple-touch-icon | ✅ correct, **and it also already swaps in a per-tenant manifest**, cross-origin from Railway (see G1) | `FE/App.jsx:168-169` |
| Main dashboard has no push/install; `PayApp.jsx` has both | ✅ correct; backend push exists too | `FE/pay/PayApp.jsx:178-180, 224-243`; `BE/vula/tap/push.py`; `BE/vula/tap/appapi.py:223-242` |

Other things that differ from the brief:
- **Home is not `VulaDashboard.jsx`.** That file is the master "Debug › Dashboard" (RAG upload/query). It injects a global `* {margin:0;padding:0}` reset and re-imports fonts (`VulaDashboard.jsx:499-506`). The merchant Home is `OverviewTab` in `VulaMerchantAdmin.jsx:481-618`.
- **Shared primitives are at `FE/components/ui/index.jsx`**, not `src/ui/`. There are also 18 local copies of Card/StatCard/Stat elsewhere.
- **Home cards can already be customised per tenant** (`GET/PUT /v1/tenants/{t}/home`, migration 186, defaults by business type). This hosts E1's "choose which KPIs Home shows".
- **There is no church/NPO business type**: `BUSINESS_TYPES` = food, retail, services, trades, health, rep, solo, other (`BE/vula/api/tenants.py:68`).
- **No router.** `react-router-dom` is a dependency but is never imported. Merchant navigation is state-only (`merchTab`), so there are no deep links into merchant screens today.

## 3. Audit table: Phase B (foundation)

| Item | Status | Evidence | Decision |
|---|---|---|---|
| B1 tokens | PRESENT (old palette) | `index.css:7-96`; JS handles `theme/tokens.js:6-26` (`T`) | **Extend** `index.css`: add v2 tokens under `:root[data-ui="v2"]` (+ dark variants); alias old names (`--bg`→`--ground`, `--accent`→`--brand`) inside that scope only. Add new names to `T`. |
| B1 dark mode mechanism | PRESENT | `:root[data-theme="dark"]` `index.css:69-81` + `prefers-color-scheme` block `:82-96` | **Reuse** as-is |
| B1 spacing / motion tokens | PRESENT | `--s1..--s7` `index.css:55`; `--ease`, `--t-fast 150ms`, `--t-med 220ms` `:58-60` | **Extend** (`--space-*` aliases, `--t-sheet 240ms`) |
| B2 tenant brand → tokens | PARTIAL | `applyBrand` `tokens.js:73-94` → `applyAccent` `:36-49`; `contrastRatio` `:169`; `liftForDark` `:175` (already reaches 4.5:1 on dark surface); `contrastOn` `:159` uses a luminance cut-off, not a 4.5:1 check | **Extend** `applyAccent`: derive `brand-strong`/`brand-soft`/`on-brand` per theme, enforce 4.5:1 on-brand. Add unit tests (no JS test runner exists, so add a tiny `node --test` file; no new framework). |
| B2 legacy themes | PARTIAL / stale | `theme/tenantThemes.js` hard-codes real tenants, still used by `VulaPages.jsx:15`, `VulaPageRender.jsx:14` | **Skip** in B; retire in Phase H with the storefront work |
| B3 fonts | PARTIAL | CSS `@import` `index.css:5` (`display=swap`, no preconnect, discovered late); again in `VulaDashboard.jsx:500`; pairings `tokens.js:119-138` | **Extend**: move to `<link>` + preconnect in `index.html`; family via `--font-body` (already a token, so Inter/Instrument Sans is a one-line swap) |
| B3 type scale | PARTIAL | `--fs-xs..--fs-xl` `index.css:42` | **Extend** with the named scale |
| B4 money formatter | MISSING (≈38 ad-hoc variants FE, 12 BE) | e.g. `VulaMerchantAdmin.jsx:518`, `:862`; `VulaInvoices.jsx:398`; only exported one is `receipt/PrintedSlip.jsx:15`; `pay/PayApp.jsx:25` already uses NBSP. BE `pay_page.py:124` etc. | **Build** `FE/lib/money.js` (one shared helper); migrate per screen in Phase F |
| B5 icon library | MISSING | none in `package.json` | **Build**: add `lucide-react` (exact pin), map in `navConfig.jsx` |
| B6 hex check in CI | **PRESENT** | `scripts/check-design-tokens.mjs`, baseline `scripts/design-token-baseline.json` (580/58 files), CI `ci.yml:97-98` | **Reuse**, ratchet the baseline down per migrated screen. Add a small allowlist comment syntax for third-party brand colours. |
| B7 motion | PARTIAL | keyframes `index.css:113-121`; reduced-motion `:133`, `:284`; inline transitions/keyframes in `VulaQuickLauncher.jsx:74`, `VulaDocuments.jsx:169` not covered | **Extend** |
| B7 skeletons | PRESENT | `Skeleton` `components/ui/index.jsx:202`, `.vula-skeleton` `index.css:120` | **Reuse**; most screens still show "Loading…" text |
| B7 Home glass | PRESENT | `index.css:200-288`, `GlassCard.jsx`; reduced-transparency fallback `index.css:285-288` uses `#fff` (not dark-aware) | **Reuse**; fix the fallback to tokens |

## 4. Audit table: Phase C (primitives)

All present ones live in `FE/components/ui/index.jsx`. **Decision: extend that file/folder (`components/ui/`), don't create `src/ui/`.**

| Primitive | Status | Evidence | Decision |
|---|---|---|---|
| PageHeader | PRESENT | `ui/index.jsx:148` (1 importer) | Extend (actions slot, v2 styles) |
| Card | PRESENT | `:8` (+ local copies, e.g. `VulaDashboard.jsx:41`) | Extend; replace local copies as screens migrate |
| StatCard | PRESENT | `:43`; local dup `VulaMerchantAdmin.jsx:707` | Extend (hero/ring, KPI, hot variants) |
| PillTabs | PARTIAL | `SectionTabs` `:81-97` (`role=tablist`) | Extend `SectionTabs` (pill variant, arrow-key nav) and keep the name |
| StatusChip | PARTIAL | `Badge` `:53` | Extend `Badge` → add tones/outline; export `StatusChip` alias |
| ListRow | MISSING | (inline rows everywhere, e.g. `VulaInbox.jsx:80-95`, `VulaCustomers.jsx:169-188` non-keyboard `div`s) | Build |
| DataTable | PARTIAL | `Table` `:166` (loading + empty built in), `ScrollX` `:161` | Extend `Table` (sticky header, sort, stacked rows <768px) |
| FilterBar | MISSING | | Build |
| EmptyState | PRESENT | `:108` | Reuse/restyle |
| Skeleton | PRESENT | `:202` | Reuse |
| Toast | PRESENT (no undo) | `toast` `:254`, host `UiHost` `:256` (mounted `main.jsx:24`) | Extend (undo action) |
| Sheet | MISSING (Modal exists) | `Modal` `:218`; QuickLauncher's own overlay `VulaQuickLauncher.jsx:41-70` | Build `Sheet` on top of `Modal`'s focus/escape handling |
| CommandPalette | MISSING | no Cmd/Ctrl+K; `VulaQuickLauncher.jsx` is a FAB ("Ask Vula", "Smart scan") | Build; **absorb QuickLauncher's actions**, then retire its FAB in v2 (it would collide with the floating toolbar) |
| FloatingToolbar | MISSING | | Build |
| Button | PRESENT | `:28` (primary/soft/ghost/danger) | Extend (44px, round icon, "quiet" = ghost) |
| Dialogs | PRESENT | `confirmDialog` `:246`, `promptDialog` `:250` | Reuse |
| UI-kit demo route | MISSING | | Build `#/ui-kit` (master-only), mirroring the existing hash routes in `App.jsx:216-240` |

## 5. Audit table: Phase D (shell)

| Item | Status | Evidence | Decision |
|---|---|---|---|
| Shell | PRESENT | `VulaShell.jsx` (106 lines; props `:24-40`, presentation-only) | **Extend in place**: render v2 layout when flag on; keep the props contract |
| Nav config + gating | PRESENT | `MERCHANT_GROUPS` `navConfig.jsx:19-119`, `REP_DEFAULT_ACCESS` `:123`, `MASTER_ZONES` `:139-172`, `MERCHANT_CORE` `:184`, `REP_TABS` `:187`, `MERCHANT_MODMAP` `:188`, `merchantVisible` `:204`, `filterGroups` `:225`, `labelFor` `:237` | **Reuse unchanged**. Add a `house: "front" \| "back" \| "admin"` field + Lucide icon per item; regroup at render time, so no id moves |
| Section tabs | PRESENT | `useSectionTabs` `FE/hooks/useSectionTabs.js:19-38`; section wrappers `VulaMerchantAdmin.jsx:113, 173-193` | Reuse |
| Rep home | PRESENT | `VulaRepHome.jsx:12-81` swapped in at `VulaMerchantAdmin.jsx:170-171` | Reuse inside new layout |
| Responsive breakpoints | PARTIAL | only 860px (`index.css:125, 186`); no `useIsMobile`; `matchMedia` only in `GlassCard.jsx:26`, `receipt/PrintedSlip.jsx:26`, `pay/PayApp.jsx:263` | **Build** a `useBreakpoint` hook in `FE/hooks/` (next to `useSectionTabs`) + 768/1200 CSS breakpoints under `[data-ui="v2"]` |
| Inbox badge | PRESENT (capped, S7) | `App.jsx:195-213`, `VulaShell.jsx:83` | Reuse |
| Unread counts | MISSING | no `read_at`/`last_read` anywhere | Build later (E/F); badge = open escalations until then |
| Feature flag `VITE_UI_V2` + per-tenant override | MISSING | only `DARK_MODE_READY` constant (`tokens.js:99`); VITE vars listed in §8 | **Build**: `FE/lib/flags.js` reads `VITE_UI_V2`; per-tenant override from the `/v1/tenants/{t}` response (add a `ui_v2` boolean to `vula_tenant_config`, the table that holds `home_layout` (migration 186). That needs migration 214; RLS is already on, migration 040). Until the migration is applied: `?ui=v2` / localStorage for testing |
| Per-tenant labels | MISSING | only `vula_tenant_menu_overrides` (migration 170; WhatsApp staff menu copy) | **Build** as an extension of `tenant_profile` / `home_layout` (`tenants.py:248`, `:559`); defaults by `business_type`. Needs a church/NPO type (decision) |
| "Powered by Vula" | PRESENT ×11 | `VulaShell.jsx:89`, `VulaLogin.jsx:297`, `VulaSettings.jsx:173`, `VulaTeam.jsx:266`, `VulaContacts.jsx:127`, `VulaProjects.jsx:234`, `VulaFinances.jsx:291`, `VulaQSRates.jsx:149`, `VulaFollowups.jsx:61`, `VulaPayments.jsx:105`, `receipt/ReceiptPage.jsx:96` | Keep shell + login + receipt; remove page footers as screens migrate (F) |
| Quick Launcher FAB | PRESENT | `VulaQuickLauncher.jsx:68` (fixed bottom-right) | Hide under v2; actions move to Add sheet / palette |
| Master shell zones + tint | PRESENT | `VulaShell.jsx:60-69`, `.vshell--zone-*` | Reuse |

## 6. Audit table: Phase E (Home, activity, Today)

| Item | Status | Evidence | Decision |
|---|---|---|---|
| Home stats | PRESENT | `admin_stats` `BE/vula/api/commerce.py:3981-4112` (all server-side, integer cents): orders/revenue, pending/dispatch, invoices outstanding/overdue/paid, 7-day series, bank money in, low stock, escalations, agent_recent | **Extend** (`admin_stats` or a sibling `admin/home` endpoint); no LLM figures |
| Home card layout per tenant | PRESENT | `GET/PUT /v1/tenants/{t}/home` `tenants.py:559, 572`; defaults `:533`; `HomeCustomiser` `VulaMerchantAdmin.jsx:419` | **Reuse** for "which KPIs Home shows" |
| Sales target | MISSING | only `expense_budget_cents` (migration 139), project `fee_target` (`job_costing.py:248`) | Build (setting) or **hide hero** until set, as the brief allows |
| First-reply time | MISSING (computable) | `commerce_conversation_messages` (migration 007; role + created_at, index (session_id, created_at)) | Build server-side query |
| Chats waiting for a human | PRESENT | `vula_escalations` (migration 042), `GET /admin/escalations` `commerce.py:7615` | Reuse (fix count cap S7) |
| Docs needing review | PARTIAL | `vula_filed_documents.needs_review` (migration 102); `fields._unverified_figures` (`whatsapp.py:4223`); `GET /v1/documents/{t}/health` `documents.py:76`; no `needs_review` filter on `/filed` (`documents.py:30`) | Extend `/filed` with a `needs_review` filter |
| Bank unmatched | PRESENT | `GET /admin/bank/reconciliation` `commerce.py:3008` | Reuse |
| Stock alerts | PARTIAL | `service.get_low_stock_products` `service.py:2161`; `low_stock_count` in stats | Reuse count; list endpoint if needed |
| Supplier bills due | PRESENT | `GET /admin/expenses/due` `commerce.py:7189` | Reuse |
| E2 activity feed | PARTIAL | `GET /admin/agent-activity` `commerce.py:1899` (Vula's own actions, telemetry, limit only); `_customer_timeline` `commerce.py:6708` (orders+invoices+messages per customer, merge-in-Python); `/admin/automations/firings` `:7396`; `commerce_shop_events` (209) | **Extend**: new `GET /v1/commerce/{t}/activity` built on `_customer_timeline`'s merge pattern + `agent-activity`'s narration, cursor `(created_at, source, id)`. Labels only, never `content` |
| Audit trail | PARTIAL | `vula_merchant_audit` (migration 107, RLS on), `merchant_audit.audit()` `merchant_audit.py:23`, read `GET /v1/team/{t}/audit` `team.py:164`. **Conversation views are not audited** (`admin_get_thread` `commerce.py:7674`) | **Reuse table**, add `conversation_viewed` audit call. No new migration needed |
| Consent changes | PRESENT | `commerce_consent_log` (migration 064), `_log_consent_event` `commerce.py:5321` | Reuse as feed source |
| Contact merges | MISSING | no merge function or column | **Skip** from the feed until merges exist |
| E3 Today | PARTIAL | `GET /admin/rep/today` `commerce.py:2011` (bookings + meeting notes), `vula_reminders` (117) `GET /admin/reminders` `:1978`, escalations, `vula_email_followups` (028), `vula_open_questions` (194, no endpoint), `/admin/projects/today` `:3696` | **Extend** `rep/today` into a tenant-wide Today (no parallel task system) |
| Promised payments | MISSING | no table (only a QA label `conversation_check.py:36`) | Skip until it exists; don't invent |
| Role filtering on server | **MISSING** | see S2–S4 | **Blocked**: needs the security PR first |

## 7. Audit table: Phase G (PWA)

| Item | Status | Evidence | Decision |
|---|---|---|---|
| Static manifest | PRESENT | `public/manifest.webmanifest` (SVG icons, portrait, no `id`, 2 shortcuts) | Keep as Vula fallback; fix icons + drop portrait |
| Per-tenant manifest | PARTIAL, **broken by origin** | `GET /{t}/manifest.webmanifest` `commerce.py:4904` (no id/shortcuts, `"any maskable"` combined); linked cross-origin from Railway at `App.jsx:168` | **Extend** the endpoint; link it as `/api/v1/commerce/{t}/manifest.webmanifest` through the existing Vercel rewrite (`vercel.json:7-8`) so it's same-origin; make icon URLs relative to `/api/` |
| App icons PNG | PRESENT | `GET /{t}/app-icon/{size}.png` `commerce.py:4884` (180/192/512, Pillow `vula/commerce/brand_icon.py`) | Reuse; add maskable variant (safe-zone padding) |
| Static PNG icon set | MISSING | `public/` has only `icon.svg`, `icon-maskable.svg` | Build (decision 5) |
| Service worker | PARTIAL | `public/sw.js` (skipWaiting, network-first, caches non-OK); registered `main.jsx:30-33`; `/sw.js` has no no-cache header (`vercel.json` only covers `/pay/sw.js`) | **Replace** hand-written (no vite-plugin-pwa today; adding it is a choice for Phase G) |
| Pay app SW + push handler | PRESENT | `public/pay/sw.js` (push + notificationclick), `vercel.json:33-45` | Reuse its handlers |
| Install prompt / iOS hint | PRESENT (pay only) | `pay/PayApp.jsx:178-180, 263, 322-323` | Reuse code |
| Web push backend | PRESENT (tap app) | `WebPusher` `vula/tap/push.py:13-39`; `pywebpush` `requirements.txt:68`; VAPID `config.py:238-242`; `scripts/gen_vapid.py`; table `kb_push_subs` (migration 202, RLS on, `tenant_id`, `member_id`) | **Extend**: reuse `WebPusher` and the table shape. `kb_push_subs.device_id` FKs to tap devices, so dashboard subs need a nullable device or a sibling table (migration 214+) |
| Share target | MISSING | | Build (reuse document upload endpoint) |
| IndexedDB / offline | PARTIAL | Stock scanner offline queue in localStorage `VulaStock.jsx:41-42, 258-298` | Reuse pattern |
| Logout clears caches | MISSING | `store/auth.js:32-35` (signOut + reset only) | Build |
| Lighthouse CI | MISSING | | Build (perf target after Phase F) |

## 8. Audit table: Phase H (brand beyond the dashboard)

| Item | Status | Evidence | Decision |
|---|---|---|---|
| Pay page (dashboard) | PARTIAL | `src/pay/pay.css`, `PayApp.jsx` | Extend with shared tokens |
| Receipt | PARTIAL | `src/receipt/ReceiptPage.css` hard-coded dark theme (`#12171a`…), system fonts, no tenant accent; "Powered by Vula" `ReceiptPage.jsx:96` | Extend |
| Hosted pay page (backend) | PARTIAL | `BE/vula/commerce/pay_page.py:133,165` (tenant accent) | Extend |
| Invoice/quote PDFs | PARTIAL | WeasyPrint + Jinja2, `BE/vula/commerce/pdf.py` (themes `:45-229`, `merge_branding` `:426`, fonts `:24-38`); no "Powered by Vula" | Extend (tokens + mark) |
| Statements PDF | PARTIAL | `BE/vula/commerce/reports.py:190-219` (no branding) | Extend |
| Emails | PARTIAL | `BE/vula/api/email.py:92-133` (tenant accent, "sent with Vula") | Extend |
| Order slip image | PRESENT | `BE/vula/commerce/order_slip.py:234` | Extend |
| Storefronts | UNKNOWN | submodules not checked out | Audit when available |
| Shared `tokens.json` | MISSING | | Build in Phase H (sync script across submodules) |

## 9. Audit table: Phase I (quality gates)

| Item | Status | Evidence | Decision |
|---|---|---|---|
| Playwright | ADDED in Phase A | `vula_dashboard/playwright.config.js`, `tests/visual/` | Reuse |
| axe | ADDED in Phase A (baseline report) | `tests/visual/a11y.spec.js` | Reuse |
| Hex check | PRESENT | `check:tokens` | Reuse |
| Lighthouse | MISSING | | Build (Phase G) |
| Backend tests / RLS check | PRESENT | `tests/` (~196 files), `tools/check_migrations_rls.py` | Reuse |

## 10. Must be reused, not rewritten

- `navConfig.jsx`: `MERCHANT_GROUPS` ids, `merchantVisible`, `filterGroups`, `REP_DEFAULT_ACCESS`, `MASTER_ZONES`, `MERCHANT_MODMAP`, `labelFor`
- `VulaShell.jsx` props contract; `useSectionTabs` + `SectionTabs`
- `components/ui/index.jsx` primitives (Card, Button, StatCard, Badge, Table, EmptyState, Skeleton, Modal, toast, confirmDialog/promptDialog, PageHeader)
- `theme/tokens.js` (`applyBrand`, `applyAccent`, `liftForDark`, `contrastRatio`, theme mode) and the `index.css` dark-mode mechanism
- `scripts/check-design-tokens.mjs` + baseline
- Home layout API (`/v1/tenants/{t}/home`) and `HomeCustomiser`
- `admin_stats`, `agent-activity`, `_customer_timeline`, `rep/today`, `reminders`, `expenses/due`, `bank/reconciliation`, `documents/health`
- `merchant_audit` + `vula_merchant_audit`
- `WebPusher`, `kb_push_subs` shape, `pay/sw.js` push handlers, `PayApp.jsx` install/push code
- per-tenant `app-icon` and `manifest.webmanifest` routes; Vercel `/api/*` rewrite
- Stock scanner offline queue pattern
- `pdf.py` themes / `merge_branding`; `email.py::_brand_parts`

## 11. New modules this redesign will add (each justified)

| New | Why no existing module can host it |
|---|---|
| `FE/lib/money.js` | No shared formatter exists anywhere (38 ad-hoc copies); `lib/` is the home for shared helpers |
| `FE/lib/flags.js` | No flag mechanism exists; `lib/` hosts env-driven helpers (`authFetch.js` reads `VITE_API_URL`) |
| `FE/hooks/useBreakpoint.js` | No responsive hook exists; `hooks/` hosts `useSectionTabs` |
| `components/ui/` additions (ListRow, FilterBar, Sheet, CommandPalette, FloatingToolbar) | Added to the existing primitives file/folder |
| `BE` `GET /v1/commerce/{t}/activity` | No tenant-wide feed exists; lives in `commerce.py` next to `agent-activity` |
| migration 214 (`ui_v2` flag, labels) | New columns on the existing `vula_tenant_config` (beside `home_layout`); no new table |

## 12. Visual and accessibility baseline (captured in Phase A)

`vula_dashboard/tests/visual/baseline/`: 8 screens (home, inbox, customers, orders, invoices, documents, settings, master) × 390×844 / 834×1112 / 1440×900 × light / dark = 48 PNGs. The API is mocked from fictional seed data (`tests/visual/fixtures/demo.js`, "Harbour & Vine Deli"). Network is blocked, so screenshots use fallback fonts and the Settings delivery map has no tiles. Run instructions: `tests/visual/README.md`.

axe (desktop, light), serious / critical:

| Screen | Serious | Critical |
|---|---|---|
| home | 0 | 0 |
| inbox | color-contrast ×13 | 0 |
| customers | 0 | 0 |
| orders | color-contrast ×14, label-title-only ×2 | 0 |
| invoices | color-contrast ×17 | 0 |
| documents | color-contrast ×1 | label ×2, select-name ×5 |
| settings | color-contrast ×16 | label ×14, select-name ×4 |
| master | color-contrast ×3, label-title-only ×1 | 0 |

Horizontal page scroll at 390px: **inbox (452px), invoices (412px), master (421px)**; the others fit.

Bugs noticed while seeding (reported, not fixed):
- `GoLiveChecklist` (`VulaMerchantAdmin.jsx`) crashes when `/admin/setup` returns no `steps`.
- `LearnedSummary` (`VulaSettings.jsx`) crashes on a partial response (`data.voice.current`, `data.learned_answers.*`).
- Home "Your assistant, recently" prints UTC times as local (`String(a.at).slice(11,16)`).
- Home "Customer waiting" hint cuts the question at 46 characters ("…party of 1" for "party of 12").
- Master tenants table is clipped at 1440px (panel capped at 1000px; last action column cut off).
- A master login lands on Debug › Dashboard, not Master (`App.jsx` `initialTab`).
- Inbox canned-reply chip hard-codes `#F3F1EA` (not dark-aware).
- Home "AI economics" shows the platform-wide local % (S6).
