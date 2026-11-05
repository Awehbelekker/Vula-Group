/**
 * VulaBudget.jsx — Expense tracking + budget overview for a tenant.
 *
 * - Add expenses manually (or via Smart Scanner)
 * - See revenue (from orders) vs expenses vs profit
 * - Filter by month, export to CSV
 */

import BudgetV2 from './budget/BudgetV2'

export default function VulaBudget({ ...props }) {
  return <BudgetV2 {...props} />
}

