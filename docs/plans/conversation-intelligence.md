# Conversation intelligence: the plan, matched to the code (22 Nov 2026)

This replaces the "Conversation Intelligence Build Brief" for engineering use. The brief's goal
stands — **conversation → structured outcome → action a person confirms** — but much of what it
asked for already exists. Each feature below says what is built, what is missing, and where it
belongs.

## Facts the original brief had wrong

| Brief said | Actually |
|---|---|
| 739+ tests | About 8,600 backend tests (`pytest tests/`), plus dashboard unit and Playwright visual tests |
| Propose-confirm is migration 137 | Migration 146, `commerce_pending_confirmations` (137 is automation firings) |
| The Supabase connector shows the wrong organisation | It reaches production (`jzccetzmahpcoiqwlljm`) correctly. Production stays read-only without the owner's OK |
| Build a job queue | `vula_background_jobs` (migration 206) and the scheduler-leader loops in `vula/api/server.py`; per-business job settings in `vula/commerce/job_config.py` |
| Build a Whisper wrapper | `core/transcribe.py` (local first, language detection); `vula/voice_retry.py` parks a failed transcription and retries it locally, telling the customer |
| Existing "pillars and guidelines" for sensitive topics | Not in the code. `vula/safety.py` handles crisis messages (self-harm); topic routing would be new |
| i18n files in three languages, Storybook | The dashboard is English with no i18n framework, and uses Playwright visual specs. Replies on WhatsApp already follow the customer's language |
| Escalation only understands English and Afrikaans | Phrase lists cover English and Afrikaans fully and isiZulu, isiXhosa, Sesotho, Sepedi and Setswana as starters; a cheap AI check runs for those languages (`vula/escalation.py`) |

## Non-negotiables (unchanged, and already how Vula works)

- Every table has `tenant_id` and RLS (CI-checked by `tools/check_migrations_rls.py`).
- Nothing the AI proposes runs without a confirm (`commerce_pending_confirmations`, read back after).
- Every figure and claim traces to a record or a span of text; untrusted text is fenced
  (`core/prompt_safety.py`).
- New behaviour is off until switched on, per business (`vula/features.py`, `job_config`).
- POPIA: deletion periods in `vula/retention.py`, erase and export in `vula/privacy_data.py`.

## Feature by feature

### A. Conversation health and the handoff deadline

**Built:**
- `vula/handoff_watch.py` reminds the person handling a paused chat when the customer waits, then
  gives the chat back to Vula with an apology to the customer.
- `vula/escalation.py` sends questions Vula can't answer to a helper, nudges stale ones, and
  learns the answer.

**Step 1 (this PR, before launch):**
- Per-business timings in the Scheduling tab (remind after, give back after), migration 295.
- Buttons on the reminder: *Send holding reply*, *Give back to Vula*, *Pass to someone*. Only a
  team member of that business can press them.
- The AI check also runs for mixed-language messages (English with isiXhosa, isiZulu or other
  South African language words), with code-switching test cases.

**Later:**
- An inbox-health screen: waiting now, deadlines missed today, median first reply.
- The 24-hour window warning on an open issue.

### B. Safe outbound webhooks

**Built:** `vula/commerce/reference_url.safe_fetch_bytes` (http(s) only, every hop's address
checked, size and time caps). Webhooks reuse it.

**When:** when the first client needs a POS or CRM to hear from Vula. Then: HMAC signing with a
timestamp, retries, a dead-letter list, and auto-disable after repeated failures.

### C. Voice note → Outcome (after launch, the most valuable new piece)

**Built:** voice notes are fetched, transcribed locally and retried; pending confirmations exist.

**Missing:** an Outcome (overview, cited key points, action items) built from the fenced
transcript, each action a pending confirmation, items whose quote isn't in the transcript dropped.

**Open question:** isiXhosa and isiZulu speech. Whisper's support for them is weak or absent;
test before promising. Today Vula tells customers it understands voice notes in English and
Afrikaans.

### D. Meeting capture in the dashboard (after C)

Recording in the browser with consent, offline chunk upload, iOS Safari handling, and audio
deleted after transcription. A participant may record under RICA; POPIA still needs the
consent record. Large, so it comes after C is proven.

### E. Pre-appointment brief

**Built on branch `ccr-203348f0-3nschv`** (another session, being merged): meetings booked on
WhatsApp, a brief from WhatsApp, emails, money and orders (`vula/meeting_brief.py`), check-ins
with location, and Google/Outlook calendars. Its migrations (275–283) are already in production.
Finish and merge that branch rather than building a second one.

## Order

1. Step 1 of A (this PR).
2. Merge the meetings branch (E).
3. Voice note → Outcome (C), after launch.
4. Webhooks (B) when a client needs one; meeting capture (D) after C.

Staging (`docs/staging.md`) needs the owner's dashboard access (Supabase branch, Railway
environment, a Meta test number) before any of C or D ships.
