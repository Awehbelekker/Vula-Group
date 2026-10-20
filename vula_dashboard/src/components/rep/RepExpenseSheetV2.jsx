/**
 * RepExpenseSheetV2.jsx — the redesigned My Work › Claim sheet (2026-10-20, UX redesign Phase F).
 * Same calls as VulaRepExpenseSheet (rep/useRepExpenseSheet.js): where and when the monthly claim
 * workbook goes, the monthly budget, and this month's spend against it (integer cents from the
 * server). The fields are filled from the saved sheet and save when they change.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, Receipt } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { formatMoney } from "../../lib/money";
import { Card, EmptyState, Field, Input, Select, Skeleton } from "../ui/index.jsx";
import { useRepExpenseSheet } from "./useRepExpenseSheet";
import "./rep.css";

export default function RepExpenseSheetV2({ tenantId, repPhone }) {
  const x = useRepExpenseSheet(tenantId, repPhone);
  const c = x.config;
  const savedBudget = x.budgetCents ? String(x.budgetCents / 100) : "";
  const [email, setEmail] = useState("");
  const [budget, setBudget] = useState("");
  useEffect(() => { setEmail(c.expense_sheet_recipient_email || ""); setBudget(savedBudget); }, [c]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!repPhone) return <EmptyState Icon={Receipt} title="No rep phone number">Couldn't identify your rep phone number. Ask the owner to check your team login.</EmptyState>;
  if (!x.loaded) return <Card><Skeleton lines={4} /></Card>;
  const tone = x.pct >= 100 ? " vrp-meter--over" : x.pct >= 90 ? " vrp-meter--warn" : "";
  return (
    <section className="vrp" aria-label="My claim sheet">
      <p className="vrp-sub">Every receipt you scan on WhatsApp is added by itself. A workbook with the receipt photos attached goes out on your schedule.</p>

      <Card className="vrp-card" role="group" aria-labelledby="vrp-es-month">
        <h2 className="vrp-h" id="vrp-es-month">This month so far</h2>
        <p className="vrp-money">{formatMoney(x.mtdCents, { cents: "always" })}</p>
        <p className="vrp-muted">{x.budgetCents ? `of a ${formatMoney(x.budgetCents)} budget` : "No budget set"}</p>
        {x.budgetCents > 0 && (
          <div className={`vrp-meter${tone}`} role="progressbar" aria-label="Spent of budget" aria-valuemin={0} aria-valuemax={100} aria-valuenow={x.pct}>
            <span style={{ width: `${x.pct}%` }} />
          </div>)}
        {x.pct >= 90 && <p className="vrp-warn"><AlertTriangle size={16} aria-hidden="true" />{x.pct >= 100 ? "Over budget for this month." : "Getting close to your monthly budget."}</p>}
      </Card>

      <Card className="vrp-card" role="group" aria-labelledby="vrp-es-schedule">
        <h2 className="vrp-h" id="vrp-es-schedule">Schedule</h2>
        <div className="vrp-grid">
          <Field label="Email it to">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              onBlur={() => email !== (c.expense_sheet_recipient_email || "") && x.saveConfig({ recipient_email: email })} />
          </Field>
          <Field label="Day of the month">
            <Select value={c.expense_sheet_day_of_month ?? 1} onChange={(e) => x.saveConfig({ day_of_month: Number(e.target.value) })}>
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
            </Select>
          </Field>
          <Field label="Monthly budget (R)" hint="Leave empty for no budget">
            <Input type="number" min="0" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)}
              onBlur={() => budget !== savedBudget && x.saveConfig({ budget_rands: budget === "" ? 0 : Number(budget) })} />
          </Field>
        </div>
        {x.msg && <p className="vrp-error" role="alert">{x.msg}</p>}
        {c.expense_sheet_last_sent_at && <p className="vrp-muted">Last sent {new Date(c.expense_sheet_last_sent_at).toLocaleString("en-ZA", { timeZone: SA_TZ, dateStyle: "medium", timeStyle: "short" })}</p>}
      </Card>
    </section>
  );
}
