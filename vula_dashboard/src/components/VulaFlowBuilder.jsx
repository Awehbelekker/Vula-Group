/**
 * VulaFlowBuilder.jsx — owner-configurable WhatsApp conversation flows (MVP, migration 118).
 * A linear, keyword-branching flow: trigger phrases → a sequence of steps (prompt + expected
 * reply type + branch rules) → a terminal action drawn from the commerce_admin tool vocabulary.
 * Deliberately NOT a drag-and-drop canvas — same simple-form shell as VulaAutomations.jsx.
 */
import FlowBuilderV2 from './assistant/FlowBuilderV2'

export default function VulaFlowBuilder({ ...props }) {
  return <FlowBuilderV2 {...props} />
}

