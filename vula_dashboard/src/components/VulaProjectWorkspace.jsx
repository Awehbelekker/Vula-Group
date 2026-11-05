/**
 * VulaProjectWorkspace.jsx — "Claude Projects" for Vula. Pick a project → a scoped
 * workspace: named chat threads (left), project-aware chat (center), and a context rail
 * (right): editable brief, finances snapshot, recent docs, and to-dos (sync to ClickUp).
 */
import ProjectWorkspaceV2 from "./construction/ProjectWorkspaceV2";

export default function VulaProjectWorkspace({ ...props }) {
  return <ProjectWorkspaceV2 {...props} />;
}
