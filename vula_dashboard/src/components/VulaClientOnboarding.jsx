/**
 * VulaClientOnboarding.jsx — merchant-facing panel: introduce the WhatsApp line to imported clients.
 * Staci sends the approved intro/opt-in template in small batches (protects the number's quality
 * rating), previews to her own phone first, and watches the opt-in funnel fill.
 * (Distinct from VulaOnboarding.jsx, which is the Vula platform trial-signup wizard.)
 */
import ClientOnboardingV2 from "./marketing/ClientOnboardingV2";

export default function VulaClientOnboarding({ ...props }) {
  return <ClientOnboardingV2 {...props} />;
}

