/**
 * VulaCustomers.jsx — client list / lightweight CRM for a tenant.
 *
 * Aggregates customers from orders + WhatsApp conversation contacts (auto-captured
 * by the AI assistant — no form). Search, segment by broadcast audience, and see
 * exactly who a campaign would reach. WhatsApp any customer in one tap.
 * Data and actions live in customers/useCustomers.js (shared with customers/CustomersV2.jsx).
 */

import CustomersV2 from './customers/CustomersV2'

/** v2: the redesigned Customers (same hook, so the same data and actions). */
export default function VulaCustomers({ tenantId }) {
  return <CustomersV2 tenantId={tenantId} />;
}

