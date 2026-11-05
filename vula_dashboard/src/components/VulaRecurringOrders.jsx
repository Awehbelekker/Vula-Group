/**
 * VulaRecurringOrders.jsx — customer product subscriptions ("fish every Friday").
 * Standing orders that auto-create a real order each cycle. Backend: /v1/subscriptions/{tenant} (migration 051).
 * Distinct from Vula's own SaaS plan billing (VulaSubscriptions).
 */
import RecurringV2 from "./recurring/RecurringV2";

export default function VulaRecurringOrders({ tenantId }) {
  return <RecurringV2 tenantId={tenantId} />;
}

