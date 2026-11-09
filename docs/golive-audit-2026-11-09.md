# Go-live audit, 9 Nov 2026

Base: `master` at `a905bd7` (#283). This follows `docs/golive-and-takeoff-audit.md` (31 Oct to 3 Nov)
and re-checks what it left open. It also covers four areas that audit didn't: security and tenant
isolation, money, WhatsApp/AI runtime, and the client apps.

**Method.** I ran the whole CI gate locally. Production was **read only**: Supabase SQL selects
and advisors, and Railway logs, deployments and environment. Nothing in production was changed.
Five reviewers each read one area end to end. Every BLOCKER below was re-read in the code before
it was listed, and production facts come from production itself. Paths are relative to `vula_mind/`
unless they start with `vula_dashboard/`, `vula_site/` or `docs/`.

## Gate: green

| Check | Result |
|---|---|
| `pytest tests/` (Python 3.11) | **7,235 passed, 2 skipped, 0 failed** (178 s) |
| ruff, import check, RLS coverage, migration sentinels, client secrets, takeoff bench | all pass |
| Dashboard `npm ci`, build, size budget, design tokens, unit tests | all pass |
| `npm audit --omit=dev` (dashboard) | 5 moderate (react-router / @remix-run/router, uuid) |
| `pip-audit` | filelock 3.32.7 (transitive), 13 CVEs, fixed in 4.2.1 |

A green suite doesn't cover the gaps below. Most of them are paths no test exercises.

## Fixed since the last audit

- `ENFORCE_TENANT_AUTH` is **on** in production: no "tenant auth is off" error at the 8 Nov boot.
- Migrations 240–249 and 252 were applied and recorded by `tools/migrate.py`. 250 and 251 were
  applied by hand (their columns exist) but not recorded. Next `migrate.py apply` re-runs them;
  both are idempotent.
- 242 is applied, so the six tables the anon key could read and write are closed.
- 241 `search_path` is set. `ops_wa_token_backup_20261022` is dropped. The test rows in
  `vula_material_rates` and `vula_takeoff_jobs` are deleted.

---

## BLOCKERS: fix before go-live

### Production right now

| # | What | Evidence | Fix |
|---|---|---|---|
| P1 | **Saved credentials can't be decrypted.** The encryption key changed after they were saved. **Off the Hook:** Yoco secret key, Yoco webhook secret, Yoco payment-provider keys and the `info@offthehook.capetown` mailbox. So no card payments, no Yoco webhook verification and no email sync. **DIGG:** `judy@digg-ct.co.za` mailbox and ClickUp token. **PayFast keys** of aweh-be-lekker, awake-sa and gerflor (all three hold the *same* stored value). | Railway logs `vula.secret_keys` "can't be unlocked" (8 fingerprints) at every boot. The fingerprints still match rows today (sha256 of the stored values, computed in SQL). Hourly "email sync skipped" for both mailboxes. | If the old key is known (an earlier `SECRETS_KEY`, or a rotated Supabase service key), put it in `SECRETS_PREVIOUS_KEYS`, `railway up`, then re-encrypt (`secret_keys.reencrypt_all`). Otherwise each business re-enters the keys (Settings › Payments / Email / ClickUp). Then check why three tenants share one PayFast value. |
| P2 | **Nobody is told when things break.** Operator alerts aren't sent: "not sent: Vula's own number is not set". Sentry is off ("SENTRY_DSN not set"). Nothing outside the app watches it. The local AI was down **3 days 17 h** (5 Nov 17:34 to 9 Nov 09:22 SAST) and every AI call went to the cloud model. Nobody was alerted. | Railway logs `vula.integrations.platform_support`, `vula.local_llm_watch`, `vula.api` | Set Vula's own number (`PLATFORM_TENANT_ID` + its WhatsApp line) and `SENTRY_DSN`. Add an external uptime check on `/status`, plus a webhook-silence check. Spend-cap, signup and mass-incident alerts still bypass `alert_operator` and go free-form to `TEAM_WHATSAPP`, whose default is the placeholder `+27820000000` (`config.py:170`, `server.py:1333`, `core/llm_router.py:667`, `vula/api/onboarding.py:485`). |
| P3 | **No persistent disk.** `railway.toml` declares `/data`, but the service has **no volume** attached. Every redeploy wipes `/data/uploads` (bank statements, email/OneDrive attachments, onboarding docs), `/data/takeoff` plans and `ingestion_log.db`. | Railway `describe-environment`: `volumeMounts: []`. `vula/durable.py:3-5` | Attach a Railway volume at `/data`, or move these files to Supabase Storage. |
| P4 | **Production is one commit behind.** #283 (setup asks how orders reach customers; collection-only checkout) was **SKIPPED**: CI's "Fresh database" job failed because GitHub returned 504 while downloading the Supabase CLI (a network flake). Its migration 252 is already applied. | Railway deployment `ee837618…` SKIPPED. CI run 42465667192 | Re-run the failed CI job (or `railway up`). |

### In the code

| # | What | Where | Failure scenario |
|---|---|---|---|
| S1 | **A tenant owner can make themselves master.** | `vula/api/users.py:72` (`_TENANT_USER_ROLES` includes `master`), `:172-181` (`update_role` writes `body.role` unchecked) | An owner sends `PATCH /v1/users/{t}/users/{own_id} {"role":"master"}` and gets every tenant plus `/v1/master/*`. Allow only `owner`/`staff` in tenant endpoints. |
| S2 | **Account takeover via "create login" / "reset password".** | `users.py:106-118`, `:161-169` | An owner "creates a login" with an email that already exists, e.g. the master's. The code finds that user, sets a new password and **returns it**. `reset_password` takes any `user_id` without checking the tenant. Refuse users outside this tenant. |
| S3 | **WhatsApp sign-in as any email.** | `vula/api/wa_login.py:222-248` (`_login_for`), `vula/api/team.py:100` | An owner adds a team member with their own phone and someone else's email. The WhatsApp code flow then mints a magic-link session for that email. Use the member's verified login, or the synthetic `wa-…` email. |
| S4 | **Another tenant's tenant_id in the body gets past the auth check.** | `vula/api/master_auth.py:161` (`"application/json" in ctype`, case-sensitive) | A member of A sends `?tenant_id=A` with `Content-Type: Application/JSON` and body `tenant_id=B`. Auth passes on A and the handler acts on B, e.g. reads B's knowledge base via `/v1/agent/run` and `/query`. The reviewer **reproduced this locally** (TestClient, mocked auth). Parse every json content type, or refuse a body tenant that differs from the authorised one. |
| S5 | **Platform-wide routes accept any tenant member.** | `master_auth.py:144-157`; e.g. `server.py:2167-2200` (`/v1/oth/briefing/chase-unpaid`), `/scrape/*`, `/ready`, `/metrics`, the recurring-invoice and campaign crons in `vula/api/commerce.py` | 63 routes are guarded only by `require_auth` with no tenant in the path. Self-signup creates a tenant (`vula/api/signup.py:74`). So a stranger can sign up and then message Off the Hook's customers, run every tenant's crons, or use `/scrape/custom` for SSRF (see H1). Make them API-key or master only. |
| S6 | **Forged "order paid" via the Yoco webhook.** | `vula/api/yoco.py:394-416`, `:463-470` | The order path looks up and updates `commerce_orders` by `id` alone, with no amount, currency or checkout check. The signing secret is chosen by the payload's own `tenant_id`, and a tenant can bring its own Yoco account or secret. Tenant A can therefore sign a payload naming B's order, and B's order is marked paid and fulfilled. The invoice path is scoped correctly. Add `.eq("tenant_id")` and the amount check from `vula/api/payments.py:_amount_covers`. |
| S7 | **Forged PayFast payment when a tenant has no passphrase.** | `vula/payments/__init__.py:145-158`, `config.py:373` | The connect step doesn't require a passphrase. The source-IP check only logs, and this path never calls PayFast `/validate` (tap-to-pay and onboarding do). A forged ITN with `COMPLETE` marks an order or invoice paid. |
| M1 | **An order's automatic invoice has the wrong total.** | `vula/commerce/service.py:1774-1795` (`send_order_invoice`) | It rebuilds the invoice from the item lines plus delivery. It drops the discount and doesn't pass `prices_include_vat`. Example: R1,000 − R100 code + R50 delivery is charged R950, but the invoice says **R1,207.50**. Paying the order never marks the invoice paid, so it sits in debtors. If the owner then marks it paid, revenue is booked twice. **Production has 0 order-linked invoices so far**, so no customer has seen this yet. |
| M2 | **Typing "yes" runs the model's new arguments, not the ones previewed.** | `core/skills/commerce_admin.py:1919-1934` (`_preview_was_shown` matches the tool name only), `:2440-2445` | The owner previews a "R1,200 payment link to Sam" and says "yes". The model calls `payment_link(amount_rands=12000, confirm=true)` and a R12,000 link goes out. The Confirm *button* path is correct (it re-runs the stored arguments). |

---

## HIGH: fix before go-live, or go live without the feature

| # | What | Where |
|---|---|---|
| H1 | SSRF: `web_scraper` (crawl4ai, follows redirects, no IP checks), project fetch-title, template media and storage links don't use `reference_url.safe_fetch_bytes` | `vula/skills/web_scraper.py:124-139`, `vula/api/projects.py:435`, `vula/commerce/wa_templates.py:69`, `vula/storage_links.py:137,148` |
| H2 | OAuth `state` is the raw tenant id (Google, Microsoft, ClickUp, Dynamics). An attacker links *their* account into a victim's tenant, and the victim's mail and Drive filing flow to the attacker. Xero already signs its state; reuse that. | `vula/api/google.py:52,69`, `vula/api/microsoft.py:39`, `vula/api/clickup.py:146`, `vula/api/dynamics365.py:35-41` |
| H3 | Money tools missing from `_PREVIEW_FIRST`: `order_payment`, `create_manual_order` (`mark_paid` books cash; a deposit messages the customer), `create_discount_code`, `update_order_status` (cancel/refund), `staff_notice`, `staff_poll`, `cancel_booking` | `commerce_admin.py:1915` |
| H4 | Orders paid through PayFast, Ozow, Paystack, Peach or iKhokha are flipped to paid by a direct table write. **No journal entry**, so the revenue never reaches the books. | `vula/api/payments.py:166-176` |
| H5 | The invoice PATCH accepts totals the client computed. The dashboard recomputes VAT-exclusive, so R1,200 VAT-inclusive is re-saved as R1,380. Status has no transition rules: paid → draft, then edit or delete, breaks the SARS sequence and the ledger. | `vula/api/commerce.py:4710-4765`, `vula/commerce/service.py:1892`, `vula_dashboard/src/components/VulaInvoices.jsx:590-612` |
| H6 | A storefront checkout with no `store_url` sends the paying customer back to **offthehook.co.za** | `vula/api/commerce.py:580` |
| H7 | The abandoned-question apology is free text, sent outside the 24 h window, and retried **hourly forever**. Each failure adds a template plus a team notice. No production rows are waiting today. | `vula/api/server.py:1222-1235`, `vula/escalation.py:663-698` |
| H8 | The 131047 template fallback has no per-recipient cooldown. Digest and advisor retries give an owner about 8–12 template nudges a morning once `WHATSAPP_NOTIFY_TEMPLATE` is set. | `vula/api/whatsapp.py:8722-8787` |
| H9 | A business whose numbers are all disconnected (or a Supabase read failure) **falls back to the env `WHATSAPP_PHONE_ID` line**, so its messages go out from another line | `whatsapp.py:8729-8736, 8030-8035, 8331-8334, 8373-8376` |
| H10 | A shop set up on Vula's own number (#277/#278/#283) is routed as a knowledge line. The owner's "what orders came in today?" goes to **commerce_assistant** (the customer agent) with the owner as the customer. | `whatsapp.py` platform branch, `core/hrm/orchestrator.py:58-90` |
| H11 | `requirements.txt` is all `>=` with no lockfile (only litellm is pinned). A build can pick up a breaking release, as litellm 1.106 did. | `requirements.txt` |
| H12 | `require_auth` lets everything through when `API_KEY` is empty, even in production. Only a boot warning. Confirm it's set, and make it fail closed. | `master_auth.py:114-115` |

## MEDIUM

- **Money.** Each item below says what goes wrong and where it is.
  - A partial refund reverses the full sale (`vula/api/commerce.py:764-800`, `vula/commerce/ledger.py:83`).
  - An unpaid order marked "refunded" posts a refund, and paid → cancelled posts nothing.
  - Credit notes have no cap and re-price at the tenant's VAT setting (`service.py:3309-3402`).
  - Recording a payment has no overpayment cap (`service.py:2098-2150`).
  - `vat_registered` defaults to true (`service.py:1701`).
  - Concurrent Yoco events give a double confirmation and double dispatch.
  - `journal_entries`, `journal_lines` and `commerce_invoices` have no append-only or immutability trigger (migration 121).
  - Rounding: `round()` is half-to-even, while Xero and the dashboard use half-up (1c differences).
- **WhatsApp.**
  - A double tap on a checkout pay button can place two orders. There's no per-sender lock and no unique `cart_id` (`vula/commerce/button_checkout.py:221-246`).
  - Button taps are awaited inside the webhook, not backgrounded (`whatsapp.py:993-1052`).
  - Re-drive can run a queued message twice (`whatsapp.py:607-611, 640`).
  - Customers of a business hosted on Vula's number are treated as leads.
  - Lead names and businesses asked for on Vula's number are dropped (`vula/onboard_wa.py:917-931`).
  - A revoked or invalid OpenRouter key raises no alert (`llm_router.py:131-134, 214-221`).
- **Auth.**
  - Campaign deactivate and segment delete are by id only (`commerce.py:6527, 6678`).
  - `X-Forwarded-For` is trusted for the WhatsApp sign-in IP limit.
  - CORS allows any `*.vercel.app` with credentials.
- **Ops.**
  - The scheduler leader lease flaps about every 60 s across `WEB_CONCURRENCY=2` (`server.py:1626-1641`). Not root-caused; it tripled sends before.
  - Nothing runs after `yield` in the lifespan: no task cancel and no lock release.
  - The global `200/minute` rate limit never applies (no `SlowAPIMiddleware`), and per-route limits share the proxy IP.
  - `.dockerignore` doesn't exclude `.env`.
  - The container runs as root.
  - 96 of 163 settings aren't documented, and the CF-Access tunnel vars are read from `os.environ` directly.
  - `vula_env` defaults to `production`.
  - `/status` returns raw exception text publicly.
- **Dashboard.**
  - A missing `VITE_SUPABASE_URL` gives a blank white page that ErrorBoundary can't catch (`vula_dashboard/src/lib/supabase.js:7-14`).
  - A preview build without `VITE_API_URL` talks to the production API.
  - A failed `/me` shows a restricted rep the full menu (`vula_dashboard/src/App.jsx:226`).
  - There is one app-wide error boundary, and it tells tenant staff to "tell Ian".
  - When the API is down, lists render empty, with no banner.
- **POPIA.**
  - Retention exists only for takeoff plans. Turns (including crisis messages), the outbound preview, dedupe, escalations and feedback are kept forever (`vula/retention.py:39`, `docs/security-model.md:111`).
  - The `vula_site` lead form has no privacy notice or consent (`vula_site/index.html:525-535`).
  - The API runs in Railway **us-west2** and the database in eu-central-1, so customer data leaves SA whatever the LLM routing does. Document it as a cross-border transfer (s72).

## LOW (summary)

- Stock restore can double up on concurrent cancel paths.
- An invoice number is drawn before the insert (a gap on failure), and the insert's retry drops the deposit and discount.
- Ledger VAT is understated on `order_paid`.
- Logs carry the outbound reply text on the JSON-leak paths, and raw foreign numbers.
- Brand substring fallback ("digg"/"awake" in the host).
- No CSP.
- No robots.txt.
- `check_client_secrets.py` misses `vula_site/index.html` and `vula_mobile/app.json`.
- Stale docs: CLAUDE.md says "no migration runner, ~157 migrations"; DEPLOYMENT.md is a bare-metal guide; `order_workflow.py:129` says migration 251 where it means 252.

## Scope

| App | Verdict |
|---|---|
| vula_mind (API) | In scope. Blockers above. |
| vula_dashboard | In scope, close to ready (see the dashboard bullets under MEDIUM). |
| vula_site | In scope. Needs a privacy notice. Its API URL is hardcoded to production. |
| vula_storefront, off_the_hook | **Not audited**: the submodules aren't checked out in this environment. Their backend contract has H6, and `*.vula.site` is missing from CORS. Audit them in a full checkout before storefront checkout goes live. |
| vula_mobile | **Out of scope.** No real auth: every call needs the all-tenant server API key. Don't submit it to the stores. Confirm the key exposed on 26 Oct was rotated. |

## Still open from the last audit

- **Staging:** still none. No Supabase branch, and Railway has only `production`. The per-tenant go-live
  eval (≥85%) has never been run.
- **Supabase:** leaked-password protection is still **off**. The two hand-made backup tables remain
  (`ops_digg_self_supplier_backup_20261026`, `vula_cleanup_log_20261002`). Drop them once you're happy with them.
- **Seedleme's scheduled templates** still fail every day, now with `133010 Account not registered`:
  the number isn't registered on Cloud API. Register it, or switch those jobs off.
- **Meta portal:** Business Verification, App Review Advanced Access, Embedded Signup config →
  `VITE_FB_CONFIG_ID`, the 8 webhook fields, `VULA_FB_APP_ID/SECRET`. I can't see these from here.
- **DR:** confirm Supabase PITR, Storage backup coverage, and a restore drill (`docs/dr.md`).
- **Payments not set up** for ShopEazy, alice, devo, drako-was-here, kelp-boardbags, medusa and seedleme
  (boot log). Fine if they don't sell yet.
- **Takeoff:** real plans for the benchmark; the sheet-vinyl decision.

## Status

- **9 Nov, security PR** (branch `ccr-2e32801b-3mouyf`): S1–S5, H1, H2 and H12 fixed.
  Tests: `vula_mind/tests/test_security_audit_1109.py` (40). 38 of them fail on the code before
  the fix. The other 2 are a lowercase `application/json` case that already worked and a
  "same tenant and forms still work" regression check. S6 and S7 (forged payments) are in the
  money PR.
- **9 Nov, money fixes:** S6, S7, M1, M2, H3, H4 and H5 fixed.
  Tests: `vula_mind/tests/test_money_audit_1109.py` (35). 33 of them fail on the code before the
  fix. The other 2 are regression checks: a full payment is marked paid once, and a draft still
  moves to "sent". MEDIUM money items (partial refunds, credit-note caps, overpayment, the
  `vat_registered` default, journal immutability, rounding) are still open.
- **9 Nov, WhatsApp fixes:** H7–H10 and the checkout double tap fixed.
  Tests: `vula_mind/tests/test_whatsapp_audit_1109.py` (10). 9 of them fail on the code before the
  fix; the other checks that a failed placement can be tried again. Still open: 8 businesses have
  no number row and send from the server's default line by design. Whether that line should
  speak for a business at all is a product decision.

## Suggested order

1. **Today (no code):** P1 (old key or re-entry), P2 (Vula's number, Sentry, uptime check), P3 (attach a volume),
   P4 (re-run CI), leaked-password protection.
2. **One security PR:** S1–S5, H1, H2, H12, each with a failing-first test. Extend
   `tests/test_route_auth_sweep.py` to cover mixed-case content types, platform routes called by
   another tenant's member, and role escalation.
3. **One money PR:** S6, S7, M1, M2, H3–H5, with tests for each path listed as untested.
4. **One WhatsApp PR:** H7–H10 and the checkout double-tap.
5. Then staging, the go-live eval per tenant, and the MEDIUMs.
