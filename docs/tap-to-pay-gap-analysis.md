# Tap-to-pay (KakEnBetaal) — what Vula already has, what is missing, and where to build it

Inputs reviewed: `Tap-to-pay_WhatsApp_flow_spec.pdf` (19 pp, dated 2026-10-02) and
`kakenbetaal-build-pack.zip` (SPEC, ARCHITECTURE, MESSAGES, MILESTONES, TEST_PLAN, OPEN_QUESTIONS,
`db/schema.sql`, `api/openapi.yaml`, CLAUDE.md). Audited against `vula_mind/` at this commit.

## 1. Verdict

**Build it as a separate, plug-and-play service (own repo), not inside `vula_mind`.** Vula integrates
as one tenant/consumer over HTTP + webhooks. Reasons:

1. **Stack mismatch.** The pack is decided as TypeScript / Fastify / Kysely / Postgres 16 / pg-boss
   with its own Postgres schema and a ledger that must be append-only (DB trigger). Vula is
   Python / FastAPI / Supabase REST client, migrations applied by hand. Porting the pack into Vula
   means rewriting its pure-function core, jobs and tests and losing its guarantees.
2. **Different risk class.** This product moves money between third parties (tips, revenue splits,
   payouts). Vula's non-negotiables (integer cents, tenant isolation, read-back) all apply, but the
   funds-flow/licensing question (spec "Compliance", pack L1/L5/L6) should not be coupled to the
   release cycle of the general AI backend or its blast radius.
3. **Reusable by other projects.** The pack already isolates every external system behind ports
   (`PaymentProvider`, `WhatsAppClient`, `PushClient`, `Clock`, `Notifier`) with sim/mock defaults and
   `PRODUCT_NAME` as config. That is exactly the "plug into other projects" shape. Vula, or any other
   product, becomes a consumer through a thin adapter.
4. **WhatsApp number ownership.** The spec's customer chat goes to *the product's* business number
   (Open decision: who owns the WABA). Vula's WhatsApp webhook is per-tenant; sharing one number would
   couple the two inbound routers. Keep them separate and forward only what is needed.

The one thing worth lifting out of the pack *into Vula* is not the engine but a small **"Take a
payment" capability** (see section 5) that calls the service.

## 2. What Vula already has (verified in code)

| Pack need | Vula today | Where | Reuse verdict |
| --- | --- | --- | --- |
| Hosted-checkout pay link, many SA gateways | Yes: Yoco, PayFast, Peach, iKhokha, Ozow, Paystack behind one `_Provider` interface (`create_link`, `verify_webhook`), per-tenant creds Fernet-encrypted | `vula/payments/__init__.py`, `vula/api/payments.py`, `vula/api/yoco_connect.py` | **Reuse the design, not the code.** Interface is link-only: no `refund`, `getPaymentStatus`, `createPayout`, `splits`, `capabilities`. Pack's `PaymentProvider` is a superset. Peach/PayFast signature logic is a useful reference for the TS adapters. |
| Verified, idempotent payment webhooks | Partly. Yoco is HMAC-verified; generic route verifies per provider, ignores already-paid invoices, checks `paid >= owed` via `_amount_covers` | `vula/api/payments.py`, `vula/api/yoco.py` | Pattern matches the spec (verify, match reference, check amount, never trust redirect). **Gap:** idempotency is "status already paid" not a `webhook_events` unique key; no replay window; generic route returns 200 on verify failure (swallowed). |
| WhatsApp inbound webhook with signature check | Yes, `X-Hub-Signature-256`, rejects missing sig | `vula/api/whatsapp.py:365-385` | Reference only; service gets its own number/webhook. |
| Inbound message dedup | Yes, durable `vula_wa_msg_dedup` (mig 071/179) with processing/done/abandoned states and crash recovery | `whatsapp.py:213-331` | Good model for the pack's `webhook_events`. |
| Interactive messages | Reply buttons (max 3) and list messages sent, replies parsed by id | `whatsapp.py:7589+`, `:512-521` | Confirms feasibility of the tip list / Pay-now buttons. Not reusable (Python, creds per tenant). |
| WhatsApp templates | Create/list/delete via Graph API | `vula/commerce/wa_templates.py` | Reference for `reminder_1..3`, `paid_alert` templates. |
| STOP / opt-out | Yes, STOP = suppress + pause subscriptions, keep records; DELETE = erase; START re-opts in (POPIA split) | `whatsapp.py:56-59, 4291+`, `record_opt_out` in `api/commerce.py` | Policy matches spec §11.3. Pack's `opt_outs` (hashed number, scope merchant/global) is stricter; keep pack's. |
| Reminders / dunning | Yes for invoices (stage cadence, mig 131) and bookings (24 h look-ahead) | `commerce.py::_process_overdue_invoices`, `bookings/reminders.py` | Different problem (overdue invoices vs. abandoned tap-bills with 08:00-20:00 SAST window, 1/day, max 3). Not reusable. |
| Refunds | Yoco refund only, tracked on orders (`refund_status`, `refunded_amount_cents`) | `api/yoco.py:230`, mig 124 | No proportional split reversal, no partial-refund line reversal. |
| Ledger | Double-entry GL posting hooks for orders/invoices/expenses, trial balance | `commerce/ledger.py`, mig 121 | Accounting GL for the tenant's books, **not** a party-level append-only money-movement ledger. Not a substitute; but the tap-to-pay service should post a summarised journal into this GL per payment. |
| Audit log | `merchant_audit.audit()` and admin audit tables | `api/merchant_audit.py`, mig 072/107 | Pack requires insert-only, every state change; build in the service. |
| Merchant PWA shell | Dashboard has manifest + `sw.js` + icons | `vula_dashboard/public/` | Installable PWA exists for the dashboard. No offline price cache, push, SSE, or Web NFC. |
| Multi-tenant isolation + RLS | Yes, CI-enforced RLS | `tools/check_migrations_rls.py` | Pack uses `merchant_id` scoping + optional RLS. Keep; add tenant-isolation test (pack TEST_PLAN). |
| Receipts / PDF | PDF generation exists | `commerce/pdf.py` | Reference for tax invoice; pack wants satori+resvg PNG slip. |
| Booking / appointment mode | Bookings module exists | `vula/bookings/` | Natural **bill source** for appointment mode (create a bill from a confirmed booking). |
| Integer cents everywhere | Yes, stated rule | CLAUDE.md | Same rule as pack. |

## 3. What is missing entirely (no trace in Vula)

Searched `vula_mind/` for: NFC/NTAG/SDM, tips, tip pools/shifts, revenue/tip split rules, payouts,
Web Push/VAPID, SSE streams, OTP/PIN staff sign-in, QR generation of pay links, claim tokens,
bill-code matching, `wa.me` deep-link entry flow. **None exist.** Specifically absent:

- NTAG424 DNA tap verification (AES-CMAC, key diversification, counter replay check) and the tap endpoint `/t/{code}`
- Bill matching (number match, first-tap claim, bill code with 3 attempts/15 min lock, release, shares)
- Tip step, quick-tip mode, custom-tip validation
- Party-level append-only ledger, split strategies (`ledger_only` / `native` / `collect_then_payout`), balances, payouts, proportional refund reversal, chargeback handling
- Shifts and tip pools
- Notification chain (SSE -> Web Push -> WhatsApp template -> SMS) with per-payment dedupe
- Staff OTP + PIN sign-in, devices, refresh-token family revocation
- Slip PNG rendering and VAT tax invoice on request via chat
- Nightly provider settlement reconciliation
- Operator console (tag inventory, webhook replay, unreconciled payments)

Roughly 80 percent of the pack is net-new. The overlap is concentrated in plumbing (webhook verification,
dedup, templates, opt-out), which is cheap to rebuild and already specified more strictly in the pack.

## 4. Review of the spec and pack (issues to resolve before building)

Checked the PDF against the pack. They agree on the core flow; these are the points I would fix or decide first.

1. **Spec/pack drift.** PDF names six modes in a table (Appointment, Counter, Table, Quick tip, Field, Remote invoice) — pack matches. PDF bill states are `open, claimed, paid, cancelled, expired` plus `abandoned, needs_follow_up, written_off, paid_other` later; the pack consolidates these in SPEC §6.1. Use the pack's version as source of truth.
2. **Product name risk (pack Q5).** "KakEnBetaal" contains Afrikaans vulgar slang; Meta template review and some merchants may object. Keep `PRODUCT_NAME` configurable (pack already does) and decide before registering templates, since template names/branding are painful to change.
3. **`wa.me/<number>?text=PAY <token>` is forgeable and shareable.** A token in prefilled text can be forwarded. Spec mitigates with single-use 2-minute claim tokens and merchant name in the confirmation. Also keep: payer can differ from claimer (spec rule 8), so the slip goes to the payer; make sure the merchant-visible number is masked.
4. **Webhook-only paid state.** Both documents say never trust the redirect; keep. Add a 60 s poll fallback (spec edge case) — present in pack as `reconcile.payments` every 15 min, which is too slow versus the spec's 60 s. Use a faster targeted poll for sessions older than 60 s.
5. **Funds flow is the real blocker, not code.** `collect_then_payout` means holding third-party money (pack L1/L6; spec compliance checklist). Pack correctly gates it behind `FUNDS_FLOW_LEGAL_SIGNOFF=true`. Pilot on `ledger_only` or provider-native split. Provider split support (PayFast Split, Peach sub-merchants) is **unconfirmed** in both documents and must be confirmed in writing (pack M8).
6. **Apple Pay / Google Pay via the chosen provider is an assumption.** Vula already integrates Yoco, PayFast, Peach, iKhokha, Ozow and Paystack, which is a head start: but wallet availability per gateway/channel was not verified here. Treat as T1/T2 in the pack's open questions.
7. **Tag security.** NTAG213 cannot be signed: cloned tags work. Pack correctly requires NTAG424 DNA for production and `ALLOW_STATIC_TAGS` for pilot. Programming signed tags needs an operator tool or an NFC-capable app, not Web NFC (pack T4) — budget for this.
8. **Reminder compliance.** Reminders to non-opted-in numbers outside the 24 h window need approved templates and may be charged; pack handles opt-in text in the first message. Confirm wording with a lawyer (POPIA).
9. **Tips tax / VAT treatment** is an open accounting question (spec, pack L4). Do not hard-code until confirmed.
10. **Cost numbers are estimates** (tags, FX at about R16.5-17/USD) — re-quote before ordering.

## 5. Recommended shape: plug-and-play

```
tap-pay/ (new repo, uses the build pack as-is)
  apps/api  apps/worker  apps/web
  packages/{core,db,providers,whatsapp,tag,wa-sim,slip,config,testkit}

Consumers (Vula, others) integrate through three seams:
  1. Bill source      POST /v1/bills                    (Vula: booking confirmed / invoice issued)
  2. Event out        signed webhook  bill.paid, tip.received, refund.created  -> consumer
  3. Merchant identity  merchant_id mapping + API key per consumer
```

Adapters inside the service (already the pack's design): swap `PaymentProvider` (mock / peach / payfast /
add yoco), `WhatsAppClient` (cloud / sim), `Notifier`. A new project plugs in by registering a merchant,
choosing a mode, and calling `POST /bills`; no engine code changes ("new merchant types are a new
combination of settings, not new code").

**Vula-side work (small, after the service exists):**

- `vula/integrations/tappay.py` client: create bill from a booking or invoice, receive `bill.paid`.
- Webhook receiver that marks the invoice/order paid through the existing service path
  (`cs.update_invoice_status`) so GL posting fires, using the same `_amount_covers` check.
- A `commerce_admin` tool "request tap payment" with the existing confirm-before-send guard and read-back.
- Optional: post a summarised journal per payment to the GL (`commerce/ledger.py`).
- Add `yoco` as a provider adapter in the new service by porting `vula/payments` Yoco logic (Vula already has the creds flow).

## 6. Suggested sequence

1. Create the new repo from the pack; run M0-M1 (scaffold, tap to slip against mock provider and wa-sim). No external accounts needed.
2. Resolve the owner decisions that change design: **Q1 split provider, Q3 WABA owner/number, Q5 name, Q4 pilot merchants and modes**, plus legal review of funds flow (L1).
3. M2-M5 (matching, tips, PWA/notifications, money). M5 is where money-correctness tests (sum of lines equals payment, append-only ledger, idempotent payout) are the gate.
4. M8 real adapters only against current official docs and sandbox credentials; write `PROVIDER_NOTES.md`.
5. Add the Vula integration seam (section 5) and run a pilot with one existing Vula tenant plus one coach.

## 7. What I did and did not verify

- Verified by reading code: provider interface and providers list, payment webhook behaviour, WhatsApp
  signature check, dedup, interactive buttons/list, STOP/DELETE handling, templates API, GL ledger,
  refund tracking, bookings reminders, dashboard PWA manifest/service worker, and the absence of
  NFC/tips/splits/payouts/push/SSE/OTP.
- Not verified: any external provider, Meta or NXP behaviour (the pack itself marks these as unverified
  and defers them to M8), and wallet support per gateway. I did not run the pack or any tests.
- No code was changed in Vula; this document is the only addition.
