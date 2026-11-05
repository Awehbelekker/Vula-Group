/**
 * VulaAssistant.jsx — "Chat to Vula" in-portal admin assistant.
 *
 * Same AI agent that powers the owner's WhatsApp admin (commerce_admin skill),
 * available right inside the dashboard. The owner can run the shop by chatting:
 * sales, orders, stock, invoices, expenses, broadcast previews — and it takes
 * real actions via the admin tools.
 */
import AssistantV2 from './assistant/AssistantV2'

export default function VulaAssistant({ ...props }) {
  return <AssistantV2 {...props} />
}

