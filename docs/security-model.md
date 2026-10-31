# Security model: database access

Written 2026-10-31 (go-live audit, Phase 2). Explains the Supabase security-advisor findings
that are accepted on purpose, and the two settings that have to be right in production.

## Who talks to the database

| Caller | Key / role | RLS |
| --- | --- | --- |
| The API (`vula_mind`) | `SUPABASE_SERVICE_KEY` → `service_role` | **Bypassed.** The service role has `BYPASSRLS`, so policies never apply to it |
| `tools/migrate.py` | `DATABASE_URL` → the `postgres` owner | Bypassed (owner) |
| The dashboard (browser) | anon key + the user's session → `authenticated` | Enforced |
| Anyone else with the anon key | `anon` | Enforced |

Tenant isolation for the API comes from the API itself: every query is scoped by `tenant_id`, and
the tenant guard (`tenant_admin_guard` in `vula/api/server.py`, with `ENFORCE_TENANT_AUTH`) checks
that the caller belongs to the tenant named in the path. RLS is the backstop for anything that
reaches PostgREST directly with the public anon key.

## "RLS enabled, no policy" (about 54 tables)

The advisor lists roughly 54 tables as `rls_enabled_no_policy`. These are tables only the API
reads and writes. With RLS on and no policy, PostgREST answers every `anon` or `authenticated`
request with nothing (reads return no rows, writes are refused), while the service role bypasses
RLS and works normally. That is the intended result: the browser can't touch them at all.

Two patterns exist in the migrations, and both are safe:

1. **Explicit `service_role` policy.** `migrations/173_security_advisor_hardening.sql` (header,
   lines 4-11) added `create policy … for all to service_role using (true) with check (true)` to
   the 7 tables the advisor flagged then, following 148, 152 and 153. The policy documents intent
   but changes nothing at runtime, because the service role bypasses RLS anyway.
2. **RLS on, no policies.** Most later migrations (e.g. 146, 167, 178, and 212-240) say in their
   header "Read and written only by the API with the service key. RLS on, no policies." That is
   the pattern going forward, and what the advisor counts.

Neither pattern grants `anon` or `authenticated` anything. CI enforces that every table created
in `migrations/*.sql` gets `enable row level security` (`tools/check_migrations_rls.py`).
A table the dashboard does read from the browser needs a real tenant-scoped policy instead; it
must not be added to this list.

## `vula_storage_can_write`: `authenticated` keeps EXECUTE

The advisor flags `public.vula_storage_can_write(text)` because it is `security definer` and
callable by `authenticated`. That grant is required:

- `migrations/193_storage_tenant_writes.sql` lines 10-21 define it (`security definer`,
  `set search_path = public`, `revoke … from public, anon`, `grant execute … to authenticated`).
- Lines 23-47 of the same file use it in the `storage.objects` insert/update/delete policies of
  the `product-images` and `signatures` buckets, plus the signatures read policy. Those policies
  run as the signed-in user, so the user must be able to execute the function.
- The dashboard uploads with the user's session, not the service key:
  `vula_dashboard/src/lib/brandUpload.js:39` (logo, app icon),
  `vula_dashboard/src/components/VulaInvoices.jsx:1298,1316,1337` (signature, invoice logo,
  product images), `vula_dashboard/src/components/VulaProjectBoard.jsx:126`.

Revoking it breaks every one of those uploads. What it reveals: called directly, it returns
true or false for "is the caller (`auth.uid()`) a member, or master, of the tenant whose folder
this path starts with". It reads only the caller's own rows in `vula_tenant_users`, so a user
learns nothing they don't already know. The warning is accepted.

## Function `search_path`

Functions without a pinned `search_path` are flagged `function_search_path_mutable`. Migration
173 fixed nine, and `migrations/241_trigger_search_path.sql` fixes the three Tap to Pay trigger
functions (`kb_ledger_append_only`, `kb_tax_invoice_immutable`, `kb_audit_append_only`). This is
metadata only and doesn't change what the triggers do.

## Must be set in production

- **`ENFORCE_TENANT_AUTH=true`.** The default in `config.py` is `false`, which leaves
  `/v1/commerce/{tenant}/admin/*`, `/v1/team/{tenant}/*`, `/v1/users/{tenant}/*` and the other
  guarded tenant routes open to anyone who knows a tenant slug. `GET /ready` lists it as a
  platform gap while it's off, and `tools/smoke.py` fails its "tenant admin route, no auth"
  check (expects 401).
- **Leaked-password protection.** A Supabase dashboard toggle (Authentication › Policies /
  Providers › Email, "leaked password protection"). No migration or API can set it; switch it on
  by hand in each project (production and the staging branch).

## Retention (POPIA s14)

One daily sweep, `vula/retention.py::run_all` (started as `_retention_loop` in
`vula/api/server.py`), deletes data past its period. Each kind of expiring data is one `Rule`
there: a name, the setting that holds its period in days (0 keeps it), and the function that
deletes. There is deliberately one sweep, not a loop per feature.

| Rule | Setting | Default | Deletes |
|---|---|---|---|
| `takeoff_plans` | `TAKEOFF_RETENTION_DAYS` | 90 | uploaded drawings on the volume, `vula_takeoff_jobs` rows, in-memory jobs |

Data that is **not** swept, and why:

- Invoices, quotes, bank lines, expenses, payroll and other accounting records: kept at least 5
  years (Tax Administration Act s29).
- Filed documents (`vula/api/server.py` upload dir, Supabase storage): business records, the
  owner's to keep or delete.

Candidates waiting for an owner decision (each would be one more `Rule`): WhatsApp message
dedupe rows (`vula_wa_msg_dedup`), app error reports (`vula_app_errors`), WhatsApp login requests
(`vula_wa_login_requests`), website leads that never converted (`vula_website_leads`),
conversation turns (`vula_turns`, `vula_kb_turns`).
