# Operator alerts

Vula's own alerts go to the operator (Ian, `PLATFORM_SUPPORT_PHONE`) on WhatsApp. They come from
**Vula's own number**, never from a business's number. This covers setup gaps (`/ready`), the local
AI computer being unreachable, a stalled server, cloud AI being down, trials ending, a stuck
WhatsApp move, the knowledge-base checks, a business's WhatsApp account health from Meta's
webhook (a rejected, paused or disabled template, a quality drop, a banned or restricted account:
`vula/api/whatsapp_account_events.py`), and feedback a business's team sends about Vula itself.
All of them go through one function: `vula/integrations/platform_support.py::alert_operator`.

Until 31 Oct 2026 they went out on Off the Hook's number, so a client's line carried Vula's alerts
and other clients' names. A business's WhatsApp number now only ever carries that business's own
conversations.

## Setting up Vula's number
1. Create a Meta Business account for Vula, separate from any client's, and add WhatsApp to it.
2. Add a number that is not on any WhatsApp app, and register it (you choose the PIN).
3. Create a system user with a permanent token for that WhatsApp Business Account.
4. In Railway, set `PLATFORM_WA_PHONE_ID`, `PLATFORM_WABA_ID` and `PLATFORM_WA_TOKEN`, then deploy.

At boot Vula submits the `vula_operator_alert` template to that account ("🔧 Vula alert: …"). It
arrives outside Meta's 24-hour window once approved; until then, free text works only if the
operator has messaged Vula's number in the last 24 hours.

## Without Vula's number
Nothing is sent. Each alert is written to the server log as `operator alert (not sent: …)`, and
`/ready` lists "Vula's own WhatsApp number is not set". Feedback text is kept in
`vula_platform_feedback`, not in the log.

## Repeats
The readiness summary goes out only when the list changes. The last one sent is remembered in
`vula_platform_state` (migration 234), so a redeploy doesn't send the same list again.

## New businesses set up over WhatsApp (5 Nov 2026)
Vula's own number also sets up a new business (`vula/onboard_wa.py`, migration 249):
1. Master › Tenants › New tenant, with the owner's name and WhatsApp number (`POST
   /v1/master/clients`). Vula makes the business, adds the owner to its team (role owner, so they
   can sign in with WhatsApp) and sends the `vula_business_setup` template from Vula's number
   ("Hi Sam, this is Vula. Seaview Tiles is ready on Vula. Reply START …"). Vula submits that
   template at boot, next to `vula_operator_alert`.
2. The owner answers 12 questions one at a time: name, what you do, area, hours, payment, pricing,
   email, VAT, bank details, logo, documents, team. Each answer is saved to the business profile or
   invoice settings and read back before Vula says it's saved. *skip* leaves a question for later,
   *stop* pauses, *start* carries on. Documents and slips sent meanwhile are filed as usual.
3. Until the business connects a number of its own, its owner and team work with Vula through
   Vula's number. Their messages are handled as that business, and every reply for it (proactive
   ones too) comes from Vula's number, never from the server's default number.
4. Master › Tenants shows "Setting up on WhatsApp": how far each owner has got, with **Nudge** to
   send the welcome again. If an owner goes quiet for 24 hours, one reminder goes out by itself.

If someone who isn't on any of those businesses messages Vula's number, they get one polite reply a
day and the operator gets an alert. Set `PLATFORM_WA_NUMBER` (digits) as well: the WhatsApp
sign-in page needs it to show the number such a business messages.
