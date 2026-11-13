# Rolling out the November 2026 work

What PR #304 (`ccr-a280af13-v50oe0`) changes, in the order to switch it on, and what to watch.
Nothing here has run against live data yet: go one step at a time.

## 1. Before deploying

1. **Apply migrations 262–266** in the Supabase SQL editor, in order (or `python tools/migrate.py`):
   | # | What |
   |---|---|
   | 262 | `vula_tenant_config.team_voice_prompt`, `vula_team_members.prefs` |
   | 263 | `commerce_client_summaries` (client notes) |
   | 264 | `vula_personal_memory` (staff private notes, encrypted) |
   | 265 | `commerce_conversation_sessions.last_skill_at` |
   | 266 | `vula_tenant_config.features` (per-business switches) |
2. Deploy the backend (`railway up`) and the dashboard (Vercel).
3. Open `/ready`: no migration should be listed as missing.

## 2. Live for everyone as soon as it deploys

No switch. Check each once after the deploy.

| Change | Check |
|---|---|
| A staff member's own thread with Vula is private (inbox, colleagues, owner, operator screens) | The Inbox lists customers only |
| Dashboard chat knows who is signed in and offers only their access | A restricted login can't call an invoice tool from chat |
| A restricted member signing in by WhatsApp stays restricted | Their dashboard hides Money |
| "Delete my data" removes every conversation store | Test with a spare number |
| Client record (`customer_lookup`): orders, invoices, messages, follow-ups | Ask Vula "what's the story with <client>" |
| Invoices by email from chat | Ask Vula to email an invoice to a test address |
| Home: "Finish setting up", plan and trial, document set-up | Open Home as an owner |
| Website import improvements, product photos copied (`SITE_IMPORT_COPY_IMAGES`) | Run `tools/site_check.py` first (section 4) |
| First-run welcome for businesses in their first 14 days | Sign up a test business |
| Conversations deleted 24 months after the last message (`CONVERSATION_RETENTION_DAYS=730`) | Set to `0` first if you want to hold off |

## 3. Pilot, one business at a time

Switch on per business in **Master › a tenant › Overview › Features to pilot**. A week each,
then turn it on for everyone with the env setting (and `railway up`).

| Order | Switch | Pilot on | Watch | Everyone |
|---|---|---|---|---|
| 1 | Company voice, follow-ups keep their thread, short answers checked | DIGG and Off the Hook | Assistant › Activity › "How Vula answered this week"; the Sunday benchmark | `VOICE_COMPOSE_ENABLED`, `FOLLOW_UP_ROUTING_ENABLED`, `VERIFY_SHORT_ASSERTIONS` |
| 2 | Client notes | Off the Hook | Read a few rows of `commerce_client_summaries`: dated, no amounts, nothing invented | `CLIENT_MEMORY_ENABLED` |
| 3 | "I don't have that" for business facts with nothing on file | A business whose profile is filled in | "Handed to a person" should rise a little, not a lot | `STRICT_TENANT_GROUNDING` |
| 4 | Staff private notes, earlier-in-the-week topics, the AI advisor | Staff who ask for them | Staff feedback | `PERSONAL_MEMORY_ENABLED`, `HISTORY_EARLIER_TOPICS_ENABLED`, `ADVISOR_MODE_ENABLED` |

Before step 1, set each pilot business's voices: **Assistant › Activity › How Vula sounds**
(customers and team).

## 4. Measure the website import on real sites

From `vula_mind/`, on a machine with internet access (writes nothing):

```
python tools/site_check.py clientsite.co.za anothersite.co.za
```

Send the output. Every miss becomes a test fixture and a fix.

## 5. Switching something off

- **One business:** untick it in Master › Features to pilot.
- **Everyone:** set the env setting to `false` and `railway up`.
- **Photo copying:** `SITE_IMPORT_COPY_IMAGES=false`.
- **Retention:** `CONVERSATION_RETENTION_DAYS=0` keeps conversations indefinitely.

## Still open from the go-live audit (accounts, not code)

- Set `SENTRY_DSN`.
- Configure Vula's own WhatsApp number so operator alerts are delivered.
- Attach the Railway volume at `/data`.
- Set up staging (`docs/staging.md`).
- Re-enter the credentials that can't be decrypted.
