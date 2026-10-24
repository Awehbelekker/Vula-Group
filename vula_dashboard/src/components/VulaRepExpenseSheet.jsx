/**
 * VulaRepExpenseSheet.jsx — configure the rep's monthly expense-claim sheet (recipient email,
 * day of month, budget) and see this month's spend-vs-budget progress at a glance. Thin wrapper
 * — reuses the same schedule/budget columns the WhatsApp configure_expense_sheet tool already
 * writes to (vula/commerce/expense_sheet.py, migrations 139/140).
 */
import { useRepExpenseSheet } from "./rep/useRepExpenseSheet";
import RepExpenseSheetV2 from "./rep/RepExpenseSheetV2";
import { sidePad } from "../lib/layout";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)",
            amber: "var(--warn)", text: "var(--text)", muted: "var(--muted)" };
const R = (cents) => `R${((Number(cents) || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function VulaRepExpenseSheetV1({ tenantId, repPhone }) {
  const { config, mtdCents, msg, saveConfig, budgetCents, pct } = useRepExpenseSheet(tenantId, repPhone);

  if (!repPhone) return <div style={{ padding: 20, color: C.muted, fontSize: 13 }}>Couldn't identify your rep phone number.</div>;

  const barColor = pct >= 100 ? C.red : pct >= 90 ? C.amber : C.green;

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: `20px ${sidePad(20)}` }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: C.text, margin: "0 0 4px" }}>My Expense Sheet</h2>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 16px" }}>
        Every receipt you scan on WhatsApp is added automatically. A compiled workbook with the
        receipt photos attached goes out on your schedule below.
      </p>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>Schedule</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input placeholder="Recipient email" defaultValue={config.expense_sheet_recipient_email || ""}
                 onBlur={(e) => saveConfig({ recipient_email: e.target.value })}
                 style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, minWidth: 220 }} />
          <label style={{ fontSize: 13, color: C.muted, display: "flex", alignItems: "center", gap: 6 }}>
            Day of month
            <select defaultValue={config.expense_sheet_day_of_month ?? 1}
                    onChange={(e) => saveConfig({ day_of_month: Number(e.target.value) })}
                    style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13 }}>
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 13, color: C.muted, display: "flex", alignItems: "center", gap: 6 }}>
            Monthly budget (R)
            <input type="number" min="0" placeholder="e.g. 2000" defaultValue={budgetCents ? budgetCents / 100 : ""}
                   onBlur={(e) => saveConfig({ budget_rands: e.target.value === "" ? 0 : Number(e.target.value) })}
                   style={{ padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, width: 100 }} />
          </label>
        </div>
        {msg && <div style={{ fontSize: 12.5, color: C.red, marginTop: 8 }}>{msg}</div>}
        {config.expense_sheet_last_sent_at && (
          <div style={{ fontSize: 11.5, color: C.muted, marginTop: 8 }}>
            Last sent {new Date(config.expense_sheet_last_sent_at).toLocaleString()}
          </div>
        )}
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>This month so far</div>
        <div style={{ fontSize: 13, color: C.text, marginBottom: 8 }}>
          {R(mtdCents)}{budgetCents ? ` of ${R(budgetCents)} budget` : " spent (no budget set)"}
        </div>
        {budgetCents > 0 && (
          <div style={{ background: "var(--surface-alt)", borderRadius: 6, height: 10, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: barColor, transition: "width 0.2s" }} />
          </div>
        )}
        {pct >= 90 && (
          <div style={{ fontSize: 12, color: barColor, marginTop: 8 }}>
            {pct >= 100 ? "⚠️ Over budget for this month." : "⚠️ Getting close to your monthly budget."}
          </div>
        )}
      </div>
    </div>
  );
}

export default function VulaRepExpenseSheet({ v2 = false, ...props }) {
  return v2 ? <RepExpenseSheetV2 {...props} /> : <VulaRepExpenseSheetV1 {...props} />;
}
