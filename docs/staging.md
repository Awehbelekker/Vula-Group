# Staging environment

Written 2026-09-27. Vula has no staging environment. Every merge goes straight to the live
businesses, and migrations are applied by hand in the Supabase SQL editor. Changes that alter
behaviour, such as `ENFORCE_TENANT_AUTH` or the route-auth protections added in PR #76, need
somewhere to fail first. This guide sets one up. Nothing below is created automatically, and
each step says where it costs money.

## What staging is

It's a copy of production that talks to nothing real:

| Piece | Production | Staging |
| --- | --- | --- |
| Backend | Railway service in the main environment | Same Railway project, a `staging` environment, same repo and Dockerfile |
| Database | Supabase `vula-production` | A Supabase **branch** of it (or a separate small project) |
| Dashboard | Vercel production | The Vercel **preview** deployment for the staging branch |
| WhatsApp | Each tenant's live number | One Meta **test number** on the same app |
| Payments | Live Yoco / PayFast | Yoco test keys, PayFast **sandbox** |

## 1. Database: Supabase branch

1. In the Supabase dashboard for `vula-production`, open **Branches** and create a branch (e.g.
   `staging`). A branch starts with the schema but no production data, which is what you want:
   test tenants only.
2. Open the branch's SQL editor and apply migrations. The quickest way: point staging's
   dashboard at the staging API (step 3), sign in as master, open **Master › Health**, press
   **Copy SQL to apply**, then paste and run it. Every migration is idempotent.
3. Create one test tenant through the normal signup flow, so the signup path is exercised too.

**Cost:** Supabase branches are billed per hour while they exist. See Supabase's
"Branching" pricing. Delete the branch when you're not testing; it can be recreated.

## 2. Backend: Railway `staging` environment

1. In the Railway project, **New environment** → `staging`, duplicating the web service.
2. Deploy from a `staging` git branch (merge the branch under test into it) or straight from
   the PR branch.
3. **Environment variables.** Start from a copy of production's, then change these:

   | Variable | Staging value |
   | --- | --- |
   | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | The **branch's** URL and keys, never production's |
   | `API_KEY` | A **different** key from production |
   | `WHATSAPP_PHONE_ID`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_TOKEN`, `WHATSAPP_VERIFY_TOKEN` | The test number's (step 4) |
   | `VULA_FB_APP_SECRET` | Same Meta app secret (webhook signatures) |
   | `YOCO_SECRET_KEY`, `YOCO_PUBLIC_KEY`, `YOCO_WEBHOOK_SECRET` | Yoco **test** keys |
   | `PAYFAST_MERCHANT_ID`, `PAYFAST_MERCHANT_KEY`, `PAYFAST_PASSPHRASE` | PayFast **sandbox** credentials |
   | `RESEND_API_KEY`, `FROM_EMAIL` | A test sender, or leave empty so no real email goes out |
   | `ENFORCE_TENANT_AUTH` | `true` from day one |
   | `RUN_SCHEDULED_JOBS` | `true` (staging runs a single service) |
   | `DEBUG` | `false`, so staging behaves like production (webhook signatures required) |
   | `SENTRY_DSN` | Empty, or a separate Sentry environment |
   | `OPENROUTER_API_KEY` | Same key is fine. Set a small spend cap on the test tenant |

4. Run `railway up` in the staging environment after any env change. Env changes alone
   redeploy the old code.

**Cost:** a Railway environment is billed on usage like production. One small service is
usually a fraction of production's bill. Remove the environment's service when it's idle.

## 3. Dashboard: Vercel preview

Vercel already builds a preview for every pushed branch (PR #76 has one). For the staging
branch, set `VITE_API_URL` to the staging Railway URL in the project's **Preview** environment
variables (scoped to that branch). Sign in with a user on the staging branch's database.

**Cost:** previews are included in the existing Vercel plan.

## 4. WhatsApp: Meta test number

1. In Meta's developer console for the same app, add a **test phone number** (WhatsApp →
   API Setup). Meta provides one free test number with a limited list of recipient numbers.
2. Add the team's phones as allowed recipients.
3. Subscribe the webhook for the test number to `https://<staging-railway-url>/v1/whatsapp/webhook`,
   using staging's `WHATSAPP_VERIFY_TOKEN`.
4. Map the test number to the staging test tenant (dashboard → Settings → WhatsApp, or the
   `vula_tenant_config` row).

**Cost:** free within Meta's test-number limits.

## 5. Smoke checklist before promoting to production

Run this on staging for any PR that changes auth, payments, migrations or the WhatsApp paths:

- [ ] **Master › Health** shows no migrations pending.
- [ ] **Customer WhatsApp:** browse, add to cart, place an order (delivery and collection),
      track it, send STOP then START.
- [ ] **Owner WhatsApp:** sales today, mark an order paid (confirm button), add an expense,
      create a reminder.
- [ ] **Staff / sales-rep WhatsApp:** limited tools only; a rep can't invoice.
- [ ] **Dashboard sign-in** as owner, staff and master. Each sees only its own business.
      Takeoff upload and BOQ download work. Field Ops tasks load.
- [ ] **A payment** through the Yoco test card or PayFast sandbox marks the order paid, and
      only once.
- [ ] **Master › Models:** one small run completes and shows results.
- [ ] Nothing in the staging logs shows 401s from the dashboard. That would mean a call
      missing its tenant.

Only then merge and deploy to production, following the PR's deploy steps.

## Tearing it down

Delete the Supabase branch and remove the Railway staging service when testing is done.
Keep the Meta test number and the Vercel env var; they cost nothing and make the next setup a
few minutes' work.
