/**
 * VulaAutomations.jsx — lean trigger → action rules (P3 automations builder).
 * v1 scope: order reaches a status / product hits its reorder threshold → WhatsApp the
 * customer or the team helper. Evaluated by a backend poller every 5 minutes.
 */
import AutomationsV2 from './assistant/AutomationsV2'

export default function VulaAutomations({ ...props }) {
  return <AutomationsV2 {...props} />
}

