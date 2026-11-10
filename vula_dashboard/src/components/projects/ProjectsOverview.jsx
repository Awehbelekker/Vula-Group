/**
 * ProjectsOverview.jsx — every project at a glance (10 Nov 2026, Ian: "when Judy logs in it's not
 * showing where DIGG is at, the status, or what's still owed"). GET /admin/projects/overview
 * (vula/commerce/project_tracker.overview): status, budget, spent, received and what's still owed to
 * the business per project, the total owed on top, and each project's warnings. The same figures as
 * each project's Money tab and its client link. Money access only — nothing shows otherwise.
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { formatMoney } from "../../lib/money";
import { Card, Skeleton, StatusChip } from "../ui/index.jsx";
import "./projects.css";

const money = (c) => formatMoney(c || 0, { cents: "always" });
const BASIS = { cost_plus: "cost + %", fixed_fee: "fixed fee", per_bill: "as per the bill" };

export default function ProjectsOverview({ tenantId, onOpen, compact = false }) {
  const [d, setD] = useState(undefined);
  useEffect(() => {
    let live = true;
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/projects/overview`)
      .then((r) => (r.ok ? r.json() : null)).then((x) => live && setD(x)).catch(() => live && setD(null));
    return () => { live = false; };
  }, [tenantId]);
  if (d === undefined) return <Card pad={4}><Skeleton lines={3} /></Card>;
  if (!d || !d.count) return null;
  const rows = compact ? d.projects.slice(0, 6) : d.projects;
  return (
    <Card pad={4} className="vpo">
      <div className="vpo-head">
        <h2 className="vpo-h">All projects at a glance</h2>
        <div className="vpo-tot">
          <span><small>Still owed to you</small><b className="vula-money">{money(d.owed_cents)}</b></span>
          <span><small>Received on projects</small><b className="vula-money">{money(d.received_cents)}</b></span>
          {d.warnings > 0 && <span><small>To check</small><b>{d.warnings}</b></span>}
        </div>
      </div>
      <ul className="vpo-list" aria-label="Projects">
        {rows.map((p) => (
          <li key={p.project}>
            <button type="button" className="vpo-row" onClick={() => onOpen?.(p.project)}>
              <span className="vpo-name">
                <b>{p.project}</b>
                <span className="vpo-sub">
                  {p.status && <StatusChip tone={p.status === "active" ? "accent" : "muted"}>{p.status}</StatusChip>}
                  {p.fee_basis ? BASIS[p.fee_basis] : "fee not agreed"}{p.has_final_account ? " · final account" : ""}
                  {p.warnings.length > 0 && <span className="vpo-warn"> · ⚠ {p.warnings.length}</span>}
                </span>
              </span>
              <span className="vpo-fig"><small>Budget</small>{p.budget_cents ? money(p.budget_cents) : "—"}</span>
              <span className="vpo-fig"><small>Spent</small>{money(p.spent_cents)}</span>
              <span className="vpo-fig"><small>Received</small>{money(p.received_cents)}</span>
              <span className="vpo-fig vpo-owed"><small>Still owed</small>
                {p.owed_cents == null ? <em title={p.owed_basis}>{p.owed_basis}</em> : money(p.owed_cents)}</span>
            </button>
          </li>))}
      </ul>
      {compact && d.projects.length > rows.length && <p className="vpo-more">…and {d.projects.length - rows.length} more on Projects</p>}
    </Card>
  );
}
