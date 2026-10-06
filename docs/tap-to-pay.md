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
Slip as PNG/PDF (text slip only), merchant PWA screens + SSE + push, PIN/OTP sign-in, reminders for
abandoned bills, GL journal posting for tap payments (per-party lines live in `kb_ledger_lines`),
merchant audit rows for bill actions, per-tag rate limiting beyond the global IP limit, NTAG424 SDM
verification (static tags only), group bills, shifts/pools, payouts, nightly reconciliation.

## Switching it on (self-serve)
Apply migrations 199, 200 and 201 in the Supabase SQL editor (staging first — docs/staging.md), then the
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
