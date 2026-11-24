/**
 * What kind of business the dashboard is showing (tenants.tenant_profile), for wording and
 * examples only — never for access. App.jsx sets it from GET /v1/tenants/{tid}.
 *
 * 24 Nov 2026 audit: every business saw one client's examples (yellowtail and snoek specials,
 * "Catch of the day", "Skipper Jan"). Fish wording is for a food business; everyone else gets
 * neutral examples (lib/examples.js).
 */
import { create } from 'zustand'

export const useBusinessStore = create((set) => ({
  businessType: null,      // food|retail|services|trades|health|real_estate|rep|other, null = not known yet
  displayName: '',
  setBusiness: ({ businessType, displayName } = {}) =>
    set({ businessType: businessType || null, displayName: displayName || '' }),
}))

export function useBusinessType() {
  return useBusinessStore((s) => s.businessType)
}

export function useIsFood() {
  return useBusinessStore((s) => s.businessType === 'food')
}
