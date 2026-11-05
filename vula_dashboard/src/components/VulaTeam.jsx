/**
 * VulaTeam.jsx — tenant team directory. Add members with their own WhatsApp, choose what
 * each can see (access) and which notifications they get by WhatsApp (per event).
 * Data and actions live in team/useTeam.js (shared with team/TeamV2.jsx).
 */
import TeamV2 from "./team/TeamV2";

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaTeam({ tenantId }) {
  return <TeamV2 tenantId={tenantId} />;
}

