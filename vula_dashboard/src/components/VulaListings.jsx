/**
 * VulaListings.jsx — real-estate listings, enquiries and agents (2026-11-10, `listings` module).
 * Data and actions live in listings/useListings.js; the screen is listings/ListingsV2.jsx.
 */
import ListingsV2 from "./listings/ListingsV2";

export default function VulaListings({ tenantId }) {
  return <ListingsV2 tenantId={tenantId} />;
}
