# Tap-to-pay (KakEnBetaal) — status and open items

NFC tag or QR -> WhatsApp `PAY <token>` -> tip -> one-time pay link -> PayFast -> slip. Built as a
module inside `vula_mind/vula/tap/`. **Off everywhere** until a tenant id is listed in
`TAP_TO_PAY_TENANTS` (comma separated). Pilot: appointment mode, `ledger_only` split (Vula holds no
money; the ledger records who is owed what).

## Layout
| Path | Role |
| --- | --- |
| `vula/tap/core/` | Pure rules: integer-cent money/tip/split maths, bill + session state machines, bill matching, bill-code gate. Property-tested. |
| `vula/tap/service.py` | Orchestration (tap, PAY, tip, confirm, pay link, ITN confirm, ledger, slip, merchant actions) |
| `vula/tap/ports.py` | Seams: `Repo`, `Messenger`, `Gateway` |
| `vula/tap/repo.py`, `adapters.py` | Supabase, WhatsApp (reuses existing Meta senders), PayFast gateway |
| `vula/tap/api.py` | `/t/{code}`, `/v1/tap/pay/...`, merchant bill endpoints, WhatsApp + ITN hooks |
| `migrations/199`, `200` | `kb_*` tables (RLS on all), append-only ledger trigger, claim tokens |

Hooks into existing code (all additive): `whatsapp.py` (text + `kb:` interactive ids),
`payments.py::payment_webhook` (PayFast ITN with `m_payment_id` starting `kb-`), `server.py`
(router + tenant guard for `/v1/tap/{tenant}/...`), `config.py` (two settings).

## Must be verified before the pilot (PayFast docs were unreachable when this was built)
1. **ITN server-side confirmation** — `adapters.PayFastGateway._server_confirms` POSTs the ITN back to
   `/eng/query/validate` and requires `VALID`. Endpoint and body shape from memory; fails CLOSED, so a
   wrong guess shows up as "payments never confirm" in the sandbox, not as a security hole.
2. **Fee field** — `amount_fee` is read as an absolute value; confirm sign/format in a sandbox ITN.
3. **Source-IP allowlist** for ITNs is NOT implemented (signature + postback + amount check are).
4. **Apple Pay / Google Pay on PayFast hosted checkout** — unconfirmed; the 30-second target must be
   re-measured with the methods PayFast actually offers.
5. **Existing latent issue (not touched):** `payments.PayFast.create_link` appends `email_address`
   after `item_name`; PayFast signs in its documented order (email before `m_payment_id`). The tap path
   passes no email so it is unaffected, but invoice links with a customer email may fail signature.
   Verify against PayFast's published example, then fix with a test.
6. **Refunds** for PayFast are not built (only Yoco has one). Refund UI must stay off for tap payments
   until it is.
7. **Split payments**: native PayFast split deliberately not used. Needs written confirmation first.

## Not built yet
Slip as PNG/PDF (text slip only; tax invoices are PDF), credit notes for tax invoices, GL journal posting for tap payments (per-party lines live in `kb_ledger_lines`),
merchant audit rows for bill actions, per-tag rate limiting beyond the global IP limit, NTAG424 SDM
verification (static tags only), group bills, shifts/pools, payouts, nightly reconciliation.

## Coach app (Vula Pay, `/pay/`)
Installable PWA for coaches/cashiers (own manifest + service worker, scope `/pay/`; source `vula_dashboard/src/pay/`,
`pay/index.html`, `public/pay/`). Sign-in is a one-time 6-digit code issued by the owner (dashboard -> Tap to Pay ->
Coach app) + the person's WhatsApp number + a PIN they choose; no email/password. 5 wrong codes burn the code, 5 wrong
PINs lock the phone for 15 minutes, "Sign out" in the dashboard revokes a phone on its next request. Staff see only their
own bills; owners/managers see all. Live "Paid" status is streamed from `/v1/tap/app/events` (polls the DB every 2 s, so it
works across workers); a payment vibrates + beeps and shows tip and the person's share. Shell and recent bills open
offline; creating a bill needs a connection.

Web Push (optional, needs keys): set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` on Railway (generate once
with `python vula_mind/scripts/gen_vapid.py`; keep the private key secret and never regenerate it, or every existing subscription dies) and `railway up`. iPhone only delivers push to the
installed home-screen app. The WhatsApp alert still goes out regardless. Apply migration 202.

Not yet: WhatsApp-OTP sign-in (needs a Meta-approved authentication template), tip-pool/manager summaries, PNG icons
(the app reuses the dashboard's SVG icon; fine for Chrome/Android installs, add 192/512 PNGs for older/iOS icons).

## Receipts (animated slip, both sides)
`vula_dashboard/src/receipt/PrintedSlip.jsx` is one shared component, shown FULL SCREEN on both sides: a full-width
printer bar sits flush with the top edge of the screen (under the status bar) and the slip feeds down out of its slot
in one continuous glide after the printer bar has slid in (feed delay `FEED_DELAY` = 0.55 s, kept in sync between the CSS and the sound); the ink is faint at the slot and develops as the paper leaves it, a print-head glow sweeps the slot, and
the slip gives a small tear-off settle at the end (the barcode end emerges first, as on a printer that prints in
reverse). It also has a choice of five sound styles in `src/receipt/printSound.js`, default `chime` (no printer noise at all, just a warm chime when the slip lands); others: `paper` (soft whoosh), `digital` (rising sweep + sparkles), `modern` (thermal printer) and `classic` (dot-matrix), picked per phone via `localStorage vp.soundstyle`. The `modern` style is: quiet warm motor whirr, soft paper glide, faint feed ticks, a soft tear, then a four-note chime in the A4-A5 range (measured: centroid ~530 Hz, no energy above 2 kHz); the earlier dot-matrix sound stays available as style `classic` via `localStorage vp.soundstyle`) (volume Off / Low / Medium / High, default Low, remembered per phone in `localStorage vp.vol`; Low peaks at about -22 dBFS, measured offline), VAT line for
VAT-registered merchants, a PAID stamp and barcode. It respects `prefers-reduced-motion` (appears instantly,
silent) and stays silent when the browser blocks autoplay (the Replay button, being a tap, can play sound).
- **Customer:** the WhatsApp slip now ends with `Your receipt: <dashboard>/r/<token>`. `/r/` is its own Vite page
  (noindex, no-referrer). The token is an HMAC of (payment id, nonce) — unguessable, nothing stored, revoked by
  `POST /v1/tap/{tenant}/payments/{id}/receipt/revoke`. The public JSON carries only merchant, service, first name
  of who served, amounts, VAT, date, ref — never numbers or the coach's share. All failures look identical (404).
  "Save / print" uses the browser's print-to-PDF with print CSS (no animation).
- **Coach:** when a payment lands in Vula Pay the slip prints in an overlay with "Your share"; any paid bill has
  "View slip". Sound can be switched off under "This phone".
Apply migration 203. VAT shown on a receipt is 15% of the VAT-inclusive BILL only; tips are excluded until the
accountant confirms their treatment.

## Tax invoice on request
A customer who has paid can ask for a VAT **tax invoice** two ways: reply `TAX` on WhatsApp (the slip says "Need a VAT tax
invoice? Reply TAX." when the merchant can issue one; the bot asks "Send your company name and VAT number for a tax invoice.",
or accept it all in one message: `TAX Acme Trading (Pty) Ltd 4123456789`), or tap **Get tax invoice** on the receipt page. The PDF
comes from the existing invoice renderer (`vula/commerce/pdf.py`, new "VAT No" line under Bill To) and is sent as a WhatsApp
document / opened from the receipt page. Code: `vula/tap/tax.py`, pure rules in `vula/tap/core/taxinvoice.py`.
- **Who can issue:** only a merchant with *VAT registered* ticked, a VAT number **and a registered address** in Invoice
  settings. Otherwise the option isn't offered, and a customer who asks anyway is told to ask the merchant. The supplier name
  comes from the same settings (company name, else the tenant name).
- **Buyer details:** company name + SARS VAT number (10 digits starting with 4; spaces tolerated). Address is optional (the page has a
  field). Wrong number -> the bot explains and keeps the request open for 30 minutes; "cancel"/"no thanks" closes it. Anything that
  isn't an answer (a question, a long message) is passed to normal routing untouched.
- **One immutable invoice per payment.** Asking again returns the same PDF; the details cannot be changed once issued (a DB trigger
  forbids update/delete; a correction needs a credit note, not built). Supplier and buyer details are snapshotted, so later
  settings changes never alter an issued invoice. Numbers are `TI-000001`... sequential per tenant, never reused (unique + retry).
- **Amounts:** the invoice is for the **bill** only: excl-VAT, VAT (15%, half-up) and total = what was charged for the service. A tip is
  not on it; if there was one, a note says it was paid separately. Setup test payments can't be invoiced.
- **Who can ask:** by WhatsApp, the number that paid, within 30 days (a message from anyone else, or from someone who never paid, is
  not intercepted). On the page, anyone with the unguessable receipt link (same token; revoking the receipt revokes this too).
- Open question for the accountant: VAT treatment of tips, and whether an abridged invoice is wanted for bills under R5 000 (we
  always print the buyer's details, which is also valid).
Apply migration 205 (`kb_tax_invoices`, `kb_tax_requests`).

## Unpaid-bill reminders
When a customer has seen their total and then leaves (the 10-minute session lapses) or their payment fails, the bill
becomes **abandoned** (still reserved for that customer; they can tap the tag again and carry on). A background sweeper
(`_tap_sweep_loop` in `server.py`, every minute, safe on several workers) then sends up to **3** reminders, each with a
fresh one-time pay link for the same total (an older link stops working): **#1 about 10 minutes** after they left,
**#2 the next morning (08:00 SAST)**, **#3 on day 3** which says it is the last. Rules, enforced in
`vula/tap/core/reminders.py` and property-tested: never more than 3, never more than one per SAST calendar day, only
between 08:00 and 20:00 SAST (anything due outside waits for 08:00). The first reminder tells the customer how many to
expect; every reminder names the merchant and the amount and says "Reply STOP". STOP (the platform's existing opt-out)
ends the sequence at once and hands the bill to the owner, as does paying, cancelling, writing off or "paid another
way". After the last reminder, or when reminders are off, the bill becomes **needs follow-up**.
Owner (dashboard -> Tap to Pay -> Unpaid bills): reminders per bill (Off / 1 / 2 / 3), the list with reminders sent and the
next one due, **Resend link** (same window, one-a-day and STOP rules; logged as a manual reminder that does not use up
the three), **Paid another way** (cash / EFT / other), **Release** (frees the bill for anyone) and **Write off**. The coach
sees their own unpaid bills with the same Resend link. Customers are shown as "ending 482" only.
WhatsApp only allows free text within 24 hours of the customer's last message, so reminder #1 normally goes as text and
later ones (day 3, sometimes #2) must be **approved templates**. Create two utility templates in Meta and set
`TAP_REMINDER_TEMPLATE` and (optional) `TAP_REMINDER_FINAL_TEMPLATE`; each takes three body variables
{{1}} merchant, {{2}} amount, {{3}} pay link, e.g. `You haven't finished paying {{1}} {{2}}. Pay now: {{3}} Reply STOP to
stop reminders.` and `Last reminder: {{2}} to {{1}} is still unpaid. Pay now: {{3}} Reply STOP to stop.` Meta may not
accept a link variable in the body (it often wants a URL button); if so, adapt the template and `_send_wa_template`
accordingly. Without a template, an out-of-window reminder is **skipped and logged** (`skipped_no_template`, shown to the
owner), never sent as free text. Apply migration 204.
Trade-off to know: an abandoned bill stays reserved to the customer who left, so another customer tapping that tag sees
"being paid from another phone" until the owner uses Release (or the sequence ends and they close it).

## Link buttons
The pay link and the receipt link are sent as WhatsApp **URL buttons** ("Pay R 550.00", "View receipt"), so the long server address is
never shown in the chat (`Messenger.link_button` -> `whatsapp._send_wa_cta_url`, interactive type `cta_url`). Buttons only work inside
the 24-hour customer window, which is always true for these two messages. If WhatsApp refuses the button for any reason, the same
message is sent as plain text with the link, so nothing breaks. Reminders sent later still use text or approved templates (a template
can carry a URL button; see the reminder notes). The address shown when the page opens in the browser is the server's own domain; a
custom domain (for example `pay.<yourdomain>`) is set in Railway -> Networking, then `PUBLIC_BASE_URL`.

## Switching it on (self-serve)
Apply migrations 199, 200, 201, 202, 203, 204 and 205 (or the combined `migrations/_APPLY_2026-10-06_tap_to_pay_199-205.sql`) in the Supabase SQL editor (staging first — docs/staging.md), then the
owner does everything in the dashboard: **Money -> Tap to Pay**.

1. Connect PayFast (merchant ID, key, passphrase; start with sandbox keys).
2. Set each person's share of the bill (tips always go 100% to the person who served) and create their tag
   (link + printable QR; write the link to an NFC tag).
3. Run the R5 test payment (scan the QR, pay). The screen ticks by itself when PayFast's notification is
   confirmed; a test books nothing to anyone's earnings.
4. Go live. "Go live" stays locked until a test payment has been confirmed. "Pause" switches it off.

State lives in `kb_settings.mode` (off / testing / live), cached 30 s per worker. Any DB error reads as
off. `TAP_TO_PAY_TENANTS` is still honoured as an operator override (treated as live) and
`TAP_HASH_PEPPER` should be set once on Railway so customer-number hashes survive a service-key rotation.
Tags can be replaced from the screen; the old tag and QR stop working immediately.
