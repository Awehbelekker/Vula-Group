# Operator alerts

Vula's own alerts go to the operator (Ian, `PLATFORM_SUPPORT_PHONE`) on WhatsApp. They come from
**Vula's own number**, never from a business's number. This covers setup gaps (`/ready`), the local
AI computer being unreachable, a stalled server, cloud AI being down, trials ending, a stuck
WhatsApp move, the knowledge-base checks, and feedback a business's team sends about Vula itself.
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
