/**
 * VulaInbox.jsx — shared inbox: live customer WhatsApp conversations.
 *
 * The owner sees every customer chat the AI is having, can open a thread,
 * TAKE OVER (bot goes quiet, they reply as themselves over WhatsApp), and
 * HAND BACK to the bot when done. Auto-refreshes so new messages appear.
 * Data and actions live in inbox/useInbox.js (shared with the redesign's inbox/InboxV2.jsx).
 */

import InboxV2 from './inbox/InboxV2'

/** v2: the redesigned Inbox (same hooks, so the same data and actions). */
export default function VulaInbox({ tenantId }) {
  return <InboxV2 tenantId={tenantId} />;
}

