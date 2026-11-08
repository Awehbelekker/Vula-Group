/**
 * Auth store — holds the logged-in user + their tenant access.
 *
 * role: 'master'  → Ian, sees all tenants
 * role: 'owner'   → client, sees only their tenant_id
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { supabase } from '../lib/supabase'
import { clearPersonData, readyToSignOut } from '../lib/deviceData'
import { turnOff as turnOffPush } from '../lib/push'

export const useAuthStore = create(
  persist(
    (set, get) => ({
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
      expired: false,      // the sign-in lapsed on its own (lib/session.js) — the login page says so

      login: (user, tenantId, role) => set({ user, tenantId, role, expired: false }),
      setMember: ({ access, full, role: teamRole, whatsapp }) =>
        set({ access: access || [], full: !!full, teamRole: teamRole || null, teamPhone: whatsapp || null }),
      // Sign-out must kill the SUPABASE session too — clearing only the store left the session
      // alive, and the login screen's mount effect immediately logged the user back in
      // (confirmed live 2026-07-17: sign-out was impossible).
      // 2026-10-27: and leaves nothing of this person on a shared phone (lib/deviceData.js) —
      // after asking, when a stock count's scans haven't been sent yet.
      logout: async () => {
        if (!(await readyToSignOut())) return
        // This device stops getting this person's alerts (lib/push.js) — while still signed in,
        // so the API can drop it.
        try { if (get().tenantId) await turnOffPush(get().tenantId) } catch { /* best effort */ }
        try { await supabase.auth.signOut() } catch { /* still clear local state */ }
        await clearPersonData()
        set({ user: null, tenantId: null, role: null, access: [], full: true, teamRole: null, teamPhone: null, expired: false })
      },
      // 8 Nov 2026: the Supabase session ended on its own (lib/session.js) while the store still
      // showed the person as signed in, so every API call came back 401 and lists were empty.
      // Back to the sign-in page with a note. Unlike logout this keeps unsent stock scans and
      // scanner photos: the same person usually signs straight back in.
      expire: async () => {
        try { await supabase.auth.signOut({ scope: 'local' }) } catch { /* already gone */ }
        set({ user: null, tenantId: null, role: null, access: [], full: true, teamRole: null, teamPhone: null, expired: true })
      },
    }),
    { name: 'vula-auth' }
  )
)
