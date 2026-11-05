/**
 * VulaBroadcast.jsx — WhatsApp broadcast / campaign builder.
 *
 * Send promotional messages to customers via approved Meta templates.
 * Audience filters: all / ordered last 30 days / high value (>R500).
 * History shows sent/delivered/read counts.
 */

import BroadcastV2 from './broadcast/BroadcastV2'

export default function VulaBroadcast({ ...props }) {
  return <BroadcastV2 {...props} />
}

