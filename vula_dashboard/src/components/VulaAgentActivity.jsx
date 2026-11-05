/**
 * VulaAgentActivity.jsx — a window into the AI agent: what it did (tools it called) and how it
 * thought (local vs cloud routing), plus a "teach it" box that stores a correction as KB the
 * assistants ground on. Backend: /admin/agent-activity + /admin/agent-teach (reuses telemetry 045).
 */
import AgentActivityV2 from "./assistant/AgentActivityV2"

export default function VulaAgentActivity({ ...props }) {
  return <AgentActivityV2 {...props} />
}

