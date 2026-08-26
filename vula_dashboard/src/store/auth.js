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
      teamRole: null,      // this member's vula_team_members.role (e.g. 'sales_rep') — distinct
                           // from the dashboard-login `role` above ('owner'/'staff'/'master'),
                           // which every restricted login still holds regardless of team role.
      teamPhone: null,     // this member's own vula_team_members.whatsapp — used to scope the
                           // sales-rep dashboard tabs (contacts/reminders/call sheet/etc.) to
                           // their own data without the frontend having to guess/pass it around.

      login: (user, tenantId, role) => set({ user, tenantId, role }),
      setMember: ({ access, full, role: teamRole, whatsapp }) =>
        set({ access: access || [], full: !!full, teamRole: teamRole || null, teamPhone: whatsapp || null }),
      // Sign-out must kill the SUPABASE session too — clearing only the store left the session
      // alive, and the login screen's mount effect immediately logged the user back in
      // (confirmed live 2026-07-17: sign-out was impossible).
      logout: async () => {
        try { await supabase.auth.signOut() } catch { /* still clear local state */ }
        set({ user: null, tenantId: null, role: null, access: [], full: true, teamRole: null, teamPhone: null })
      },
    }),
    { name: 'vula-auth' }
  )
)
