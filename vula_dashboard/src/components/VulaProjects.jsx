/**
 * VulaProjects.jsx — Vula Projects command centre.
 *
 * Projects grouped by client, each with its phases (a phase is its own project with its own
 * BOQ, budget and documents, rolled up under the main project), the other names it's filed
 * under, and one page per project: documents (by type, upload straight to the project), BOQ,
 * money, standards and team. Plus the master code library. 2026-10-02 (Ian: "how can a tenant
 * add projects and aliases, phases, more projects for one client, each with their own BOQ and
 * filed invoices and slips?").
 * Data and actions live in projects/useProjects.js (shared with projects/ProjectsV2.jsx).
 */
import ProjectsV2 from "./projects/ProjectsV2";

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaProjects({ tenantId }) {
  return <ProjectsV2 tenantId={tenantId} />;
}

