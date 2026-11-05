/**
 * VulaScheduledJobs.jsx — ⏰ Scheduling: per-tenant config for the 5 system WhatsApp jobs
 * (delivery briefing, low-stock alert, sales summary, weekly specials reminder, unpaid-order
 * chase). Times, template and on/off per job — previously hardcoded in the backend.
 * Backend: /v1/commerce/{tenant}/admin/scheduled-jobs (migration 069).
 */
import ScheduledJobsV2 from "./marketing/ScheduledJobsV2";

export default function VulaScheduledJobs({ ...props }) {
  return <ScheduledJobsV2 {...props} />;
}

