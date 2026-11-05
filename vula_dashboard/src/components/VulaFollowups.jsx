/**
 * VulaFollowups.jsx — emails awaiting a reply. Vula flags inbound questions/requests/
 * scheduling and nudges on WhatsApp daily until they're handled.
 */
import FollowupsV2 from "./followups/FollowupsV2";

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaFollowups({ tenantId }) {
  return <FollowupsV2 tenantId={tenantId} />;
}

