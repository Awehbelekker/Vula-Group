/**
 * Auth store — holds the logged-in user + their tenant access.
 *
 * role: 'master'  → Ian, sees all tenants
 * role: 'owner'   → client, sees only their tenant_id
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const useAuthStore = create(
  persist(
    (set) => ({
      user: null,          // { id, email, name }
      tenantId: null,      // 'off-the-hook' | 'master' | etc.
      role: null,          // 'master' | 'owner' | 'staff'

      login: (user, tenantId, role) => set({ user, tenantId, role }),
      logout: () => set({ user: null, tenantId: null, role: null }),
    }),
    { name: 'vula-auth' }
  )
)
