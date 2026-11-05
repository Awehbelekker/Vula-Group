/**
 * VulaPayments.jsx — connect SA payment gateways (onboarding).
 * Pick a gateway → see exactly which keys it needs + where to find them → connect → set default.
 * Pay-links + checkout use the default. Secrets are write-only (never shown back).
 */
import PaymentsV2 from "./payments/PaymentsV2";

export default function VulaPayments({ tenantId }) {
  return <PaymentsV2 tenantId={tenantId} />;
}

