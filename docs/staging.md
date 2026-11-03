# Staging environment

Written 2026-09-27. Vula has no staging environment. Every merge goes straight to the live
businesses, and migrations were applied by hand in the Supabase SQL editor (since 31 Oct 2026,
`vula_mind/tools/migrate.py` applies and records them). Changes that alter
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

## 0. Local staging (free, on your own machine)

Added 2026-11-03. For day-to-day testing, run the whole stack locally first; the hosted staging
below is for what local can't do (always on, shared, built exactly like Railway). Needs Docker
and Node.

1. Database: a local Supabase (Postgres 17, auth, storage, REST API, matching production's
   Postgres 17.6). From the repo root:

   ```bash
   cd infrastructure/local-supabase
   npx supabase@2.54.11 start -x imgproxy,vector,supavisor,postgres-meta
   # prints the API URL (http://127.0.0.1:54321), the database URL and the local keys
   ```

   Realtime, Studio, the mail catcher, analytics and edge functions are switched off in its
   `config.toml`: Vula uses none of them. If image pulls fail with "Forbidden" (some networks
   block Amazon's registry), prefix the command with `SUPABASE_INTERNAL_IMAGE_REGISTRY=docker.io`.
2. Migrations, from `vula_mind/`:

   ```bash
   export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres VULA_ENV=staging
   python tools/migrate.py apply --target staging --yes        # every migration, ~1 minute
   python tools/schema_fingerprint.py check migrations/production_schema.txt   # matches production
   ```

   CI runs exactly this on every push ("Fresh database" job).
3. Backend: in `vula_mind/.env` point `SUPABASE_URL` at `http://127.0.0.1:54321` and
   `SUPABASE_SERVICE_KEY` at the local service key that `supabase start` printed (local-only,
   not a real secret). Then `docker compose up -d qdrant redis` from the repo root and
   `uvicorn vula.api.server:app --reload --port 7438` from `vula_mind/`. Leave payment and
   email keys out.
4. Smoke check: `python tools/smoke.py --base-url http://127.0.0.1:7438` (see section 5).
   Then whole WhatsApp conversations, with nothing sent: `python tools/staging_flows.py seed`,
   restart the backend with `GRAPH_HOST=http://127.0.0.1:8799 VULA_FB_APP_SECRET=staging-flows-secret
   WHATSAPP_TOKEN=staging-token WHATSAPP_PHONE_ID=STAGINGPNID`, and run
   `python tools/staging_flows.py run`. A stand-in Meta on this machine records every reply;
   the flows cover crisis support, opt-out, duplicate delivery, the takeoff "Price it" flow and
   a reply when the AI is down. `GRAPH_HOST` is ignored unless it is a loopback address. CI
   runs the same flows on every push. The AI's answers themselves are checked by the go-live
   test (Master › a business › Go-live test, `evals/golive.py`), which needs the real models.
5. WhatsApp and payment callbacks (Meta, PayFast, Yoco) need a public HTTPS address. Run a
   Cloudflare quick tunnel to the backend (`cloudflared tunnel --url http://127.0.0.1:7438`)
   and give its URL to the Meta test number's webhook and the sandbox keys only, never to a
   live tenant.

Stop with `npx supabase@2.54.11 stop --no-backup` (deletes the local data; `stop` alone keeps it).

## 1. Database: Supabase branch

1. In the Supabase dashboard for `vula-production`, open **Branches** and create a branch (e.g.
   `staging`). It has no production data, which is what you want: test tenants only. It may also
   start without Vula's tables (production's migrations were applied by hand, so Supabase has
   none recorded); applying them all on an empty database works, as above.
2. Apply migrations with the runner (`vula_mind/tools/migrate.py`). It needs the branch's
   Postgres connection string (branch › Project settings › Database › Connection string, the
   direct or session-pooler URI, port 5432) in `DATABASE_URL`. It prints the host with the
   password removed, and every command is a dry run until you add `--yes`. From `vula_mind/`:

   ```bash
   export DATABASE_URL='postgresql://postgres.<branch-ref>:<password>@<host>:5432/postgres'
   python tools/migrate.py status --target staging
   # Only if the branch already has the migrations (it copied production's schema): record
   # 1..239 as applied without running them. Skip this on an empty database.
   python tools/migrate.py baseline --target staging --upto 239          # shows the list
   python tools/migrate.py baseline --target staging --upto 239 --yes
   python tools/migrate.py apply --target staging                        # shows what would run
   python tools/migrate.py apply --target staging --yes
   python tools/migrate.py status --target staging                       # 0 pending
   ```

   Each file runs in its own transaction and the runner stops at the first failure, recording
   nothing for that file. `--target staging` is refused if `DATABASE_URL` names the production
   project. Fallback without a connection string: **Master › Health › Copy SQL to apply** and
   paste it into the branch's SQL editor (every migration is idempotent).
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
   | `VULA_ENV` | `staging` |
   | `PUBLIC_BASE_URL` | The staging service's own URL. Left on production's, pay links and callbacks point at production, and `/ready` reports it as a gap |
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

Run this on staging for any PR that changes auth, payments, migrations or the WhatsApp paths.

First the automatic part, from `vula_mind/` (the API key is read from the named variable and
never printed):

```bash
export STAGING_API_KEY='<staging API_KEY>'
python tools/smoke.py --base-url https://<staging-railway-url> --api-key-env STAGING_API_KEY
```

It checks `/status` (200), `/ready` (200, or 503 with the gap list summarised), that the WhatsApp
webhook refuses a wrong verify token and an unsigned POST (403), and that a tenant admin route
and `/takeoff/rates` refuse a request with no credentials (401). It exits non-zero if any check
fails. Then by hand:

- [ ] `python tools/smoke.py …` above passes.
- [ ] `python tools/migrate.py status --target staging` shows 0 pending, and **Master › Health**
      shows no migrations pending.
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
