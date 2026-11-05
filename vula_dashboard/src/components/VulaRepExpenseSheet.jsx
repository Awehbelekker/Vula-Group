/**
 * VulaRepExpenseSheet.jsx — configure the rep's monthly expense-claim sheet (recipient email,
 * day of month, budget) and see this month's spend-vs-budget progress at a glance. Thin wrapper
 * — reuses the same schedule/budget columns the WhatsApp configure_expense_sheet tool already
 * writes to (vula/commerce/expense_sheet.py, migrations 139/140).
 */
import RepExpenseSheetV2 from "./rep/RepExpenseSheetV2";

export default function VulaRepExpenseSheet({ ...props }) {
  return <RepExpenseSheetV2 {...props} />;
}
