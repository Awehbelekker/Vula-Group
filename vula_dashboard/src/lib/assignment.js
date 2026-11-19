/**
 * assignment.js — which business (and as what) a login enters (19 Nov 2026).
 *
 * A login can belong to several businesses (vula_tenant_users rows). Master wins, then the
 * business the person owns, then a team login. An accountant's login (view-only, never also a team
 * login) opens the client they last had open, else the first; they move between clients in the
 * workspace (store/auth.switchTenant).
 */
const ORDER = { master: 0, owner: 1, staff: 2, accountant: 3 };

export function pickAssignment(rows, remembered) {
  const list = (rows || []).filter((r) => r && r.tenant_id && r.role in ORDER);
  if (!list.length) return null;
  const sorted = [...list].sort((a, b) => ORDER[a.role] - ORDER[b.role] || String(a.tenant_id).localeCompare(String(b.tenant_id)));
  const best = sorted[0];
  if (best.role !== 'accountant') return best;
  let last = remembered;
  if (last === undefined) {
    try { last = localStorage.getItem('vula.accountant.tenant'); } catch { last = null; }
  }
  return sorted.find((r) => r.tenant_id === last) || best;
}
