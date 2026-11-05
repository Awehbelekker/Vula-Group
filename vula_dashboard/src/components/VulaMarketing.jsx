/**
 * VulaMarketing.jsx — AI marketing copy generator.
 * "Write today's specials post / a product description / promo copy / a broadcast blast."
 * Grounded in the tenant's real catalogue. Backend: /v1/commerce/{tenant}/admin/marketing/generate
 */
import MarketingCopyV2 from "./marketing/MarketingCopyV2";

export default function VulaMarketing({ ...props }) {
  return <MarketingCopyV2 {...props} />;
}

