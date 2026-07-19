/**
 * Auth store — holds the logged-in user + their tenant access.
 *
 * role: 'master'  → Ian, sees all tenants
 * role: 'owner'   → client, sees only their tenant_id
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { supabase } from '../lib/supabase'

export const useAuthStore = create(
  persist(
    (set) => ({
      user: null,          // { id, email, name }
      tenantId: null,      // 'off-the-hook' | 'master' | etc.
      role: null,          // 'master' | 'owner' | 'staff'
      access: [],          // module keys this member may see (empty = all)
      full: true,          // owner/manager (or no access list) → sees everything

      login: (user, tenantId, role) => set({ user, tenantId, role }),
      setMember: ({ access, full }) => set({ access: access || [], full: !!full }),
      // Sign-out must kill the SUPABASE session too — clearing only the store left the session
      // alive, and the login screen's mount effect immediately logged the user back in
      // (confirmed live 2026-07-17: sign-out was impossible).
      logout: async () => {
        try { await supabase.auth.signOut() } catch { /* still clear local state */ }
        set({ user: null, tenantId: null, role: null, access: [], full: true })
      },
    }),
    { name: 'vula-auth' }
  )
)
